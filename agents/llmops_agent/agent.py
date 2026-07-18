"""LLMOps agent.

Capability: llm.complete. Renders a (versioned) prompt, routes to a model by
complexity, calls the provider (mock by default), estimates cost, and enforces a
budget. Real providers (OpenAI / Anthropic / Ollama) activate when their keys
are present.
"""

import asyncio
import logging
import os

from sdk.base_agent import BaseAgent

from . import prompts, providers, routing

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("llmops-agent")


class LLMOpsAgent(BaseAgent):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.default_provider = os.getenv("LLM_PROVIDER", "mock")
        self.budget = float(os.getenv("LLM_BUDGET_USD", "5.0"))
        self.spend = 0.0

    async def handle(self, payload: dict) -> dict:
        provider = payload.get("provider", self.default_provider)
        if payload.get("template"):
            prompt = prompts.render(payload["template"], payload.get("version"), payload.get("vars"))
            version = payload.get("version") or prompts.latest_version(payload["template"])
            prompt_version = f"{payload['template']}@{version}"
        else:
            prompt = payload.get("prompt", "")
            prompt_version = "raw"

        model, price_in, price_out = routing.choose(
            provider, payload.get("complexity", "low"), payload.get("model")
        )
        in_tok = routing.count_tokens(prompt)
        completion = await providers.complete(provider, model, prompt)
        out_tok = routing.count_tokens(completion)
        call_cost = routing.cost(price_in, price_out, in_tok, out_tok)

        if self.spend + call_cost > self.budget:
            raise RuntimeError(
                f"budget exceeded: {self.spend:.4f} + {call_cost:.4f} > {self.budget:.4f} USD"
            )
        self.spend += call_cost
        log.info("llm %s/%s tokens=%d/%d cost=%.6f", provider, model, in_tok, out_tok, call_cost)
        return {
            "completion": completion,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "tokens": {"in": in_tok, "out": out_tok},
            "cost_usd": call_cost,
            "spend_usd": round(self.spend, 6),
            "budget_usd": self.budget,
        }


if __name__ == "__main__":
    agent = LLMOpsAgent(name=os.getenv("AGENT_NAME", "llmops-agent-1"), capabilities=["llm.complete"])
    asyncio.run(agent.run())
