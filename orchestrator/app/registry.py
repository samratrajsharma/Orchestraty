"""Agent registry: registration, heartbeats, liveness, and capability lookup."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from .enums import AgentStatus, EventType
from .liveness import has_capability, is_stale
from .models import Agent, Event

logger = logging.getLogger("orchestrator.registry")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentRegistry:
    """Stateless service operating on a caller-provided AsyncSession."""

    async def register(self, session, *, name, capabilities, endpoint=None, meta=None):
        existing = (
            await session.execute(select(Agent).where(Agent.name == name))
        ).scalar_one_or_none()
        if existing is None:
            agent = Agent(
                name=name,
                capabilities=capabilities,
                endpoint=endpoint,
                meta=meta or {},
                status=AgentStatus.ONLINE.value,
                last_heartbeat=_now(),
            )
            session.add(agent)
            await session.flush()
        else:
            existing.capabilities = capabilities
            existing.endpoint = endpoint
            existing.meta = meta or {}
            existing.status = AgentStatus.ONLINE.value
            existing.last_heartbeat = _now()
            agent = existing
        session.add(
            Event(
                type=EventType.AGENT_REGISTERED.value,
                agent_id=agent.id,
                payload={"name": name, "capabilities": capabilities},
            )
        )
        logger.info("agent registered: %s (%s)", name, capabilities)
        return agent

    async def heartbeat(self, session, agent_id):
        agent = await session.get(Agent, agent_id)
        if agent is None:
            return None
        agent.last_heartbeat = _now()
        agent.status = AgentStatus.ONLINE.value
        return agent

    async def mark_offline(self, session, agent_id):
        agent = await session.get(Agent, agent_id)
        if agent is not None:
            agent.status = AgentStatus.OFFLINE.value
            session.add(
                Event(type=EventType.AGENT_OFFLINE.value, agent_id=agent.id,
                      payload={"reason": "deregister"})
            )
        return agent

    async def list_agents(self, session, *, status=None):
        q = select(Agent)
        if status is not None:
            q = q.where(Agent.status == status)
        return (await session.execute(q)).scalars().all()

    async def find_for_capability(self, session, capability):
        rows = (
            await session.execute(
                select(Agent).where(Agent.status == AgentStatus.ONLINE.value)
            )
        ).scalars().all()
        return [a for a in rows if has_capability(a.capabilities, capability)]

    async def reap_stale(self, session, ttl_seconds: float = 45.0):
        now = _now()
        rows = (
            await session.execute(
                select(Agent).where(Agent.status == AgentStatus.ONLINE.value)
            )
        ).scalars().all()
        reaped = []
        for a in rows:
            if is_stale(a.last_heartbeat, now, ttl_seconds):
                a.status = AgentStatus.OFFLINE.value
                session.add(
                    Event(type=EventType.AGENT_OFFLINE.value, agent_id=a.id,
                          payload={"reason": "heartbeat_timeout"})
                )
                reaped.append(a.id)
        return reaped
