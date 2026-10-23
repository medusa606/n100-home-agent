"""Tool registry. Each tool is an async function plus a JSON schema the LLM sees.

`confirm` (optional) inspects the arguments and returns a human-readable prompt if the
action must be approved in chat before it runs; None means run immediately.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Awaitable[str]]
    confirm: Callable[[dict], str | None] | None = None

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def params(required: list[str] | None = None, **props) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def build_tools(settings, memory) -> dict[str, Tool]:
    from . import home, system, web
    from .. import linkedin

    tools = web.make_tools(settings) + system.make_tools(settings) + linkedin.make_tools(settings, memory)
    if settings.ha_token:
        tools += home.make_tools(settings)
    return {t.name: t for t in tools}
