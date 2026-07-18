"""Workflow API: submit an intent, list / inspect workflows, and stream status."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..compiler import IntentCompiler
from ..db import SessionLocal, get_session
from ..engine import TaskEngine
from ..intent import CompileError
from ..models import Event, Task, Workflow
from ..schemas import (
    CreateWorkflowRequest,
    EventOut,
    TaskOut,
    WorkflowDetail,
    WorkflowOut,
    WorkflowSummary,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])
logger = logging.getLogger("orchestrator.api.workflows")

compiler = IntentCompiler()
engine = TaskEngine()
_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


@router.post("", response_model=WorkflowOut)
async def create_workflow(
    body: CreateWorkflowRequest, request: Request,
    session: AsyncSession = Depends(get_session),
):
    try:
        if body.template:
            workflow = await compiler.create_from_template(session, body.template)
        elif body.spec:
            workflow = await compiler.create_from_spec(session, body.spec)
        else:
            raise HTTPException(status_code=400, detail="provide either 'template' or 'spec'")
    except CompileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    dispatched = 0
    dispatcher = request.app.state.dispatcher
    if dispatcher is not None:
        dispatched = await dispatcher.dispatch_ready(session, workflow.id)
    await engine.reconcile_workflow(session, workflow)
    await session.commit()

    tasks = (
        await session.execute(select(Task).where(Task.workflow_id == workflow.id))
    ).scalars().all()
    return WorkflowOut(
        id=workflow.id, name=workflow.name, intent=workflow.intent,
        status=workflow.status, dispatched=dispatched,
        tasks=[TaskOut.model_validate(t) for t in tasks],
    )


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(select(Workflow).order_by(Workflow.created_at.desc()))
    ).scalars().all()
    return [WorkflowSummary.model_validate(w) for w in rows]


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(workflow_id: str, session: AsyncSession = Depends(get_session)):
    w = await session.get(Workflow, workflow_id)
    if w is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    tasks = (
        await session.execute(select(Task).where(Task.workflow_id == workflow_id))
    ).scalars().all()
    return WorkflowDetail(
        id=w.id, name=w.name, intent=w.intent, status=w.status,
        created_at=w.created_at, tasks=[TaskOut.model_validate(t) for t in tasks],
    )


@router.get("/{workflow_id}/events", response_model=list[EventOut])
async def workflow_events(workflow_id: str, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Event).where(Event.workflow_id == workflow_id).order_by(Event.created_at)
        )
    ).scalars().all()
    return [EventOut.model_validate(e) for e in rows]


@router.get("/{workflow_id}/stream")
async def stream_workflow(workflow_id: str):
    async def gen():
        last = None
        for _ in range(180):
            async with SessionLocal() as session:
                w = await session.get(Workflow, workflow_id)
                if w is None:
                    yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                    return
                tasks = (
                    await session.execute(select(Task).where(Task.workflow_id == workflow_id))
                ).scalars().all()
                snap = {"workflow": w.status, "tasks": {t.name: t.status for t in tasks}}
            payload = json.dumps(snap)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if snap["workflow"] in _TERMINAL:
                return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")
