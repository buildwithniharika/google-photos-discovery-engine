"""SQLAlchemy ORM for every table in architecture Section 13.2.

Only portable column types are used (JSON, LargeBinary, timezone-aware DateTime), so the
same models run on local SQLite and hosted Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, datetime: DateTime(timezone=True)}


# --- Ingestion and items -----------------------------------------------------


class RawItemRow(Base):
    """Immutable raw payloads (replaces the local JSONL raw store when deployed)."""

    __tablename__ = "raw_items"

    raw_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    fetched_at: Mapped[datetime]
    payload: Mapped[dict[str, Any]]


class ItemRow(Base):
    __tablename__ = "items"

    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    primary_source_name: Mapped[str] = mapped_column(String(32), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    original_text: Mapped[str] = mapped_column(Text)
    clean_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    date: Mapped[datetime | None] = mapped_column(index=True)
    rating: Mapped[int | None] = mapped_column(Integer)
    engagement: Mapped[dict[str, Any]] = mapped_column(default=dict)
    author_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False)
    similar_count: Mapped[int] = mapped_column(Integer, default=0)
    # `metadata` is reserved on declarative classes, hence the attribute alias.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)

    __table_args__ = (
        CheckConstraint("rating IS NULL OR (rating BETWEEN 1 AND 5)", name="ck_items_rating"),
    )


class ItemSourceRow(Base):
    """One row per raw item, so a canonical item can have several sources."""

    __tablename__ = "item_sources"

    raw_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id"), index=True)
    source_name: Mapped[str] = mapped_column(String(32))
    platform: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(String(64), index=True)


# --- AI outputs --------------------------------------------------------------


class RelevanceRow(Base):
    __tablename__ = "relevance"

    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id"), primary_key=True)
    stage_reached: Mapped[str] = mapped_column(String(1))
    is_google_photos: Mapped[bool | None] = mapped_column(Boolean)
    is_retrieval: Mapped[bool | None] = mapped_column(Boolean)
    retrieval_type: Mapped[str | None] = mapped_column(String(32), index=True)
    vague_memory_relevance: Mapped[float | None] = mapped_column(Float)
    excluded_topic: Mapped[str | None] = mapped_column(String(16))
    excluded_topic_blocks_retrieval: Mapped[bool | None] = mapped_column(Boolean)
    rationale: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    __table_args__ = (
        CheckConstraint("stage_reached IN ('A', 'B', 'C')", name="ck_relevance_stage"),
    )


class InsightRow(Base):
    __tablename__ = "insights"

    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id"), primary_key=True)
    trying_to_find: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(32), index=True)
    remembered_cues: Mapped[list[Any]] = mapped_column(default=list)
    forgotten_details: Mapped[list[Any]] = mapped_column(default=list)
    search_attempts: Mapped[list[Any]] = mapped_column(default=list)
    breakdown_point: Mapped[str | None] = mapped_column(String(48))
    outcome: Mapped[str | None] = mapped_column(String(32))
    emotion: Mapped[str | None] = mapped_column(String(16))
    frustration_intensity: Mapped[int | None] = mapped_column(Integer)
    primary_category: Mapped[str | None] = mapped_column(String(64), index=True)
    secondary_categories: Mapped[list[Any]] = mapped_column(default=list)
    high_stakes: Mapped[bool | None] = mapped_column(Boolean)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    evidence_strength: Mapped[int | None] = mapped_column(Integer)
    useful_for_discovery: Mapped[bool | None] = mapped_column(Boolean)
    problem_statement: Mapped[str | None] = mapped_column(Text)
    user_reported_issue: Mapped[str | None] = mapped_column(Text)
    extracted_retrieval_problem: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    quote_grounded: Mapped[bool | None] = mapped_column(Boolean)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class EmbeddingRow(Base):
    """Pipeline-only. Vectors stored as float32 bytes (pgvector is a later upgrade)."""

    __tablename__ = "embeddings"

    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


# --- Synthesis and scoring (run-scoped) --------------------------------------


class OpportunityAreaRow(Base):
    __tablename__ = "opportunity_areas"

    area_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))
    is_emergent: Mapped[bool] = mapped_column(Boolean, default=False)
    problem_summary: Mapped[str | None] = mapped_column(Text)
    aggregates: Mapped[dict[str, Any]] = mapped_column(default=dict)
    research_questions: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="active")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'merged', 'archived')", name="ck_area_status"),
    )


class ClusterRow(Base):
    __tablename__ = "clusters"

    cluster_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    area_id: Mapped[str | None] = mapped_column(String(64), index=True)
    label: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    mapped_category: Mapped[str | None] = mapped_column(String(64))
    centroid: Mapped[bytes | None] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer, default=0)


class OpportunityScoreRow(Base):
    __tablename__ = "opportunity_scores"

    area_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    frequency: Mapped[float] = mapped_column(Float)
    severity: Mapped[float] = mapped_column(Float)
    strategic_fit: Mapped[float] = mapped_column(Float)
    evidence_quality: Mapped[float] = mapped_column(Float)
    product_leverage: Mapped[float] = mapped_column(Float)
    research_value: Mapped[float] = mapped_column(Float)
    composite: Mapped[float] = mapped_column(Float)
    band: Mapped[str] = mapped_column(String(8))
    inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    weights: Mapped[dict[str, Any]] = mapped_column(default=dict)
    low_evidence_flag: Mapped[bool] = mapped_column(Boolean, default=False)


class OpportunityEvidenceRow(Base):
    __tablename__ = "opportunity_evidence"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    area_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id"), primary_key=True)
    is_representative: Mapped[bool] = mapped_column(Boolean, default=False)
    rank: Mapped[int | None] = mapped_column(Integer)


# --- Human in the loop -------------------------------------------------------


class PMOverrideRow(Base):
    __tablename__ = "pm_overrides"

    override_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(8))
    target_id: Mapped[str] = mapped_column(String(64))
    field: Mapped[str] = mapped_column(String(64))
    ai_value: Mapped[Any | None] = mapped_column(JSON)
    override_value: Mapped[Any] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    __table_args__ = (
        CheckConstraint("target_type IN ('item', 'area')", name="ck_override_target"),
        Index("ix_pm_overrides_target", "target_type", "target_id"),
    )


# --- Operations --------------------------------------------------------------


class PipelineRunRow(Base):
    """One row per (run, stage)."""

    __tablename__ = "pipeline_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stage: Mapped[str] = mapped_column(String(32), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16), index=True)
    counts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    errors: Mapped[list[Any]] = mapped_column(default=list)
    llm_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'partial', 'failed', 'published', 'skipped')",
            name="ck_pipeline_runs_status",
        ),
    )


class PublishedRunRow(Base):
    """Single-row pointer to the run the dashboard shows."""

    __tablename__ = "published_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    published_run_id: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(default=_utcnow)

    __table_args__ = (CheckConstraint("id = 1", name="ck_published_run_single_row"),)


class LLMCacheRow(Base):
    __tablename__ = "llm_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]]
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


CORE_TABLES = frozenset(Base.metadata.tables)
