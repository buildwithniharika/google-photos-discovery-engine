"""Tables must match architecture Section 13.2, on SQLite and (in CI) Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from discovery.db import init_db, make_session_factory, session_scope
from discovery.models.orm import (
    InsightRow,
    ItemRow,
    ItemSourceRow,
    PipelineRunRow,
    PMOverrideRow,
    PublishedRunRow,
    RawItemRow,
    RelevanceRow,
)

# Columns named in Section 13.2 (each table may have a few extra bookkeeping columns).
SPEC_COLUMNS = {
    "items": {
        "item_id",
        "primary_source_name",
        "platform",
        "source_url",
        "title",
        "original_text",
        "clean_text",
        "language",
        "date",
        "rating",
        "engagement",
        "author_hash",
        "content_hash",
        "is_spam",
        "similar_count",
        "metadata",
    },
    "item_sources": {"item_id", "raw_id", "source_name", "platform", "source_url", "run_id"},
    "relevance": {
        "item_id",
        "stage_reached",
        "is_google_photos",
        "is_retrieval",
        "retrieval_type",
        "vague_memory_relevance",
        "excluded_topic",
        "excluded_topic_blocks_retrieval",
        "rationale",
        "confidence",
        "model",
        "prompt_version",
    },
    "insights": {
        "item_id",
        "trying_to_find",
        "content_type",
        "remembered_cues",
        "forgotten_details",
        "search_attempts",
        "breakdown_point",
        "outcome",
        "emotion",
        "frustration_intensity",
        "primary_category",
        "secondary_categories",
        "high_stakes",
        "evidence_quote",
        "evidence_strength",
        "useful_for_discovery",
        "problem_statement",
        "user_reported_issue",
        "extracted_retrieval_problem",
        "confidence",
        "model",
        "prompt_version",
        "quote_grounded",
    },
    "clusters": {
        "cluster_id",
        "run_id",
        "label",
        "summary",
        "mapped_category",
        "centroid",
        "size",
    },
    "opportunity_areas": {
        "area_id",
        "name",
        "category",
        "is_emergent",
        "problem_summary",
        "aggregates",
        "research_questions",
        "status",
    },
    "opportunity_scores": {
        "area_id",
        "run_id",
        "frequency",
        "severity",
        "strategic_fit",
        "evidence_quality",
        "product_leverage",
        "research_value",
        "composite",
        "band",
        "inputs",
        "weights",
        "low_evidence_flag",
    },
    "opportunity_evidence": {"area_id", "item_id", "is_representative", "rank"},
    "pm_overrides": {
        "override_id",
        "target_type",
        "target_id",
        "field",
        "ai_value",
        "override_value",
        "note",
        "created_at",
    },
    "pipeline_runs": {
        "run_id",
        "stage",
        "started_at",
        "finished_at",
        "status",
        "counts",
        "errors",
        "llm_tokens",
        "llm_cost_usd",
    },
    "published_run": {"published_run_id", "published_at"},
    "llm_cache": {"cache_key", "response", "created_at"},
    "raw_items": {"raw_id", "source_name", "run_id", "fetched_at", "payload"},
    "embeddings": {"item_id", "model", "vector"},
}


def test_all_core_tables_exist(engine):
    assert set(inspect(engine).get_table_names()) >= set(SPEC_COLUMNS)


@pytest.mark.parametrize("table", sorted(SPEC_COLUMNS))
def test_table_has_spec_columns(engine, table):
    actual = {c["name"] for c in inspect(engine).get_columns(table)}
    missing = SPEC_COLUMNS[table] - actual
    assert not missing, f"{table} is missing {sorted(missing)}"


def test_init_db_is_idempotent(engine):
    init_db(engine)
    init_db(engine)


def _item(item_id: str = "item-1") -> ItemRow:
    return ItemRow(
        item_id=item_id,
        primary_source_name="play_store",
        platform="Android",
        source_url="https://play.google.com/store/apps/details?id=com.google.android.apps.photos",
        original_text="Can't find the screenshot of my train ticket from last month",
        rating=2,
        engagement={"thumbs_up": 3},
        metadata_={"app_version": "6.90"},
    )


def test_round_trip_item_with_relations(engine):
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        s.add(
            RawItemRow(
                raw_id="play_store:abc",
                source_name="play_store",
                run_id="r1",
                fetched_at=datetime.now(UTC),
                payload={"content": "x"},
            )
        )
        s.add(_item())
        s.flush()
        s.add(
            ItemSourceRow(
                raw_id="play_store:abc",
                item_id="item-1",
                source_name="play_store",
                platform="Android",
                source_url="https://x",
                run_id="r1",
            )
        )
        s.add(
            RelevanceRow(
                item_id="item-1",
                stage_reached="C",
                retrieval_type="general_retrieval",
                confidence=0.8,
                model="m",
                prompt_version="relevance_v1",
            )
        )
        s.add(
            InsightRow(
                item_id="item-1",
                remembered_cues=[{"cue": "train", "cue_type": "situation"}],
                forgotten_details=["exact_date"],
                content_type="screenshot",
            )
        )
    with session_scope(factory) as s:
        item = s.get(ItemRow, "item-1")
        assert item.metadata_ == {"app_version": "6.90"}
        assert item.engagement == {"thumbs_up": 3}
        insight = s.get(InsightRow, "item-1")
        assert insight.remembered_cues[0]["cue"] == "train"
        assert insight.forgotten_details == ["exact_date"]


def test_rating_check_constraint(engine):
    factory = make_session_factory(engine)
    bad = _item("bad")
    bad.rating = 9
    with pytest.raises(IntegrityError), session_scope(factory) as s:
        s.add(bad)


def test_item_source_requires_existing_item(engine):
    factory = make_session_factory(engine)
    with pytest.raises(IntegrityError), session_scope(factory) as s:
        s.add(
            ItemSourceRow(
                raw_id="r",
                item_id="does-not-exist",
                source_name="play_store",
                platform="Android",
                source_url="x",
                run_id="r1",
            )
        )


def test_published_run_is_single_row(engine):
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        s.add(PublishedRunRow(published_run_id="run-1"))
    with pytest.raises(IntegrityError), session_scope(factory) as s:
        s.add(PublishedRunRow(id=2, published_run_id="run-2"))


def test_pipeline_run_status_constraint(engine):
    factory = make_session_factory(engine)
    with pytest.raises(IntegrityError), session_scope(factory) as s:
        s.add(PipelineRunRow(run_id="r", stage="ingest", status="exploded"))


def test_pm_override_json_values(engine):
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        s.add(
            PMOverrideRow(
                target_type="area",
                target_id="a1",
                field="product_leverage",
                ai_value=3.2,
                override_value=4.0,
                note="Clear AI fit",
            )
        )
    with session_scope(factory) as s:
        row = s.query(PMOverrideRow).one()
        assert row.override_id is not None
        assert row.override_value == 4.0
