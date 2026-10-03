"""Run Stages A, B, and C and store one `relevance` row per eligible item."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from discovery.ai.embeddings import Encoder, SentenceEncoder, pack_vector
from discovery.ai.llm_client import (
    LLMBudgetExceeded,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMRateLimitExhausted,
)
from discovery.ai.prefilter import StageA
from discovery.ai.prompts import Prompt, load_prompt
from discovery.ai.relevance import (
    PROMPT_NAME,
    PROMPT_VERSION,
    BatchMismatchError,
    Classified,
    classify_batch,
    plan_batches,
    topic_name,
)
from discovery.ai.semantic_filter import audit_ids, keep_margin, margins_for
from discovery.config import AppConfig, RelevanceSettings
from discovery.db import session_scope
from discovery.models.orm import EmbeddingRow, ItemRow, RelevanceRow
from discovery.models.schemas import RelevanceResult, RetrievalType
from discovery.runs import StageRun

log = logging.getLogger(__name__)

_WRITE_CHUNK = 400
AUDIT_SEED = 0
_RETRIEVAL_LABELS = ("vague_memory_retrieval", "general_retrieval", "not_retrieval")


@dataclass(frozen=True)
class WorkItem:
    item_id: str
    source: str
    text: str
    rating: int | None


@dataclass
class FunnelReport:
    eligible: int = 0
    stage_a: int = 0
    stage_b: int = 0
    stage_b_rejected: int = 0
    audit: int = 0
    classified: int = 0
    reused: int = 0
    llm_errors: int = 0
    stopped_early: str | None = None
    by_source: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    retrieval_types: Counter[str] = field(default_factory=Counter)
    audit_retrieval: int = 0

    def as_counts(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "stage_a": self.stage_a,
            "stage_b": self.stage_b,
            "stage_b_rejected": self.stage_b_rejected,
            "audit": self.audit,
            "classified": self.classified,
            "reused": self.reused,
            "llm_errors": self.llm_errors,
            "stopped_early": self.stopped_early,
            "retrieval_types": dict(self.retrieval_types),
            "audit_retrieval": self.audit_retrieval,
            "by_source": {src: dict(counts) for src, counts in sorted(self.by_source.items())},
        }


def render_funnel_report(report: FunnelReport, *, tau: float) -> str:
    lines = [
        "# Relevance funnel",
        "",
        f"Eligible items: {report.eligible:,}",
        f"Stage A kept: {report.stage_a:,}",
        f"Stage B kept (margin > {tau:.3f}): {report.stage_b:,}",
        f"Stage B rejected: {report.stage_b_rejected:,}",
        f"Stage B audit sent to the classifier: {report.audit:,}",
        f"Stage C new classifications: {report.classified:,}",
        f"Stage C reused from an earlier run: {report.reused:,}",
        f"Stage C item errors: {report.llm_errors:,}",
    ]
    if report.stopped_early:
        lines.append(f"Stopped early: {report.stopped_early}")
    if report.audit:
        lines.append(
            f"Audit items judged retrieval: {report.audit_retrieval:,} of {report.audit:,}"
        )
    lines.extend(["", "## Retrieval type", ""])
    if report.retrieval_types:
        for name, count in sorted(report.retrieval_types.items()):
            lines.append(f"- {name}: {count:,}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## By source",
            "",
            "| Source | Eligible | Stage A | Stage B | Vague memory | General | Not retrieval |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for source, counts in sorted(report.by_source.items()):
        lines.append(
            "| "
            + " | ".join(
                [
                    source,
                    f"{counts['eligible']:,}",
                    f"{counts['stage_a']:,}",
                    f"{counts['stage_b']:,}",
                    f"{counts['vague_memory_retrieval']:,}",
                    f"{counts['general_retrieval']:,}",
                    f"{counts['not_retrieval']:,}",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def load_work_items(session: Session, *, limit: int | None = None) -> list[WorkItem]:
    """Items prep marked ready for the AI stages."""
    rows = session.scalars(select(ItemRow).order_by(ItemRow.item_id)).all()
    items: list[WorkItem] = []
    for row in rows:
        if not _eligible(row):
            continue
        items.append(
            WorkItem(
                item_id=row.item_id,
                source=row.primary_source_name,
                text=row.clean_text or "",
                rating=row.rating,
            )
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def run_funnel(
    factory: sessionmaker[Session],
    cfg: AppConfig,
    run: StageRun,
    *,
    client: LLMClient | None = None,
    client_factory: Callable[[], LLMClient] | None = None,
    encoder: Encoder | None = None,
    limit: int | None = None,
    force: bool = False,
    prompt: Prompt | None = None,
    model: str | None = None,
) -> FunnelReport:
    """Classify every eligible item. Pass `client=None` to stop after Stage B."""
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION, cfg.prompts_dir)
    model = model or cfg.settings.llm.small_model
    keywords = cfg.keywords
    if not keywords.seed_exemplars.positive or not keywords.seed_exemplars.negative:
        raise ValueError("config/keywords.yaml seed_exemplars.positive and .negative must be set")
    tau = cfg.settings.relevance.semantic_margin_threshold
    stage_a = StageA.from_keywords(keywords)

    with session_scope(factory) as session:
        items = load_work_items(session, limit=limit)
        fresh = {} if force else _fresh_stage_c(session, prompt.id, model)

    report = FunnelReport(eligible=len(items))
    if not items:
        run.counts.update(report.as_counts())
        return report

    passed_a = [it for it in items if stage_a.keep(it.text, it.rating)]
    passed_ids = {it.item_id for it in passed_a}
    report.stage_a = len(passed_a)
    rejected_a = [it for it in items if it.item_id not in passed_ids]

    encoder = encoder or SentenceEncoder(cfg.settings.clustering.embedding_model)
    margin_by_id: dict[str, float] = {}
    if passed_a:
        margins, vectors = margins_for(
            encoder,
            [it.text for it in passed_a],
            keywords.seed_exemplars.positive,
            keywords.seed_exemplars.negative,
        )
        margin_by_id = {
            it.item_id: float(value) for it, value in zip(passed_a, margins, strict=True)
        }
        _store_vectors(factory, encoder.model_name, passed_a, vectors)

    passed_b = [it for it in passed_a if keep_margin(margin_by_id[it.item_id], tau)]
    passed_b_ids = {it.item_id for it in passed_b}
    rejected_b = [it for it in passed_a if it.item_id not in passed_b_ids]
    report.stage_b = len(passed_b)
    report.stage_b_rejected = len(rejected_b)
    audit = audit_ids(
        [it.item_id for it in rejected_b],
        cfg.settings.relevance.audit_sample_rate,
        seed=AUDIT_SEED,
    )
    report.audit = len(audit)
    stage_c_items = passed_b + [it for it in rejected_b if it.item_id in audit]

    heuristic: list[RelevanceRow] = []
    for item in rejected_a:
        heuristic.append(_heuristic_row(item, "A", "Stage A: no retrieval signal."))
        _tally(report, item, "not_retrieval", stage_a=False, stage_b=False)
    for item in rejected_b:
        if item.item_id in audit:
            continue
        margin = margin_by_id[item.item_id]
        heuristic.append(
            _heuristic_row(
                item,
                "B",
                f"Stage B: similarity margin {margin:.3f} is not above {tau:.3f}.",
            )
        )
        _tally(report, item, "not_retrieval", stage_a=True, stage_b=False)
    _write_rows(factory, heuristic)

    pending = [it for it in stage_c_items if it.item_id not in fresh]
    reused = [it for it in stage_c_items if it.item_id in fresh]
    report.reused = len(reused)
    for item in reused:
        label = fresh[item.item_id]
        _tally(report, item, label, stage_a=True, stage_b=item.item_id in passed_b_ids)
        if item.item_id in audit and label in ("general_retrieval", "vague_memory_retrieval"):
            report.audit_retrieval += 1

    if pending and client is None and client_factory is not None:
        client = client_factory()
    try:
        if pending and client is None:
            report.stopped_early = "Stage C was not run (no LLM client)."
        elif pending and client is not None:
            classified, errors, stopped = classify_pending(
                client,
                prompt,
                pending,
                model,
                batching=cfg.settings.relevance,
                max_item_chars=cfg.settings.llm.max_input_chars,
                workers=cfg.settings.llm.max_concurrency,
            )
            report.classified = len(classified)
            report.llm_errors = len(errors)
            report.stopped_early = stopped
            for message in errors:
                run.error(message)
            llm_rows = []
            for item, outcome in classified:
                llm_rows.append(_llm_row(item, outcome, audit=item.item_id in audit))
                label = outcome.result.retrieval_type.value
                _tally(report, item, label, stage_a=True, stage_b=item.item_id in passed_b_ids)
                if item.item_id in audit and outcome.result.is_retrieval:
                    report.audit_retrieval += 1
            _write_rows(factory, llm_rows)
    finally:
        # Spend counts toward llm.project_budget_usd even when the stage fails.
        if client is not None:
            usage = client.usage
            run.add_llm_usage(usage.total_tokens, usage.cost_usd)
            run.counts["llm_requests"] = usage.requests
            run.counts["llm_cache_hits"] = usage.cache_hits

    run.counts.update(report.as_counts())
    return report


def _eligible(row: ItemRow) -> bool:
    if row.is_spam or not (row.clean_text or "").strip():
        return False
    meta = row.metadata_ if isinstance(row.metadata_, dict) else {}
    prep = meta.get("prep") if isinstance(meta.get("prep"), dict) else {}
    return bool(prep.get("ai_eligible"))


def _fresh_stage_c(session: Session, prompt_id: str, model: str) -> dict[str, str]:
    rows = session.scalars(
        select(RelevanceRow).where(
            RelevanceRow.stage_reached == "C",
            RelevanceRow.prompt_version == prompt_id,
            RelevanceRow.model == model,
        )
    ).all()
    return {row.item_id: row.retrieval_type or RetrievalType.NOT_RETRIEVAL.value for row in rows}


def _heuristic_row(item: WorkItem, stage: str, rationale: str) -> RelevanceRow:
    return RelevanceRow(
        item_id=item.item_id,
        stage_reached=stage,
        is_google_photos=None,
        is_retrieval=False,
        retrieval_type=RetrievalType.NOT_RETRIEVAL.value,
        vague_memory_relevance=None,
        excluded_topic=None,
        excluded_topic_blocks_retrieval=None,
        rationale=rationale,
        confidence=None,
        model=None,
        prompt_version=None,
    )


def _llm_row(item: WorkItem, outcome: Classified, *, audit: bool) -> RelevanceRow:
    result: RelevanceResult = outcome.result
    rationale = f"Stage B audit sample. {result.rationale}" if audit else result.rationale
    return RelevanceRow(
        item_id=item.item_id,
        stage_reached="C",
        is_google_photos=result.is_google_photos,
        is_retrieval=result.is_retrieval,
        retrieval_type=result.retrieval_type.value,
        vague_memory_relevance=result.vague_memory_relevance,
        excluded_topic=topic_name(result.excluded_topic),
        excluded_topic_blocks_retrieval=result.excluded_topic_blocks_retrieval,
        rationale=rationale,
        confidence=result.confidence,
        model=outcome.model,
        prompt_version=outcome.prompt_version,
    )


def _tally(
    report: FunnelReport,
    item: WorkItem,
    label: str,
    *,
    stage_a: bool,
    stage_b: bool,
) -> None:
    bucket = report.by_source[item.source]
    bucket["eligible"] += 1
    if stage_a:
        bucket["stage_a"] += 1
    if stage_b:
        bucket["stage_b"] += 1
    if label in _RETRIEVAL_LABELS:
        bucket[label] += 1
        report.retrieval_types[label] += 1


def classify_pending(
    client: LLMClient,
    prompt: Prompt,
    pending: list[WorkItem],
    model: str,
    *,
    batching: RelevanceSettings,
    max_item_chars: int,
    workers: int,
) -> tuple[list[tuple[WorkItem, Classified]], list[str], str | None]:
    """Classify `pending` in batches. Returns results, per-item errors, and the reason the
    run stopped early (daily rate limit or budget), if it did."""
    batches = plan_batches(
        pending,
        lambda it: it.text,
        max_items=batching.batch_max_items,
        max_chars=batching.batch_max_chars,
        max_item_chars=max_item_chars,
    )

    def run(batch: list[WorkItem]) -> tuple[list[tuple[WorkItem, Classified]], list[str]]:
        return _classify_splitting(
            client,
            prompt,
            batch,
            model,
            max_item_chars=max_item_chars,
            max_output_tokens=batching.batch_max_output_tokens,
        )

    found: list[tuple[WorkItem, Classified]] = []
    errors: list[str] = []
    stopped: str | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, batch): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            if future.cancelled():
                continue
            try:
                done, failed = future.result()
                found.extend(done)
                errors.extend(failed)
            except (LLMRateLimitExhausted, LLMBudgetExceeded) as exc:
                if stopped is None:
                    stopped = str(exc)
                    for queued in futures:
                        queued.cancel()
            except LLMConfigError:
                raise
            except LLMError as exc:
                errors.extend(f"{item.item_id}: {exc}" for item in batch)
    return found, errors, stopped


def _classify_splitting(
    client: LLMClient,
    prompt: Prompt,
    batch: list[WorkItem],
    model: str,
    *,
    max_item_chars: int,
    max_output_tokens: int,
) -> tuple[list[tuple[WorkItem, Classified]], list[str]]:
    """One batch call; when the ids come back wrong, retry each half on its own."""
    try:
        results = classify_batch(
            client,
            prompt,
            [it.text for it in batch],
            model=model,
            max_item_chars=max_item_chars,
            max_output_tokens=max_output_tokens,
        )
    except BatchMismatchError as exc:
        if len(batch) == 1:
            return [], [f"{batch[0].item_id}: {exc}"]
        log.warning("%s; splitting the batch of %d", exc, len(batch))
        middle = len(batch) // 2
        found: list[tuple[WorkItem, Classified]] = []
        errors: list[str] = []
        for half in (batch[:middle], batch[middle:]):
            done, failed = _classify_splitting(
                client,
                prompt,
                half,
                model,
                max_item_chars=max_item_chars,
                max_output_tokens=max_output_tokens,
            )
            found.extend(done)
            errors.extend(failed)
        return found, errors
    return list(zip(batch, results, strict=True)), []


def _store_vectors(
    factory: sessionmaker[Session],
    model_name: str,
    items: list[WorkItem],
    vectors: np.ndarray,
) -> None:
    with session_scope(factory) as session:
        have = set(
            session.scalars(
                select(EmbeddingRow.item_id).where(EmbeddingRow.model == model_name)
            ).all()
        )
        session.add_all(
            EmbeddingRow(item_id=item.item_id, model=model_name, vector=pack_vector(vector))
            for item, vector in zip(items, vectors, strict=True)
            if item.item_id not in have
        )


def preview_funnel(
    items: list[WorkItem],
    cfg: AppConfig,
    encoder: Encoder,
) -> tuple[set[str], set[str], set[str]]:
    """Return Stage A ids, Stage B ids, and Stage C candidate ids (B keepers plus the audit)."""
    stage_a = StageA.from_keywords(cfg.keywords)
    tau = cfg.settings.relevance.semantic_margin_threshold
    kept_a = [it for it in items if stage_a.keep(it.text, it.rating)]
    stage_a_ids = {it.item_id for it in kept_a}
    if not kept_a:
        return stage_a_ids, set(), set()
    margins, _vectors = margins_for(
        encoder,
        [it.text for it in kept_a],
        cfg.keywords.seed_exemplars.positive,
        cfg.keywords.seed_exemplars.negative,
    )
    kept_b = [
        it for it, margin in zip(kept_a, margins, strict=True) if keep_margin(float(margin), tau)
    ]
    stage_b_ids = {it.item_id for it in kept_b}
    rejected = [it.item_id for it in kept_a if it.item_id not in stage_b_ids]
    audit = audit_ids(rejected, cfg.settings.relevance.audit_sample_rate, seed=AUDIT_SEED)
    return stage_a_ids, stage_b_ids, stage_b_ids | audit


def _write_rows(factory: sessionmaker[Session], rows: list[RelevanceRow]) -> None:
    """Replace the relevance rows for these items. Delete-then-insert in chunks: a per-row
    merge is one round trip per row, far too slow against hosted Postgres."""
    if not rows:
        return
    with session_scope(factory) as session:
        for start in range(0, len(rows), _WRITE_CHUNK):
            chunk = rows[start : start + _WRITE_CHUNK]
            session.execute(
                delete(RelevanceRow).where(RelevanceRow.item_id.in_([r.item_id for r in chunk]))
            )
            session.add_all(chunk)
            session.flush()
