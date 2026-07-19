"""Knowledge layer: insights + model lineage over the event/task history."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..enums import AgentStatus
from ..insights import matches_model, success_rate
from ..models import Agent, Event, Task, Workflow

router = APIRouter(tags=["knowledge"])


@router.get("/insights")
async def insights(session: AsyncSession = Depends(get_session)):
    wf = {r[0]: r[1] for r in (await session.execute(
        select(Workflow.status, func.count()).group_by(Workflow.status))).all()}
    tk = {r[0]: r[1] for r in (await session.execute(
        select(Task.status, func.count()).group_by(Task.status))).all()}
    ev = {r[0]: r[1] for r in (await session.execute(
        select(Event.type, func.count()).group_by(Event.type))).all()}
    online = (await session.execute(
        select(func.count()).select_from(Agent).where(Agent.status == AgentStatus.ONLINE.value))).scalar() or 0
    total_agents = (await session.execute(select(func.count()).select_from(Agent))).scalar() or 0
    return {
        "workflows": {"total": sum(wf.values()), "by_status": wf, "success_rate": success_rate(wf)},
        "tasks": {"total": sum(tk.values()), "by_status": tk},
        "events": {"total": sum(ev.values()), "by_type": ev},
        "agents": {"online": online, "total": total_agents},
    }


@router.get("/lineage/{model_name}")
async def lineage(model_name: str, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Task).where(Task.capability.in_(["mlops.train", "deploy.model"])))).scalars().all()
    trace = []
    for t in rows:
        if matches_model(t.result, model_name) or matches_model(t.payload, model_name):
            trace.append({
                "workflow_id": t.workflow_id, "capability": t.capability, "task": t.name,
                "status": t.status,
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
                "result": t.result,
            })
    if not trace:
        raise HTTPException(status_code=404, detail=f"no lineage found for '{model_name}'")
    return {"model": model_name, "records": len(trace), "lineage": trace}
