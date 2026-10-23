"""Signal via signal-cli-rest-api (json-rpc mode, websocket receive).

Typical setup without a spare number: link signal-cli as a *linked device* on your own account and
chat with the bot in "Note to Self". Bot replies are prefixed so they're never re-read as commands.
"""
import asyncio
import json
import logging

import httpx
import websockets

log = logging.getLogger(__name__)
BOT_PREFIX = "🤖"


def parse_envelope(event: dict, own_number: str) -> tuple[str, str] | None:
    env = event.get("envelope") or {}
    sent = (env.get("syncMessage") or {}).get("sentMessage")
    if sent:  # something sent from one of *my* devices: only Note to Self counts
        to_self = own_number in (sent.get("destinationNumber"), sent.get("destination")) or (
            sent.get("destinationUuid") and sent.get("destinationUuid") == env.get("sourceUuid"))
        text = sent.get("message") or ""
        if to_self and text and not text.startswith(BOT_PREFIX):
            return own_number, text
        return None
    data = env.get("dataMessage") or {}
    if data.get("message") and not (data.get("groupInfo") or data.get("groupV2")):
        return env.get("sourceNumber") or env.get("source"), data["message"]
    return None


class Signal:
    name = "signal"

    def __init__(self, url: str, number: str):
        self.url, self.number = url.rstrip("/"), number
        self.client = httpx.AsyncClient(base_url=self.url, timeout=30)

    async def send(self, chat_id: str, text: str) -> None:
        r = await self.client.post("/v2/send", json={"message": f"{BOT_PREFIX} {text}", "number": self.number,
                                                     "recipients": [chat_id]})
        r.raise_for_status()

    async def typing(self, chat_id: str, on: bool) -> None:
        pass  # pointless in Note to Self

    async def listen(self, on_message) -> None:
        ws_url = self.url.replace("http", "ws", 1) + f"/v1/receive/{self.number}"
        while True:
            try:
                async with websockets.connect(ws_url, ping_interval=30) as ws:
                    log.info("listening on Signal for %s", self.number)
                    async for raw in ws:
                        if msg := parse_envelope(json.loads(raw), self.number):
                            await on_message(*msg)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("signal websocket dropped; reconnecting")
                await asyncio.sleep(5)
