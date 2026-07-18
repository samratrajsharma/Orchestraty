"""Infrastructure agent.

Capability: infra.provision. Generates Terraform IaC from a provision spec. In
local/plan mode (default) it records the desired state and returns the plan +
HCL without touching any cloud. Real `terraform apply` activates only when
TF_APPLY=true and terraform + credentials are available in the image.
"""

import asyncio
import logging
import os

from sdk.base_agent import BaseAgent

from .plan import build_plan, to_terraform

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("infra-agent")


class InfraAgent(BaseAgent):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.apply = os.getenv("TF_APPLY", "false").lower() == "true"

    async def handle(self, payload: dict) -> dict:
        plan = build_plan(payload)
        hcl = to_terraform(plan)
        if self.apply:
            detail = await asyncio.to_thread(self._terraform_apply, hcl)
            status = "applied"
        else:
            detail = {"mode": "plan", "note": "local plan - no cloud resources created"}
            status = "planned"
        log.info("infra %s: %s/%s", status, plan["resource"], plan["name"])
        return {
            "status": status,
            "plan": plan,
            "terraform": hcl,
            "resource_id": f"{plan['resource']}/{plan['name']}",
            "detail": detail,
        }

    def _terraform_apply(self, hcl: str) -> dict:
        import pathlib
        import subprocess
        import tempfile

        workdir = tempfile.mkdtemp(prefix="orch-tf-")
        pathlib.Path(workdir, "main.tf").write_text(hcl)
        subprocess.run(["terraform", "init", "-input=false"], cwd=workdir, check=True, capture_output=True)
        out = subprocess.run(
            ["terraform", "apply", "-auto-approve", "-input=false"],
            cwd=workdir, check=True, capture_output=True, text=True,
        )
        return {"stdout": out.stdout[-2000:]}


if __name__ == "__main__":
    agent = InfraAgent(name=os.getenv("AGENT_NAME", "infra-agent-1"), capabilities=["infra.provision"])
    asyncio.run(agent.run())
