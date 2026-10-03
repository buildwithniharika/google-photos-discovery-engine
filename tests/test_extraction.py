"""Insight extraction: quote grounding, batching, escalation, Batch API, stage, and eval."""

from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from discovery.ai.extraction import (
    PROMPT_VERSION,
    UNGROUNDED_STRENGTH_CAP,
    Extracted,
    ExtractionItem,
    estimate_cost,
    extract_items,
    extraction_input,
    ground_quote,
    plan_jobs,
    tidy,
)
from discovery.ai.insight_stage import (
    render_extraction_report,
    run_extraction,
    write_review_queue,
    write_review_sample,
)
from discovery.ai.llm_client import LLMBatchPending, LLMBudgetExceeded, LLMClient
from discovery.ai.prompts import load_prompt
from discovery.config import PROJECT_ROOT
from discovery.db import session_scope
from discovery.eval.extraction_eval import evaluate_extraction
from discovery.eval.gold_set import exemplar_overlap, read_sheet
from discovery.models.orm import InsightRow, ItemRow, RelevanceRow
from discovery.models.schemas import Category, Insight
from discovery.runs import StageRun
from tests.test_llm_client import FakeAnthropic, a_ok
from tests.test_rate_limiter import FakeTime
from tests.test_schemas import INSIGHT_EXAMPLE

GOLD = PROJECT_ROOT / "eval" / "gold_set.csv"
CAFE = "I searched Goa café and it showed me every beach photo but not the café"
CAFE_TEXT = f"I know the photo exists. {CAFE}. So annoying."


def insight(**overrides) -> dict:
    return {**INSIGHT_EXAMPLE, **overrides}


def batch_of(*insights: dict, ids: list[int] | None = None) -> dict:
    ids = ids or list(range(1, len(insights) + 1))
    return {"results": [{"id": n, "insight": i} for n, i in zip(ids, insights, strict=True)]}


def _client(cfg, session_factory, fake, **overrides) -> LLMClient:
    fake_time = FakeTime()
    spent = overrides.pop("spent_before_usd", 0.0)
    return LLMClient(
        cfg.settings.llm.model_copy(update=overrides),
        session_factory,
        sdk_client=fake,
        spent_before_usd=spent,
        clock=fake_time.clock,
        sleep=fake_time.sleep,
    )


@pytest.fixture
def prompt(cfg):
    from discovery.ai.extraction import PROMPT_NAME, PROMPT_VERSION

    return load_prompt(PROMPT_NAME, PROMPT_VERSION, cfg.prompts_dir)


# --- quote grounding -----------------------------------------------------------


def test_exact_quote_is_grounded_as_is():
    assert ground_quote(CAFE, CAFE_TEXT) == CAFE


def test_case_whitespace_and_curly_quotes_snap_to_the_source_span():
    text = "Ugh.  I can't   find the\nphoto of “Mum's” garden anywhere!"
    quote = 'i can’t find the photo of "mum\'s" garden'
    assert ground_quote(quote, text) == "I can't   find the\nphoto of “Mum's” garden"


def test_quote_with_redaction_marker_is_grounded_against_clean_text():
    text = "Called support at [PHONE] and they still can't find my wedding album."
    assert ground_quote("Called support at [PHONE] and they still can't find", text)


def test_small_typo_fix_passes_fuzzy_but_rewrites_fail():
    text = "i cant find the screenshot of my train ticket from last month anywhere"
    fixed = "i can't find the screenshot of my train ticket from last month anywhere"
    assert ground_quote(fixed, text) is not None
    assert ground_quote(fixed, text).startswith("i cant")
    joined = "i cant find the screenshot ... anywhere"
    assert ground_quote(joined, text) is None
    assert ground_quote("The user cannot locate a train ticket screenshot", text) is None


def test_short_or_empty_or_overlong_quotes():
    assert ground_quote("cant find", "i cant find it") == "cant find"
    assert ground_quote("can't find", "i cant find it") is None  # short: exact only
    assert ground_quote(None, "text") is None
    assert ground_quote("  ", "text") is None
    assert ground_quote("much longer than the text itself", "short") is None


def test_every_gold_text_grounds_a_span_of_itself():
    for row in read_sheet(GOLD)[:60]:
        text = row["clean_text"]
        span = text[: min(len(text), 80)].strip()
        if span:
            assert ground_quote(span, text) == span


# --- planning and tidying --------------------------------------------------------


def test_long_text_goes_to_the_large_model_alone(cfg):
    settings = cfg.settings.extraction
    items = [ExtractionItem(f"s{n}", "play_store", "short text") for n in range(8)]
    items.append(ExtractionItem("long", "google_community", "x" * (settings.long_text_chars + 1)))
    jobs = plan_jobs(items, settings, small_model="small", large_model="large", max_item_chars=6000)
    assert [len(j.items) for j in jobs if j.model == "small"] == [6, 2]
    assert [(j.items[0].item_id, j.model) for j in jobs if j.model == "large"] == [
        ("long", "large")
    ]


def test_tidy_drops_primary_from_secondary_and_dedupes():
    raw = Insight.model_validate(
        insight(
            primary_category="location_ambiguity",
            secondary_categories=[
                "location_ambiguity",
                "time_based_memory_gap",
                "time_based_memory_gap",
                "life_event_retrieval",
                "search_trust_breakdown",
            ],
            forgotten_details=["exact_date", "exact_date"],
        )
    )
    clean = tidy(raw)
    assert clean.secondary_categories == [
        Category.TIME_BASED_MEMORY_GAP,
        Category.LIFE_EVENT_RETRIEVAL,
    ]
    assert [f.value for f in clean.forgotten_details] == ["exact_date"]


def test_extraction_input_wraps_items_and_adds_note():
    text = extraction_input(["one", "two"], max_item_chars=100, note="NOTE")
    assert "NOTE" in text
    assert "<<<FEEDBACK 2>>>\ntwo\n<<<END 2>>>" in text


# --- normal-call flow ------------------------------------------------------------


def test_ungrounded_quote_is_retried_once_then_nulled(cfg, session_factory, prompt):
    items = [
        ExtractionItem("a", "play_store", CAFE_TEXT),
        ExtractionItem("b", "play_store", "Where are my dog's photos from 2019? Search is empty."),
        ExtractionItem("c", "play_store", "Lost all the pics from our Kerala trip, help."),
    ]
    fake = FakeAnthropic(
        a_ok(
            batch_of(
                insight(),
                insight(evidence_quote="The user wants dog photos", evidence_strength=4),
                insight(evidence_quote="Kerala pics vanished", evidence_strength=5),
            )
        ),
        a_ok(batch_of(insight(evidence_quote="Search is empty."))),
        a_ok(batch_of(insight(evidence_quote="still not a span"))),
    )
    client = _client(cfg, session_factory, fake)
    run = extract_items(
        client, prompt, items, settings=cfg.settings.extraction, llm=cfg.settings.llm, workers=1
    )
    assert run.errors == [] and run.stopped is None
    a, b, c = (run.found[k][1] for k in "abc")
    assert a.quote_grounded and a.first_pass_grounded and not a.quote_retried
    assert b.quote_grounded and b.quote_retried and not b.first_pass_grounded
    assert b.insight.evidence_quote == "Search is empty."
    assert not c.quote_grounded and c.quote_retried
    assert c.insight.evidence_quote is None
    assert c.insight.evidence_strength == UNGROUNDED_STRENGTH_CAP
    assert run.quote_retries == 2
    retry_inputs = [call["messages"][0]["content"] for call in fake.calls[1:]]
    assert all("not an exact span" in text for text in retry_inputs)


def test_hard_stop_leaves_unfinished_items_for_the_next_run(cfg, session_factory, prompt):
    import anthropic

    from tests.test_llm_client import a_error

    items = [
        ExtractionItem("a", "play_store", CAFE_TEXT),
        ExtractionItem("b", "play_store", "Where are my dog's photos from 2019?"),
    ]
    fake = FakeAnthropic(
        a_ok(batch_of(insight(), insight(evidence_quote="invented"))),
        a_error(anthropic.RateLimitError, 429),  # spend limit: no retry-after
    )
    client = _client(cfg, session_factory, fake)
    run = extract_items(
        client, prompt, items, settings=cfg.settings.extraction, llm=cfg.settings.llm, workers=1
    )
    assert set(run.found) == {"a"}
    assert run.stopped and "spend limit" in run.stopped


def test_low_confidence_is_escalated_to_the_large_model(cfg, session_factory, prompt):
    items = [
        ExtractionItem("sure", "play_store", CAFE_TEXT),
        ExtractionItem("unsure", "play_store", CAFE_TEXT + " Maybe."),
    ]
    fake = FakeAnthropic(
        a_ok(batch_of(insight(), insight(confidence=0.3))),
        a_ok(batch_of(insight(confidence=0.8, primary_category="search_trust_breakdown"))),
    )
    client = _client(cfg, session_factory, fake)
    run = extract_items(
        client, prompt, items, settings=cfg.settings.extraction, llm=cfg.settings.llm, workers=1
    )
    unsure = run.found["unsure"][1]
    assert unsure.escalated and unsure.model == cfg.settings.llm.large_model
    assert unsure.insight.primary_category == Category.SEARCH_TRUST_BREAKDOWN
    assert run.found["sure"][1].model == cfg.settings.llm.small_model
    assert fake.calls[1]["model"] == cfg.settings.llm.large_model
    assert run.escalated == 1


def test_wrong_ids_split_the_batch(cfg, session_factory, prompt):
    items = [ExtractionItem(f"i{n}", "play_store", CAFE_TEXT + str(n)) for n in range(2)]
    fake = FakeAnthropic(
        a_ok(batch_of(insight())),  # 2 items, 1 result: split
        a_ok(batch_of(insight())),
        a_ok(batch_of(insight())),
    )
    client = _client(cfg, session_factory, fake)
    run = extract_items(
        client, prompt, items, settings=cfg.settings.extraction, llm=cfg.settings.llm, workers=1
    )
    assert set(run.found) == {"i0", "i1"} and run.errors == []
    assert len(fake.calls) == 3


# --- Message Batches API ---------------------------------------------------------


class FakeBatches(FakeAnthropic):
    """FakeAnthropic plus messages.batches. `responder(params)` returns a message body
    (dict), or a string naming a failed result type ("errored")."""

    def __init__(self, responder, *responses, polls_before_end: int = 1):
        super().__init__(*responses)
        self.responder = responder
        self.polls_before_end = polls_before_end
        self.submitted: list[dict] = []
        self.retrieves = 0
        self.messages.batches = SimpleNamespace(
            create=self._batch_create, retrieve=self._retrieve, results=self._results
        )

    def _batch_create(self, *, requests):
        self.submitted = list(requests)
        return SimpleNamespace(id="msgbatch_1", processing_status="in_progress")

    def _retrieve(self, batch_id):
        self.retrieves += 1
        status = "ended" if self.retrieves > self.polls_before_end else "in_progress"
        return SimpleNamespace(
            id=batch_id,
            processing_status=status,
            request_counts=SimpleNamespace(processing=1, succeeded=0),
        )

    def _results(self, batch_id):
        for request in self.submitted:
            body = self.responder(request["params"])
            if isinstance(body, str):
                result = SimpleNamespace(type=body, error="boom")
            else:
                result = SimpleNamespace(type="succeeded", message=a_ok(body).parse())
            yield SimpleNamespace(custom_id=request["custom_id"], result=result)


def _count_items(params) -> int:
    return params["messages"][0]["content"].count("<<<END ")


def test_batch_api_first_pass_is_cached_and_half_price(cfg, session_factory, prompt):
    items = [ExtractionItem(f"i{n}", "play_store", CAFE_TEXT) for n in range(3)]
    fake = FakeBatches(lambda p: batch_of(*[insight()] * _count_items(p)))
    client = _client(cfg, session_factory, fake)
    submitted: list[str] = []
    run = extract_items(
        client,
        prompt,
        items,
        settings=cfg.settings.extraction,
        llm=cfg.settings.llm,
        use_batch_api=True,
        on_batch_submit=submitted.append,
    )
    assert submitted == ["msgbatch_1"] and run.batch_id == "msgbatch_1"
    assert set(run.found) == {"i0", "i1", "i2"}
    assert fake.calls == []  # no normal calls
    assert fake.retrieves == 2
    full_price = (300 * 2.0 + 80 * 10.0) / 1e6
    assert client.usage.cost_usd == pytest.approx(full_price * 0.5)

    again = _client(cfg, session_factory, FakeBatches(lambda p: "errored"))
    second = extract_items(
        again,
        prompt,
        items,
        settings=cfg.settings.extraction,
        llm=cfg.settings.llm,
        use_batch_api=True,
    )
    assert set(second.found) == {"i0", "i1", "i2"} and second.batch_id is None
    assert again.usage.cache_hits == 1 and again.usage.cost_usd == 0


def test_batch_api_escalation_is_a_second_batch(cfg, session_factory, prompt):
    items = [ExtractionItem("unsure", "play_store", CAFE_TEXT)]
    large = cfg.settings.llm.large_model

    def respond(params):
        sure = params["model"] == large
        return batch_of(insight(confidence=0.9 if sure else 0.3))

    fake = FakeBatches(respond)
    client = _client(cfg, session_factory, fake)
    run = extract_items(
        client,
        prompt,
        items,
        settings=cfg.settings.extraction,
        llm=cfg.settings.llm,
        use_batch_api=True,
    )
    _, ex = run.found["unsure"]
    assert ex.escalated and ex.model == large
    assert run.batch_requests == 2 and fake.calls == []


def test_failed_batch_results_fall_back_to_normal_calls(cfg, session_factory, prompt):
    items = [ExtractionItem("only", "play_store", CAFE_TEXT)]
    fake = FakeBatches(lambda p: "errored", a_ok(batch_of(insight())))
    client = _client(cfg, session_factory, fake)
    run = extract_items(
        client,
        prompt,
        items,
        settings=cfg.settings.extraction,
        llm=cfg.settings.llm,
        use_batch_api=True,
    )
    assert "only" in run.found and len(fake.calls) == 1


def test_batch_submission_is_trimmed_to_the_budget(cfg, session_factory, prompt):
    budget = cfg.settings.llm.project_budget_usd
    client = _client(
        cfg, session_factory, FakeBatches(lambda p: {}), spent_before_usd=budget - 0.001
    )
    calls = [
        client.prepare(
            prompt=prompt, user_input=f"item {n}", response_model=Insight, max_output_tokens=500
        )
        for n in range(3)
    ]
    with pytest.raises(LLMBudgetExceeded):
        client.submit_batch(calls)

    roomy = _client(cfg, session_factory, FakeBatches(lambda p: {}), spent_before_usd=0.0)
    calls = [
        roomy.prepare(
            prompt=prompt, user_input=f"item {n}", response_model=Insight, max_output_tokens=500
        )
        for n in range(3)
    ]
    one = calls[0].max_cost_usd * cfg.settings.llm.batch_price_factor
    tight = _client(
        cfg,
        session_factory,
        FakeBatches(lambda p: {}),
        spent_before_usd=budget - one * 2.5,
    )
    batch_id, submitted = tight.submit_batch(calls)
    assert batch_id == "msgbatch_1" and len(submitted) == 2


def test_batch_that_does_not_finish_raises_pending(cfg, session_factory, prompt):
    items = [ExtractionItem("only", "play_store", CAFE_TEXT)]
    fake = FakeBatches(lambda p: batch_of(insight()), polls_before_end=10_000)
    client = _client(cfg, session_factory, fake)
    settings = cfg.settings.extraction.model_copy(
        update={"batch_poll_seconds": 60, "batch_max_wait_minutes": 5}
    )
    with pytest.raises(LLMBatchPending) as info:
        extract_items(
            client, prompt, items, settings=settings, llm=cfg.settings.llm, use_batch_api=True
        )
    assert info.value.batch_id == "msgbatch_1"
    assert "--batch-id msgbatch_1" in str(info.value)


# --- stage -----------------------------------------------------------------------


def _add_item(factory, item_id: str, text: str, label: str, source: str = "play_store") -> None:
    with session_scope(factory) as session:
        session.add(
            ItemRow(
                item_id=item_id,
                primary_source_name=source,
                platform="Android",
                source_url=f"https://example.test/{item_id}",
                original_text=text,
                clean_text=text,
                language="en",
                metadata_={"prep": {"ai_eligible": True}},
            )
        )
        session.add(
            RelevanceRow(
                item_id=item_id,
                stage_reached="C",
                is_retrieval=label != "not_retrieval",
                retrieval_type=label,
                confidence=0.9,
            )
        )


def test_stage_writes_rows_reuses_them_and_removes_stale(cfg, session_factory, tmp_path):
    _add_item(session_factory, "v1", CAFE_TEXT, "vague_memory_retrieval")
    _add_item(session_factory, "g1", "=cmd " + CAFE_TEXT, "general_retrieval", "app_store")
    _add_item(session_factory, "n1", "Price is too high", "not_retrieval")
    # Items load in id order: g1, then v1.
    fake = FakeAnthropic(
        a_ok(batch_of(insight(confidence=0.45), insight())),
        a_ok(batch_of(insight(confidence=0.4))),
    )
    client = _client(cfg, session_factory, fake)
    run = StageRun(run_id="r1", stage="extract")
    report = run_extraction(session_factory, cfg, run, client=client)
    assert report.in_scope == 2 and report.extracted == 2 and report.mode == "sync"
    assert report.summary.total == 2 and report.summary.grounding_rate == 1.0
    assert run.counts["grounding_rate"] == 1.0

    with session_scope(session_factory) as session:
        rows = {r.item_id: r for r in session.scalars(select(InsightRow))}
        assert set(rows) == {"v1", "g1"}
        assert rows["v1"].extracted_retrieval_problem == rows["v1"].problem_statement
        assert rows["v1"].user_reported_issue
        assert rows["v1"].prompt_version == f"extraction_v{PROMPT_VERSION}"
        assert rows["v1"].remembered_cues[0] == {"cue": "Goa trip", "cue_type": "trip_or_event"}
        assert rows["g1"].model == cfg.settings.llm.large_model  # escalated (0.45 < 0.6)

    queue_path = tmp_path / "queue.csv"
    assert write_review_queue(queue_path, session_factory, below=0.5) == 1
    queued = list(csv.DictReader(queue_path.open()))
    assert queued[0]["item_id"] == "g1" and "confidence < 0.5" in queued[0]["reason"]
    assert queued[0]["user_text"].startswith("'=cmd")  # formula injection guard

    sample_path = tmp_path / "sample.csv"
    assert write_review_sample(sample_path, session_factory, size=50) == 2
    header = next(csv.reader(sample_path.open()))
    assert {"user_text", "remembered", "forgotten", "category_ok"} <= set(header)

    again = run_extraction(
        session_factory,
        cfg,
        StageRun("r2", "extract"),
        client=_client(cfg, session_factory, FakeAnthropic()),
    )
    assert again.reused == 2 and again.pending == 0 and again.extracted == 0

    with session_scope(session_factory) as session:
        session.get(RelevanceRow, "g1").retrieval_type = "not_retrieval"
    third = run_extraction(session_factory, cfg, StageRun("r3", "extract"))
    assert third.stale_removed == 1 and third.summary.total == 1
    assert "Quote grounding rate" in render_extraction_report(third)


def test_stage_estimate_makes_no_calls(cfg, session_factory):
    _add_item(session_factory, "v1", CAFE_TEXT, "vague_memory_retrieval")
    run = StageRun(run_id="e", stage="extract")
    report = run_extraction(session_factory, cfg, run, estimate_only=True)
    assert report.estimate is not None and report.estimate.items == 1
    assert report.estimate.batch_usd == pytest.approx(report.estimate.sync_usd * 0.5)
    assert "Cost estimate" in render_extraction_report(report)


def test_cost_estimate_scales_with_items(cfg, prompt):
    one = estimate_cost(
        [ExtractionItem("a", "s", CAFE_TEXT)],
        prompt,
        settings=cfg.settings.extraction,
        llm=cfg.settings.llm,
    )
    many = estimate_cost(
        [ExtractionItem(str(n), "s", CAFE_TEXT) for n in range(60)],
        prompt,
        settings=cfg.settings.extraction,
        llm=cfg.settings.llm,
    )
    assert 0 < one.sync_usd < many.sync_usd
    assert many.calls >= 10


# --- evaluation ------------------------------------------------------------------


def _extracted(**overrides) -> Extracted:
    grounded = overrides.pop("grounded", True)
    return Extracted(
        insight=Insight.model_validate(insight(**overrides)),
        model="m",
        prompt_version="extraction_v1",
        cached=False,
        quote_grounded=grounded,
        first_pass_grounded=grounded,
    )


def test_evaluate_extraction_scores_category_not_stated_and_grounding():
    rows = [
        {
            "item_id": "1",
            "retrieval_type": "vague_memory_retrieval",
            "primary_category": "location_ambiguity",
            "content_type": "photo",
            "remembered_cue_types": "",
            "forgotten_details": "",
            "breakdown_point": "results_irrelevant_or_too_broad",
            "evidence_strength": "4",
        },
        {
            "item_id": "2",
            "retrieval_type": "general_retrieval",
            "primary_category": "search_trust_breakdown",
            "content_type": "screenshot",
            "remembered_cue_types": "trip_or_event|album_name",
            "forgotten_details": "exact_date",
            "breakdown_point": "no_results",
            "evidence_strength": "1",
        },
        {"item_id": "3", "retrieval_type": "not_retrieval"},
        {"item_id": "4", "retrieval_type": "vague_memory_retrieval"},
    ]
    predictions = {
        # top-2 hit via the first secondary category; invented cues and forgotten details
        "1": _extracted(),
        "2": _extracted(
            primary_category="search_trust_breakdown",
            secondary_categories=[],
            remembered_cues=[{"cue": "Goa trip", "cue_type": "trip_or_event"}],
            grounded=False,
        ),
    }
    m = evaluate_extraction(rows, predictions)
    assert m.scored == 3 and m.extracted == 2 and m.missing == 1
    assert (m.category_top1.hits, m.category_top1.total) == (1, 2)
    assert (m.category_top2.hits, m.category_top2.total) == (2, 2)
    assert (m.not_stated.hits, m.not_stated.total) == (0, 2)
    assert (m.grounding.hits, m.grounding.total) == (1, 2)
    assert (m.content_type.hits, m.content_type.total) == (1, 2)
    assert (m.cue_recall.hits, m.cue_recall.total) == (1, 1)  # album_name ignored
    assert (m.strength_within_one.hits, m.strength_within_one.total) == (1, 2)
    assert m.confusions[("location_ambiguity", "context_based_retrieval_failure")] == 1
    gaps = " ".join(m.misses_targets())
    assert "Quote grounding" in gaps and "not_stated" in gaps and "Incomplete" in gaps


# --- prompt ----------------------------------------------------------------------


def test_prompt_lists_every_enum_and_keeps_examples_off_the_gold_set(cfg, prompt):
    assert prompt.id == f"extraction_v{PROMPT_VERSION}"
    tax = cfg.taxonomy
    values = [
        *tax.categories,
        *tax.content_types,
        *tax.cue_types,
        *tax.forgotten_details,
        *tax.breakdown_points,
        *tax.attempt_types,
        *tax.emotions,
        *tax.outcomes,
    ]
    missing = [v for v in values if f"`{v}`" not in prompt.text]
    assert missing == []
    assert "<<<FEEDBACK n>>>" in prompt.text
    examples = [
        line.removeprefix("Feedback: ").strip()
        for line in prompt.text.splitlines()
        if line.startswith("Feedback: ")
    ]
    assert len(examples) >= 8
    gold_text = [row["clean_text"] for row in read_sheet(GOLD)]
    assert exemplar_overlap(gold_text, examples) == []
    json.loads(prompt.text.rsplit("\n", 1)[-1])  # the output-shape example is valid JSON
