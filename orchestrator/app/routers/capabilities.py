"""Capability catalog: what the platform can do right now, from live agents."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..enums import AgentStatus
from ..intent import TEMPLATES
from ..registry import AgentRegistry

router = APIRouter(tags=["capabilities"])
registry = AgentRegistry()


@router.get("/capabilities")
async def capabilities(session: AsyncSession = Depends(get_session)):
    agents = await registry.list_agents(session, status=AgentStatus.ONLINE.value)
    cap_map: dict[str, list[str]] = {}
    for a in agents:
        for c in (a.capabilities or []):
            cap_map.setdefault(c, []).append(a.name)
    caps = [
        {"capability": c, "agents": names, "count": len(names)}
        for c, names in sorted(cap_map.items())
    ]
    return {
        "capabilities": caps,
        "online_agents": len(agents),
        "templates": sorted(TEMPLATES.keys()),
    }
