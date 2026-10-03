"""Gate G4 scoring: the PM's verdicts on the opportunity review sheet.

`discovery cluster` writes `eval/opportunity_review.csv` with blank columns for the PM:
coherence (1-5), specific or generic, and an action. This module reads the filled sheet,
checks it against the latest LLM-labeled cluster run, and computes the Phase 5 acceptance
criterion: average coherence of the top 8 active areas is at least 4.

Next to each PM score the report shows embedding coherence (mean cosine of the area's items
to its centroid), a machine signal that helps spot areas worth a second look. It does not
replace the PM's judgment. Scores are stored in `pm_overrides` (fields `coherence` and
`specific`), so they survive re-runs and later phases can read them.
"""

from __future__ import annotations

import csv
import shlex
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from discovery.ai.cluster_stage import latest_cluster_run
from discovery.ai.clustering import centroid
from discovery.ai.embeddings import INSIGHT_SUFFIX, unpack_vector
from discovery.db import session_scope
from discovery.models.orm import (
    EmbeddingRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    PMOverrideRow,
)

TOP_N = 8
TARGET = 4.0
ACTIONS = ("keep", "rename", "merge", "split", "archive")
REVIEW_FIELDS = ("coherence", "specific")
UNKNOWN = "unknown"  # status of a sheet row whose area is not in the cluster run
_YES = {"yes", "y", "true", "1", "specific"}
_NO = {"no", "n", "false", "0", "generic"}


@dataclass
class AreaVerdict:
    rank: int
    area_id: str
    name: str
    status: str
    items: int
    coherence: int | None = None
    specific: bool | None = None
    action: str | None = None
    action_detail: str = ""
    notes: str = ""


@dataclass
class ReviewSummary:
    run_id: str
    verdicts: list[AreaVerdict]
    top: list[AreaVerdict]
    problems: list[str] = field(default_factory=list)
    embedding_coherence: dict[str, float] = field(default_factory=dict)
    top_n: int = TOP_N
    target: float = TARGET

    @property
    def scored_top(self) -> list[AreaVerdict]:
        return [v for v in self.top if v.coherence is not None]

    @property
    def mean_top(self) -> float | None:
        scored = self.scored_top
        return sum(v.coherence for v in scored) / len(scored) if scored else None

    @property
    def complete(self) -> bool:
        return bool(self.top) and len(self.scored_top) == len(self.top)

    @property
    def passed(self) -> bool:
        return self.complete and not self.problems and (self.mean_top or 0) >= self.target

    @property
    def specific_share(self) -> float | None:
        judged = [v for v in self.top if v.specific is not None]
        return sum(v.specific for v in judged) / len(judged) if judged else None

    def as_counts(self) -> dict[str, Any]:
        return {
            "cluster_run_id": self.run_id,
            "top_n": len(self.top),
            "scored_top": len(self.scored_top),
            "mean_coherence_top": None if self.mean_top is None else round(self.mean_top, 3),
            "target": self.target,
            "passed": self.passed,
            "specific_share_top": self.specific_share,
            "areas_scored": sum(1 for v in self.verdicts if v.coherence is not None),
            "problems": len(self.problems),
        }


# --- reading the sheet ---------------------------------------------------------


def _text(value: str | None) -> str:
    """Undo the sheet's formula guard ('=x is written as "'=x")."""
    text = (value or "").strip()
    return text[1:] if text[:2] in ("'=", "'+", "'-", "'@") else text


def parse_row(row: dict[str, str], line: int) -> tuple[AreaVerdict | None, list[str]]:
    problems: list[str] = []
    area_id = _text(row.get("area_id"))
    if not area_id:
        return None, [f"line {line}: no area_id"]
    where = f"line {line} ({area_id})"
    rank_text, items_text = _text(row.get("rank")), _text(row.get("items"))
    if rank_text and not rank_text.isdigit():
        problems.append(f"{where}: rank is not a number")
    verdict = AreaVerdict(
        rank=int(rank_text) if rank_text.isdigit() else line - 1,
        area_id=area_id,
        name=_text(row.get("name")),
        status=_text(row.get("status")) or "active",
        items=int(items_text) if items_text.isdigit() else 0,
        action_detail=_text(row.get("action_detail")),
        notes=_text(row.get("pm_notes")),
    )
    raw = _text(row.get("coherence_1_5"))
    if raw:
        try:
            score = float(raw)
        except ValueError:
            score = None
        if score is None or score != int(score) or not 1 <= score <= 5:
            problems.append(f"{where}: coherence_1_5 must be a whole number 1-5, got {raw!r}")
        else:
            verdict.coherence = int(score)
    raw = _text(row.get("specific_not_generic")).lower()
    if raw in _YES:
        verdict.specific = True
    elif raw in _NO:
        verdict.specific = False
    elif raw:
        problems.append(f"{where}: specific_not_generic must be yes or no, got {raw!r}")
    raw = _text(row.get("action")).lower()
    if raw in ACTIONS:
        verdict.action = raw
        if raw in ("rename", "merge", "split") and not verdict.action_detail:
            what = {"rename": "the new name", "merge": "the target area id", "split": "cluster ids"}
            problems.append(f"{where}: action {raw} needs {what[raw]} in action_detail")
    elif raw:
        problems.append(f"{where}: action must be one of {', '.join(ACTIONS)}, got {raw!r}")
    return verdict, problems


def read_review(path: Path) -> tuple[list[AreaVerdict], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {"area_id", "coherence_1_5"} - set(reader.fieldnames or [])
        if missing:
            return [], [f"{path} has no column {', '.join(sorted(missing))}"]
        verdicts, problems = [], []
        for line, row in enumerate(reader, start=2):
            verdict, issues = parse_row(row, line)
            problems.extend(issues)
            if verdict is not None:
                verdicts.append(verdict)
    return verdicts, problems


# --- scoring -------------------------------------------------------------------


def summarize(
    verdicts: list[AreaVerdict],
    run_id: str,
    run_areas: dict[str, str],
    *,
    top_n: int = TOP_N,
    target: float = TARGET,
) -> ReviewSummary:
    """`run_areas` maps area id -> status in the cluster run. The top areas are the first
    `top_n` active areas in sheet rank order (largest first until Phase 6 scores them)."""
    problems: list[str] = []
    seen: set[str] = set()
    for v in verdicts:
        if v.area_id in seen:
            problems.append(f"{v.area_id} appears more than once")
        seen.add(v.area_id)
        if v.area_id not in run_areas:
            problems.append(f"{v.area_id} is not in cluster run {run_id} (sheet from another run?)")
            v.status = UNKNOWN
        else:
            v.status = run_areas[v.area_id]
    active_ids = {a for a, status in run_areas.items() if status == "active"}
    absent = sorted(active_ids - seen)
    if absent:
        problems.append(f"Active areas missing from the sheet: {', '.join(absent)}")
    active = sorted((v for v in verdicts if v.status == "active"), key=lambda v: v.rank)
    return ReviewSummary(
        run_id=run_id,
        verdicts=sorted(verdicts, key=lambda v: v.rank),
        top=active[:top_n],
        problems=problems,
        top_n=top_n,
        target=target,
    )


def load_run_areas(session: Session) -> tuple[str | None, dict[str, str]]:
    run_id = latest_cluster_run(session, labeled_only=True)
    if run_id is None:
        return None, {}
    rows = session.execute(
        select(OpportunityAreaRow.area_id, OpportunityAreaRow.status).where(
            OpportunityAreaRow.run_id == run_id
        )
    ).all()
    return run_id, {area_id: status for area_id, status in rows}


def embedding_coherence(session: Session, run_id: str, model_key: str) -> dict[str, float]:
    """Area id -> mean cosine of its items to the area centroid (insight vectors)."""
    members: dict[str, list[str]] = defaultdict(list)
    for area_id, item_id in session.execute(
        select(OpportunityEvidenceRow.area_id, OpportunityEvidenceRow.item_id).where(
            OpportunityEvidenceRow.run_id == run_id
        )
    ):
        members[area_id].append(item_id)
    vectors = {
        item_id: unpack_vector(vec)
        for item_id, vec in session.execute(
            select(EmbeddingRow.item_id, EmbeddingRow.vector).where(EmbeddingRow.model == model_key)
        )
    }
    out: dict[str, float] = {}
    for area_id, ids in members.items():
        rows = [vectors[i] for i in ids if i in vectors]
        if rows:
            matrix = np.vstack(rows)
            out[area_id] = round(float((matrix @ centroid(matrix)).mean()), 3)
    return out


def review_areas(
    factory: sessionmaker[Session], sheet: Path, embedding_model: str
) -> ReviewSummary:
    verdicts, problems = read_review(sheet)
    with session_scope(factory) as session:
        run_id, run_areas = load_run_areas(session)
        if run_id is None:
            raise ValueError("No LLM-labeled cluster run yet. Run `discovery cluster` first.")
        summary = summarize(verdicts, run_id, run_areas)
        summary.problems[:0] = problems
        summary.embedding_coherence = embedding_coherence(
            session, run_id, embedding_model + INSIGHT_SUFFIX
        )
    return summary


def record_review(factory: sessionmaker[Session], summary: ReviewSummary) -> int:
    """Store each score in `pm_overrides`, skipping values already recorded. Returns rows added."""
    added = 0
    with session_scope(factory) as session:
        latest: dict[tuple[str, str], Any] = {}
        for row in session.scalars(
            select(PMOverrideRow)
            .where(PMOverrideRow.target_type == "area", PMOverrideRow.field.in_(REVIEW_FIELDS))
            .order_by(PMOverrideRow.created_at, PMOverrideRow.override_id)
        ):
            latest[(row.target_id, row.field)] = row.override_value
        for v in summary.verdicts:
            if v.status == UNKNOWN:
                continue
            for field_name, value in (("coherence", v.coherence), ("specific", v.specific)):
                if value is None or latest.get((v.area_id, field_name)) == value:
                    continue
                session.add(
                    PMOverrideRow(
                        target_type="area",
                        target_id=v.area_id,
                        field=field_name,
                        ai_value=summary.embedding_coherence.get(v.area_id)
                        if field_name == "coherence"
                        else None,
                        override_value=value,
                        note=f"G4 review of cluster run {summary.run_id}"
                        + (f": {v.notes}" if v.notes else ""),
                    )
                )
                added += 1
    return added


# --- report --------------------------------------------------------------------


def curation_commands(verdicts: list[AreaVerdict]) -> list[str]:
    """`discovery curate-area` lines for the actions the PM marked in the sheet."""
    lines = []
    for v in verdicts:
        base = f"uv run discovery curate-area {v.area_id}"
        detail = v.action_detail
        if v.action == "rename":
            lines.append(f"{base} --rename {shlex.quote(detail)}")
        elif v.action == "merge":
            lines.append(f"{base} --merge-into {shlex.quote(detail)}")
        elif v.action == "split":
            clusters = [c for c in detail.replace(",", " ").split() if c]
            lines.append(base + "".join(f" --split-cluster {shlex.quote(c)}" for c in clusters))
        elif v.action == "archive":
            lines.append(f"{base} --archive")
    return lines


def _md(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render_review(summary: ReviewSummary) -> str:
    mean = summary.mean_top
    if summary.passed:
        verdict = f"**Met.** Average coherence of the top {len(summary.top)} areas: {mean:.2f}"
    elif summary.problems:
        verdict = "**Not checked yet:** fix the sheet problems listed below."
    elif not summary.complete:
        verdict = (
            f"**Not checked yet:** {len(summary.scored_top)} of {len(summary.top)} top areas "
            "have a coherence score."
        )
    else:
        verdict = (
            f"**Not met.** Average coherence of the top {len(summary.top)} areas: {mean:.2f} "
            f"(target ≥ {summary.target:g}). Curate the low-scoring areas (merge, split, "
            "archive), re-run `discovery cluster`, and score the new sheet."
        )
    lines = [
        "# Gate G4: opportunity area review",
        "",
        f"Cluster run: `{summary.run_id}`. Criterion: average PM-judged coherence ≥ "
        f"{summary.target:g} / 5 for the top {summary.top_n} active areas.",
        "",
        verdict,
        "",
    ]
    share = summary.specific_share
    if share is not None:
        lines += [f"Top areas judged specific (not generic): {share:.0%}.", ""]
    if summary.problems:
        lines += ["## Sheet problems", "", *[f"- {p}" for p in summary.problems], ""]
    top_ids = {v.area_id for v in summary.top}
    lines += [
        "## Areas",
        "",
        "Embedding coherence is the mean cosine of the area's items to its centroid. It is a "
        "machine signal for a second look, not part of the criterion.",
        "",
        "| Rank | Area | Items | Status | Top | PM coherence | Specific | Embedding coherence "
        "| Action | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for v in summary.verdicts:
        specific = {True: "yes", False: "no", None: ""}[v.specific]
        emb = summary.embedding_coherence.get(v.area_id)
        action = f"{v.action} {v.action_detail}".strip() if v.action else ""
        lines.append(
            f"| {v.rank} | {_md(v.name)} (`{v.area_id}`) | {v.items} | {v.status} | "
            f"{'✓' if v.area_id in top_ids else ''} | "
            f"{v.coherence if v.coherence is not None else '-'} | {specific} | "
            f"{'-' if emb is None else f'{emb:.3f}'} | {_md(action)} | {_md(v.notes)} |"
        )
    low = [v for v in summary.top if v.coherence is not None and v.coherence <= 3]
    if low:
        lines += [
            "",
            "## Low-coherence top areas (3 or below)",
            "",
            *[f"- `{v.area_id}` {_md(v.name)}: {v.coherence}/5" for v in low],
        ]
    commands = curation_commands(summary.verdicts)
    if commands:
        lines += [
            "",
            "## Curation from the sheet",
            "",
            "Run these, then `uv run discovery cluster` (unchanged LLM calls come from the cache):",
            "",
            "```bash",
            *commands,
            "```",
        ]
    lines.append("")
    return "\n".join(lines)
