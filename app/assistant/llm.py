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
    content = THINK_RE.sub("", msg.get("content") or "")
    if "</think>" in content:  # thinking-only models can emit reasoning with just a closing tag
        content = content.rsplit("</think>", 1)[1]
    content = content.strip()
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


def to_ollama_messages(messages: list[dict]) -> list[dict]:
    """Ollama's native API wants tool-call arguments as objects, not JSON strings."""
    out = []
    for m in messages:
        if m.get("tool_calls"):
            m = {**m, "tool_calls": [{"function": {"name": c["function"]["name"],
                                                   "arguments": json.loads(c["function"]["arguments"])}}
                                     for c in m["tool_calls"]]}
        out.append(m)
    return out


class LLM:
    """Talks to Ollama's native /api/chat when the URL points at Ollama (so thinking can be switched off
    reliably), otherwise to any OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 300, think: bool = False):
        self.model, self.think = model, think
        base_url = base_url.rstrip("/")
        self.native = ":11434" in base_url or "ollama" in base_url
        if self.native:
            base_url = base_url.removesuffix("/v1")
        self.client = httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                        headers={"Authorization": f"Bearer {api_key}"})

    async def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        known = {t["function"]["name"] for t in tools}
        if self.native:
            return await self._chat_ollama(messages, tools, known)
        body = {"model": self.model, "messages": messages, "temperature": 0.3}
        if tools:
            body["tools"] = tools
        r = await self.client.post("/chat/completions", json=body)
        r.raise_for_status()
        data = r.json()
        reply = parse_reply(data["choices"][0]["message"], known)
        reply.usage = data.get("usage") or {}
        return reply

    async def _chat_ollama(self, messages: list[dict], tools: list[dict], known: set[str]) -> LLMReply:
        body = {"model": self.model, "messages": to_ollama_messages(messages), "stream": False,
                "options": {"temperature": 0.3}, "keep_alive": "24h"}
        if tools:
            body["tools"] = tools
        if self.think is not None:
            body["think"] = self.think
        r = await self.client.post("/api/chat", json=body)
        if r.status_code == 400 and "thinking" in r.text:  # model has no thinking mode: drop the flag
            self.think = None
            return await self._chat_ollama(messages, tools, known)
        r.raise_for_status()
        data = r.json()
        reply = parse_reply(data["message"], known)
        reply.usage = {"prompt_tokens": data.get("prompt_eval_count", 0),
                       "completion_tokens": data.get("eval_count", 0),
                       "prompt_s": round(data.get("prompt_eval_duration", 0) / 1e9, 1),
                       "generate_s": round(data.get("eval_duration", 0) / 1e9, 1)}
        return reply
