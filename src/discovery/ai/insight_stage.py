"""Run insight extraction on every retrieval item and store one `insights` row per item.

In scope: items whose Stage C label is `general_retrieval` or `vague_memory_retrieval`
(the funnel already dropped excluded topics that do not block retrieval). Rows for items
that have left that set are removed, so `insights` always matches the current funnel.
"""

from __future__ import annotations

import csv
import logging
import random
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from discovery.ai.extraction import (
    PROMPT_NAME,
    PROMPT_VERSION,
    CostEstimate,
    Extracted,
    ExtractionItem,
    estimate_cost,
    extract_items,
)
from discovery.ai.llm_client import LLMBatchPending, LLMClient
from discovery.ai.prompts import Prompt, load_prompt
from discovery.config import AppConfig
from discovery.db import session_scope
from discovery.models.orm import InsightRow, ItemRow, RelevanceRow
from discovery.runs import StageRun, save_counts

log = logging.getLogger(__name__)

RETRIEVAL_LABELS = ("vague_memory_retrieval", "general_retrieval")
_WRITE_CHUNK = 400


@dataclass
class InsightSummary:
    """Table-wide view of `insights` after the run."""

    total: int = 0
    grounded: int = 0
    low_confidence: int = 0
    by_category: Counter[str] = field(default_factory=Counter)
    by_source: Counter[str] = field(default_factory=Counter)
    by_model: Counter[str] = field(default_factory=Counter)
    by_breakdown: Counter[str] = field(default_factory=Counter)
    by_content_type: Counter[str] = field(default_factory=Counter)

    @property
    def grounding_rate(self) -> float | None:
        return self.grounded / self.total if self.total else None


@dataclass
class ExtractionReport:
    in_scope: int = 0
    by_retrieval_type: Counter[str] = field(default_factory=Counter)
    pending: int = 0
    reused: int = 0
    extracted: int = 0
    errors: int = 0
    escalated: int = 0
    escalation_failures: int = 0
    quote_retries: int = 0
    first_pass_grounded: int = 0
    grounded: int = 0
    stale_removed: int = 0
    mode: str = "sync"
    batch_id: str | None = None
    batch_requests: int = 0
    stopped_early: str | None = None
    estimate: CostEstimate | None = None
    summary: InsightSummary | None = None

    def as_counts(self) -> dict[str, object]:
        counts: dict[str, object] = {
            "in_scope": self.in_scope,
            "by_retrieval_type": dict(self.by_retrieval_type),
            "pending": self.pending,
            "reused": self.reused,
            "extracted": self.extracted,
            "errors": self.errors,
            "escalated": self.escalated,
            "escalation_failures": self.escalation_failures,
            "quote_retries": self.quote_retries,
            "first_pass_grounded": self.first_pass_grounded,
            "grounded": self.grounded,
            "stale_removed": self.stale_removed,
            "mode": self.mode,
            "batch_id": self.batch_id,
            "batch_requests": self.batch_requests,
            "stopped_early": self.stopped_early,
        }
        if self.summary is not None:
            counts["insights_total"] = self.summary.total
            counts["grounding_rate"] = self.summary.grounding_rate
            counts["low_confidence"] = self.summary.low_confidence
            counts["by_category"] = dict(self.summary.by_category)
        return counts


def load_in_scope(session: Session) -> tuple[list[ExtractionItem], dict[str, str]]:
    rows = session.execute(
        select(
            ItemRow.item_id,
            ItemRow.primary_source_name,
            ItemRow.clean_text,
            RelevanceRow.retrieval_type,
        )
        .join(RelevanceRow, RelevanceRow.item_id == ItemRow.item_id)
        .where(
            RelevanceRow.stage_reached == "C",
            RelevanceRow.retrieval_type.in_(RETRIEVAL_LABELS),
        )
        .order_by(ItemRow.item_id)
    ).all()
    items: list[ExtractionItem] = []
    labels: dict[str, str] = {}
    for item_id, source, text, label in rows:
        if not (text or "").strip():
            continue
        items.append(ExtractionItem(item_id=item_id, source=source, text=text))
        labels[item_id] = label
    return items, labels


def run_extraction(
    factory: sessionmaker[Session],
    cfg: AppConfig,
    run: StageRun,
    *,
    client: LLMClient | None = None,
    client_factory: Callable[[], LLMClient] | None = None,
    limit: int | None = None,
    force: bool = False,
    use_batch_api: bool | None = None,
    batch_id: str | None = None,
    estimate_only: bool = False,
    prompt: Prompt | None = None,
) -> ExtractionReport:
    """Extract every in-scope item that has no row for this prompt and model pair."""
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION, cfg.prompts_dir)
    llm = cfg.settings.llm
    settings = cfg.settings.extraction
    models = (llm.small_model, llm.large_model)

    with session_scope(factory) as session:
        items, labels = load_in_scope(session)
        fresh = set() if force else _fresh(session, prompt.id, models)

    report = ExtractionReport(in_scope=len(items), by_retrieval_type=Counter(labels.values()))
    pending = [it for it in items if it.item_id not in fresh]
    report.reused = len(items) - len(pending)
    if limit is not None:
        pending = pending[:limit]
    report.pending = len(pending)

    if estimate_only:
        report.estimate = estimate_cost(pending, prompt, settings=settings, llm=llm)
        run.counts.update(report.as_counts())
        return report

    report.stale_removed = _delete_stale(factory, {it.item_id for it in items})

    if use_batch_api is None:
        use_batch_api = (
            llm.use_batch_api_for_backfill
            and llm.provider == "anthropic"
            and len(pending) >= settings.batch_api_min_items
        )
    if batch_id is not None:
        use_batch_api = True
    report.mode = "batch_api" if use_batch_api else "sync"

    if pending and client is None and client_factory is not None:
        client = client_factory()

    def on_submit(new_batch_id: str) -> None:
        report.batch_id = new_batch_id
        run.counts["batch_id"] = new_batch_id
        save_counts(factory, run)
        log.info(
            "Message Batch %s submitted. If this command stops, collect it with "
            "`discovery extract --batch-id %s`.",
            new_batch_id,
            new_batch_id,
        )

    try:
        if pending and client is None:
            report.stopped_early = "Extraction was not run (no LLM client)."
        elif pending and client is not None:
            outcome = extract_items(
                client,
                prompt,
                pending,
                settings=settings,
                llm=llm,
                workers=llm.max_concurrency,
                use_batch_api=use_batch_api,
                batch_id=batch_id,
                on_batch_submit=on_submit,
            )
            report.batch_id = outcome.batch_id
            report.batch_requests = outcome.batch_requests
            report.stopped_early = outcome.stopped
            report.errors = len(outcome.errors)
            report.escalated = outcome.escalated
            report.escalation_failures = outcome.escalation_failures
            report.quote_retries = outcome.quote_retries
            for message in outcome.errors:
                run.error(message)
            found = list(outcome.found.values())
            report.extracted = len(found)
            report.first_pass_grounded = sum(ex.first_pass_grounded for _, ex in found)
            report.grounded = sum(ex.quote_grounded for _, ex in found)
            _write_rows(factory, [_insight_row(item.item_id, ex) for item, ex in found])
    except LLMBatchPending as exc:
        report.stopped_early = str(exc)
        report.batch_id = exc.batch_id
    finally:
        if client is not None:
            usage = client.usage
            run.add_llm_usage(usage.total_tokens, usage.cost_usd)
            run.counts["llm_requests"] = usage.requests
            run.counts["llm_cache_hits"] = usage.cache_hits

    with session_scope(factory) as session:
        report.summary = insight_summary(session, settings.review_below_confidence)
    run.counts.update(report.as_counts())
    return report


def _fresh(session: Session, prompt_id: str, models: tuple[str, ...]) -> set[str]:
    return set(
        session.scalars(
            select(InsightRow.item_id).where(
                InsightRow.prompt_version == prompt_id,
                InsightRow.model.in_(models),
            )
        ).all()
    )


def _delete_stale(factory: sessionmaker[Session], keep: set[str]) -> int:
    with session_scope(factory) as session:
        stale = [
            item_id
            for item_id in session.scalars(select(InsightRow.item_id)).all()
            if item_id not in keep
        ]
        for start in range(0, len(stale), _WRITE_CHUNK):
            chunk = stale[start : start + _WRITE_CHUNK]
            session.execute(delete(InsightRow).where(InsightRow.item_id.in_(chunk)))
    return len(stale)


def _insight_row(item_id: str, ex: Extracted) -> InsightRow:
    i = ex.insight
    return InsightRow(
        item_id=item_id,
        trying_to_find=i.trying_to_find,
        content_type=i.content_type.value,
        remembered_cues=[c.model_dump(mode="json") for c in i.remembered_cues],
        forgotten_details=[f.value for f in i.forgotten_details],
        search_attempts=[a.model_dump(mode="json") for a in i.search_attempts],
        breakdown_point=i.breakdown_point.value,
        outcome=i.outcome.value,
        emotion=i.emotion.value,
        frustration_intensity=i.frustration_intensity,
        primary_category=i.primary_category.value,
        secondary_categories=[c.value for c in i.secondary_categories],
        high_stakes=i.high_stakes,
        evidence_quote=i.evidence_quote,
        evidence_strength=i.evidence_strength,
        useful_for_discovery=i.useful_for_discovery,
        problem_statement=i.problem_statement,
        user_reported_issue=i.user_reported_issue,
        extracted_retrieval_problem=i.problem_statement,
        confidence=i.confidence,
        quote_grounded=ex.quote_grounded,
        model=ex.model,
        prompt_version=ex.prompt_version,
    )


def _write_rows(factory: sessionmaker[Session], rows: list[InsightRow]) -> None:
    """Replace the insight rows for these items, in chunks (one round trip per chunk)."""
    if not rows:
        return
    with session_scope(factory) as session:
        for start in range(0, len(rows), _WRITE_CHUNK):
            chunk = rows[start : start + _WRITE_CHUNK]
            session.execute(
                delete(InsightRow).where(InsightRow.item_id.in_([r.item_id for r in chunk]))
            )
            session.add_all(chunk)
            session.flush()


def insight_summary(session: Session, review_below: float) -> InsightSummary:
    rows = session.execute(
        select(
            InsightRow.primary_category,
            InsightRow.quote_grounded,
            InsightRow.confidence,
            InsightRow.model,
            InsightRow.breakdown_point,
            InsightRow.content_type,
            ItemRow.primary_source_name,
        ).join(ItemRow, ItemRow.item_id == InsightRow.item_id)
    ).all()
    summary = InsightSummary(total=len(rows))
    for category, grounded, confidence, model, breakdown, content_type, source in rows:
        summary.grounded += bool(grounded)
        summary.low_confidence += confidence is not None and confidence < review_below
        summary.by_category[category or "none"] += 1
        summary.by_source[source] += 1
        summary.by_model[model or "none"] += 1
        summary.by_breakdown[breakdown or "none"] += 1
        summary.by_content_type[content_type or "none"] += 1
    return summary


def render_extraction_report(report: ExtractionReport) -> str:
    lines = [
        "# Insight extraction",
        "",
        f"In-scope retrieval items: {report.in_scope:,} "
        + _counter_inline(report.by_retrieval_type),
        f"Already extracted with this prompt: {report.reused:,}",
        f"Pending this run: {report.pending:,}",
    ]
    if report.estimate is not None:
        lines.extend(["", "## Cost estimate (no calls made)", "", report.estimate.render(), ""])
        return "\n".join(lines)
    lines.append(f"Mode: {report.mode}")
    if report.batch_id:
        lines.append(f"Message Batch: {report.batch_id} ({report.batch_requests:,} requests)")
    lines.extend(
        [
            f"Extracted this run: {report.extracted:,}",
            f"Item errors: {report.errors:,}",
            f"Escalated to the large model (low confidence): {report.escalated:,}"
            + (f" ({report.escalation_failures} failed)" if report.escalation_failures else ""),
            f"Quote retries: {report.quote_retries:,}",
            f"Quotes grounded on the first pass: {report.first_pass_grounded:,} of "
            f"{report.extracted:,}",
            f"Quotes grounded after the retry: {report.grounded:,} of {report.extracted:,}",
            f"Stale rows removed (item left the retrieval set): {report.stale_removed:,}",
        ]
    )
    if report.stopped_early:
        lines.append(f"Stopped early: {report.stopped_early}")
    summary = report.summary
    if summary is not None and summary.total:
        rate = summary.grounding_rate or 0.0
        lines.extend(
            [
                "",
                "## Insights table",
                "",
                f"Rows: {summary.total:,}",
                f"Quote grounding rate: {rate:.1%} ({summary.grounded:,}/{summary.total:,}; "
                "target ≥ 98%)",
                f"Low-confidence queue: {summary.low_confidence:,}",
                "",
                "### Primary category",
                "",
                *_counter_lines(summary.by_category),
                "",
                "### Breakdown point",
                "",
                *_counter_lines(summary.by_breakdown),
                "",
                "### Content type",
                "",
                *_counter_lines(summary.by_content_type),
                "",
                "### Source",
                "",
                *_counter_lines(summary.by_source),
                "",
                "### Model",
                "",
                *_counter_lines(summary.by_model),
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _counter_inline(counter: Counter[str]) -> str:
    if not counter:
        return ""
    return "(" + ", ".join(f"{k} {v:,}" for k, v in sorted(counter.items())) + ")"


def _counter_lines(counter: Counter[str]) -> list[str]:
    return [f"- {name}: {count:,}" for name, count in counter.most_common()] or ["- none"]


# --- PM review sheets --------------------------------------------------------


def _cell(value: object) -> str:
    """CSV-safe text: a leading = + - @ would run as a formula in Sheets or Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _cues(values: list | None) -> str:
    return "; ".join(f"{v.get('cue')} ({v.get('cue_type')})" for v in values or [])


def _attempts(values: list | None) -> str:
    return "; ".join(f"{v.get('attempt')} ({v.get('attempt_type')})" for v in values or [])


def _review_rows(session: Session) -> list[tuple[InsightRow, ItemRow, str | None]]:
    return list(
        session.execute(
            select(InsightRow, ItemRow, RelevanceRow.retrieval_type)
            .join(ItemRow, ItemRow.item_id == InsightRow.item_id)
            .outerjoin(RelevanceRow, RelevanceRow.item_id == InsightRow.item_id)
            .order_by(InsightRow.item_id)
        ).all()
    )


def _row_fields(insight: InsightRow, item: ItemRow, label: str | None) -> dict[str, str]:
    return {
        "item_id": insight.item_id,
        "source": item.primary_source_name,
        "platform": item.platform,
        "source_url": item.source_url,
        "retrieval_type": label or "",
        "user_text": _cell(item.clean_text),
        "trying_to_find": _cell(insight.trying_to_find),
        "content_type": insight.content_type or "",
        "remembered": _cell(_cues(insight.remembered_cues)),
        "forgotten": "|".join(insight.forgotten_details or []),
        "search_attempts": _cell(_attempts(insight.search_attempts)),
        "breakdown_point": insight.breakdown_point or "",
        "outcome": insight.outcome or "",
        "emotion": insight.emotion or "",
        "frustration_intensity": str(insight.frustration_intensity or ""),
        "primary_category": insight.primary_category or "",
        "secondary_categories": "|".join(insight.secondary_categories or []),
        "high_stakes": str(insight.high_stakes),
        "evidence_quote": _cell(insight.evidence_quote),
        "quote_grounded": str(insight.quote_grounded),
        "evidence_strength": str(insight.evidence_strength or ""),
        "useful_for_discovery": str(insight.useful_for_discovery),
        "user_reported_issue": _cell(insight.user_reported_issue),
        "problem_statement": _cell(insight.problem_statement),
        "confidence": "" if insight.confidence is None else f"{insight.confidence:.2f}",
        "model": insight.model or "",
    }


REVIEW_FIELDS = (
    "item_id",
    "source",
    "platform",
    "source_url",
    "retrieval_type",
    "user_text",
    "trying_to_find",
    "content_type",
    "remembered",
    "forgotten",
    "search_attempts",
    "breakdown_point",
    "outcome",
    "emotion",
    "frustration_intensity",
    "primary_category",
    "secondary_categories",
    "high_stakes",
    "evidence_quote",
    "quote_grounded",
    "evidence_strength",
    "useful_for_discovery",
    "user_reported_issue",
    "problem_statement",
    "confidence",
    "model",
)
SAMPLE_PM_FIELDS = ("remembered_ok", "forgotten_ok", "breakdown_ok", "category_ok", "pm_notes")
QUEUE_FIELDS = ("reason", *REVIEW_FIELDS, "pm_decision", "pm_notes")


def write_review_sample(
    path: Path, factory: sessionmaker[Session], *, size: int = 50, seed: int = 0
) -> int:
    """Gate G3 sheet: `size` insights spread across sources, user text next to every
    extracted field, plus blank columns for the PM's verdicts."""
    with session_scope(factory) as session:
        rows = [_row_fields(*row) for row in _review_rows(session)]
    by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_source[row["source"]].append(row)
    rng = random.Random(seed)
    for bucket in by_source.values():
        rng.shuffle(bucket)
    picked: list[dict[str, str]] = []
    while len(picked) < size and any(by_source.values()):
        for source in sorted(by_source):
            if by_source[source] and len(picked) < size:
                picked.append(by_source[source].pop())
    _write_csv(path, (*REVIEW_FIELDS, *SAMPLE_PM_FIELDS), picked)
    return len(picked)


def write_review_queue(path: Path, factory: sessionmaker[Session], *, below: float) -> int:
    """P4.6: insights with confidence below `below`, plus any whose quote was nulled."""
    queue: list[dict[str, str]] = []
    with session_scope(factory) as session:
        for insight, item, label in _review_rows(session):
            reasons = []
            if insight.confidence is not None and insight.confidence < below:
                reasons.append(f"confidence < {below:g}")
            if not insight.quote_grounded:
                reasons.append("quote not grounded")
            if reasons:
                queue.append({"reason": "; ".join(reasons), **_row_fields(insight, item, label)})
    queue.sort(key=lambda r: (r["confidence"] or "0", r["item_id"]))
    _write_csv(path, QUEUE_FIELDS, queue)
    return len(queue)


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fields})
