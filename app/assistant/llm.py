"""Minimal OpenAI-compatible chat client with tool calling (works with Ollama, llama.cpp, vLLM, cloud APIs)."""
import json
import re
import uuid
from dataclasses import dataclass, field

import httpx

THINK_RE = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    error: str | None = None


@dataclass
class LLMReply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)  # prompt_tokens / completion_tokens when the server reports them

    def as_message(self) -> dict:
        """Assistant message to append to the transcript before tool results."""
        msg = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                                 for c in self.tool_calls]
        return msg


def _parse_args(raw) -> tuple[dict, str | None]:
    if isinstance(raw, dict):
        return raw, None
    try:
        args = json.loads(raw or "{}")
        return (args, None) if isinstance(args, dict) else ({}, "arguments must be a JSON object")
    except json.JSONDecodeError as e:
        return {}, f"invalid JSON arguments: {e}"


def parse_reply(msg: dict, known_tools: set[str]) -> LLMReply:
    content = THINK_RE.sub("", msg.get("content") or "").strip()
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        args, err = _parse_args(fn.get("arguments"))
        calls.append(ToolCall(tc.get("id") or uuid.uuid4().hex[:8], fn.get("name", ""), args, err))

    # Small models sometimes write the tool call as JSON text instead of using the tool_calls field.
    if not calls and content.startswith("{"):
        try:
            obj = json.loads(content.strip("` \n"))
            if isinstance(obj, dict) and obj.get("name") in known_tools:
                args, err = _parse_args(obj.get("arguments", obj.get("parameters", {})))
                calls.append(ToolCall(uuid.uuid4().hex[:8], obj["name"], args, err))
                content = ""
        except json.JSONDecodeError:
            pass
    return LLMReply(content, calls)


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 300):
        self.model = model
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout,
                                        headers={"Authorization": f"Bearer {api_key}"})

    async def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        body = {"model": self.model, "messages": messages, "temperature": 0.3}
        if tools:
            body["tools"] = tools
        r = await self.client.post("/chat/completions", json=body)
        r.raise_for_status()
        known = {t["function"]["name"] for t in tools}
        data = r.json()
        reply = parse_reply(data["choices"][0]["message"], known)
        reply.usage = data.get("usage") or {}
        return reply
