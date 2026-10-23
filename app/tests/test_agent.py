import pytest

from assistant.agent import Agent
from assistant.config import Settings
from assistant.llm import LLMReply, ToolCall, parse_reply
from assistant.memory import Memory
from assistant.tools import Tool, home, params
from assistant.tools.web import is_public_url
from assistant.channels.whatsapp import parse_webhook


class FakeLLM:
    model = "fake"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    async def chat(self, messages, tools):
        self.seen.append(messages)
        return self.replies.pop(0)


def make_agent(replies, extra_tools=()):
    s = Settings(db_path=":memory:", _env_file=None)
    calls = []

    async def echo(text: str) -> str:
        calls.append(text)
        return f"echo:{text}"

    tools = {"echo": Tool("echo", "", params(["text"], text={"type": "string"}), echo),
             "publish": Tool("publish", "", params(["text"], text={"type": "string"}), echo,
                             confirm=lambda a: f"Publish {a['text']}?")}
    for t in extra_tools:
        tools[t.name] = t
    llm = FakeLLM(replies)
    return Agent(llm, tools, Memory(":memory:"), s), llm, calls


async def test_plain_reply_is_stored_in_history():
    agent, llm, _ = make_agent([LLMReply("hi there")])
    assert await agent.handle("c", "hello") == "hi there"
    assert agent.memory.history("c", 5)[-1] == {"role": "assistant", "content": "hi there"}


async def test_tool_result_is_fed_back_to_model():
    agent, llm, calls = make_agent([LLMReply("", [ToolCall("1", "echo", {"text": "x"})]), LLMReply("done")])
    assert await agent.handle("c", "go") == "done"
    assert calls == ["x"]
    assert llm.seen[1][-1] == {"role": "tool", "tool_call_id": "1", "content": "echo:x"}


async def test_confirmation_gate_then_yes_executes():
    agent, _, calls = make_agent([LLMReply("", [ToolCall("1", "publish", {"text": "post"})])])
    reply = await agent.handle("c", "publish it")
    assert "Publish post?" in reply and calls == []
    assert (await agent.handle("c", "yes")).startswith("✅")
    assert calls == ["post"]


async def test_other_message_discards_pending_and_ok_goes_to_llm():
    agent, _, calls = make_agent([LLMReply("", [ToolCall("1", "publish", {"text": "p"})]),
                                  LLMReply("changed my mind?"), LLMReply("cool")])
    await agent.handle("c", "publish")
    await agent.handle("c", "actually wait")
    assert await agent.handle("c", "ok") == "cool"  # no pending -> normal chat, not an execution
    assert calls == []


async def test_unknown_tool_and_bad_args_are_reported_to_model():
    agent, llm, _ = make_agent([LLMReply("", [ToolCall("1", "nope", {}), ToolCall("2", "echo", {"wrong": 1})]),
                                LLMReply("sorry")])
    await agent.handle("c", "x")
    results = [m["content"] for m in llm.seen[1] if m["role"] == "tool"]
    assert results[0].startswith("ERROR: unknown tool") and results[1].startswith("ERROR: bad arguments")


async def test_step_limit():
    loop = [LLMReply("", [ToolCall(str(i), "echo", {"text": "x"})]) for i in range(10)]
    agent, _, _ = make_agent(loop)
    assert "ran out of steps" in await agent.handle("c", "x")


def test_parse_reply_strips_think_and_recovers_json_tool_call():
    r = parse_reply({"content": '<think>hmm</think>{"name": "echo", "arguments": {"text": "a"}}'}, {"echo"})
    assert r.tool_calls[0].name == "echo" and r.tool_calls[0].args == {"text": "a"}
    r = parse_reply({"content": "", "tool_calls": [{"id": "1", "function": {"name": "echo", "arguments": "{bad"}}]},
                    {"echo"})
    assert r.tool_calls[0].error


async def test_home_assistant_allowlist_and_sensitive_confirm():
    s = Settings(ha_token="t", ha_allowed_domains="light,lock", ha_confirm_domains="lock", _env_file=None)
    tools = {t.name: t for t in home.make_tools(s)}
    call = tools["ha_call_service"]
    assert (await call.func("alarm_control_panel", "disarm", "alarm_control_panel.house")).startswith("ERROR")
    assert (await call.func("shell_command", "x", "light.kitchen")).startswith("ERROR")
    assert call.confirm({"domain": "lock", "service": "unlock", "entity_id": "lock.front"})
    assert call.confirm({"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"}) is None


@pytest.mark.parametrize("url", ["http://localhost:8123", "http://192.168.1.5/", "http://127.0.0.1",
                                 "file:///etc/passwd", "http://10.0.0.1:3000/api"])
async def test_ssrf_guard_blocks_private(url):
    assert not await is_public_url(url)


def test_parse_webhook_filters():
    msg = lambda **p: {"event": "message", "payload": {"from": "1@c.us", "body": "hi", **p}}
    assert parse_webhook(msg()) == ("1@c.us", "hi")
    assert parse_webhook(msg(fromMe=True)) is None
    assert parse_webhook(msg(**{"from": "x@g.us"})) is None
    assert parse_webhook({"event": "session.status"}) is None


def test_telegram_parse_update():
    from assistant.channels.telegram import parse_update
    upd = lambda **m: {"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}, "text": "hi", **m}}
    assert parse_update(upd()) == ("42", "hi")
    assert parse_update(upd(chat={"id": -5, "type": "group"})) is None
    assert parse_update({"update_id": 2, "message": {"chat": {"id": 42, "type": "private"}, "sticker": {}}}) is None


def test_signal_note_to_self_and_loop_protection():
    from assistant.channels.signal import parse_envelope
    me = "+447700900123"
    sync = lambda text, dest=me: {"envelope": {"sourceNumber": me, "sourceUuid": "u1", "syncMessage": {
        "sentMessage": {"destinationNumber": dest, "message": text}}}}
    assert parse_envelope(sync("lights off"), me) == (me, "lights off")
    assert parse_envelope(sync("🤖 Done."), me) is None            # our own reply echoed back
    assert parse_envelope(sync("hi mum", "+447700900999"), me) is None  # I messaged someone else
    dm = {"envelope": {"sourceNumber": "+44111", "dataMessage": {"message": "yo"}}}
    assert parse_envelope(dm, me) == ("+44111", "yo")             # main.py's allowlist decides


def test_entity_matching_handles_plurals_and_ranking():
    from assistant.tools.home import match_entities
    states = [{"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen Light"}},
              {"entity_id": "switch.kitchen_radio", "state": "off", "attributes": {"friendly_name": "Radio"}}]
    hits = match_entities(states, "kitchen lights", "", {"light", "switch"})
    assert hits[0]["entity_id"] == "light.kitchen"


def test_eval_scoring():
    from eval.scoring import score
    case = {"expect": [{"tool": "ha_call_service", "args": {"entity_id": "light.x", "data.brightness_pct": 30}}],
            "forbid": ["linkedin_post"]}
    good = [{"name": "ha_find_entities", "args": {}},
            {"name": "ha_call_service", "args": {"entity_id": "light.x", "data": {"brightness_pct": "30"}}}]
    assert score(case, good, "ok", False, None)["passed"]
    assert not score(case, good[:1], "ok", False, None)["passed"]
    assert not score(case, good + [{"name": "linkedin_post", "args": {}}], "", False, None)["passed"]


async def test_eval_fakes_keep_real_schemas_and_confirm_rules():
    from eval.fakes import build_fake_tools
    from eval.run import eval_settings
    s = eval_settings().model_copy(update={"db_path": ":memory:"})
    tools = build_fake_tools(s, Memory(":memory:"))
    assert {"web_search", "ha_call_service", "linkedin_post", "system_status"} <= set(tools)
    assert tools["ha_call_service"].confirm({"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"})
    assert "light.kitchen" in await tools["ha_find_entities"].func("kitchen")
