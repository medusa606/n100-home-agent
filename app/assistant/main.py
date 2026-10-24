"""FastAPI app: chat channel in/out, daily digest scheduler, LinkedIn OAuth."""
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import linkedin
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

DIGEST_PROMPT = ("Morning digest. 1) Use web_search with category news, time_range day, to find the 3 most relevant "
                 "stories for my interests. 2) Use find_events for 2 upcoming events near me. Reply with a numbered "
                 "list: one line + URL each. End with: 'Reply e.g. \"draft a post on 2\"'.")


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


async def on_message(chat_id: str, text: str) -> None:
    if chat_id not in settings.allowed_chats:
        log.warning("ignored message from non-allowlisted chat %s (add to ALLOWED_CHAT_IDS if it's you)", chat_id)
        return
    task = asyncio.create_task(process(chat_id, text))  # don't block the listener while the LLM works
    running.add(task)
    task.add_done_callback(running.discard)


async def daily_digest() -> None:
    chat = settings.owner_chat_id
    async with locks[chat]:
        reply = await agent.run(chat, DIGEST_PROMPT)
        memory.add_turn(chat, "user", "[scheduled morning digest]")
        memory.add_turn(chat, "assistant", reply)  # so follow-ups like "draft a post on 2" work
    await channel.send(chat, reply)


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    if settings.digest_time and settings.owner_chat_id:
        hh, mm = settings.digest_time.split(":")
        scheduler.add_job(daily_digest, CronTrigger(hour=int(hh), minute=int(mm)))
        scheduler.start()
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
    """Fire the morning digest now (handy for demos): curl -X POST localhost:8000/digest"""
    task = asyncio.create_task(daily_digest())
    running.add(task)
    task.add_done_callback(running.discard)
    return {"ok": True}
