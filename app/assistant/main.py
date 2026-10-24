"""FastAPI app: chat channel in/out, daily digest scheduler, LinkedIn OAuth."""
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import digest, linkedin
from .agent import Agent
from .channels import make_channel
from .channels.whatsapp import parse_webhook
from .config import get_settings
from .llm import LLM
from .memory import Memory
from .tools import build_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("assistant")

settings = get_settings()
memory = Memory(settings.db_path)
agent = Agent(LLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout,
              think=not settings.llm_no_think),
              build_tools(settings, memory), memory, settings)
channel = make_channel(settings)
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)  # one run at a time per chat
running: set[asyncio.Task] = set()



async def process(chat_id: str, text: str) -> None:
    async with locks[chat_id]:
        await channel.typing(chat_id, True)
        try:
            reply = await agent.handle(chat_id, text)
        except Exception as e:
            log.exception("agent failed")
            reply = f"⚠️ Something broke: {e}"
        finally:
            await channel.typing(chat_id, False)
        await channel.send(chat_id, reply)


def background(coro) -> None:
    task = asyncio.create_task(coro)  # don't block the listener while the LLM works
    running.add(task)
    task.add_done_callback(running.discard)


async def on_message(chat_id: str, text: str) -> None:
    if chat_id not in settings.allowed_chats:
        log.warning("ignored message from non-allowlisted chat %s (add to ALLOWED_CHAT_IDS if it's you)", chat_id)
        return
    if text.strip().lower() == "/digest":
        await channel.send(chat_id, "Building today's digest now - this takes a while on the N100, I'll send it when done.")
        background(make_digest(chat_id, send_now=True))
        return
    background(process(chat_id, text))


async def make_digest(chat_id: str, send_now: bool) -> None:
    """Build the digest; send it straight away, or park it until DIGEST_SEND_TIME."""
    log.info("building digest")
    try:
        text = await digest.build_digest(agent.llm, settings)
    except Exception as e:
        log.exception("digest failed")
        text = f"⚠️ Digest failed: {e}"
    log.info("digest built")
    if send_now:
        await deliver_digest(chat_id, text)
    else:
        memory.put("pending_digest", text)


async def deliver_digest(chat_id: str, text: str | None = None) -> None:
    text = text or memory.get("pending_digest")
    if not text:
        return
    memory.put("pending_digest", "")
    memory.add_turn(chat_id, "user", "[daily digest]")
    memory.add_turn(chat_id, "assistant", text)  # so follow-ups like "draft a post on 2" work
    await channel.send(chat_id, text)


def at(hhmm: str) -> CronTrigger:
    hh, mm = hhmm.split(":")
    return CronTrigger(hour=int(hh), minute=int(mm))


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    owner = settings.owner_chat_id
    if settings.digest_time and owner:
        separate_send = bool(settings.digest_send_time)
        scheduler.add_job(make_digest, at(settings.digest_time), args=[owner, not separate_send],
                          misfire_grace_time=3600)
        if separate_send:
            scheduler.add_job(deliver_digest, at(settings.digest_send_time), args=[owner], misfire_grace_time=3600)
        scheduler.start()
        log.info("digest: build at %s, send at %s", settings.digest_time, settings.digest_send_time or "when built")
    listener = asyncio.create_task(channel.listen(on_message))
    log.info("channel=%s model=%s tools: %s", channel.name, settings.llm_model, ", ".join(agent.tools))
    yield
    listener.cancel()
    if scheduler.running:
        scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.post("/webhook/waha")
async def waha_webhook(request: Request):
    if settings.channel == "whatsapp" and (msg := parse_webhook(await request.json())):
        await on_message(*msg)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True, "channel": channel.name, "model": settings.llm_model, "tools": list(agent.tools)}


@app.get("/linkedin/login")
async def linkedin_login():
    return RedirectResponse(linkedin.login_url(settings, memory))


@app.get("/linkedin/callback", response_class=HTMLResponse)
async def linkedin_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return f"LinkedIn error: {error}"
    name = await linkedin.handle_callback(settings, memory, code, state)
    return f"<h3>Connected LinkedIn as {name}. You can close this tab.</h3>"


@app.post("/digest")
async def trigger_digest():
    """Build and send the digest now (handy for demos): curl -X POST localhost:8000/digest"""
    background(make_digest(settings.owner_chat_id, send_now=True))
    return {"ok": True}
