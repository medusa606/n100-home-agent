"""WhatsApp via a self-hosted WAHA container (outbound connection only - no public webhook needed)."""
import logging

import httpx

log = logging.getLogger(__name__)


class WhatsApp:
    name = "whatsapp"

    def __init__(self, url: str, api_key: str, session: str):
        self.session = session
        self.client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=30, headers={"X-Api-Key": api_key})

    async def send(self, chat_id: str, text: str) -> None:
        r = await self.client.post("/api/sendText", json={"session": self.session, "chatId": chat_id, "text": text})
        r.raise_for_status()

    async def typing(self, chat_id: str, on: bool) -> None:
        try:  # cosmetic; the N100 takes a few seconds per reply so it's nice feedback
            await self.client.post("/api/startTyping" if on else "/api/stopTyping",
                                   json={"session": self.session, "chatId": chat_id})
        except httpx.HTTPError:
            pass

    async def listen(self, on_message) -> None:
        """WAHA pushes messages to POST /webhook/waha instead (see main.py)."""


def parse_webhook(event: dict) -> tuple[str, str] | None:
    """Return (chat_id, text) for an incoming text message, else None."""
    if event.get("event") != "message":
        return None
    p = event.get("payload") or {}
    if p.get("fromMe") or not p.get("body") or str(p.get("from", "")).endswith("@g.us"):
        return None  # ignore our own messages, media without text, and groups
    return p["from"], p["body"]
