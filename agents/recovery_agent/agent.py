"""Recovery agent.

Capability: recovery.heal. Turns a monitor verdict into corrective actions and
(when auto) submits the corrective workflows back to the orchestrator - retrain
via mlops.train, rollback via deploy.model. This is the self-healing step that
closes the reconcile loop.
"""

import asyncio
import logging
import os

import httpx

from sdk.base_agent import BaseAgent

from . import policies as policies_mod

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("recovery-agent")


class RecoveryAgent(BaseAgent):
    async def handle(self, payload: dict) -> dict:
        verdict = payload.get("verdict") or {
            "status": payload.get("status"), "breaches": payload.get("breaches") or []
        }
        actions = policies_mod.decide(verdict, payload.get("policies"))
        triggered = []
        if payload.get("auto", True):
            for a in actions:
                wid = await self._trigger(a, payload)
                if wid:
                    triggered.append({"action": a["action"], "workflow_id": wid})
        log.info("recovery -> %d action(s), %d triggered", len(actions), len(triggered))
        return {"actions": actions, "triggered": triggered}

    async def _trigger(self, action, payload):
        act = action["action"]
        if act == "retrain":
            spec = {"name": "auto-retrain", "tasks": [{
                "key": "t", "capability": "mlops.train",
                "payload": {"dataset": payload.get("dataset", "iris"),
                            "registered_model_name": payload.get("model_name", "iris-classifier")}}]}
        elif act == "rollback":
            spec = {"name": "auto-rollback", "tasks": [{
                "key": "t", "capability": "deploy.model", "payload": {"action": "rollback"}}]}
        else:
            return None  # alert: no corrective workflow
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(f"{self.api_url}/workflows", json={"spec": spec})
                r.raise_for_status()
                return r.json().get("id")
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to trigger %s: %s", act, exc)
            return None


if __name__ == "__main__":
    agent = RecoveryAgent(name=os.getenv("AGENT_NAME", "recovery-agent-1"), capabilities=["recovery.heal"])
    asyncio.run(agent.run())
