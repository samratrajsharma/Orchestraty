"""Data-Transformation agent.

Capability: data.transform. Turns unstructured text into structured JSON using a
caller-supplied schema (built-in field types: email, phone, number, url, date;
or a custom regex pattern per field).
"""

import asyncio
import logging
import os

from sdk.base_agent import BaseAgent

from .extract import extract

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("data-transform-agent")


class DataTransformAgent(BaseAgent):
    async def handle(self, payload: dict) -> dict:
        text = payload.get("text", "")
        schema = payload.get("schema", [])
        structured = extract(text, schema)
        matched = sum(1 for v in structured.values() if v is not None)
        log.info("extracted %d/%d fields", matched, len(schema))
        return {"structured": structured, "matched": matched, "fields": len(schema)}


if __name__ == "__main__":
    agent = DataTransformAgent(
        name=os.getenv("AGENT_NAME", "data-transform-agent-1"),
        capabilities=["data.transform"],
    )
    asyncio.run(agent.run())
