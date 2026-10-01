"""`pipeline_runs` logging: every CLI stage records start, end, status, counts, and errors."""

from __future__ import annotations

import logging
import secrets
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from discovery.db import session_scope
from discovery.models.orm import PipelineRunRow
from discovery.models.schemas import RunStatus

log = logging.getLogger(__name__)


def new_run_id() -> str:
    """Sortable, human-readable run id, e.g. '2026-10-05T073012_a1b2c3'."""
    return f"{datetime.now(UTC):%Y-%m-%dT%H%M%S}_{secrets.token_hex(3)}"


@dataclass
class StageRun:
    run_id: str
    stage: str
    counts: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    llm_tokens: int = 0
    llm_cost_usd: float = 0.0
    final_status: RunStatus | None = None

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def error(self, message: str, **context: Any) -> None:
        """Record a non-fatal error; the stage finishes as `partial`."""
        log.warning("[%s] %s %s", self.stage, message, context or "")
        self.errors.append({"message": message, **context})

    def skip(self, reason: str) -> None:
        self.counts["skipped_reason"] = reason
        self.final_status = RunStatus.SKIPPED

    def add_llm_usage(self, tokens: int, cost_usd: float) -> None:
        self.llm_tokens += tokens
        self.llm_cost_usd += cost_usd


def _write(factory: sessionmaker[Session], stage: StageRun, status: RunStatus, *, start: bool):
    now = datetime.now(UTC)
    with session_scope(factory) as s:
        row = s.get(PipelineRunRow, (stage.run_id, stage.stage))
        if row is None:
            row = PipelineRunRow(run_id=stage.run_id, stage=stage.stage)
            s.add(row)
        if start:
            row.started_at = now
            row.finished_at = None
        else:
            row.finished_at = now
        row.status = status.value
        row.counts = dict(stage.counts)
        row.errors = list(stage.errors)
        row.llm_tokens = stage.llm_tokens
        row.llm_cost_usd = round(stage.llm_cost_usd, 6)


@contextmanager
def track_stage(factory: sessionmaker[Session], run_id: str, stage: str) -> Iterator[StageRun]:
    """Record a stage in `pipeline_runs`. Status is written in its own transaction, so it
    survives even when the stage's own work is rolled back."""
    run = StageRun(run_id=run_id, stage=stage)
    _write(factory, run, RunStatus.RUNNING, start=True)
    log.info("Stage '%s' started (run %s)", stage, run_id)
    try:
        yield run
    except BaseException as exc:
        run.errors.append(
            {
                "message": str(exc) or exc.__class__.__name__,
                "type": exc.__class__.__name__,
                "traceback": traceback.format_exc(limit=5),
            }
        )
        _write(factory, run, RunStatus.FAILED, start=False)
        log.error("Stage '%s' failed (run %s): %s", stage, run_id, exc)
        raise
    status = run.final_status or (RunStatus.PARTIAL if run.errors else RunStatus.COMPLETED)
    _write(factory, run, status, start=False)
    log.info("Stage '%s' finished: %s (run %s)", stage, status.value, run_id)
