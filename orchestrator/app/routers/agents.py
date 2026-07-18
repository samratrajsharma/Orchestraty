"""Agent registry HTTP API: agents register, heartbeat, and are listed here."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..registry import AgentRegistry
from ..schemas import AgentOut, AgentRegisterRequest

router = APIRouter(prefix="/agents", tags=["agents"])
registry = AgentRegistry()


@router.post("/register", response_model=AgentOut)
async def register_agent(body: AgentRegisterRequest, session: AsyncSession = Depends(get_session)):
    agent = await registry.register(
        session, name=body.name, capabilities=body.capabilities,
        endpoint=body.endpoint, meta=body.meta,
    )
    await session.commit()
    return agent


@router.post("/{agent_id}/heartbeat")
async def heartbeat(agent_id: str, session: AsyncSession = Depends(get_session)):
    agent = await registry.heartbeat(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await session.commit()
    return {"status": "ok", "agent_id": agent_id}


@router.get("", response_model=list[AgentOut])
async def list_agents(session: AsyncSession = Depends(get_session)):
    return await registry.list_agents(session)


@router.delete("/{agent_id}")
async def deregister(agent_id: str, session: AsyncSession = Depends(get_session)):
    agent = await registry.mark_offline(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await session.commit()
    return {"status": "offline", "agent_id": agent_id}
