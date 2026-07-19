"""Pydantic request/response schemas for the API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- agents ---
class AgentRegisterRequest(BaseModel):
    name: str
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    meta: dict = Field(default_factory=dict)


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    capabilities: list[str]
    status: str
    endpoint: str | None = None
    last_heartbeat: datetime | None = None


# --- workflows ---
class CreateWorkflowRequest(BaseModel):
    template: str | None = None
    spec: dict | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    capability: str
    status: str
    depends_on: list[str] = Field(default_factory=list)
    result: dict | None = None
    error: str | None = None


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    intent: str | None = None
    status: str
    dispatched: int = 0
    tasks: list[TaskOut] = Field(default_factory=list)


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str
    created_at: datetime | None = None
    task_count: int = 0


class WorkflowDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    intent: str | None = None
    status: str
    created_at: datetime | None = None
    tasks: list[TaskOut] = Field(default_factory=list)


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    task_id: str | None = None
    agent_id: str | None = None
    payload: dict = Field(default_factory=dict)
    created_at: datetime | None = None
