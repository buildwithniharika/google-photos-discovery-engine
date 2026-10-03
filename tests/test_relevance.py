"""Stage A, Stage B, scope rule, and gold-set recall."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from discovery.ai.funnel import WorkItem, run_funnel
from discovery.ai.prefilter import StageA
from discovery.ai.relevance import enforce_scope, in_scope
from discovery.ai.semantic_filter import audit_ids, keep_margin, similarity_margin
from discovery.config import PROJECT_ROOT
from discovery.eval.gold_set import exemplar_overlap, read_sheet
from discovery.eval.metrics import (
    STAGE_A_RECALL_TARGET,
    evaluate_gold,
)
from discovery.models.orm import ItemRow, RelevanceRow
from discovery.models.schemas import ExcludedTopic, RelevanceResult, RetrievalType
from discovery.runs import StageRun
from tests.test_llm_client import FakeAnthropic, a_ok
from tests.test_schemas import RELEVANCE_EXAMPLE

GOLD = PROJECT_ROOT / "eval" / "gold_set.csv"


def _result(**overrides) -> RelevanceResult:
    data = {**RELEVANCE_EXAMPLE, **overrides}
    return RelevanceResult.model_validate(data)


def test_stage_a_rules(cfg):
    stage = StageA.from_keywords(cfg.keywords)
    assert stage.keep("I can't find that photo from the trip", 5)
    assert stage.keep("The album view is broken", 2)
    assert not stage.keep("The album view is broken", 5)
    assert not stage.keep("The album view is broken", None)
    assert not stage.keep("Best gallery I have ever used", 5)


def test_stage_a_recall_on_gold_set(cfg):
    stage = StageA.from_keywords(cfg.keywords)
    rows = read_sheet(GOLD)
    relevant = [
        row
        for row in rows
        if row["retrieval_type"] in ("general_retrieval", "vague_memory_retrieval")
    ]
    kept = set()
    for row in relevant:
        rating_raw = (row.get("rating") or "").strip()
        rating = int(rating_raw) if rating_raw.isdigit() else None
        if stage.keep(row["clean_text"], rating):
            kept.add(row["item_id"])
    metrics = evaluate_gold(rows, stage_a_ids=kept, stage_b_ids=kept, stage_c_labels={})
    assert metrics.stage_a_recall.value is not None
    assert metrics.stage_a_recall.value >= STAGE_A_RECALL_TARGET


def test_seed_exemplars_cover_both_sides_and_stay_off_the_gold_set(cfg):
    positive = cfg.keywords.seed_exemplars.positive
    negative = cfg.keywords.seed_exemplars.negative
    assert len(positive) >= 40
    assert len(negative) >= 40
    gold_text = [row["clean_text"] for row in read_sheet(GOLD)]
    leaked = exemplar_overlap(gold_text, [*positive, *negative])
    assert leaked == []
    prompt = (PROJECT_ROOT / "prompts" / "relevance_v5.md").read_text(encoding="utf-8")
    examples = [
        line.removeprefix("Feedback: ").strip()
        for line in prompt.splitlines()
        if line.startswith("Feedback: ")
    ]
    assert len(examples) >= 6
    assert exemplar_overlap(gold_text, examples) == []


def test_similarity_margin_and_threshold():
    positive = np.array([[1.0, 0.0]])
    negative = np.array([[0.0, 1.0]])
    assert similarity_margin(np.array([1.0, 0.0]), positive, negative) == pytest.approx(1.0)
    assert similarity_margin(np.array([0.0, 1.0]), positive, negative) == pytest.approx(-1.0)
    middle = similarity_margin(np.array([1.0, 1.0]), positive, negative)
    assert middle == pytest.approx(0.0, abs=1e-6)
    assert keep_margin(0.05, 0.05) is False
    assert keep_margin(0.051, 0.05) is True


def test_audit_sample_is_stable_and_about_five_percent():
    rejected = [f"id-{i}" for i in range(100)]
    first = audit_ids(rejected, 0.05, seed=0)
    assert first == audit_ids(rejected, 0.05, seed=0)
    assert len(first) == 5
    assert audit_ids([], 0.05) == set()
    assert audit_ids(rejected, 0) == set()


def test_excluded_topic_is_kept_only_when_it_blocks_retrieval():
    blocked = enforce_scope(
        _result(
            retrieval_type="vague_memory_retrieval",
            is_retrieval=True,
            excluded_topic="deletion",
            excluded_topic_blocks_retrieval=True,
        )
    )
    assert blocked.retrieval_type == RetrievalType.VAGUE_MEMORY_RETRIEVAL
    assert in_scope(blocked)

    priced = enforce_scope(
        _result(
            retrieval_type="general_retrieval",
            is_retrieval=True,
            excluded_topic="pricing",
            excluded_topic_blocks_retrieval=False,
            rationale="Price complaint mixed with search.",
        )
    )
    assert priced.retrieval_type == RetrievalType.NOT_RETRIEVAL
    assert priced.is_retrieval is False
    assert priced.excluded_topic == ExcludedTopic.PRICING
    assert in_scope(priced) is False
    assert "out of scope" in priced.rationale

    unset = enforce_scope(
        _result(
            retrieval_type="not_retrieval",
            is_retrieval=False,
            excluded_topic=None,
            excluded_topic_blocks_retrieval=None,
            vague_memory_relevance=0,
        )
    )
    assert unset.excluded_topic_blocks_retrieval is None


def test_funnel_stores_stage_c_and_rejections(cfg, session_factory, monkeypatch):
    def fake_margins(encoder, texts, positive, negative):
        margins = np.array([0.4 if "trip" in text else -0.4 for text in texts], dtype=np.float32)
        return margins, np.zeros((len(texts), 2), dtype=np.float32)

    monkeypatch.setattr("discovery.ai.funnel.margins_for", fake_margins)
    vague = {
        **RELEVANCE_EXAMPLE,
        "retrieval_type": "vague_memory_retrieval",
        "is_retrieval": True,
        "excluded_topic": None,
        "excluded_topic_blocks_retrieval": None,
    }
    from discovery.ai.llm_client import LLMClient

    client = LLMClient(
        cfg.settings.llm,
        session_factory,
        sdk_client=FakeAnthropic(a_ok(batch_of(vague))),
        clock=lambda: 0.0,
        sleep=lambda _s: None,
    )

    def add(item_id: str, text: str, rating: int) -> None:
        from discovery.db import session_scope

        with session_scope(session_factory) as session:
            session.add(
                ItemRow(
                    item_id=item_id,
                    primary_source_name="play_store",
                    platform="Android",
                    source_url="https://example.test",
                    original_text=text,
                    clean_text=text,
                    language="en",
                    rating=rating,
                    metadata_={"prep": {"ai_eligible": True}},
                )
            )

    add("keep", "I can't find that photo from the trip", 2)
    add("drop-b", "I can't find the button", 2)
    add("drop-a", "Best gallery and editor I have used", 5)

    class Dummy:
        model_name = "fake"

        def embed(self, texts):
            raise AssertionError(texts)

    run = StageRun(run_id="r1", stage="classify")
    report = run_funnel(
        session_factory,
        cfg,
        run,
        client=client,
        encoder=Dummy(),
    )
    assert report.eligible == 3
    assert report.stage_a == 2
    assert report.stage_b == 1
    assert report.classified == 1
    assert report.retrieval_types["vague_memory_retrieval"] == 1
    assert report.retrieval_types["not_retrieval"] == 2

    from discovery.db import session_scope

    with session_scope(session_factory) as session:
        rows = {row.item_id: row for row in session.scalars(select_all(session))}
    assert rows["keep"].stage_reached == "C"
    assert rows["keep"].retrieval_type == "vague_memory_retrieval"
    assert rows["keep"].prompt_version == "relevance_v5"
    assert rows["drop-b"].stage_reached == "B"
    assert rows["drop-a"].stage_reached == "A"
    assert isinstance(WorkItem("x", "play_store", "t", None), WorkItem)


def select_all(session):
    from sqlalchemy import select

    return select(RelevanceRow)


def test_prompt_file_loads(cfg):
    from discovery.ai.prompts import load_prompt
    from discovery.ai.relevance import PROMPT_NAME, PROMPT_VERSION

    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION, cfg.prompts_dir)
    assert prompt.id == "relevance_v5"
    assert "vague_memory_retrieval" in prompt.text
    assert "<<<FEEDBACK n>>>" in prompt.text
    assert Path(cfg.prompts_dir / "relevance_v5.md").exists()


# --- batching ----------------------------------------------------------------


def batch_of(*results: dict, ids: list[int] | None = None) -> dict:
    ids = ids or list(range(1, len(results) + 1))
    return {"results": [{"id": n, "result": r} for n, r in zip(ids, results, strict=True)]}


VAGUE = {
    **RELEVANCE_EXAMPLE,
    "retrieval_type": "vague_memory_retrieval",
    "is_retrieval": True,
    "excluded_topic": None,
    "excluded_topic_blocks_retrieval": None,
}
NOT = {
    **RELEVANCE_EXAMPLE,
    "retrieval_type": "not_retrieval",
    "is_retrieval": False,
    "vague_memory_relevance": 0,
    "excluded_topic": "deletion",
    "excluded_topic_blocks_retrieval": False,
}


def _client(cfg, session_factory, *responses):
    from discovery.ai.llm_client import LLMClient
    from tests.test_rate_limiter import FakeTime

    fake = FakeAnthropic(*responses)
    fake_time = FakeTime()
    client = LLMClient(
        cfg.settings.llm,
        session_factory,
        sdk_client=fake,
        clock=fake_time.clock,
        sleep=fake_time.sleep,
    )
    return client, fake


def test_plan_batches_respects_item_and_char_limits():
    from discovery.ai.relevance import plan_batches

    texts = ["a" * 10] * 5 + ["b" * 100] + ["c" * 10]
    batches = plan_batches(texts, lambda t: t, max_items=3, max_chars=50, max_item_chars=80)
    assert batches == [texts[0:3], texts[3:5], [texts[5]], [texts[6]]]
    assert plan_batches([], lambda t: t, max_items=3, max_chars=50, max_item_chars=80) == []


def test_batch_input_numbers_and_truncates_each_item():
    from discovery.ai.llm_client import TRUNCATION_MARKER
    from discovery.ai.relevance import batch_input

    text = batch_input(["first", "x" * 200], max_item_chars=50)
    assert "<<<FEEDBACK 1>>>\nfirst\n<<<END 1>>>" in text
    assert "<<<FEEDBACK 2>>>" in text and "<<<END 2>>>" in text
    assert TRUNCATION_MARKER in text
    assert "x" * 60 not in text


def test_classify_batch_returns_results_in_input_order_with_scope_rule(cfg, session_factory):
    from discovery.ai.prompts import load_prompt
    from discovery.ai.relevance import classify_batch

    blocked_off = {
        **VAGUE,
        "retrieval_type": "general_retrieval",
        "excluded_topic": "pricing",
        "excluded_topic_blocks_retrieval": False,
    }
    client, fake = _client(
        cfg, session_factory, a_ok(batch_of(NOT, VAGUE, blocked_off, ids=[2, 1, 3]))
    )
    prompt = load_prompt("relevance", 4, cfg.prompts_dir)
    out = classify_batch(
        client, prompt, ["one", "two", "three"], max_item_chars=100, max_output_tokens=999
    )
    assert [c.result.retrieval_type.value for c in out] == [
        "vague_memory_retrieval",
        "not_retrieval",
        "not_retrieval",
    ]
    assert "out of scope" in out[2].result.rationale
    assert fake.calls[0]["max_tokens"] == 999
    assert "<<<FEEDBACK 3>>>" in fake.calls[0]["messages"][0]["content"]


def test_inconsistent_is_retrieval_does_not_fail_the_batch(cfg, session_factory):
    from discovery.ai.prompts import load_prompt
    from discovery.ai.relevance import classify_batch

    sloppy = {**VAGUE, "is_retrieval": False}
    client, fake = _client(cfg, session_factory, a_ok(batch_of(sloppy)))
    prompt = load_prompt("relevance", 4, cfg.prompts_dir)
    out = classify_batch(client, prompt, ["one"], max_item_chars=100)
    assert out[0].result.is_retrieval is True
    assert len(fake.calls) == 1


def test_missing_ids_split_the_batch_until_each_item_has_a_label(cfg, session_factory):
    from discovery.ai.funnel import classify_pending
    from discovery.ai.prompts import load_prompt

    items = [WorkItem(f"id-{n}", "play_store", f"text {n}", None) for n in range(4)]
    client, fake = _client(
        cfg,
        session_factory,
        a_ok(batch_of(VAGUE, VAGUE, VAGUE)),  # 4 items, 3 results: split
        a_ok(batch_of(VAGUE, NOT)),
        a_ok(batch_of(NOT, NOT, ids=[1, 1])),  # duplicate id: split again
        a_ok(batch_of(VAGUE)),
        a_ok(batch_of(NOT)),
    )
    found, errors, stopped = classify_pending(
        client,
        load_prompt("relevance", 4, cfg.prompts_dir),
        items,
        "claude-sonnet-5-5",
        batching=cfg.settings.relevance.model_copy(update={"batch_max_items": 4}),
        max_item_chars=100,
        workers=1,
    )
    assert errors == [] and stopped is None
    labels = {item.item_id: c.result.retrieval_type.value for item, c in found}
    assert labels == {
        "id-0": "vague_memory_retrieval",
        "id-1": "not_retrieval",
        "id-2": "vague_memory_retrieval",
        "id-3": "not_retrieval",
    }
    assert len(fake.calls) == 5


def test_single_item_mismatch_is_an_item_error(cfg, session_factory):
    from discovery.ai.funnel import classify_pending
    from discovery.ai.prompts import load_prompt

    client, _ = _client(cfg, session_factory, a_ok(batch_of(VAGUE, ids=[7])))
    found, errors, stopped = classify_pending(
        client,
        load_prompt("relevance", 4, cfg.prompts_dir),
        [WorkItem("only", "play_store", "text", None)],
        "claude-sonnet-5-5",
        batching=cfg.settings.relevance,
        max_item_chars=100,
        workers=1,
    )
    assert found == [] and stopped is None
    assert len(errors) == 1 and errors[0].startswith("only:")
