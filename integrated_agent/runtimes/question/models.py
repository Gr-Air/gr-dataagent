from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreate(DomainModel):
    question: str = Field(min_length=1)
    requester: str = "course-user"
    channel: str = "web"


class TaskRequest(TaskCreate):
    task_id: str = Field(min_length=1)


class TaskAccepted(DomainModel):
    task_id: str
    status: Literal["accepted"] = "accepted"
    task_url: str
    events_url: str


class Claim(DomainModel):
    claim_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceRef(DomainModel):
    evidence_id: str
    query_id: str
    result_ref: str
    summary: str


class ChartSeries(DomainModel):
    name: str
    values: list[float]


class ChartSpec(DomainModel):
    chart_id: str
    title: str
    chart_type: Literal["bar", "line"]
    unit: str
    categories: list[str]
    series: list[ChartSeries]
    evidence_ids: list[str] = Field(default_factory=list)


class TaskResult(DomainModel):
    task_id: str
    status: Literal["completed", "partial"]
    answer: str
    claims: list[Claim]
    evidence: list[EvidenceRef]
    evidence_refs: list[str]
    charts: list[ChartSpec] = Field(default_factory=list)
    data_snapshot_id: str
    trace_ref: str


class TaskSnapshot(DomainModel):
    task_id: str
    status: Literal["accepted", "running", "completed", "failed"]
    result: TaskResult | None = None
    error: str | None = None


class TaskEvent(DomainModel):
    task_id: str
    sequence: int
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
