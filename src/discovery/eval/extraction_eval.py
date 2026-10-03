"""Score insight extraction against the gold set (architecture Section 17.2, P4.7).

Every gold row labeled `general_retrieval` or `vague_memory_retrieval` is extracted,
whatever Stage C said about it, so the score measures extraction on its own. Gold values
outside the taxonomy (a labeling slip) are ignored.

- Category: top-1 = primary matches; top-2 = gold primary is the predicted primary or the
  first secondary category.
- `not_stated` correctness: on rows where the labeler recorded no remembered cues (or no
  forgotten details), the model must leave that list empty too. It measures invented cues.
- Quote grounding: share of extracted items whose final quote matches `clean_text`.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from discovery.ai.extraction import Extracted, ExtractionItem, ExtractionRun, extract_items
from discovery.ai.llm_client import LLMClient
from discovery.ai.prompts import Prompt
from discovery.config import AppConfig
from discovery.eval.gold_set import allowed_values, read_sheet
from discovery.eval.metrics import RETRIEVAL_LABELS, Rate

GROUNDING_TARGET = 0.98
CATEGORY_TOP1_TARGET = 0.70
CATEGORY_TOP2_TARGET = 0.85
NOT_STATED_TARGET = 0.90


@dataclass
class ExtractionMetrics:
    scored: int
    extracted: int
    category_top1: Rate
    category_top2: Rate
    content_type: Rate
    breakdown: Rate
    not_stated: Rate
    not_stated_cues: Rate
    not_stated_forgotten: Rate
    grounding: Rate
    first_pass_grounding: Rate
    strength_within_one: Rate
    cue_precision: Rate
    cue_recall: Rate
    forgotten_precision: Rate
    forgotten_recall: Rate
    category_by_type: dict[str, Rate] = field(default_factory=dict)
    confusions: Counter[tuple[str, str]] = field(default_factory=Counter)

    @property
    def missing(self) -> int:
        return self.scored - self.extracted

    def misses_targets(self) -> list[str]:
        gaps: list[str] = []
        checks = (
            ("Quote grounding rate", self.grounding, GROUNDING_TARGET),
            ("Primary category accuracy", self.category_top1, CATEGORY_TOP1_TARGET),
            ("Primary category top-2 accuracy", self.category_top2, CATEGORY_TOP2_TARGET),
            ("not_stated correctness", self.not_stated, NOT_STATED_TARGET),
        )
        for name, rate, target in checks:
            if rate.value is not None and rate.value < target:
                gaps.append(f"{name} {rate.value:.1%} is below {target:.0%}")
        if self.missing:
            gaps.append(f"Incomplete: {self.missing} gold retrieval items have no extraction")
        return gaps


def gold_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if _norm(r.get("retrieval_type")) in RETRIEVAL_LABELS]


def gold_items(rows: list[dict[str, str]]) -> list[ExtractionItem]:
    return [
        ExtractionItem(item_id=r["item_id"], source=r.get("source") or "", text=r["clean_text"])
        for r in gold_rows(rows)
        if (r.get("clean_text") or "").strip()
    ]


def evaluate_extraction(
    rows: list[dict[str, str]], predictions: dict[str, Extracted]
) -> ExtractionMetrics:
    allowed = allowed_values()
    categories = set(allowed["primary_category"])
    content_types = set(allowed["content_type"])
    cue_types = set(allowed["remembered_cue_types"])
    forgotten_values = set(allowed["forgotten_details"])
    breakdowns = set(allowed["breakdown_point"])

    scored = gold_rows(rows)
    top1 = Rate(0, 0)
    top2 = Rate(0, 0)
    content = Rate(0, 0)
    breakdown = Rate(0, 0)
    ns_cues = Rate(0, 0)
    ns_forgotten = Rate(0, 0)
    grounding = Rate(0, 0)
    first_pass = Rate(0, 0)
    strength = Rate(0, 0)
    cue_p, cue_r = Rate(0, 0), Rate(0, 0)
    fg_p, fg_r = Rate(0, 0), Rate(0, 0)
    by_type: dict[str, Rate] = {}
    confusions: Counter[tuple[str, str]] = Counter()
    extracted = 0

    for row in scored:
        ex = predictions.get(row["item_id"])
        if ex is None:
            continue
        extracted += 1
        insight = ex.insight
        grounding.total += 1
        grounding.hits += ex.quote_grounded
        first_pass.total += 1
        first_pass.hits += ex.first_pass_grounded

        gold_cat = _norm(row.get("primary_category"))
        if gold_cat in categories:
            predicted = insight.primary_category.value
            secondary = [c.value for c in insight.secondary_categories]
            hit = predicted == gold_cat
            top1.total += 1
            top1.hits += hit
            top2.total += 1
            top2.hits += hit or (bool(secondary) and secondary[0] == gold_cat)
            label = _norm(row.get("retrieval_type"))
            bucket = by_type.setdefault(label, Rate(0, 0))
            bucket.total += 1
            bucket.hits += hit
            if not hit:
                confusions[(gold_cat, predicted)] += 1

        gold_content = _norm(row.get("content_type"))
        if gold_content in content_types:
            content.total += 1
            content.hits += insight.content_type.value == gold_content

        gold_breakdown = _norm(row.get("breakdown_point"))
        if gold_breakdown in breakdowns:
            breakdown.total += 1
            breakdown.hits += insight.breakdown_point.value == gold_breakdown

        gold_cues = _split(row.get("remembered_cue_types"), cue_types)
        raw_cues = _norm(row.get("remembered_cue_types"))
        pred_cues = {c.cue_type.value for c in insight.remembered_cues}
        if not raw_cues:
            ns_cues.total += 1
            ns_cues.hits += not pred_cues
        _set_overlap(gold_cues, pred_cues, cue_p, cue_r)

        gold_forgotten = _split(row.get("forgotten_details"), forgotten_values)
        raw_forgotten = _norm(row.get("forgotten_details"))
        pred_forgotten = {f.value for f in insight.forgotten_details}
        if not raw_forgotten:
            ns_forgotten.total += 1
            ns_forgotten.hits += not pred_forgotten
        _set_overlap(gold_forgotten, pred_forgotten, fg_p, fg_r)

        gold_strength = (row.get("evidence_strength") or "").strip()
        if gold_strength.isdigit():
            strength.total += 1
            strength.hits += abs(insight.evidence_strength - int(gold_strength)) <= 1

    return ExtractionMetrics(
        scored=len(scored),
        extracted=extracted,
        category_top1=top1,
        category_top2=top2,
        content_type=content,
        breakdown=breakdown,
        not_stated=Rate(ns_cues.hits + ns_forgotten.hits, ns_cues.total + ns_forgotten.total),
        not_stated_cues=ns_cues,
        not_stated_forgotten=ns_forgotten,
        grounding=grounding,
        first_pass_grounding=first_pass,
        strength_within_one=strength,
        cue_precision=cue_p,
        cue_recall=cue_r,
        forgotten_precision=fg_p,
        forgotten_recall=fg_r,
        category_by_type=by_type,
        confusions=confusions,
    )


def render_extraction_metrics(
    metrics: ExtractionMetrics,
    *,
    prompt_version: str,
    model: str,
    large_model: str,
    run: ExtractionRun | None = None,
) -> str:
    lines = [
        "# Extraction evaluation",
        "",
        f"Prompt `{prompt_version}`, small model `{model}`, large model `{large_model}`.",
        f"Gold retrieval rows: {metrics.scored} (extracted: {metrics.extracted}).",
        "",
        "## Targets",
        "",
        _line("Quote grounding rate", metrics.grounding, GROUNDING_TARGET),
        _line("Primary category accuracy (top-1)", metrics.category_top1, CATEGORY_TOP1_TARGET),
        _line("Primary category accuracy (top-2)", metrics.category_top2, CATEGORY_TOP2_TARGET),
        _line("not_stated correctness", metrics.not_stated, NOT_STATED_TARGET),
        "",
        "## Detail",
        "",
        _line("Quotes grounded on the first pass", metrics.first_pass_grounding, None),
        _line("not_stated: no invented remembered cues", metrics.not_stated_cues, None),
        _line("not_stated: no invented forgotten details", metrics.not_stated_forgotten, None),
        _line("Content type accuracy", metrics.content_type, None),
        _line("Breakdown point accuracy", metrics.breakdown, None),
        _line("Evidence strength within ±1", metrics.strength_within_one, None),
        _line("Remembered cue types: precision", metrics.cue_precision, None),
        _line("Remembered cue types: recall", metrics.cue_recall, None),
        _line("Forgotten details: precision", metrics.forgotten_precision, None),
        _line("Forgotten details: recall", metrics.forgotten_recall, None),
    ]
    for label, rate in sorted(metrics.category_by_type.items()):
        lines.append(_line(f"Primary category accuracy on {label}", rate, None))
    if metrics.confusions:
        lines.extend(["", "## Most common category confusions (gold → predicted)", ""])
        for (gold, predicted), count in metrics.confusions.most_common(8):
            lines.append(f"- {gold} → {predicted}: {count}")
    if run is not None:
        lines.extend(
            [
                "",
                "## Run",
                "",
                f"- Escalated to the large model: {run.escalated}",
                f"- Quote retries: {run.quote_retries}",
                f"- Item errors: {len(run.errors)}",
            ]
        )
        if run.stopped:
            lines.append(f"- Stopped early: {run.stopped}")
    gaps = metrics.misses_targets()
    lines.extend(["", "## Result", ""])
    if gaps:
        lines.append("Below target:")
        lines.extend(f"- {gap}" for gap in gaps)
    else:
        lines.append("Extraction meets the Section 17.2 targets.")
    lines.append("")
    return "\n".join(lines)


PREDICTION_COLUMNS = (
    "item_id",
    "source",
    "gold_retrieval_type",
    "gold_category",
    "pred_category",
    "pred_secondary",
    "gold_content_type",
    "pred_content_type",
    "gold_cue_types",
    "pred_cues",
    "gold_forgotten",
    "pred_forgotten",
    "gold_breakdown",
    "pred_breakdown",
    "gold_strength",
    "pred_strength",
    "quote_grounded",
    "evidence_quote",
    "confidence",
    "model",
    "trying_to_find",
    "problem_statement",
)


def write_extraction_predictions(
    path: Path, rows: list[dict[str, str]], predictions: dict[str, Extracted]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREDICTION_COLUMNS)
        writer.writeheader()
        for row in gold_rows(rows):
            ex = predictions.get(row["item_id"])
            if ex is None:
                continue
            i = ex.insight
            writer.writerow(
                {
                    "item_id": row["item_id"],
                    "source": row.get("source", ""),
                    "gold_retrieval_type": _norm(row.get("retrieval_type")),
                    "gold_category": _norm(row.get("primary_category")),
                    "pred_category": i.primary_category.value,
                    "pred_secondary": "|".join(c.value for c in i.secondary_categories),
                    "gold_content_type": _norm(row.get("content_type")),
                    "pred_content_type": i.content_type.value,
                    "gold_cue_types": _norm(row.get("remembered_cue_types")),
                    "pred_cues": "; ".join(
                        f"{c.cue} ({c.cue_type.value})" for c in i.remembered_cues
                    ),
                    "gold_forgotten": _norm(row.get("forgotten_details")),
                    "pred_forgotten": "|".join(f.value for f in i.forgotten_details),
                    "gold_breakdown": _norm(row.get("breakdown_point")),
                    "pred_breakdown": i.breakdown_point.value,
                    "gold_strength": (row.get("evidence_strength") or "").strip(),
                    "pred_strength": i.evidence_strength,
                    "quote_grounded": ex.quote_grounded,
                    "evidence_quote": i.evidence_quote or "",
                    "confidence": i.confidence,
                    "model": ex.model,
                    "trying_to_find": i.trying_to_find,
                    "problem_statement": i.problem_statement,
                }
            )


def run_extraction_eval(
    path: Path,
    cfg: AppConfig,
    client: LLMClient,
    prompt: Prompt,
    *,
    model: str | None = None,
    use_batch_api: bool = False,
    batch_id: str | None = None,
    on_batch_submit: Callable[[str], None] | None = None,
) -> tuple[ExtractionMetrics, ExtractionRun, list[dict[str, str]]]:
    rows = read_sheet(path)
    run = extract_items(
        client,
        prompt,
        gold_items(rows),
        settings=cfg.settings.extraction,
        llm=cfg.settings.llm,
        small_model=model,
        workers=cfg.settings.llm.max_concurrency,
        use_batch_api=use_batch_api,
        batch_id=batch_id,
        on_batch_submit=on_batch_submit,
    )
    predictions = {item_id: ex for item_id, (_, ex) in run.found.items()}
    return evaluate_extraction(rows, predictions), run, rows


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _split(value: str | None, allowed: set[str]) -> set[str]:
    return {part.strip() for part in _norm(value).split("|") if part.strip() in allowed}


def _set_overlap(gold: set[str], predicted: set[str], precision: Rate, recall: Rate) -> None:
    precision.total += len(predicted)
    precision.hits += len(predicted & gold)
    recall.total += len(gold)
    recall.hits += len(predicted & gold)


def _fmt(rate: Rate) -> str:
    if rate.value is None:
        return "n/a"
    return f"{rate.value:.1%} ({rate.hits}/{rate.total})"


def _line(name: str, rate: Rate, target: float | None) -> str:
    if target is None:
        return f"- {name}: {_fmt(rate)}"
    mark = "met" if rate.value is not None and rate.value >= target else "open"
    if rate.value is None:
        mark = "not scored"
    return f"- {name}: {_fmt(rate)} (target ≥ {target:.0%}, {mark})"
