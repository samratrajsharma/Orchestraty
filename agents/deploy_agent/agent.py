"""Deployment agent.

Capability: deploy.model. Deploys a registered model to the model-server via a
rollout strategy (direct / canary / blue-green) and emits a Dockerfile + K8s
manifests for the cloud path. Also supports action: deploy | promote | rollback.
"""

import asyncio
import logging
import os

import httpx

from sdk.base_agent import BaseAgent

from . import manifests, rollout

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("deploy-agent")


class DeployAgent(BaseAgent):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.model_server = os.getenv("MODEL_SERVER_URL", "http://model-server:8080")

    async def _post(self, path: str, json=None) -> dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{self.model_server}{path}", json=json)
            r.raise_for_status()
            return r.json()

    async def handle(self, payload: dict) -> dict:
        action = payload.get("action", "deploy")
        if action == "promote":
            return {"status": "promoted", "served": await self._post("/promote")}
        if action == "rollback":
            return {"status": "rolled_back", "served": await self._post("/rollback")}

        model_uri = payload.get("model_uri")
        if not model_uri:
            raise ValueError("deploy requires 'model_uri'")
        strategy = payload.get("strategy", "direct")
        name = payload.get("name", "model")
        steps = rollout.plan(
            strategy, payload.get("canary_weight", 10), payload.get("auto_promote", False)
        )
        served = None
        for step in steps:
            if step["op"] == "load":
                body = {"model_uri": model_uri, "slot": step["slot"]}
                if "weight" in step:
                    body["weight"] = step["weight"]
                served = await self._post("/load", body)
            elif step["op"] == "promote":
                served = await self._post("/promote")
        log.info("deployed %s via %s", model_uri, strategy)
        return {
            "status": "deployed",
            "strategy": strategy,
            "model_uri": model_uri,
            "served": served,
            "manifests": {"dockerfile": manifests.dockerfile(name), "k8s": manifests.k8s(name)},
        }


if __name__ == "__main__":
    agent = DeployAgent(name=os.getenv("AGENT_NAME", "deploy-agent-1"), capabilities=["deploy.model"])
    asyncio.run(agent.run())
