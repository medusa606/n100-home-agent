"""Benchmark local models on the agent's real prompt + tool schemas, with deterministic fake tools.

    python -m eval.run --models qwen3:4b gemma4:e4b gemma4:e2b qwen3:1.7b llama3.2:3b --pull

Results stream to eval-results/<timestamp>/raw.jsonl (re-run with --resume DIR to continue),
then report.py writes summary.csv, report.md and charts.
"""
import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from assistant.agent import Agent
from assistant.config import get_settings
from assistant.llm import LLM
from assistant.memory import Memory

from . import report
from .fakes import build_fake_tools
from .scoring import score

HERE = Path(__file__).parent
CHAT = "eval"


class RecordingLLM:
    """Wraps the real client and records every call the agent makes."""

    def __init__(self, llm: LLM):
        self.llm, self.model, self.calls = llm, llm.model, []

    async def chat(self, messages, tools):
        t0 = time.monotonic()
        reply = await self.llm.chat(messages, tools)
        self.calls.append({"ms": int((time.monotonic() - t0) * 1000), "usage": reply.usage,
                           "tool_calls": [{"name": c.name, "args": c.args, "error": c.error}
                                          for c in reply.tool_calls]})
        return reply


def eval_settings():
    return get_settings().model_copy(update={
        "user_name": "Alex", "interests": "AI agents, LLMs, home automation", "location": "London",
        "timezone": "Europe/London", "ha_token": "eval",
        "ha_allowed_domains": "light,switch,climate,cover,media_player,sensor,binary_sensor,lock",
        "ha_confirm_domains": "lock,cover,alarm_control_panel", "history_turns": 6})


async def run_case(llm: LLM, settings, case: dict, timeout: float) -> dict:
    rec = RecordingLLM(llm)
    memory = Memory(":memory:")
    for turn in case.get("history", []):
        memory.add_turn(CHAT, turn["role"], turn["content"])
    agent = Agent(rec, build_fake_tools(settings, memory, case.get("tool_overrides")), memory, settings)
    t0, reply, error = time.monotonic(), "", None
    try:
        reply = await asyncio.wait_for(agent.handle(CHAT, case["prompt"]), timeout)
    except asyncio.TimeoutError:
        error = "timeout"
    except Exception as e:
        error = repr(e)[:200]
    confirmed = memory.pop_pending(CHAT) is not None
    calls = [c for step in rec.calls for c in step["tool_calls"]]
    known = set(agent.tools)
    return {
        **score(case, calls, reply, confirmed, error),
        "total_ms": int((time.monotonic() - t0) * 1000),
        "llm_ms": sum(s["ms"] for s in rec.calls),
        "llm_calls": len(rec.calls),
        "completion_tokens": sum(s["usage"].get("completion_tokens", 0) for s in rec.calls),
        "prompt_tokens": sum(s["usage"].get("prompt_tokens", 0) for s in rec.calls),
        "invalid_calls": sum(1 for c in calls if c["error"] or c["name"] not in known),
        "calls": [{"name": c["name"], "args": c["args"]} for c in calls],
        "confirmed": confirmed, "reply": reply[:600], "error": error,
    }


async def prepare_model(root: str, model: str, pull: bool) -> dict:
    """Ollama-specific: optional pull, cold-load timing and loaded size. Skipped quietly for other servers."""
    info = {"load_s": None, "size_gb": None}
    try:
        async with httpx.AsyncClient(timeout=None) as c:
            if pull:
                print(f"  pulling {model} ...")
                (await c.post(f"{root}/api/pull", json={"model": model, "stream": False})).raise_for_status()
            r = await c.post(f"{root}/api/generate", json={"model": model, "prompt": "", "keep_alive": "30m"})
            r.raise_for_status()
            info["load_s"] = round(r.json().get("load_duration", 0) / 1e9, 1)
            for m in (await c.get(f"{root}/api/ps")).json().get("models", []):
                if model in (m.get("name"), m.get("model")):
                    info["size_gb"] = round(m.get("size", 0) / 1e9, 1)
    except httpx.HTTPError as e:
        print(f"  (ollama extras unavailable: {e})")
    return info


async def unload(root: str, model: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            await c.post(f"{root}/api/generate", json={"model": model, "keep_alive": 0})
    except httpx.HTTPError:
        pass


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--repeats", type=int, default=1, help="runs per case (sampling varies; 3 for publishable numbers)")
    ap.add_argument("--cases", default="", help="comma-separated case ids or categories to run")
    ap.add_argument("--timeout", type=float, default=300, help="seconds per case")
    ap.add_argument("--pull", action="store_true", help="ollama pull each model first")
    ap.add_argument("--resume", type=Path, help="existing run dir to continue")
    args = ap.parse_args()

    settings = eval_settings()
    root = settings.llm_base_url.rstrip("/").removesuffix("/v1")
    cases = json.loads((HERE / "cases.json").read_text())
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted or c["category"] in wanted]

    out = args.resume or Path("eval-results") / datetime.now().strftime("%Y%m%d-%H%M")
    out.mkdir(parents=True, exist_ok=True)
    raw, meta_path = out / "raw.jsonl", out / "models.json"
    done = {(r["model"], r["case"], r["rep"]) for r in map(json.loads, raw.open())} if raw.exists() else set()
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    for model in args.models:
        todo = [(c, rep) for c in cases for rep in range(args.repeats) if (model, c["id"], rep) not in done]
        if not todo:
            continue
        print(f"\n== {model}: {len(todo)} runs")
        await unload(root, model)  # so load_s is a cold start
        meta[model] = await prepare_model(root, model, args.pull)
        meta_path.write_text(json.dumps(meta, indent=2))
        llm = LLM(settings.llm_base_url, settings.llm_api_key, model, args.timeout)
        for case, rep in todo:
            result = await run_case(llm, settings, case, args.timeout)
            row = {"model": model, "case": case["id"], "category": case["category"], "rep": rep, **result}
            with raw.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            mark = "PASS" if result["passed"] else "FAIL " + "; ".join(result["reasons"])[:100]
            print(f"  {case['id']:<22} {result['total_ms'] / 1000:6.1f}s  {mark}")
        await unload(root, model)  # free RAM for the next model

    report.build(out)
    print(f"\nReport: {out / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
