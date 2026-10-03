"""Phase 5 stage: clustering, labels, synthesis, writes, PM curation across runs, CLI."""

from __future__ import annotations

import csv
import zlib

import numpy as np
import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from discovery import cli
from discovery.ai.cluster_stage import (
    area_id_for,
    record_curation,
    render_cluster_report,
    run_clustering,
    write_review_sheet,
)
from discovery.ai.embeddings import INSIGHT_SUFFIX
from discovery.db import session_scope
from discovery.models.orm import (
    ClusterRow,
    EmbeddingRow,
    InsightRow,
    ItemRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    RelevanceRow,
)
from discovery.runs import track_stage
from tests.test_extraction import FakeBatches, _client
from tests.test_llm_client import FakeAnthropic, a_ok

# theme -> (problem wording the fake encoder keys on, extraction category, label category)
THEMES = {
    "t0": ("Trip photos show the wrong date in the timeline", "time_based_memory_gap"),
    "t1": ("Face groups vanished so a person cannot be found", "people_event_association_failure"),
    "t2": ("Search for a place returns unrelated photos", "search_trust_breakdown"),
}
KEYWORDS = {"wrong date": 0, "Face groups": 1, "unrelated": 2}
WORDS = [
    "beach",
    "wedding",
    "receipt",
    "sunset",
    "mountain",
    "grandma",
    "passport",
    "puppy",
    "concert",
    "birthday",
    "lake",
    "train",
    "museum",
    "garden",
    "snow",
    "festival",
    "market",
    "bridge",
    "castle",
    "river",
    "forest",
    "desert",
    "island",
    "harbor",
    "stadium",
    "temple",
    "library",
    "airport",
    "village",
    "canyon",
]
TIME, PEOPLE, SEARCH = (cat for _, cat in THEMES.values())


class ThemeEncoder:
    """One tight blob per theme, so HDBSCAN (on the raw vectors) finds one cluster each."""

    model_name = "fake"

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = []
        for text in texts:
            theme = next(n for k, n in KEYWORDS.items() if k in text)
            rng = np.random.default_rng(zlib.crc32(text.encode()))
            vec = rng.normal(0, 0.02, 8)
            vec[theme] += 1.0
            rows.append(vec / np.linalg.norm(vec))
        return np.asarray(rows, dtype=np.float32)


def _identity(vectors: np.ndarray) -> np.ndarray:
    return vectors


def seed(factory, per_theme: int = 12, not_useful: int = 2) -> None:
    sources = ("play_store", "app_store", "google_community")
    with session_scope(factory) as session:
        for theme, (problem, category) in THEMES.items():
            for n in range(per_theme + not_useful):
                item_id = f"{theme}-{n:02d}"
                rng = np.random.default_rng(zlib.crc32(item_id.encode()))
                quote = " ".join(rng.choice(WORDS, 6, replace=False))
                session.add(
                    ItemRow(
                        item_id=item_id,
                        primary_source_name=sources[n % 3],
                        platform="Android",
                        source_url=f"https://example.test/{item_id}",
                        original_text=quote,
                        clean_text=quote,
                        language="en",
                        rating=1 + n % 5,
                    )
                )
                session.flush()
                session.add(
                    RelevanceRow(
                        item_id=item_id,
                        stage_reached="C",
                        is_retrieval=True,
                        retrieval_type="vague_memory_retrieval" if n % 2 else "general_retrieval",
                        vague_memory_relevance=0.7,
                        confidence=0.9,
                    )
                )
                session.add(
                    InsightRow(
                        item_id=item_id,
                        problem_statement=f"{problem} (case {n})",
                        trying_to_find="a specific photo",
                        content_type="photo",
                        remembered_cues=[{"cue": "Goa trip", "cue_type": "trip_or_event"}],
                        search_attempts=[
                            {"attempt": "searched Goa", "attempt_type": "keyword_search"}
                        ],
                        breakdown_point="search_results",
                        primary_category=category,
                        evidence_quote=quote,
                        evidence_strength=3 + n % 3,
                        quote_grounded=True,
                        useful_for_discovery=n < per_theme,
                        frustration_intensity=4,
                    )
                )


def _theme_category(text: str) -> str:
    counts = {cat: text.count(k) for k, cat in zip(KEYWORDS, (TIME, PEOPLE, SEARCH), strict=True)}
    return max(counts, key=counts.__getitem__)


def respond(text: str) -> dict:
    category = _theme_category(text)
    if text.startswith("A cluster of"):
        return {
            "name": f"Label for {category}",
            "summary": "Users cannot find the photo.",
            "category": category,
            "rationale": "Fits.",
            "confidence": 0.9,
        }
    return {
        "name": f"Area for {category}",
        "summary": [
            {"text": "Users describe the problem.", "citations": ["E1", "E2"]},
            {"text": "An unsupported claim.", "citations": ["E99"]},
        ],
        "why_it_matters": {"text": "They remember context.", "citations": ["E1"]},
        "research_questions": [
            {"question": f"What happened {n}?", "evidence_gap": "gap", "citations": ["E2"]}
            for n in range(5)
        ],
    }


class Responder(FakeAnthropic):
    """Answers by request content (calls run in a thread pool, so order varies)."""

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return a_ok(respond(kwargs["messages"][0]["content"]), in_tok=1000, out_tok=200)


def run(factory, cfg, run_id: str, *, use_llm: bool = True, client=None, **kwargs):
    with track_stage(factory, run_id, "cluster") as stage:
        if use_llm and client is None:
            client = _client(cfg, factory, Responder())
        return run_clustering(
            factory,
            cfg,
            stage,
            client=client,
            encoder=ThemeEncoder(),
            reducer=_identity,
            use_llm=use_llm,
            use_batch_api=kwargs.pop("use_batch_api", False),
            **kwargs,
        )


def by_category(report) -> dict:
    return {a.category: a for a in report.areas}


def rows(factory, model, run_id: str) -> list:
    with session_scope(factory) as s:
        return list(s.scalars(select(model).where(model.run_id == run_id)))


# --- full run -------------------------------------------------------------------


def test_stage_labels_synthesizes_and_writes_rows(cfg, session_factory, tmp_path):
    seed(session_factory)
    report = run(session_factory, cfg, "r1")
    assert report.written and report.insights_total == 42 and report.excluded_not_useful == 6
    assert len(report.clusters) == 3 and report.noise == 0
    assert all(c.labeled_by == "llm" for c in report.clusters)
    assert set(by_category(report)) == {TIME, PEOPLE, SEARCH}
    for area in report.active:
        assert area.name == f"Area for {area.category}"
        assert area.synthesis.uncited == 1 and area.synthesis.unknown_refs == ["E99"]
        assert 5 <= len(area.quote_ids) <= cfg.settings.clustering.max_quotes
        assert not area.agg["emerging"] and not area.agg["few_quotes"]
        assert len({report.items[i].source for i in area.members}) == 3

    areas = {r.area_id: r for r in rows(session_factory, OpportunityAreaRow, "r1")}
    assert set(areas) == {a.area_id for a in report.areas}
    row = areas[by_category(report)[TIME].area_id]
    assert row.name == f"Area for {TIME}" and row.status == "active"
    assert row.problem_summary == "Users describe the problem. An unsupported claim."
    assert row.aggregates["uncited_sentences"] == 1
    assert row.aggregates["named_by"] == "llm" and row.aggregates["items"] == 12
    assert len(row.research_questions) == 5 and row.research_questions[0]["item_ids"]
    assert row.aggregates["summary_sentences"][1]["flagged"] is True

    clusters = rows(session_factory, ClusterRow, "r1")
    assert {c.area_id for c in clusters} == set(areas)
    assert all(c.centroid and c.mapped_category for c in clusters)
    evidence = rows(session_factory, OpportunityEvidenceRow, "r1")
    assert len(evidence) == 36
    reps = [e for e in evidence if e.is_representative]
    assert len(reps) == sum(len(a.quote_ids) for a in report.areas)
    assert all(e.rank >= 1 for e in reps)
    with session_scope(session_factory) as s:
        model_key = cfg.settings.clustering.embedding_model + INSIGHT_SUFFIX
        stored = s.scalar(select(func.count()).where(EmbeddingRow.model == model_key))
    assert stored == 36

    counts = report.as_counts()
    assert counts["clusters_labeled_by_llm"] == 3 and counts["areas_synthesized"] == 3
    assert counts["uncited_sentences"] == 3

    md = render_cluster_report(report, cfg.taxonomy.categories)
    assert md.count("### ") == 3 and "Opportunity Area: Area for" in md
    assert "⚠ _uncited_" in md and "Follow-up Research Questions" in md
    assert "## Taxonomy check" in md and "Preview without the LLM" not in md
    sheet = tmp_path / "review.csv"
    assert write_review_sheet(sheet, report) == 3
    first = next(csv.DictReader(sheet.open()))
    assert first["coherence_1_5"] == "" and first["research_questions"].startswith("What")


def test_preview_estimate_and_budget_stop(cfg, session_factory):
    seed(session_factory)
    preview = run(session_factory, cfg, "p1", use_llm=False)
    assert preview.written and not preview.llm_used
    assert all(c.labeled_by == "heuristic" for c in preview.clusters)
    assert all(a.synthesis is None for a in preview.areas)
    assert set(by_category(preview)) == {TIME, PEOPLE, SEARCH}  # extraction majority
    assert "Preview without the LLM" in render_cluster_report(preview, cfg.taxonomy.categories)
    with pytest.raises(ValueError, match="previews"):
        record_curation(session_factory, preview.areas[0].area_id, "name", "X")

    estimate = run(session_factory, cfg, "e1", estimate_only=True)
    assert not estimate.written and rows(session_factory, ClusterRow, "e1") == []
    assert estimate.estimate.label.calls == 3 and estimate.estimate.synthesis.calls == 3
    assert 0 < estimate.estimate.label.usd < estimate.estimate.label.worst_case_usd

    budget = cfg.settings.llm.project_budget_usd
    broke = _client(cfg, session_factory, Responder(), spent_before_usd=budget - 1e-6)
    stopped = run(session_factory, cfg, "b1", client=broke)
    assert stopped.stopped_early and stopped.written
    assert all(c.labeled_by == "heuristic" for c in stopped.clusters)
    assert all(a.synthesis is None for a in stopped.areas)

    labeled = run(session_factory, cfg, "r1")
    assert labeled.prior_run_id is None  # previews and stopped runs are not inherited


def test_batch_api_path_is_used_and_cached(cfg, session_factory):
    seed(session_factory)
    cfg.settings.clustering.batch_api_min_calls = 1
    fake = FakeBatches(lambda params: respond(params["messages"][0]["content"]))
    client = _client(cfg, session_factory, fake)
    report = run(session_factory, cfg, "r1", client=client, use_batch_api=True)
    assert len(report.batch_ids) == 2 and fake.calls == []
    assert all(a.synthesis for a in report.areas)

    again = _client(cfg, session_factory, FakeBatches(lambda params: "errored"))
    second = run(session_factory, cfg, "r2", client=again, use_batch_api=True)
    assert again.usage.cache_hits == 6 and again.usage.cost_usd == 0
    assert second.batch_ids == []


# --- PM curation across runs ----------------------------------------------------


def test_curation_survives_reruns_merge_unmerge_and_split(cfg, session_factory):
    seed(session_factory)
    first = run(session_factory, cfg, "r1")
    ids = {cat: a.area_id for cat, a in by_category(first).items()}

    record_curation(session_factory, ids[TIME], "name", "Dates bury trips", note="clearer")
    record_curation(session_factory, ids[SEARCH], "status", "archived")
    second = run(session_factory, cfg, "r2")
    assert second.prior_run_id == "r1"
    assert {cat: a.area_id for cat, a in by_category(second).items()} == ids
    time_area = by_category(second)[TIME]
    assert time_area.name == "Dates bury trips" and time_area.ai_name == f"Area for {TIME}"
    assert by_category(second)[SEARCH].status == "archived"
    assert {a.category for a in second.active} == {TIME, PEOPLE}
    stored = {r.area_id: r for r in rows(session_factory, OpportunityAreaRow, "r2")}
    assert stored[ids[TIME]].name == "Dates bury trips"
    assert stored[ids[TIME]].aggregates["named_by"] == "pm"

    record_curation(session_factory, ids[PEOPLE], "merge_into", ids[TIME])
    merged = run(session_factory, cfg, "r3")
    target = next(a for a in merged.areas if a.area_id == ids[TIME])
    assert len(target.cluster_ids) == 2 and target.size == 24
    assert [(s.area_id, s.merged_into) for s in merged.stubs] == [(ids[PEOPLE], ids[TIME])]
    stub = next(r for r in rows(session_factory, OpportunityAreaRow, "r3") if r.status == "merged")
    assert stub.area_id == ids[PEOPLE] and stub.aggregates["merged_into"] == ids[TIME]

    record_curation(session_factory, ids[PEOPLE], "merge_into", None)
    unmerged = run(session_factory, cfg, "r4")
    assert {a.area_id for a in unmerged.areas} == set(ids.values()) and unmerged.stubs == []

    record_curation(session_factory, ids[PEOPLE], "merge_into", ids[TIME])
    remerged = run(session_factory, cfg, "r5")
    people_cluster = next(c.cluster_id for c in remerged.clusters if c.home_area_id == ids[PEOPLE])
    search_clusters = next(a for a in remerged.areas if a.area_id == ids[SEARCH]).cluster_ids
    with pytest.raises(ValueError, match="at least one cluster"):
        record_curation(session_factory, ids[SEARCH], "split_clusters", search_clusters)
    with pytest.raises(ValueError, match="not in area"):
        record_curation(session_factory, ids[TIME], "split_clusters", ["c99"])
    override_id, run_id = record_curation(
        session_factory, ids[TIME], "split_clusters", [people_cluster]
    )
    assert run_id == "r5"
    split = run(session_factory, cfg, "r6")
    new_id = area_id_for(f"split:{override_id}")
    assert {a.area_id for a in split.areas} == {ids[TIME], ids[SEARCH], new_id}
    assert next(a for a in split.areas if a.area_id == new_id).category == PEOPLE
    assert "split from" in " ".join(next(a for a in split.areas if a.area_id == new_id).curation)


def test_record_curation_validates_input(cfg, session_factory):
    seed(session_factory)
    with pytest.raises(ValueError, match="No LLM-labeled cluster run"):
        record_curation(session_factory, "oa-x", "name", "X")
    report = run(session_factory, cfg, "r1")
    area_id = report.areas[0].area_id
    with pytest.raises(ValueError, match="not in the latest"):
        record_curation(session_factory, "oa-missing", "name", "X")
    with pytest.raises(ValueError, match="itself"):
        record_curation(session_factory, area_id, "merge_into", area_id)
    with pytest.raises(ValueError, match="status"):
        record_curation(session_factory, area_id, "status", "deleted")
    with pytest.raises(ValueError, match="Unknown"):
        record_curation(session_factory, area_id, "colour", "red")


# --- CLI ------------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cli._context.cache_clear()
    yield url
    cli._context.cache_clear()


def test_cli_cluster_preview_and_curate_area(db_url, tmp_path, monkeypatch):
    from discovery.ai import cluster_stage

    cfg, factory = cli._context()
    seed(factory)
    real = cluster_stage.run_clustering

    def fake_run(factory, cfg, run, **kwargs):
        return real(factory, cfg, run, **kwargs, encoder=ThemeEncoder(), reducer=_identity)

    monkeypatch.setattr(cluster_stage, "run_clustering", fake_run)
    report, review = tmp_path / "areas.md", tmp_path / "review.csv"
    args = ["cluster", "--no-llm", "--run-id", "p1", "--report", str(report)]
    result = runner.invoke(cli.app, [*args, "--review", str(review)])
    assert result.exit_code == 0, result.output
    assert "3 active areas" in result.output and report.exists() and review.exists()

    result = runner.invoke(cli.app, ["cluster", "--estimate", "--run-id", "e1"])
    assert result.exit_code == 0, result.output
    assert "Expected total" in result.output

    result = runner.invoke(cli.app, ["curate-area", "oa-x", "--rename", "New"])
    assert result.exit_code == 1 and "previews" in result.output
    result = runner.invoke(cli.app, ["curate-area", "oa-x"])
    assert result.exit_code != 0
