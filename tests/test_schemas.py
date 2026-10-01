from __future__ import annotations

import pytest
from pydantic import ValidationError

from discovery.models.schemas import (
    AttemptType,
    BreakdownPoint,
    Category,
    ContentType,
    CueType,
    Emotion,
    ExcludedTopic,
    ForgottenDetail,
    Insight,
    Item,
    OpportunityScore,
    Outcome,
    PMOverride,
    RawItem,
    RelevanceResult,
    RetrievalType,
)

# Examples from architecture Sections 7 and 8.1.
RELEVANCE_EXAMPLE = {
    "is_google_photos": True,
    "is_retrieval": True,
    "retrieval_type": "vague_memory_retrieval",
    "vague_memory_relevance": 0.86,
    "excluded_topic": None,
    "excluded_topic_blocks_retrieval": None,
    "rationale": "User remembers a café on a Goa trip but not the date or name; search fails.",
    "confidence": 0.9,
}

INSIGHT_EXAMPLE = {
    "trying_to_find": "Photo of a small café visited during a Goa trip",
    "content_type": "photo",
    "remembered_cues": [
        {"cue": "Goa trip", "cue_type": "trip_or_event"},
        {"cue": "small café", "cue_type": "place_vague"},
    ],
    "forgotten_details": ["exact_date", "location_name"],
    "search_attempts": [
        {"attempt": "searched 'Goa café'", "attempt_type": "keyword_search"},
        {"attempt": "scrolled through 2023 photos", "attempt_type": "manual_scroll"},
    ],
    "breakdown_point": "results_irrelevant_or_too_broad",
    "outcome": "not_found",
    "emotion": "frustrated",
    "frustration_intensity": 4,
    "primary_category": "context_based_retrieval_failure",
    "secondary_categories": ["location_ambiguity"],
    "high_stakes": False,
    "evidence_quote": "I searched Goa café and it showed me every beach photo but not the café",
    "evidence_strength": 4,
    "useful_for_discovery": True,
    "user_reported_issue": "Search for 'Goa café' only returns beach photos.",
    "problem_statement": "User remembers trip context and a vague place type but search can't "
    "connect trip context to a specific venue.",
    "confidence": 0.84,
}


@pytest.mark.parametrize(
    ("enum", "key"),
    [
        (ContentType, "content_types"),
        (CueType, "cue_types"),
        (ForgottenDetail, "forgotten_details"),
        (BreakdownPoint, "breakdown_points"),
        (AttemptType, "attempt_types"),
        (Emotion, "emotions"),
        (Outcome, "outcomes"),
        (RetrievalType, "retrieval_types"),
        (ExcludedTopic, "excluded_topics"),
    ],
)
def test_enums_match_taxonomy_yaml(cfg, enum, key):
    assert [e.value for e in enum] == getattr(cfg.taxonomy, key)


def test_categories_match_taxonomy_yaml(cfg):
    assert [c.value for c in Category] == list(cfg.taxonomy.categories)


def test_relevance_example_validates():
    r = RelevanceResult.model_validate(RELEVANCE_EXAMPLE)
    assert r.retrieval_type is RetrievalType.VAGUE_MEMORY_RETRIEVAL


def test_relevance_rejects_out_of_range_scores():
    with pytest.raises(ValidationError):
        RelevanceResult.model_validate({**RELEVANCE_EXAMPLE, "vague_memory_relevance": 1.4})


def test_relevance_rejects_unknown_enum():
    with pytest.raises(ValidationError):
        RelevanceResult.model_validate({**RELEVANCE_EXAMPLE, "retrieval_type": "maybe"})


def test_relevance_not_retrieval_must_be_consistent():
    with pytest.raises(ValidationError, match="not_retrieval"):
        RelevanceResult.model_validate({**RELEVANCE_EXAMPLE, "is_retrieval": False})


def test_relevance_clears_blocks_flag_without_topic():
    r = RelevanceResult.model_validate(
        {**RELEVANCE_EXAMPLE, "excluded_topic_blocks_retrieval": True}
    )
    assert r.excluded_topic_blocks_retrieval is None


def test_insight_example_validates():
    i = Insight.model_validate(INSIGHT_EXAMPLE)
    assert i.remembered_cues[0].cue_type is CueType.TRIP_OR_EVENT
    assert i.primary_category is Category.CONTEXT_BASED_RETRIEVAL_FAILURE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frustration_intensity", 6),
        ("evidence_strength", 0),
        ("confidence", -0.1),
        ("primary_category", "search_is_bad"),
        ("forgotten_details", ["favourite_colour"]),
    ],
)
def test_insight_rejects_invalid_values(field, value):
    with pytest.raises(ValidationError):
        Insight.model_validate({**INSIGHT_EXAMPLE, field: value})


def test_insight_allows_null_quote():
    assert (
        Insight.model_validate({**INSIGHT_EXAMPLE, "evidence_quote": None}).evidence_quote is None
    )


def test_item_rating_bounds():
    base = {
        "item_id": "x",
        "primary_source_name": "play_store",
        "platform": "Android",
        "source_url": "https://example.com",
        "original_text": "can't find my screenshots",
    }
    assert Item.model_validate({**base, "rating": 2}).rating == 2
    with pytest.raises(ValidationError):
        Item.model_validate({**base, "rating": 7})


def test_raw_item_is_immutable():
    raw = RawItem(
        raw_id="app_store:123",
        source_name="app_store",
        platform="iOS",
        source_url="https://apps.apple.com",
        run_id="r1",
        payload={"title": "x"},
    )
    with pytest.raises(ValidationError):
        raw.run_id = "r2"


def test_opportunity_score_bounds():
    scores = dict.fromkeys(
        [
            "frequency",
            "severity",
            "strategic_fit",
            "evidence_quality",
            "product_leverage",
            "research_value",
            "composite",
        ],
        3.5,
    )
    OpportunityScore(area_id="a", run_id="r", band="Medium", **scores)
    with pytest.raises(ValidationError):
        OpportunityScore(area_id="a", run_id="r", band="Medium", **{**scores, "severity": 5.5})


def test_pm_override_accepts_any_json_value():
    o = PMOverride(
        target_type="item",
        target_id="i1",
        field="primary_category",
        ai_value="location_ambiguity",
        override_value="time_based_memory_gap",
    )
    assert o.created_at is not None
