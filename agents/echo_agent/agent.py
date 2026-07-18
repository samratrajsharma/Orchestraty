"""Reference echo agent, built on the shared agent SDK."""

import asyncio
import logging
import os

from sdk.base_agent import BaseAgent

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


class EchoAgent(BaseAgent):
    async def handle(self, payload: dict) -> dict:
        return {"echoed": payload.get("message", payload)}


if __name__ == "__main__":
    agent = EchoAgent(name=os.getenv("AGENT_NAME", "echo-agent-1"), capabilities=["echo"])
    asyncio.run(agent.run())
