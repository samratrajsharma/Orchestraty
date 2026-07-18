"""Workflow API: submit an intent (template or explicit spec) and enqueue it."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..compiler import IntentCompiler
from ..db import get_session
from ..engine import TaskEngine
from ..intent import CompileError
from ..models import Task
from ..schemas import CreateWorkflowRequest, TaskOut, WorkflowOut

router = APIRouter(prefix="/workflows", tags=["workflows"])
logger = logging.getLogger("orchestrator.api.workflows")

compiler = IntentCompiler()
engine = TaskEngine()


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
        id=workflow.id,
        name=workflow.name,
        intent=workflow.intent,
        status=workflow.status,
        dispatched=dispatched,
        tasks=[TaskOut.model_validate(t) for t in tasks],
    )
