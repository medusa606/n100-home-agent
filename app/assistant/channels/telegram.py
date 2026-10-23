"""Telegram Bot API via long polling. No phone number for the bot, no inbound ports, officially supported."""
import asyncio
import logging

import httpx

log = logging.getLogger(__name__)
MAX_LEN = 4000  # Telegram's limit is 4096


def parse_update(update: dict) -> tuple[str, str] | None:
    msg = update.get("message") or {}
    if msg.get("chat", {}).get("type") != "private" or not msg.get("text"):
        return None  # ignore groups, stickers, photos...
    return str(msg["chat"]["id"]), msg["text"]


class Telegram:
    name = "telegram"

    def __init__(self, token: str):
        self.client = httpx.AsyncClient(base_url=f"https://api.telegram.org/bot{token}", timeout=70)
        self._typing: dict[str, asyncio.Task] = {}

    async def send(self, chat_id: str, text: str) -> None:
        for i in range(0, max(len(text), 1), MAX_LEN):
            r = await self.client.post("/sendMessage", json={"chat_id": chat_id, "text": text[i:i + MAX_LEN]})
            r.raise_for_status()

    async def typing(self, chat_id: str, on: bool) -> None:
        if on:
            self._typing[chat_id] = asyncio.create_task(self._keep_typing(chat_id))
        elif task := self._typing.pop(chat_id, None):
            task.cancel()

    async def _keep_typing(self, chat_id: str) -> None:
        while True:  # Telegram's typing indicator only lasts ~5 s, so refresh it while the LLM thinks
            try:
                await self.client.post("/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
            except httpx.HTTPError:
                pass
            await asyncio.sleep(4)

    async def listen(self, on_message) -> None:
        offset = 0
        while True:
            try:
                r = await self.client.get("/getUpdates", params={"timeout": 50, "offset": offset,
                                                                 "allowed_updates": '["message"]'})
                r.raise_for_status()
                for update in r.json()["result"]:
                    offset = update["update_id"] + 1
                    if msg := parse_update(update):
                        await on_message(*msg)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram polling failed; retrying")
                await asyncio.sleep(5)
