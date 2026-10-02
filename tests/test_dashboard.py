"""Dashboard queries: published-run scope, filters, overrides, and live re-rank."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from dashboard.data_access import (
    latest_overrides,
    read_areas,
    read_banner,
    read_evidence,
    read_overview,
    read_published,
    read_quality,
    write_override,
)
from dashboard.filters import Filters
from dashboard.ranking import live_rank
from dashboard.views import load_views
from sqlalchemy import inspect, select

from discovery.db import session_scope
from discovery.models.orm import (
    InsightRow,
    ItemRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    OpportunityScoreRow,
    PipelineRunRow,
    PMOverrideRow,
    PublishedRunRow,
    RawItemRow,
    RelevanceRow,
)

PUBLISHED = "pub"
OTHER = "other"
WHEN = datetime(2026, 8, 1, tzinfo=UTC)
LIFE = "life_event_retrieval"
TIME = "time_based_memory_gap"
WEIGHTS = {
    "frequency": 0.2,
    "severity": 0.2,
    "strategic_fit": 0.2,
    "evidence_quality": 0.15,
    "product_leverage": 0.15,
    "research_value": 0.1,
}


def _item(item_id, *, platform, source="play_store", spam=False, rating=2):
    return ItemRow(
        item_id=item_id,
        primary_source_name=source,
        platform=platform,
        source_url=f"https://example.test/{item_id}",
        original_text=f"original {item_id}",
        clean_text=f"clean text about finding a photo {item_id}",
        language="en",
        date=WHEN,
        rating=rating,
        is_spam=spam,
    )


def _relevance(item_id, retrieval_type, vague):
    return RelevanceRow(
        item_id=item_id,
        stage_reached="C",
        retrieval_type=retrieval_type,
        vague_memory_relevance=vague,
        confidence=0.9,
    )


def _insight(item_id, category, *, frustration=4, stakes=False):
    return InsightRow(
        item_id=item_id,
        primary_category=category,
        content_type="photo",
        frustration_intensity=frustration,
        high_stakes=stakes,
        confidence=0.8,
        evidence_quote=f"quote {item_id}",
        quote_grounded=True,
        problem_statement=f"problem {item_id}",
        remembered_cues=[{"cue": "the trip", "cue_type": "trip_or_event"}],
        forgotten_details=["exact_date"],
        search_attempts=[{"attempt": "beach", "attempt_type": "keyword_search"}],
        breakdown_point="query_formulation",
    )


def _area(area_id, run_id, name):
    return OpportunityAreaRow(
        area_id=area_id,
        run_id=run_id,
        name=name,
        category=LIFE,
        problem_summary="Users cannot get back to a photo they only half remember.",
        aggregates={
            "items": 1,
            "ai_name": f"AI {name}",
            "sub_themes": [{"cluster_id": "c01", "label": "Trip photos", "size": 1}],
        },
        research_questions=[{"question": "What do they type first?", "evidence_gap": "The words."}],
        status="active",
    )


def _score(area_id, run_id, **scores):
    base = dict.fromkeys(WEIGHTS, 3.0)
    base.update(scores)
    return OpportunityScoreRow(
        area_id=area_id,
        run_id=run_id,
        frequency=base["frequency"],
        severity=base["severity"],
        strategic_fit=base["strategic_fit"],
        evidence_quality=base["evidence_quality"],
        product_leverage=base["product_leverage"],
        research_value=base["research_value"],
        composite=3.2,
        band="Medium",
        inputs={"explanations": {"frequency": "Frequency explanation."}},
        weights=dict(WEIGHTS),
        low_evidence_flag=False,
    )


def _seed(factory) -> None:
    with session_scope(factory) as session:
        session.add_all(
            [
                _item("ios", platform="iOS"),
                _item("and", platform="Android"),
                _item("spam", platform="Android", spam=True),
                _item("other", platform="iOS", rating=5),
                RawItemRow(
                    raw_id="raw-1",
                    source_name="play_store",
                    run_id=PUBLISHED,
                    fetched_at=WHEN,
                    payload={},
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                _relevance("ios", "vague_memory_retrieval", 0.95),
                _relevance("and", "general_retrieval", 0.2),
                _relevance("other", "not_retrieval", 0.0),
                _insight("ios", LIFE, stakes=True),
                _insight("and", TIME, frustration=2),
                _area("oa-ios", PUBLISHED, "Milestone photos"),
                _area("oa-and", PUBLISHED, "Screenshots"),
                _area("oa-hidden", OTHER, "Should stay hidden"),
                _score("oa-ios", PUBLISHED, frequency=5, severity=2),
                _score("oa-and", PUBLISHED, frequency=2, severity=5),
                _score("oa-hidden", OTHER, frequency=4, severity=4),
                OpportunityEvidenceRow(
                    run_id=PUBLISHED,
                    area_id="oa-ios",
                    item_id="ios",
                    is_representative=True,
                    rank=1,
                ),
                OpportunityEvidenceRow(
                    run_id=PUBLISHED,
                    area_id="oa-and",
                    item_id="and",
                    is_representative=True,
                    rank=1,
                ),
                OpportunityEvidenceRow(
                    run_id=OTHER, area_id="oa-hidden", item_id="ios", is_representative=True, rank=1
                ),
                PublishedRunRow(
                    id=1,
                    published_run_id=PUBLISHED,
                    published_at=datetime(2026, 10, 1, tzinfo=UTC),
                ),
                PipelineRunRow(
                    run_id=PUBLISHED,
                    stage="score",
                    status="published",
                    started_at=datetime(2026, 9, 30, tzinfo=UTC),
                    finished_at=datetime(2026, 10, 1, tzinfo=UTC),
                    llm_tokens=100,
                    llm_cost_usd=0.1,
                ),
                PipelineRunRow(
                    run_id="newer",
                    stage="extract",
                    status="failed",
                    started_at=datetime(2026, 10, 2, tzinfo=UTC),
                    errors=["boom"],
                ),
            ]
        )


def _open(factory, engine):
    views = load_views(engine)
    session = factory()
    return views, session


def test_views_follow_the_published_run_only(sqlite_engine, session_factory):
    _seed(session_factory)
    views, session = _open(session_factory, sqlite_engine)
    try:
        assert "v_published_areas" in inspect(sqlite_engine).get_view_names()
        areas = read_areas(session, views, Filters(), {}, {})
    finally:
        session.close()
    assert {area["area_id"] for area in areas} == {"oa-ios", "oa-and"}


def test_platform_filter_keeps_ios_areas_and_narrows_the_funnel(sqlite_engine, session_factory):
    _seed(session_factory)
    views, session = _open(session_factory, sqlite_engine)
    filters = Filters(platforms=("iOS",))
    try:
        areas = read_areas(session, views, filters, {}, {})
        overview = read_overview(session, filters, {}, len(areas))
    finally:
        session.close()
    assert [area["area_id"] for area in areas] == ["oa-ios"]
    assert areas[0]["match_count"] == 1
    assert areas[0]["vague_match_count"] == 1
    assert overview["vague"] == 1
    assert overview["retrieval"] == 1
    assert overview["raw"] is None  # platform is not known on raw rows


def test_funnel_counts_without_filters(sqlite_engine, session_factory):
    _seed(session_factory)
    views, session = _open(session_factory, sqlite_engine)
    try:
        areas = read_areas(session, views, Filters(), {}, {})
        overview = read_overview(session, Filters(), {}, len(areas))
    finally:
        session.close()
    assert overview["raw"] == 1
    assert overview["items"] == 4
    assert overview["ready"] == 3
    assert overview["retrieval"] == 2
    assert overview["vague"] == 1
    assert overview["areas"] == 2
    assert overview["categories"][0]["category"] in {LIFE, TIME}


def test_item_override_replaces_the_ai_category_and_persists(sqlite_engine, session_factory):
    _seed(session_factory)
    write_override(
        target_type="item",
        target_id="ios",
        field="primary_category",
        ai_value=LIFE,
        override_value=TIME,
        note="misclassified",
        clear_cache=False,
        session_factory=session_factory,
    )
    views, session = _open(session_factory, sqlite_engine)
    try:
        overrides = latest_overrides(session, "item")
        moved = read_evidence(session, views, Filters(categories=(TIME,)), overrides)
        original = read_evidence(session, views, Filters(categories=(LIFE,)), overrides)
        stored = session.scalar(
            select(PMOverrideRow.override_value).where(PMOverrideRow.target_id == "ios")
        )
    finally:
        session.close()
    assert moved["total"] == 2  # android was already TIME, ios moved
    assert original["total"] == 0
    ios = next(row for row in moved["rows"] if row["item_id"] == "ios")
    assert ios["primary_category"] == TIME
    assert ios["primary_category_ai"] == LIFE
    assert ios["corrected"] is True
    assert stored == TIME


def test_irrelevant_items_drop_out_of_area_counts(sqlite_engine, session_factory):
    _seed(session_factory)
    write_override(
        target_type="item",
        target_id="ios",
        field="marked_irrelevant",
        ai_value=False,
        override_value=True,
        clear_cache=False,
        session_factory=session_factory,
    )
    views, session = _open(session_factory, sqlite_engine)
    try:
        overrides = latest_overrides(session, "item")
        areas = read_areas(session, views, Filters(platforms=("iOS",)), {}, overrides)
        page = read_evidence(session, views, Filters(), overrides)
    finally:
        session.close()
    assert areas == []
    assert all(row["item_id"] != "ios" for row in page["rows"])


def test_failed_newer_run_does_not_replace_the_published_one(sqlite_engine, session_factory):
    _seed(session_factory)
    with session_scope(session_factory) as session:
        published = read_published(session)
        banner = read_banner(session, published)
        assert published["run_id"] == PUBLISHED
    assert banner["kind"] == "failed"
    assert "did not finish" in banner["text"]


def test_area_rename_and_rejected_self_merge(sqlite_engine, session_factory):
    _seed(session_factory)
    write_override(
        target_type="area",
        target_id="oa-ios",
        field="name",
        ai_value="AI Milestone photos",
        override_value="Birthday lookup",
        clear_cache=False,
        session_factory=session_factory,
        run_id=PUBLISHED,
    )
    views, session = _open(session_factory, sqlite_engine)
    try:
        areas = read_areas(session, views, Filters(), latest_overrides(session, "area"), {})
    finally:
        session.close()
    ios = next(area for area in areas if area["area_id"] == "oa-ios")
    assert ios["name"] == "Birthday lookup"
    assert ios["name_overridden"] is True
    with pytest.raises(ValueError, match="itself"):
        write_override(
            target_type="area",
            target_id="oa-ios",
            field="merge_into",
            ai_value=None,
            override_value="oa-ios",
            clear_cache=False,
            session_factory=session_factory,
            run_id=PUBLISHED,
        )


def test_live_weights_change_the_order_immediately():
    areas = [
        {
            "area_id": "a",
            "frequency": 5,
            "severity": 1,
            "strategic_fit": 1,
            "evidence_quality": 3,
            "product_leverage": 1,
            "research_value": 1,
            "low_evidence_flag": False,
            "match_count": 10,
        },
        {
            "area_id": "b",
            "frequency": 1,
            "severity": 5,
            "strategic_fit": 1,
            "evidence_quality": 3,
            "product_leverage": 1,
            "research_value": 1,
            "low_evidence_flag": False,
            "match_count": 10,
        },
    ]
    by_frequency = live_rank(
        areas, {**WEIGHTS, "frequency": 0.9, "severity": 0.02}, high=3.8, medium=3.0
    )
    by_severity = live_rank(
        areas, {**WEIGHTS, "frequency": 0.02, "severity": 0.9}, high=3.8, medium=3.0
    )
    assert [area["area_id"] for area in by_frequency] == ["a", "b"]
    assert [area["area_id"] for area in by_severity] == ["b", "a"]


def test_low_evidence_stays_below_a_weaker_score():
    areas = [
        {
            "area_id": "thin",
            "frequency": 5,
            "severity": 5,
            "strategic_fit": 5,
            "evidence_quality": 5,
            "product_leverage": 5,
            "research_value": 5,
            "low_evidence_flag": True,
            "match_count": 2,
        },
        {
            "area_id": "solid",
            "frequency": 2,
            "severity": 2,
            "strategic_fit": 2,
            "evidence_quality": 3,
            "product_leverage": 2,
            "research_value": 2,
            "low_evidence_flag": False,
            "match_count": 20,
        },
    ]
    ranked = live_rank(areas, WEIGHTS, high=3.8, medium=3.0)
    assert [area["area_id"] for area in ranked] == ["solid", "thin"]


def test_dashboard_does_not_import_the_pipeline_models():
    source = Path("dashboard/data_access.py").read_text(encoding="utf-8")
    for banned in ("sentence_transformers", "umap", "hdbscan", "playwright", "anthropic"):
        assert banned not in source


def test_quality_snapshot_sees_the_failed_run(sqlite_engine, session_factory):
    _seed(session_factory)
    with session_scope(session_factory) as session:
        quality = read_quality(session)
    assert quality["published"]["run_id"] == PUBLISHED
    assert quality["llm_tokens"] == 100
    assert quality["grounding_failed"] == 0
    assert any(row["status"] == "failed" for row in quality["runs"])
