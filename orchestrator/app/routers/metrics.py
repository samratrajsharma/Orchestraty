"""Prometheus metrics endpoint for the orchestrator."""

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..enums import AgentStatus
from ..metrics import render
from ..models import Agent, Event, Task, Workflow

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(session: AsyncSession = Depends(get_session)):
    wf = (await session.execute(select(Workflow.status, func.count()).group_by(Workflow.status))).all()
    tk = (await session.execute(select(Task.status, func.count()).group_by(Task.status))).all()
    agents_online = (
        await session.execute(
            select(func.count()).select_from(Agent).where(Agent.status == AgentStatus.ONLINE.value)
        )
    ).scalar() or 0
    events_total = (await session.execute(select(func.count()).select_from(Event))).scalar() or 0

    data = [
        ("orchestraty_workflows", "gauge", "Workflows by status",
         [({"status": s}, c) for s, c in wf]),
        ("orchestraty_tasks", "gauge", "Tasks by status",
         [({"status": s}, c) for s, c in tk]),
        ("orchestraty_agents_online", "gauge", "Online agents", [({}, agents_online)]),
        ("orchestraty_events_total", "counter", "Total events recorded", [({}, events_total)]),
    ]
    return render(data)
