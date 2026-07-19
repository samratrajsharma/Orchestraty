"""Monitor agent.

Capability: monitor.check. Computes a drift score (PSI) from baseline vs current
samples and evaluates observed metrics (accuracy, latency, error rate, cost)
against thresholds, returning a health verdict + breaches. The recovery engine
acts on the breaches.
"""

import asyncio
import logging
import os

from sdk.base_agent import BaseAgent

from . import detectors

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("monitor-agent")


class MonitorAgent(BaseAgent):
    async def handle(self, payload: dict) -> dict:
        obs = dict(payload.get("metrics") or {})
        thresholds = payload.get("thresholds") or detectors.DEFAULT_THRESHOLDS
        baseline = payload.get("baseline")
        current = payload.get("current")
        if baseline is not None and current is not None:
            obs["drift"] = detectors.psi(baseline, current)
        verdict = detectors.evaluate(obs, thresholds)
        log.info("monitor -> %s (%d breaches)", verdict["status"], len(verdict["breaches"]))
        return {
            "status": verdict["status"],
            "breaches": verdict["breaches"],
            "observation": obs,
            "thresholds": thresholds,
        }


if __name__ == "__main__":
    agent = MonitorAgent(name=os.getenv("AGENT_NAME", "monitor-agent-1"), capabilities=["monitor.check"])
    asyncio.run(agent.run())
