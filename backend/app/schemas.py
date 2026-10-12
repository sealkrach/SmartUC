from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: str = ""
    role: str = "requester"


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


class TemplateOut(BaseModel):
    id: str
    key: str
    version: int
    name: str
    description: str
    is_default: bool
    definition: dict

    model_config = {"from_attributes": True}


class EntityOut(BaseModel):
    id: str
    kind: str
    name: str
    canonical: str
    aliases: list = []

    model_config = {"from_attributes": True}


class RelationOut(BaseModel):
    id: str
    source_id: str
    target_id: str
    type: str
    status: str
    confidence: float
    similarity: float | None
    explanation: str
    suggested_action: str
    origin: str
    entity: EntityOut | None = None
    created_at: datetime
    # denormalized for display
    source_ref: str | None = None
    source_title: str | None = None
    target_ref: str | None = None
    target_title: str | None = None

    model_config = {"from_attributes": True}


class RelationReview(BaseModel):
    status: str  # confirmed | dismissed
    type: str | None = None  # allow correcting the type when confirming


class RelationCreate(BaseModel):
    target_id: str
    type: str
    explanation: str = ""


class UseCaseListItem(BaseModel):
    id: str
    ref: str
    title: str
    summary: str
    status: str
    business_unit: str
    completeness: float
    value_score: float | None
    feasibility_score: float | None
    priority_score: float | None
    risk_tier: str | None
    tags: list
    source: str
    owner: UserOut | None
    ai_capability: str | None = None
    suggested_relations: int = 0
    updated_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class UseCaseOut(UseCaseListItem):
    template_id: str
    fields: dict
    missing_required: list
    ai_suggestions: dict
    last_analyzed_at: datetime | None
    allowed_transitions: list[dict] = []


class UseCaseCreate(BaseModel):
    fields: dict[str, Any] = {}
    template_id: str | None = None
    tags: list[str] = []


class UseCaseUpdate(BaseModel):
    fields: dict[str, Any]
    change_note: str = ""
    tags: list[str] | None = None


class TransitionIn(BaseModel):
    to: str
    comment: str = ""


class DecisionOut(BaseModel):
    id: str
    from_status: str
    to_status: str
    comment: str
    decided_by: UserOut | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionOut(BaseModel):
    id: str
    version: int
    fields: dict
    change_note: str
    changed_by_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ImproveIn(BaseModel):
    field_id: str
    value: Any = None
    instruction: str = ""


class NormalizeIn(BaseModel):
    text: str
    template_id: str | None = None
    existing: dict[str, Any] | None = None


class IntakeStart(BaseModel):
    template_id: str | None = None
    initial_text: str | None = None


class IntakeMessage(BaseModel):
    content: str


class IntakeOut(BaseModel):
    id: str
    template_id: str
    messages: list
    draft: dict
    completeness: float
    missing_required: list
    assumptions: list
    phase: str
    use_case_id: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class IntakeDraftUpdate(BaseModel):
    draft: dict[str, Any]


class ImportJobOut(BaseModel):
    id: str
    filename: str
    columns: list
    sample_rows: list
    row_count: int
    mapping: dict
    ai_enrich: bool
    status: str
    progress: int
    created_ids: list
    errors: list
    template_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ImportMappingIn(BaseModel):
    mapping: dict[str, str | None]
    ai_enrich: bool = True


class GraphOut(BaseModel):
    nodes: list[dict]
    edges: list[dict]
