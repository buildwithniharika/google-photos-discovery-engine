"""Publish checks, retention, and the cost report for a full run (Phase 8)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from discovery.config import AppConfig
from discovery.db import session_scope
from discovery.models.orm import (
    ClusterRow,
    InsightRow,
    LLMCacheRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    OpportunityScoreRow,
    PipelineRunRow,
    PublishedRunRow,
    RawItemRow,
)
from discovery.models.schemas import RunStatus

# A new run whose evidence is under 10% of the previous published run is not published.
DROP_RATIO = 0.10
REQUIRED_STAGES = ("prep", "classify", "extract", "cluster", "score")

# Architecture Section 19, for the cost report. Those figures assumed Groq list prices.
SECTION_19_ITEMS = 30_000
SECTION_19_CALLS = 4_500


@dataclass(frozen=True)
class PublishOutcome:
    published: bool
    status: str
    reasons: list[str]


def snapshot_published(factory: sessionmaker[Session]) -> tuple[str | None, datetime | None]:
    with session_scope(factory) as session:
        row = session.get(PublishedRunRow, 1)
        if row is None:
            return None, None
        return row.published_run_id, row.published_at


def decide_publish(
    factory: sessionmaker[Session],
    run_id: str,
    previous: tuple[str | None, datetime | None],
    *,
    stage_failed: bool,
) -> PublishOutcome:
    """Sanity-check the run, then move or restore the published-run pointer in one transaction."""
    with session_scope(factory) as session:
        statuses = _statuses(session, run_id)
        if statuses.get("score") == RunStatus.SKIPPED.value and not stage_failed:
            return PublishOutcome(
                published=False,
                status="unchanged",
                reasons=["Scoring was skipped. The published run is unchanged."],
            )
        reasons: list[str] = []
        if stage_failed:
            reasons.append("A pipeline stage failed. The published run was left unchanged.")
        reasons.extend(_sanity(session, run_id, statuses, previous_run_id=previous[0]))
        if reasons:
            _restore(session, run_id, previous)
            status = "failed" if stage_failed or _hard_failure(statuses) else "partial"
            return PublishOutcome(published=False, status=status, reasons=reasons)
        _set_published(session, run_id)
        return PublishOutcome(published=True, status="published", reasons=[])


def _statuses(session: Session, run_id: str) -> dict[str, str]:
    rows = session.scalars(select(PipelineRunRow).where(PipelineRunRow.run_id == run_id)).all()
    return {row.stage: row.status for row in rows if row.stage != "run_all"}


def _hard_failure(statuses: dict[str, str]) -> bool:
    return any(status == RunStatus.FAILED.value for status in statuses.values())


def _sanity(
    session: Session,
    run_id: str,
    statuses: dict[str, str],
    *,
    previous_run_id: str | None,
) -> list[str]:
    reasons = []
    ingest = statuses.get("ingest")
    if ingest not in (RunStatus.COMPLETED.value, RunStatus.PARTIAL.value):
        reasons.append(f"Ingest finished as {ingest or 'missing'}; expected completed or partial.")
    for stage in REQUIRED_STAGES:
        status = statuses.get(stage)
        if status not in (RunStatus.COMPLETED.value, RunStatus.PUBLISHED.value):
            reasons.append(f"{stage} finished as {status or 'missing'}; expected completed.")
    areas = session.scalars(
        select(OpportunityAreaRow).where(
            OpportunityAreaRow.run_id == run_id,
            OpportunityAreaRow.status == "active",
        )
    ).all()
    if not areas:
        reasons.append("No active opportunity area.")
        return reasons
    scored = set(
        session.scalars(
            select(OpportunityScoreRow.area_id).where(OpportunityScoreRow.run_id == run_id)
        ).all()
    )
    quoted = _areas_with_quotes(session, run_id)
    for area in areas:
        if area.area_id not in scored:
            reasons.append(f"{area.area_id} ({area.name}) has no scores.")
        if area.area_id not in quoted:
            reasons.append(f"{area.area_id} ({area.name}) has no representative quote.")
    if previous_run_id and previous_run_id != run_id:
        previous_count = _evidence_count(session, previous_run_id)
        current_count = _evidence_count(session, run_id)
        if previous_count and current_count < previous_count * DROP_RATIO:
            reasons.append(
                f"Evidence items fell from {previous_count} to {current_count}, "
                "more than a 90% drop from the published run."
            )
    return reasons


def _areas_with_quotes(session: Session, run_id: str) -> set[str]:
    rows = session.execute(
        select(OpportunityEvidenceRow.area_id, OpportunityEvidenceRow.item_id).where(
            OpportunityEvidenceRow.run_id == run_id,
            OpportunityEvidenceRow.is_representative.is_(True),
        )
    ).all()
    item_ids = {item_id for _area, item_id in rows}
    if not item_ids:
        return set()
    quotes = {
        item_id
        for item_id, quote in session.execute(
            select(InsightRow.item_id, InsightRow.evidence_quote).where(
                InsightRow.item_id.in_(item_ids)
            )
        ).all()
        if quote and str(quote).strip()
    }
    return {area_id for area_id, item_id in rows if item_id in quotes}


def _evidence_count(session: Session, run_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(OpportunityEvidenceRow)
            .where(OpportunityEvidenceRow.run_id == run_id)
        )
        or 0
    )


def _restore(session: Session, run_id: str, previous: tuple[str | None, datetime | None]) -> None:
    row = session.get(PublishedRunRow, 1)
    if row is None or row.published_run_id != run_id:
        return
    previous_id, previous_at = previous
    if previous_id:
        row.published_run_id = previous_id
        if previous_at is not None:
            row.published_at = previous_at
    else:
        session.delete(row)


def _set_published(session: Session, run_id: str) -> None:
    now = datetime.now(UTC)
    row = session.get(PublishedRunRow, 1)
    if row is None:
        session.add(PublishedRunRow(id=1, published_run_id=run_id, published_at=now))
    else:
        row.published_run_id = run_id
        row.published_at = now


# --- retention ----------------------------------------------------------------


def parse_older_than(value: str, *, now: datetime | None = None) -> datetime:
    """'90d', '12w', or a calendar date YYYY-MM-DD. The cutoff is the start of that age."""
    text = value.strip().lower()
    now = now or datetime.now(UTC)
    if text.endswith("d") and text[:-1].isdigit():
        return now - timedelta(days=int(text[:-1]))
    if text.endswith("w") and text[:-1].isdigit():
        return now - timedelta(weeks=int(text[:-1]))
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("--older-than must look like 90d, 12w, or YYYY-MM-DD.") from exc


@dataclass
class PurgeReport:
    protected_run_id: str | None
    raw_items: int = 0
    cache_rows: int = 0
    runs: int = 0
    areas: int = 0
    raw_dirs: int = 0

    def lines(self) -> str:
        protected = self.protected_run_id or "none"
        return (
            f"Protected published run: {protected}\n"
            f"Raw items deleted: {self.raw_items}\n"
            f"LLM cache rows deleted: {self.cache_rows}\n"
            f"Old pipeline runs deleted: {self.runs}\n"
            f"Old opportunity runs deleted: {self.areas}\n"
            f"Local raw directories deleted: {self.raw_dirs}\n"
        )


def purge_older_than(
    factory: sessionmaker[Session],
    cutoff: datetime,
    *,
    raw_dir: Path | None = None,
    dry_run: bool = False,
) -> PurgeReport:
    """Drop old raw payloads and unpublished run outputs. The published run stays."""
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)
    with session_scope(factory) as session:
        published = session.get(PublishedRunRow, 1)
        protected = published.published_run_id if published else None
        old_runs = _old_run_ids(session, cutoff, protected)
        report = PurgeReport(
            protected_run_id=protected,
            raw_items=_count_raw(session, cutoff, protected),
            cache_rows=int(
                session.scalar(
                    select(func.count())
                    .select_from(LLMCacheRow)
                    .where(LLMCacheRow.created_at < cutoff)
                )
                or 0
            ),
            runs=len(old_runs),
            areas=len(old_runs),
            raw_dirs=len(_raw_dirs(raw_dir, old_runs)) if raw_dir else 0,
        )
        if dry_run:
            return report
        raw_delete = delete(RawItemRow).where(RawItemRow.fetched_at < cutoff)
        if protected:
            raw_delete = raw_delete.where(RawItemRow.run_id != protected)
        session.execute(raw_delete)
        session.execute(delete(LLMCacheRow).where(LLMCacheRow.created_at < cutoff))
        if old_runs:
            for model in (
                OpportunityEvidenceRow,
                OpportunityScoreRow,
                OpportunityAreaRow,
                ClusterRow,
            ):
                session.execute(delete(model).where(model.run_id.in_(old_runs)))
            session.execute(delete(PipelineRunRow).where(PipelineRunRow.run_id.in_(old_runs)))
        if raw_dir:
            _delete_raw_dirs(raw_dir, old_runs)
    return report


def _old_run_ids(session: Session, cutoff: datetime, protected: str | None) -> set[str]:
    rows = session.execute(select(PipelineRunRow.run_id, PipelineRunRow.started_at)).all()
    by_run: dict[str, datetime] = {}
    for run_id, started in rows:
        if run_id == protected or started is None:
            continue
        started = started if started.tzinfo else started.replace(tzinfo=UTC)
        current = by_run.get(run_id)
        if current is None or started < current:
            by_run[run_id] = started
    return {run_id for run_id, started in by_run.items() if started < cutoff}


def _count_raw(session: Session, cutoff: datetime, protected: str | None) -> int:
    stmt = select(func.count()).select_from(RawItemRow).where(RawItemRow.fetched_at < cutoff)
    if protected:
        stmt = stmt.where(RawItemRow.run_id != protected)
    return int(session.scalar(stmt) or 0)


def _raw_dirs(raw_dir: Path, run_ids: set[str]) -> list[Path]:
    if not raw_dir.is_dir() or not run_ids:
        return []
    return [path for path in raw_dir.glob("*/*") if path.is_dir() and path.name in run_ids]


def _delete_raw_dirs(raw_dir: Path, run_ids: set[str]) -> int:
    removed = 0
    for path in _raw_dirs(raw_dir, run_ids):
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


# --- cost report --------------------------------------------------------------


def render_cost_report(
    factory: sessionmaker[Session], cfg: AppConfig, *, run_id: str | None
) -> str:
    """Cost, runtime, and rate-limit notes for one run, next to architecture Section 19."""
    with session_scope(factory) as session:
        if run_id is None:
            run_id = session.scalar(
                select(PipelineRunRow.run_id).order_by(PipelineRunRow.started_at.desc()).limit(1)
            )
        rows = []
        if run_id:
            rows = session.scalars(
                select(PipelineRunRow)
                .where(PipelineRunRow.run_id == run_id)
                .order_by(PipelineRunRow.started_at)
            ).all()
        spent = float(
            session.scalar(select(func.coalesce(func.sum(PipelineRunRow.llm_cost_usd), 0.0))) or 0
        )
    llm = cfg.settings.llm
    limits = llm.rate_limits.get("default")
    rpm = limits.requests_per_minute if limits else None
    tpm = limits.tokens_per_minute if limits else None
    lines = [
        "# Cost and rate-limit report",
        "",
        f"Run: `{run_id or 'none'}`.",
        "",
        "Architecture Section 19 estimated a 30,000-item corpus at about "
        f"{SECTION_19_CALLS:,} LLM calls and well under a few US dollars on the original "
        "Groq prices. Rate limits, not the dollar cost, were the binding constraint. "
        f"This project uses Anthropic Claude. The PM cap is "
        f"${llm.project_budget_usd or 0:.2f}, and one command is capped at "
        f"${llm.max_cost_usd_per_run:.2f}.",
        "",
        "## This run",
        "",
        "| Stage | Status | Runtime | Tokens | Cost |",
        "| --- | --- | --- | --- | --- |",
    ]
    total_tokens = 0
    total_cost = 0.0
    rate_hits = 0
    starts = []
    ends = []
    for row in rows:
        runtime = _runtime(row.started_at, row.finished_at)
        tokens = int(row.llm_tokens or 0)
        cost = float(row.llm_cost_usd or 0)
        total_tokens += tokens
        total_cost += cost
        if row.started_at:
            starts.append(_aware(row.started_at))
        if row.finished_at:
            ends.append(_aware(row.finished_at))
        for error in row.errors or []:
            message = error.get("message") if isinstance(error, dict) else str(error)
            if "429" in message:
                rate_hits += 1
        lines.append(f"| {row.stage} | {row.status} | {runtime} | {tokens:,} | ${cost:.4f} |")
    lines.append("")
    lines.append(f"Run total: {total_tokens:,} tokens, ${total_cost:.4f}.")
    if starts and ends:
        elapsed = max(ends) - min(starts)
        minutes = max(elapsed.total_seconds() / 60, 1 / 60)
        lines.append(f"Wall clock from first start to last finish: {_format_span(elapsed)}.")
        if tpm:
            observed = total_tokens / minutes
            relation = "within" if observed <= tpm else "above"
            lines.append(
                f"Average tokens per minute were {observed:,.0f}, {relation} the configured "
                f"limit of {tpm:,}."
            )
    lines.extend(["", "## Limits", ""])
    lines.append(f"- Requests per minute: {rpm if rpm is not None else 'not set'}.")
    lines.append(f"- Tokens per minute: {tpm if tpm is not None else 'not set'}.")
    lines.append(f"- HTTP 429 errors recorded on this run: {rate_hits}.")
    per_run = "within" if total_cost <= llm.max_cost_usd_per_run else "above"
    lines.append(
        f"- This run's model cost is {per_run} the ${llm.max_cost_usd_per_run:.2f} per-run cap."
    )
    if llm.project_budget_usd is not None:
        budget = "within" if spent <= llm.project_budget_usd else "above"
        lines.append(
            f"- Recorded project spend is ${spent:.4f}, {budget} the "
            f"${llm.project_budget_usd:.2f} budget (illustrative corpus size in Section 19: "
            f"{SECTION_19_ITEMS:,} items)."
        )
    lines.append("")
    return "\n".join(lines)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _runtime(started: datetime | None, finished: datetime | None) -> str:
    if started is None or finished is None:
        return "—"
    return _format_span(_aware(finished) - _aware(started))


def _format_span(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"
