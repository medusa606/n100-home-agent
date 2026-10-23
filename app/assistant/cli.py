"""Chat with the agent in a terminal - no chat app needed.
   docker compose exec agent python -m assistant.cli"""
import asyncio

from .agent import Agent
from .config import get_settings
from .llm import LLM
from .memory import Memory
from .tools import build_tools


async def main() -> None:
    s = get_settings()
    memory = Memory(s.db_path)
    agent = Agent(LLM(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_timeout), build_tools(s, memory), memory, s)
    print(f"model={s.llm_model} tools={', '.join(agent.tools)}  (Ctrl+C to quit, /trace to see steps)")
    while True:
        text = await asyncio.to_thread(input, "\nyou> ")
        if text.strip():
            print("bot>", await agent.handle("cli", text))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
