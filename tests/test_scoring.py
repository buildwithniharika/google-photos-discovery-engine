"""Phase 6 scoring: dimension formulas, guardrails, and a repeatable ranked write."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from discovery.db import session_scope
from discovery.models.orm import (
    ClusterRow,
    InsightRow,
    ItemRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    OpportunityScoreRow,
    PipelineRunRow,
    PMOverrideRow,
    PublishedRunRow,
    RelevanceRow,
)
from discovery.models.schemas import AreaRubric, RubricDimension
from discovery.runs import track_stage
from discovery.scoring.dimensions import (
    DIMENSIONS,
    ScoredItem,
    adjusted_share,
    cap_by_day,
    clamp_score,
    engagement_factor,
    fixed_threshold_score,
    in_window,
    score_evidence_quality,
    score_frequency,
    score_severity,
    score_strategic_fit,
    window_start,
)
from discovery.scoring.ranker import (
    AreaRanking,
    apply_ranks,
    band_for,
    emerging_label,
    normalize_weights,
    rank_areas,
    weight_sensitivity,
    weighted_sum,
)
from discovery.scoring.rubric import heuristic_rubric, rubric_input
from discovery.scoring.stage import render_score_report, run_scoring

NOW = datetime(2026, 10, 2, tzinfo=UTC)


def _item(
    item_id: str,
    *,
    source: str = "play_store",
    platform: str = "Android",
    retrieval: str = "vague_memory_retrieval",
    relevance: float = 0.8,
    frustration: int = 4,
    high_stakes: bool = False,
    strength: int = 3,
    rating: int | None = 2,
    engagement: dict | None = None,
    date: datetime | None = None,
    spam: bool = False,
) -> ScoredItem:
    return ScoredItem(
        item_id=item_id,
        source=source,
        platform=platform,
        retrieval_type=retrieval,
        vague_memory_relevance=relevance,
        frustration_intensity=frustration,
        high_stakes=high_stakes,
        evidence_strength=strength,
        rating=rating,
        engagement=engagement or {},
        date=date,
        is_spam=spam,
        problem_statement=f"problem {item_id}",
        evidence_quote=f"quote {item_id} is long enough to ground",
        quote_grounded=True,
    )


def _vague_corpus(n: int, source: str = "play_store") -> list[ScoredItem]:
    return [_item(f"c{i}", source=source) for i in range(n)]


# --- dimensions --------------------------------------------------------------


def test_frequency_day_cap_limits_a_single_day():
    day = datetime(2026, 9, 1, tzinfo=UTC)
    items = [_item(f"d{i}", date=day) for i in range(10)]
    kept, dropped = cap_by_day(items, 0.10)
    assert dropped == 9
    assert len(kept) == 1
    assert kept[0].item_id == "d0"  # lowest id survives, so the cap is repeatable


def test_undated_items_are_not_treated_as_one_burst():
    items = [_item(f"u{i}") for i in range(5)]
    kept, dropped = cap_by_day(items, 0.10)
    assert dropped == 0 and len(kept) == 5


def test_engagement_is_log_scaled_and_capped_per_item():
    viral = _item("v", engagement={"same_question": 10_000})
    capped = _item("c", engagement={"same_question": 100})
    assert engagement_factor([viral], cap=100, alpha=0.15) == pytest.approx(
        engagement_factor([capped], cap=100, alpha=0.15)
    )
    quiet = engagement_factor([_item("q")], cap=100, alpha=0.15)
    loud = engagement_factor([capped], cap=100, alpha=0.15)
    assert quiet == 1.0
    assert 1.0 < loud <= 1.15


def test_source_balance_stops_one_source_from_dominating():
    corpus = _vague_corpus(100, "play_store") + [
        _item(f"g{i}", source="google_community", platform="Google Community") for i in range(10)
    ]
    play_heavy = [_item(f"p{i}") for i in range(50)]
    community = [
        _item(f"m{i}", source="google_community", platform="Google Community") for i in range(10)
    ]
    _, play_detail = adjusted_share(
        play_heavy, corpus, day_cap=1, engagement_cap=100, engagement_alpha=0, raw_weight=0.5
    )
    _, community_detail = adjusted_share(
        community, corpus, day_cap=1, engagement_cap=100, engagement_alpha=0, raw_weight=0.5
    )
    assert play_detail["raw_share"] > community_detail["raw_share"]
    assert community_detail["source_balanced_share"] > play_detail["source_balanced_share"]


def test_fewer_than_five_areas_use_fixed_thresholds_not_quantiles():
    scores, mapping = score_frequency(
        [0.01, 0.06, 0.30], min_areas=5, thresholds=(0.02, 0.05, 0.12, 0.25)
    )
    assert mapping == "fixed_threshold"
    assert scores == [1.0, 3.0, 5.0]
    assert fixed_threshold_score(0.25, (0.02, 0.05, 0.12, 0.25)) == 5.0


def test_quantiles_put_the_ends_at_1_and_5_and_ties_share_a_score():
    scores, mapping = score_frequency(
        [0.1, 0.4, 0.4, 0.2, 0.9], min_areas=5, thresholds=(0.02, 0.05, 0.12, 0.25)
    )
    assert mapping == "quantile"
    assert scores[0] == 1.0
    assert scores[4] == 5.0
    assert scores[1] == scores[2]


def test_severity_reweights_when_the_source_has_no_ratings():
    rated = [
        _item("a", frustration=4, high_stakes=True),
        _item("b", frustration=4, high_stakes=False),
    ]
    unrated = [
        _item("a", frustration=4, high_stakes=True, rating=None),
        _item("b", frustration=4, high_stakes=False, rating=None),
    ]
    with_ratings = score_severity(rated)
    without = score_severity(unrated)
    assert with_ratings.detail["low_rating_share"] == 1.0
    assert without.detail["ratings_reweighted"] is True
    assert without.detail["low_rating_share"] is None
    assert "re-weighted" in without.explanation
    assert without.score == clamp_score(0.625 * 4 + 0.375 * 2.5)
    assert with_ratings.score != without.score


def test_strategic_fit_is_relevance_times_vague_share_and_clamps_at_1():
    mixed = [_item("v", relevance=0.8), _item("g", retrieval="general_retrieval", relevance=0.8)]
    fit = score_strategic_fit(mixed)
    assert fit.score == 2.0  # 5 * 0.8 * 0.5
    thin = [_item("g", retrieval="general_retrieval", relevance=0.2)]
    low = score_strategic_fit(thin)
    assert low.score == 1.0
    assert "clamped" in low.explanation


def test_one_platform_is_called_out_in_the_evidence_explanation():
    quality = score_evidence_quality([_item("a"), _item("b")])
    assert quality.detail["platform_count"] == 1
    assert "One platform" in quality.explanation
    spread = score_evidence_quality(
        [
            _item("a", platform="Android"),
            _item("b", platform="iOS"),
            _item("c", platform="Reddit"),
            _item("d", platform="Google Community"),
        ]
    )
    assert spread.detail["platform_term"] == 5.0


def test_outside_window_and_spam_are_out_of_scope():
    start = window_start(NOW, 365)
    old = _item("old", date=datetime(2018, 1, 1, tzinfo=UTC))
    recent = _item("new", date=datetime(2026, 9, 1, tzinfo=UTC))
    undated = _item("undated")
    spam = _item("spam", spam=True, date=datetime(2026, 9, 1, tzinfo=UTC))
    assert in_window(old, start) is False
    assert in_window(recent, start) is True
    assert in_window(undated, start) is True
    assert in_window(spam, start) is False
    assert in_window(old, None) is True


# --- ranker ------------------------------------------------------------------


def _ranking(
    area_id: str, scores: dict[str, float], *, n: int = 20, low: str | None = None
) -> AreaRanking:
    from discovery.scoring.dimensions import DimensionScore

    dims = {
        key: DimensionScore(value, f"{key} {value:.2f}", {"ai_score": value})
        for key, value in scores.items()
    }
    weights = normalize_weights(
        {
            "frequency": 0.2,
            "severity": 0.2,
            "strategic_fit": 0.2,
            "evidence_quality": 0.15,
            "product_leverage": 0.15,
            "research_value": 0.1,
        }
    )
    return AreaRanking(
        area_id=area_id,
        name=area_id,
        category="life_event_retrieval",
        item_count=n,
        vague_items=n,
        dimensions=dims,
        composite=weighted_sum(scores, weights),
        composite_ai=weighted_sum(scores, weights),
        band="Medium",
        low_evidence_reason=low,
        weights=weights,
    )


def test_weights_that_do_not_sum_to_one_are_normalized():
    weights = normalize_weights(
        {
            "frequency": 2,
            "severity": 2,
            "strategic_fit": 2,
            "evidence_quality": 2,
            "product_leverage": 1,
            "research_value": 1,
        }
    )
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["frequency"] == pytest.approx(0.2)


def _all(score: float) -> dict[str, float]:
    return dict.fromkeys(DIMENSIONS, score)


def test_low_evidence_never_ranks_above_an_adequate_area():
    strong = _ranking(
        "low-evidence",
        _all(5),
        n=4,
        low="Emerging – low evidence: 4 items (< 10)",
    )
    plain = _ranking("adequate", _all(2))
    ordered = rank_areas([strong, plain])
    assert [a.area_id for a in ordered] == ["adequate", "low-evidence"]
    assert ordered[0].rank == 1


def test_ties_break_on_evidence_quality_then_item_count_then_id():
    base = {
        "frequency": 3,
        "severity": 3,
        "strategic_fit": 3,
        "evidence_quality": 3,
        "product_leverage": 3,
        "research_value": 3,
    }
    richer = _ranking("b", {**base, "evidence_quality": 4})
    larger = _ranking("c", base, n=30)
    smaller = _ranking("a", base, n=12)
    for area in (richer, larger, smaller):
        area.composite = 3.0
    ordered = rank_areas([smaller, larger, richer])
    assert [a.area_id for a in ordered] == ["b", "c", "a"]


def test_bands_follow_the_configured_cutoffs(cfg):
    assert band_for(3.8, cfg.scoring.bands) == "High"
    assert band_for(3.0, cfg.scoring.bands) == "Medium"
    assert band_for(2.99, cfg.scoring.bands) == "Low"


def test_emerging_label_fires_on_size_or_quality(cfg):
    settings = cfg.settings.scoring
    assert emerging_label(9, 4.0, settings).startswith("Emerging")
    assert emerging_label(20, 2.4, settings).startswith("Emerging")
    assert emerging_label(20, 2.5, settings) is None


def test_top3_changes_when_a_weight_moves_and_stays_when_scores_match():
    weights = {
        "frequency": 0.20,
        "severity": 0.20,
        "strategic_fit": 0.20,
        "evidence_quality": 0.15,
        "product_leverage": 0.15,
        "research_value": 0.10,
    }
    leader = _ranking(
        "leader",
        {**_all(5), "frequency": 1},
    )
    trailer = _ranking(
        "trailer",
        {**_all(3.8), "frequency": 5},
    )
    summary = weight_sensitivity([leader, trailer], weights)
    assert summary["baseline_top3"][0] == "leader"
    assert summary["top3_stable"] is False
    flipped = [
        change
        for change in summary["changes"]
        if change["dimension"] == "frequency" and change["top3"][0] == "trailer"
    ]
    assert flipped
    same = [_ranking("a", _all(3)), _ranking("b", _all(3))]
    assert weight_sensitivity(same, weights)["top3_stable"] is True


def test_apply_ranks_records_the_ai_only_order_when_an_override_changes_it():
    weights = {
        "frequency": 0.2,
        "severity": 0.2,
        "strategic_fit": 0.2,
        "evidence_quality": 0.15,
        "product_leverage": 0.15,
        "research_value": 0.1,
    }
    overridden = _ranking("over", _all(3))
    overridden.dimensions["product_leverage"] = type(overridden.dimensions["product_leverage"])(
        5, "override", {"ai_score": 1}
    )
    overridden.composite = weighted_sum(overridden.scores(), weights)
    overridden.composite_ai = weighted_sum(overridden.scores(ai=True), weights)
    other = _ranking("other", _all(3.2))
    summary = apply_ranks([overridden, other], weights)
    assert summary["overrides_change_top3"] is True
    assert overridden.rank == 1
    assert overridden.rank_ai == 2


# --- stage -------------------------------------------------------------------


def _persist_item(
    session,
    item: ScoredItem,
    *,
    area_id: str | None,
    run_id: str,
    representative: bool = False,
):
    session.add(
        ItemRow(
            item_id=item.item_id,
            primary_source_name=item.source,
            platform=item.platform,
            source_url=f"https://example.test/{item.item_id}",
            original_text=item.problem_statement,
            clean_text=item.problem_statement,
            date=item.date,
            rating=item.rating,
            engagement=dict(item.engagement),
            is_spam=item.is_spam,
        )
    )
    session.flush()
    session.add(
        RelevanceRow(
            item_id=item.item_id,
            stage_reached="C",
            retrieval_type=item.retrieval_type,
            vague_memory_relevance=item.vague_memory_relevance,
            confidence=0.9,
            model="m",
            prompt_version="relevance_v5",
        )
    )
    session.add(
        InsightRow(
            item_id=item.item_id,
            problem_statement=item.problem_statement,
            trying_to_find="the photo",
            frustration_intensity=item.frustration_intensity,
            high_stakes=item.high_stakes,
            evidence_strength=item.evidence_strength,
            evidence_quote=item.evidence_quote,
            quote_grounded=item.quote_grounded,
            primary_category="life_event_retrieval",
        )
    )
    if area_id is not None:
        session.add(
            OpportunityEvidenceRow(
                run_id=run_id,
                area_id=area_id,
                item_id=item.item_id,
                is_representative=representative,
                rank=1 if representative else None,
            )
        )


def _area_row(
    session,
    run_id: str,
    area_id: str,
    *,
    status: str = "active",
    name: str = "Wedding photos",
):
    session.add(
        OpportunityAreaRow(
            area_id=area_id,
            run_id=run_id,
            name=name,
            category="life_event_retrieval",
            problem_summary="Users cannot find wedding photos from a rough year.",
            research_questions=[
                {"question": "Which year do they try first?", "evidence_gap": "year"}
            ],
            status=status,
            aggregates={},
        )
    )


def test_heuristic_scoring_is_repeatable_and_does_not_publish(cfg, session_factory):
    run_id = "cluster-1"
    with session_scope(session_factory) as session:
        _area_row(session, run_id, "oa-1")
        for index in range(12):
            _persist_item(
                session,
                _item(f"i{index}", date=datetime(2026, 8, index + 1, tzinfo=UTC)),
                area_id="oa-1",
                run_id=run_id,
                representative=index == 0,
            )

    def run_once():
        with track_stage(session_factory, run_id, "score") as stage:
            return run_scoring(session_factory, cfg, stage, use_llm=False, as_of=NOW)

    def snapshot():
        with session_scope(session_factory) as session:
            rows = session.scalars(select(OpportunityScoreRow)).all()
            return [
                (
                    r.frequency,
                    r.severity,
                    r.strategic_fit,
                    r.evidence_quality,
                    r.product_leverage,
                    r.research_value,
                    r.composite,
                    r.band,
                    r.low_evidence_flag,
                    r.inputs,
                    r.weights,
                )
                for r in rows
            ]

    first = run_once()
    stored = snapshot()
    second = run_once()
    assert first.written and not first.published
    assert first.rubric_source == "heuristic"
    assert stored == snapshot()
    assert first.areas[0].composite == second.areas[0].composite
    assert 1 <= first.areas[0].composite <= 5
    with session_scope(session_factory) as session:
        assert session.get(PublishedRunRow, 1) is None
    text = render_score_report(first, first.areas[0].weights)
    assert "Wedding photos" in text
    assert "Frequency" in text and "Severity" in text


def test_llm_rubric_publishes_and_a_skipped_run_leaves_the_pointer(cfg, session_factory):
    run_id = "cluster-2"
    rubric = AreaRubric(
        product_leverage=RubricDimension(score=4, rationale="Search can ask for the year."),
        research_value=RubricDimension(score=5, rationale="The year they mean is unknown."),
    )
    with session_scope(session_factory) as session:
        _area_row(session, run_id, "oa-2", name="Old trips")
        _persist_item(session, _item("v1"), area_id="oa-2", run_id=run_id, representative=True)
        for index in range(9):
            _persist_item(
                session,
                _item(f"g{index}", retrieval="general_retrieval", relevance=0.3),
                area_id="oa-2",
                run_id=run_id,
            )
    with track_stage(session_factory, run_id, "score") as stage:
        report = run_scoring(
            session_factory, cfg, stage, use_llm=False, rubrics={"oa-2": rubric}, as_of=NOW
        )
    assert report.published
    assert stage.final_status.value == "published"
    with session_scope(session_factory) as session:
        assert session.get(PublishedRunRow, 1).published_run_id == run_id
        row = session.get(OpportunityScoreRow, ("oa-2", run_id))
        assert row.product_leverage == 4
        assert row.research_value == 5
        assert "Search can ask" in row.inputs["explanations"]["product_leverage"]

    with track_stage(session_factory, "empty", "score") as stage:
        skipped = run_scoring(session_factory, cfg, stage, use_llm=False, as_of=NOW)
    assert skipped.skipped_reason
    with session_scope(session_factory) as session:
        assert session.get(PublishedRunRow, 1).published_run_id == run_id


def test_zero_vague_items_skips_scoring(cfg, session_factory):
    run_id = "cluster-3"
    with session_scope(session_factory) as session:
        _area_row(session, run_id, "oa-3")
        for index in range(6):
            _persist_item(
                session,
                _item(f"n{index}", retrieval="general_retrieval", relevance=0.2),
                area_id="oa-3",
                run_id=run_id,
            )
    with track_stage(session_factory, run_id, "score") as stage:
        report = run_scoring(session_factory, cfg, stage, use_llm=False, as_of=NOW)
    assert "No vague-retrieval" in report.skipped_reason
    with session_scope(session_factory) as session:
        assert session.scalars(select(OpportunityScoreRow)).all() == []


def test_old_items_drop_out_of_scoring_unless_the_window_is_open(cfg, session_factory):
    cfg.settings.scoring.analysis_window_days = 365
    run_id = "cluster-4"
    with session_scope(session_factory) as session:
        _area_row(session, run_id, "oa-4")
        _persist_item(
            session,
            _item("recent", date=datetime(2026, 9, 1, tzinfo=UTC)),
            area_id="oa-4",
            run_id=run_id,
        )
        _persist_item(
            session,
            _item("stale", date=datetime(2018, 1, 1, tzinfo=UTC)),
            area_id="oa-4",
            run_id=run_id,
        )
    with track_stage(session_factory, run_id, "score") as stage:
        report = run_scoring(session_factory, cfg, stage, use_llm=False, as_of=NOW)
    assert report.excluded_outside_window == 1
    assert report.areas[0].item_count == 1
    with track_stage(session_factory, run_id, "score") as stage:
        opened = run_scoring(
            session_factory, cfg, stage, use_llm=False, include_outside_window=True, as_of=NOW
        )
    assert opened.areas[0].item_count == 2


def test_pm_override_replaces_the_rubric_and_is_explained(cfg, session_factory):
    run_id = "cluster-5"
    with session_scope(session_factory) as session:
        _area_row(session, run_id, "oa-5")
        for index in range(10):
            _persist_item(session, _item(f"p{index}"), area_id="oa-5", run_id=run_id)
        session.add(
            PMOverrideRow(
                target_type="area",
                target_id="oa-5",
                field="product_leverage",
                ai_value=3,
                override_value=5,
                note="PM: guided retrieval would help",
            )
        )
        session.add(
            PipelineRunRow(
                run_id=run_id, stage="cluster", status="completed", started_at=NOW, counts={}
            )
        )
        session.add(ClusterRow(cluster_id="c1", run_id=run_id, area_id="oa-5", size=10))
    with track_stage(session_factory, run_id, "score") as stage:
        report = run_scoring(session_factory, cfg, stage, use_llm=False, as_of=NOW)
    leverage = report.areas[0].dimensions["product_leverage"]
    assert leverage.score == 5
    assert leverage.detail["ai_score"] != 5
    assert "PM override" in leverage.explanation
    assert report.areas[0].composite != report.areas[0].composite_ai
    from discovery.scoring.stage import record_score_override

    override_id, seen = record_score_override(
        session_factory, "oa-5", "research_value", 2, note="already understood"
    )
    assert seen == run_id and override_id
    with pytest.raises(ValueError, match="between 1 and 5"):
        record_score_override(session_factory, "oa-5", "research_value", 9)


def test_archived_area_is_not_scored(cfg, session_factory):
    run_id = "cluster-6"
    with session_scope(session_factory) as session:
        _area_row(session, run_id, "oa-live")
        _area_row(session, run_id, "oa-gone", status="archived", name="Archived")
        _persist_item(session, _item("live"), area_id="oa-live", run_id=run_id)
        _persist_item(session, _item("gone"), area_id="oa-gone", run_id=run_id)
    with track_stage(session_factory, run_id, "score") as stage:
        report = run_scoring(session_factory, cfg, stage, use_llm=False, as_of=NOW)
    assert [area.area_id for area in report.areas] == ["oa-live"]


def test_rubric_input_is_stable_and_heuristic_is_deterministic():
    item = _item("q1")
    kwargs = dict(
        name="Wedding photos",
        category="life_event_retrieval",
        summary="Users name the wedding and not the date.",
        questions=[{"question": "What year?", "evidence_gap": "year"}],
        items=[item],
        representative_ids=["q1"],
        quote_limit=5,
    )
    assert rubric_input(**kwargs) == rubric_input(**kwargs)
    first = heuristic_rubric(
        category="life_event_retrieval",
        name="Wedding photos",
        summary="Users name the wedding.",
        vague_items=8,
        item_count=10,
        question_count=5,
    )
    second = heuristic_rubric(
        category="life_event_retrieval",
        name="Wedding photos",
        summary="Users name the wedding.",
        vague_items=8,
        item_count=10,
        question_count=5,
    )
    assert first == second
    backup = heuristic_rubric(
        category="context_based_retrieval_failure",
        name="Backup failed after reset",
        summary="The backup failed and photos are gone.",
        vague_items=1,
        item_count=10,
        question_count=1,
    )
    assert backup.product_leverage.score == 2
