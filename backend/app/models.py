"""Database models.

Design notes
------------
* A UseCase stores its normalized content in ``fields`` (JSON) keyed by template field ids,
  so templates can evolve without migrations. ``template_id``/``template_version`` pin the schema.
* Relations between use cases are first-class and typed (duplicate, overlap, depends_on, ...).
  They can be *suggested* by the AI (status=suggested) and confirmed/dismissed by humans.
* Entities (systems, data sources, teams, models) are extracted from fields and shared across
  use cases — shared entities are the structural signal behind dependency detection.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class EmbeddingType(TypeDecorator):
    """pgvector on PostgreSQL, JSON everywhere else (tests on SQLite)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            from .config import get_settings

            return dialect.type_descriptor(Vector(get_settings().embedding_dims))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]


class Role(str, enum.Enum):
    requester = "requester"
    reviewer = "reviewer"
    admin = "admin"


class Status(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    qualified = "qualified"
    approved = "approved"
    in_build = "in_build"
    in_production = "in_production"
    retired = "retired"
    rejected = "rejected"


class RelationType(str, enum.Enum):
    duplicate = "duplicate"          # same problem, same users: should be merged
    overlap = "overlap"              # partially overlapping scope: align
    complementary = "complementary"  # adjacent, could share components
    depends_on = "depends_on"        # source needs target's output / capability first
    enables = "enables"              # source unlocks target
    shares_entity = "shares_entity"  # structural: shares a system / data source / team / model


class RelationStatus(str, enum.Enum):
    suggested = "suggested"
    confirmed = "confirmed"
    dismissed = "dismissed"


class EntityKind(str, enum.Enum):
    system = "system"
    data = "data"
    team = "team"
    model = "model"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.requester.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiKey(Base):
    """Personal API keys for programmatic / MCP access. Only a SHA-256 hash is stored."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    prefix: Mapped[str] = mapped_column(String(12))  # first chars, for display
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")


class Template(Base):
    """A versioned use case template loaded from YAML (see app/templates)."""

    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_template_key_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    definition: Mapped[dict] = mapped_column(JSON)  # parsed YAML
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UseCase(Base):
    __tablename__ = "use_cases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ref: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # e.g. UC-0042
    title: Mapped[str] = mapped_column(String(255), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default=Status.draft.value, index=True)

    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"))
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)
    missing_required: Mapped[list] = mapped_column(JSON, default=list)

    # Scoring (deterministic from fields) and AI suggestions
    value_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feasibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_tier: Mapped[str | None] = mapped_column(String(30), nullable=True)  # validated tier
    ai_suggestions: Mapped[dict] = mapped_column(JSON, default=dict)  # {risk_tier, rationale, ...}

    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    business_unit: Mapped[str] = mapped_column(String(255), default="", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(30), default="manual")  # manual | intake | import | api

    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)
    embedding_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    template: Mapped[Template] = relationship(lazy="joined")
    owner: Mapped[User | None] = relationship(lazy="joined")


class UseCaseVersion(Base):
    """Snapshot of fields at each save, for history/diff."""

    __tablename__ = "use_case_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    use_case_id: Mapped[str] = mapped_column(ForeignKey("use_cases.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    fields: Mapped[dict] = mapped_column(JSON)
    changed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Decision(Base):
    """Workflow transitions and governance decisions (audit trail)."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    use_case_id: Mapped[str] = mapped_column(ForeignKey("use_cases.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    decided_by: Mapped[User | None] = relationship(lazy="joined")


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("kind", "canonical", name="uq_entity_kind_canonical"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(255))
    canonical: Mapped[str] = mapped_column(String(255), index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UseCaseEntity(Base):
    __tablename__ = "use_case_entities"
    __table_args__ = (UniqueConstraint("use_case_id", "entity_id", "field_id", name="uq_uc_entity_field"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    use_case_id: Mapped[str] = mapped_column(ForeignKey("use_cases.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[str] = mapped_column(String(100))  # which template field it came from
    role: Mapped[str] = mapped_column(String(30), default="uses")  # uses | produces | owns

    entity: Mapped[Entity] = relationship(lazy="joined")


class Relation(Base):
    __tablename__ = "relations"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "type", "entity_id", name="uq_relation"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("use_cases.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("use_cases.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default=RelationStatus.suggested.value, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, default="")
    suggested_action: Mapped[str] = mapped_column(Text, default="")
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="ai")  # ai | human | structural
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    entity: Mapped[Entity | None] = relationship(lazy="joined")


class IntakeSession(Base):
    """A conversational intake: messages + the live draft the agent is filling."""

    __tablename__ = "intake_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"))
    messages: Mapped[list] = mapped_column(JSON, default=list)  # [{role, content, ts}]
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)
    missing_required: Mapped[list] = mapped_column(JSON, default=list)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    phase: Mapped[str] = mapped_column(String(20), default="interview")  # interview | ready | converted
    use_case_id: Mapped[str | None] = mapped_column(ForeignKey("use_cases.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"))
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    columns: Mapped[list] = mapped_column(JSON, default=list)
    sample_rows: Mapped[list] = mapped_column(JSON, default=list)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)  # column -> field_id | null
    ai_enrich: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded|mapped|running|done|failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    created_ids: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(20))  # usecase | portfolio
    use_case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
