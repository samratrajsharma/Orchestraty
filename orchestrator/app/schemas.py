"""Pydantic request/response schemas for the API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
