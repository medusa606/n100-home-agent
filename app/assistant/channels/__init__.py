"""Chat channels. Each one can send, show 'typing', and listen for messages.

Telegram and Signal poll outwards; WhatsApp (WAHA) pushes to a webhook. None need inbound ports.
"""
from typing import Awaitable, Callable, Protocol

OnMessage = Callable[[str, str], Awaitable[None]]  # (chat_id, text)


class Channel(Protocol):
    name: str

    async def send(self, chat_id: str, text: str) -> None: ...
    async def typing(self, chat_id: str, on: bool) -> None: ...
    async def listen(self, on_message: OnMessage) -> None: ...


def make_channel(settings) -> Channel:
    if settings.channel == "telegram":
        from .telegram import Telegram
        return Telegram(settings.telegram_bot_token)
    if settings.channel == "signal":
        from .signal import Signal
        return Signal(settings.signal_url, settings.signal_number)
    if settings.channel == "whatsapp":
        from .whatsapp import WhatsApp
        return WhatsApp(settings.waha_url, settings.waha_api_key, settings.waha_session)
    raise ValueError(f"unknown CHANNEL '{settings.channel}' (telegram, signal or whatsapp)")
