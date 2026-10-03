"""Gold-set metrics for the relevance funnel (architecture Section 17.2).

Ambiguous gold rows are left out of every rate (EVAL-02). Stage A and Stage A+B recall
are on items labeled `general_retrieval` or `vague_memory_retrieval`. Stage C precision
and recall are for `vague_memory_retrieval`, measured on items that reached the classifier.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

RETRIEVAL_LABELS = frozenset({"general_retrieval", "vague_memory_retrieval"})
VAGUE = "vague_memory_retrieval"
AMBIGUOUS = "ambiguous"

STAGE_A_RECALL_TARGET = 0.95
STAGE_AB_RECALL_TARGET = 0.90
STAGE_C_PRECISION_TARGET = 0.80
STAGE_C_RECALL_TARGET = 0.75


@dataclass
class Rate:
    hits: int
    total: int

    @property
    def value(self) -> float | None:
        if self.total == 0:
            return None
        return self.hits / self.total


@dataclass
class FunnelMetrics:
    scored: int
    ambiguous_excluded: int
    stage_a_recall: Rate
    stage_ab_recall: Rate
    stage_c_precision: Rate
    stage_c_recall: Rate
    end_to_end_vague_recall: Rate
    stage_c_pending: int = 0
    by_source: dict[str, dict[str, Rate]] = field(default_factory=dict)

    def misses_targets(self) -> list[str]:
        gaps: list[str] = []
        checks = (
            ("Stage A recall", self.stage_a_recall.value, STAGE_A_RECALL_TARGET),
            ("Stages A+B recall", self.stage_ab_recall.value, STAGE_AB_RECALL_TARGET),
            ("Stage C precision", self.stage_c_precision.value, STAGE_C_PRECISION_TARGET),
            ("Stage C recall", self.stage_c_recall.value, STAGE_C_RECALL_TARGET),
        )
        for name, value, target in checks:
            if value is None:
                continue
            if value < target:
                gaps.append(f"{name} {value:.1%} is below {target:.0%}")
        if self.stage_c_pending:
            gaps.append(
                "Stage C incomplete: "
                f"{self.stage_c_pending} vague items that passed Stage B have no classifier label"
            )
        return gaps


def evaluate_gold(
    rows: list[dict[str, str]],
    *,
    stage_a_ids: set[str],
    stage_b_ids: set[str],
    stage_c_labels: dict[str, str],
) -> FunnelMetrics:
    """Score gold rows.

    `stage_a_ids` passed the keyword prefilter. `stage_b_ids` also passed the semantic
    filter. `stage_c_labels` maps item id to the classifier's retrieval type for items
    that reached Stage C (including the Stage B audit sample).
    """
    labeled = [r for r in rows if (r.get("retrieval_type") or "").strip()]
    ambiguous = [r for r in labeled if _label(r) == AMBIGUOUS]
    scored = [r for r in labeled if _label(r) != AMBIGUOUS]
    relevant = [r for r in scored if _label(r) in RETRIEVAL_LABELS]
    vague = [r for r in scored if _label(r) == VAGUE]

    stage_a_recall = _recall(relevant, stage_a_ids)
    stage_ab_recall = _recall(relevant, stage_b_ids)

    reached = [r for r in vague if r["item_id"] in stage_c_labels]
    pending = [
        r for r in vague if r["item_id"] in stage_b_ids and r["item_id"] not in stage_c_labels
    ]
    stage_c_recall = Rate(
        hits=sum(1 for r in reached if stage_c_labels[r["item_id"]] == VAGUE),
        total=len(reached),
    )
    predicted_vague = [
        r
        for r in scored
        if r["item_id"] in stage_c_labels and stage_c_labels[r["item_id"]] == VAGUE
    ]
    stage_c_precision = Rate(
        hits=sum(1 for r in predicted_vague if _label(r) == VAGUE),
        total=len(predicted_vague),
    )
    end_to_end = Rate(
        hits=sum(1 for r in vague if stage_c_labels.get(r["item_id"]) == VAGUE),
        total=len(vague),
    )
    return FunnelMetrics(
        scored=len(scored),
        ambiguous_excluded=len(ambiguous),
        stage_a_recall=stage_a_recall,
        stage_ab_recall=stage_ab_recall,
        stage_c_precision=stage_c_precision,
        stage_c_recall=stage_c_recall,
        end_to_end_vague_recall=end_to_end,
        stage_c_pending=len(pending),
        by_source=_by_source(scored, stage_a_ids, stage_b_ids, stage_c_labels),
    )


def render_metrics(metrics: FunnelMetrics, *, tau: float, prompt_version: str, model: str) -> str:
    lines = [
        "# Relevance evaluation",
        "",
        f"Prompt `{prompt_version}`, model `{model}`, Stage B threshold τ = {tau:.3f}.",
        f"Gold rows scored: {metrics.scored} (ambiguous excluded: {metrics.ambiguous_excluded}).",
        "",
        "## Targets",
        "",
        _rate_line("Stage A recall", metrics.stage_a_recall, STAGE_A_RECALL_TARGET),
        _rate_line("Stages A+B recall", metrics.stage_ab_recall, STAGE_AB_RECALL_TARGET),
        _rate_line(
            "Stage C precision (vague memory)",
            metrics.stage_c_precision,
            STAGE_C_PRECISION_TARGET,
        ),
        _rate_line(
            "Stage C recall (vague memory, items that reached the classifier)",
            metrics.stage_c_recall,
            STAGE_C_RECALL_TARGET,
        ),
        _rate_line(
            "End-to-end vague recall (Stage A/B drops count as misses)",
            metrics.end_to_end_vague_recall,
            None,
        ),
        "",
        "Stage C precision and recall use only items the funnel sent to the classifier.",
        "End-to-end recall also counts vague items dropped at Stage A or Stage B.",
        "",
        "## By source",
        "",
        "| Source | Stage A recall | Stages A+B recall | Stage C vague recall |",
        "| --- | --- | --- | --- |",
    ]
    for source, rates in sorted(metrics.by_source.items()):
        lines.append(
            "| "
            + " | ".join(
                [
                    source,
                    _fmt(rates["stage_a"]),
                    _fmt(rates["stage_ab"]),
                    _fmt(rates["stage_c"]),
                ]
            )
            + " |"
        )
    gaps = metrics.misses_targets()
    lines.extend(["", "## Result", ""])
    if gaps:
        lines.append("Below target:")
        lines.extend(f"- {gap}" for gap in gaps)
    elif metrics.stage_c_precision.total == 0 and metrics.stage_c_recall.total == 0:
        lines.append(
            "Stage A and Stage A+B are scored. Stage C has no classifier labels yet. "
            "Run `discovery eval --llm` to score it."
        )
    else:
        lines.append("Stage A, Stages A+B, and Stage C meet the Section 17.2 targets.")
    lines.append("")
    return "\n".join(lines)


def _label(row: dict[str, str]) -> str:
    return (row.get("retrieval_type") or "").strip().lower()


def _recall(rows: list[dict[str, str]], kept: set[str]) -> Rate:
    return Rate(hits=sum(1 for r in rows if r["item_id"] in kept), total=len(rows))


def _by_source(
    scored: list[dict[str, str]],
    stage_a_ids: set[str],
    stage_b_ids: set[str],
    stage_c_labels: dict[str, str],
) -> dict[str, dict[str, Rate]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in scored:
        grouped[row.get("source") or "unknown"].append(row)
    out: dict[str, dict[str, Rate]] = {}
    for source, rows in grouped.items():
        relevant = [r for r in rows if _label(r) in RETRIEVAL_LABELS]
        vague = [r for r in rows if _label(r) == VAGUE and r["item_id"] in stage_c_labels]
        out[source] = {
            "stage_a": _recall(relevant, stage_a_ids),
            "stage_ab": _recall(relevant, stage_b_ids),
            "stage_c": Rate(
                hits=sum(1 for r in vague if stage_c_labels[r["item_id"]] == VAGUE),
                total=len(vague),
            ),
        }
    return out


def _fmt(rate: Rate) -> str:
    if rate.value is None:
        return "n/a"
    return f"{rate.value:.0%} ({rate.hits}/{rate.total})"


def _rate_line(name: str, rate: Rate, target: float | None) -> str:
    shown = _fmt(rate)
    if target is None:
        return f"- {name}: {shown}"
    mark = "met" if rate.value is not None and rate.value >= target else "open"
    if rate.value is None:
        mark = "not scored"
    return f"- {name}: {shown} (target ≥ {target:.0%}, {mark})"
