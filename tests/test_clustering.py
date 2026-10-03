"""Phase 5 pure functions: clustering, grouping, run matching, aggregates, quotes, citations."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from discovery.ai.clustering import (
    NOISE,
    ClusterNode,
    PriorCluster,
    centroid,
    cluster_labels,
    diverse_top,
    group_clusters,
    majority_category,
    match_prior,
    relabel_by_size,
    render_sweep,
    sweep,
)
from discovery.ai.embeddings import insight_text
from discovery.ai.opportunity import EvidenceItem, aggregates, normalize_cue, select_quotes
from discovery.ai.prompts import load_prompt
from discovery.ai.synthesis import (
    LABEL_PROMPT_NAME,
    LABEL_PROMPT_VERSION,
    estimate_step,
    resolve_synthesis,
    synthesis_input,
)
from discovery.config import ClusteringSettings
from discovery.models.schemas import OpportunitySynthesis


def unit(*values: float) -> np.ndarray:
    v = np.asarray(values, dtype=np.float32)
    return v / np.linalg.norm(v)


def blobs(n_per: int = 12, dims: int = 6, k: int = 3, noise: float = 0.02) -> np.ndarray:
    rng = np.random.default_rng(0)
    rows = []
    for c in range(k):
        base = np.zeros(dims, dtype=np.float32)
        base[c] = 1.0
        rows.extend(base + rng.normal(0, noise, dims) for _ in range(n_per))
    m = np.asarray(rows, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def ev(item_id: str, source: str = "play_store", **overrides) -> EvidenceItem:
    base = {
        "item_id": item_id,
        "source": source,
        "platform": "Android",
        "source_url": f"https://example.test/{item_id}",
        "retrieval_type": "vague_memory_retrieval",
        "vague_memory_relevance": 0.8,
        "problem_statement": "Cannot find the photo",
        "trying_to_find": "a photo",
        "content_type": "photo",
    }
    return EvidenceItem(**{**base, **overrides})


# --- clustering -----------------------------------------------------------------


def test_relabel_by_size_puts_the_largest_first_and_keeps_noise():
    labels = relabel_by_size(np.array([5, 5, -1, 2, 2, 2, 7]))
    assert labels.tolist() == [1, 1, NOISE, 0, 0, 0, 2]


def test_cluster_labels_finds_blobs_and_leaves_small_input_as_noise(cfg):
    settings = cfg.settings.clustering
    labels = cluster_labels(blobs(), settings, reducer=lambda v: v)
    assert sorted(set(labels.tolist()) - {NOISE}) == [0, 1, 2]
    assert (labels == NOISE).sum() <= 3
    tiny = cluster_labels(blobs(n_per=2), settings)
    assert (tiny == NOISE).all()


def test_sweep_runs_every_combination_and_marks_current(cfg):
    settings = cfg.settings.clustering
    grid = {"n_neighbors": (settings.umap.n_neighbors,), "min_cluster_size": (10, 50)}
    grid |= {"min_samples": (settings.hdbscan.min_samples,), "method": ("eom",)}
    results = sweep(blobs(), settings, grid, reducer_for=lambda nn: lambda v: v)
    assert [r.clusters for r in results] == [3, 0]
    assert 0.9 < results[0].coherence <= 1.0
    table = render_sweep(results, settings)
    assert table.count("(current)") == 1 and table.count("\n| 15") == 2


# --- representative selection ---------------------------------------------------


def test_diverse_top_takes_each_source_first_then_returns_best_first():
    candidates = [
        ("a1", "play_store", 0.9),
        ("a2", "play_store", 0.8),
        ("a3", "play_store", 0.7),
        ("b1", "app_store", 0.1),
        ("c1", "google_community", 0.2),
    ]
    assert diverse_top(candidates, 3) == ["a1", "c1", "b1"]
    assert diverse_top(candidates, 4) == ["a1", "a2", "c1", "b1"]
    assert diverse_top(candidates, 10) == ["a1", "a2", "a3", "c1", "b1"]
    no_a2 = diverse_top(candidates, 4, reject=lambda cid, picked: cid == "a2")
    assert no_a2 == ["a1", "a3", "c1", "b1"]


# --- P5.4 grouping --------------------------------------------------------------


def test_group_clusters_merges_close_same_category_clusters_only():
    near = [
        ClusterNode("c1", "time", unit(1, 0.05, 0), 20),
        ClusterNode("c2", "time", unit(1, 0.1, 0), 10),
        ClusterNode("c3", "people", unit(1, 0.08, 0), 15),  # close but another category
        ClusterNode("c4", "time", unit(0, 1, 0), 30),  # same category, far away
    ]
    groups = group_clusters(near, threshold=0.93)
    assert [sorted(g.keys) for g in groups] == [["c1", "c2"], ["c4"], ["c3"]]
    assert groups[1].size == 30 and groups[1].category == "time"
    assert len(group_clusters(near, threshold=0.99999)) == 4


def test_fixed_areas_are_kept_and_never_merged_with_each_other():
    nodes = [
        ClusterNode("c1", "time", unit(1, 0, 0), 10, area_id="oa-a"),
        ClusterNode("c2", "time", unit(1, 0.01, 0), 10, area_id="oa-b"),  # a PM split
        ClusterNode("c3", "time", unit(1, 0.02, 0), 5),
        ClusterNode("c4", "people", unit(0, 1, 0), 8, area_id="oa-a"),
    ]
    groups = {g.area_id: sorted(g.keys) for g in group_clusters(nodes, threshold=0.9)}
    assert groups == {"oa-a": ["c1", "c4"], "oa-b": ["c2", "c3"]}


def test_majority_category_is_size_weighted():
    nodes = [
        ClusterNode("c1", "time", unit(1, 0), 5),
        ClusterNode("c2", "time", unit(1, 0), 5),
        ClusterNode("c3", "people", unit(0, 1), 12),
    ]
    assert majority_category(nodes) == "people"


# --- P5.9 run matching ----------------------------------------------------------


def test_match_prior_picks_the_most_similar_cluster_above_threshold():
    prior = [
        PriorCluster("c01", "oa-1", unit(1, 0, 0)),
        PriorCluster("c02", "oa-2", unit(0.9, 0.3, 0)),
        PriorCluster("c09", "oa-9", unit(1, 0)),  # other dimension (model change): ignored
    ]
    new = {"x": unit(1, 0.05, 0), "y": unit(0, 0, 1)}
    matches = match_prior(new, prior, threshold=0.9)
    assert set(matches) == {"x"}
    best, sim = matches["x"]
    assert best.area_id == "oa-1" and sim > 0.99


def test_centroid_is_normalized():
    c = centroid(np.array([[1.0, 0.0], [0.0, 1.0]]))
    assert np.linalg.norm(c) == pytest.approx(1.0)


# --- P5.1 text, P5.5 aggregates, P5.6 quotes --------------------------------------


def test_insight_text_skips_missing_parts():
    assert insight_text("Search misses the café.", "the café photo", "search_results") == (
        "Search misses the café. Looking for: the café photo. Breakdown: search results."
    )
    assert insight_text("Lost it", "", "not_stated") == "Lost it."


def test_normalize_cue_folds_wording():
    assert normalize_cue('My  "Goa Trip"!') == "goa trip"
    assert normalize_cue("the  my  beach") == "beach"


def test_aggregates_count_per_item_and_skip_not_stated_attempts():
    items = [
        ev(
            "i1",
            remembered_cues=[
                {"cue": "Goa trip", "cue_type": "trip_or_event"},
                {"cue": "my goa trip", "cue_type": "trip_or_event"},
            ],
            search_attempts=[{"attempt": "searched goa", "attempt_type": "keyword_search"}],
            breakdown_point="search_results",
            frustration_intensity=4,
            high_stakes=True,
            rating=1,
        ),
        ev(
            "i2",
            "app_store",
            retrieval_type="general_retrieval",
            remembered_cues=[{"cue": "Goa Trip", "cue_type": "trip_or_event"}],
            search_attempts=[{"attempt": "", "attempt_type": "not_stated"}],
            frustration_intensity=2,
            rating=4,
        ),
    ]
    agg = aggregates(items)
    assert agg["items"] == 2 and agg["vague_items"] == 1 and agg["general_items"] == 1
    assert agg["cue_types"] == {"trip_or_event": 2}
    assert agg["top_cues"][0]["cue"] == "goa trip" and agg["top_cues"][0]["count"] == 2
    assert agg["attempt_types"] == {"keyword_search": 1} and agg["items_with_attempts"] == 1
    assert agg["dominant_breakdown"] == "search_results"
    assert agg["breakdown"] == {"not_stated": 1, "search_results": 1}
    assert agg["mean_frustration"] == 3 and agg["high_stakes_share"] == 0.5
    assert agg["low_rating_share"] == 0.5 and agg["sources"] == {"app_store": 1, "play_store": 1}


def test_select_quotes_needs_grounded_quotes_and_drops_near_duplicates():
    quote = "I searched for the beach in Goa and it only showed me my screenshots"
    items = [
        ev("a", evidence_quote=quote, quote_grounded=True, evidence_strength=5),
        ev("b", evidence_quote=quote + " again", quote_grounded=True, evidence_strength=5),
        ev("c", "app_store", evidence_quote="Faces vanished after the update", quote_grounded=True),
        ev("d", evidence_quote="Ungrounded words here", quote_grounded=False, evidence_strength=5),
    ]
    sims = {"a": 0.9, "b": 0.85, "c": 0.5, "d": 0.99}
    picked = select_quotes(items, sims, max_quotes=8, dedup_ratio=85)
    assert picked == ["a", "c"]
    assert select_quotes(items[3:], sims, max_quotes=8, dedup_ratio=85) == []


# --- P5.7-P5.8 citations --------------------------------------------------------


def _synthesis(**overrides) -> OpportunitySynthesis:
    body = {
        "name": "Wrong dates bury trip photos",
        "summary": [
            {"text": "Users cannot find trip photos by date.", "citations": ["E1", "[E2]"]},
            {"text": "Some blame the app.", "citations": []},
        ],
        "why_it_matters": {"text": "They remember when, not where.", "citations": ["e2", "E9"]},
        "research_questions": [
            {"question": f"Q{n}?", "evidence_gap": "gap", "citations": ["E1"]} for n in range(5)
        ],
    }
    return OpportunitySynthesis.model_validate({**body, **overrides})


def test_resolve_synthesis_maps_ids_and_flags_uncited_sentences():
    resolved = resolve_synthesis(_synthesis(), {"E1": "item-1", "E2": "item-2"})
    assert resolved.summary[0] == {
        "text": "Users cannot find trip photos by date.",
        "item_ids": ["item-1", "item-2"],
        "flagged": False,
    }
    assert resolved.summary[1]["flagged"] is True
    assert resolved.why_it_matters["item_ids"] == ["item-2"]
    assert resolved.unknown_refs == ["E9"]
    assert resolved.uncited == 1
    assert resolved.research_questions[0]["item_ids"] == ["item-1"]
    assert (
        resolved.problem_summary.startswith("Users cannot")
        and "Some blame" in resolved.problem_summary
    )


def test_synthesis_input_numbers_evidence_and_quotes_only_grounded_text():
    items = [
        ev("x1", evidence_quote="grounded words", quote_grounded=True),
        ev("x2", evidence_quote="made up", quote_grounded=False),
    ]
    agg = aggregates(items)
    text, refs = synthesis_input(
        draft_name="Draft",
        category="time_based_memory_gap",
        category_name="Time-Based Memory Gap",
        sub_themes=[{"label": "Theme", "size": 2, "summary": "s"}],
        agg=agg,
        evidence=items,
    )
    assert refs == {"E1": "x1", "E2": "x2"}
    assert "<<<E1>>>" in text and "<<<E2>>>" in text
    assert '"grounded words"' in text and "made up" not in text


def test_estimate_step_worst_case_covers_expected(cfg):
    prompt = load_prompt(LABEL_PROMPT_NAME, LABEL_PROMPT_VERSION, cfg.prompts_dir)
    llm = cfg.settings.llm
    est = estimate_step(prompt, ["input one", "input two"], llm.large_model, 250, 700, llm)
    assert est.calls == 2 and 0 < est.usd < est.worst_case_usd
    assert estimate_step(prompt, [], llm.large_model, 250, 700, llm).usd == 0


# --- config ---------------------------------------------------------------------


def test_clustering_settings_validate_and_resolve_models(cfg):
    settings = cfg.settings.clustering
    llm = cfg.settings.llm
    assert settings.resolve_model("large", llm) == llm.large_model
    assert settings.resolve_model("small", llm) == llm.small_model
    assert settings.resolve_model("some-model", llm) == "some-model"
    assert settings.min_quotes <= settings.max_quotes
    with pytest.raises(ValidationError):
        ClusteringSettings.model_validate(
            {**settings.model_dump(), "min_quotes": 9, "max_quotes": 8}
        )
