"""Score the gold sheet with Stages A and B, and optionally Stage C."""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from discovery.ai.embeddings import Encoder
from discovery.ai.funnel import WorkItem, classify_pending, preview_funnel
from discovery.ai.llm_client import LLMClient
from discovery.ai.prompts import Prompt, load_prompt
from discovery.ai.relevance import PROMPT_NAME, PROMPT_VERSION, Classified, topic_name
from discovery.config import AppConfig
from discovery.db import session_scope
from discovery.eval.gold_set import read_sheet
from discovery.eval.metrics import FunnelMetrics, evaluate_gold, render_metrics
from discovery.models.orm import RelevanceRow
from discovery.models.schemas import RetrievalType

AMBIGUOUS = "ambiguous"


def gold_items(rows: list[dict[str, str]]) -> list[WorkItem]:
    items: list[WorkItem] = []
    for row in rows:
        rating_raw = (row.get("rating") or "").strip()
        rating = int(rating_raw) if rating_raw.isdigit() else None
        items.append(
            WorkItem(
                item_id=row["item_id"],
                source=row.get("source") or "",
                text=row.get("clean_text") or "",
                rating=rating,
            )
        )
    return items


def stored_stage_c(
    session: Session,
    item_ids: set[str],
    *,
    prompt_id: str,
    model: str,
) -> dict[str, str]:
    if not item_ids:
        return {}
    rows = session.scalars(
        select(RelevanceRow).where(
            RelevanceRow.item_id.in_(item_ids),
            RelevanceRow.stage_reached == "C",
            RelevanceRow.prompt_version == prompt_id,
            RelevanceRow.model == model,
        )
    ).all()
    return {row.item_id: row.retrieval_type or RetrievalType.NOT_RETRIEVAL.value for row in rows}


def evaluate_sheet(
    path: Path,
    cfg: AppConfig,
    encoder: Encoder,
    *,
    factory: sessionmaker[Session] | None = None,
    client: LLMClient | None = None,
    model: str | None = None,
    workers: int = 2,
    predictions_out: Path | None = None,
) -> tuple[FunnelMetrics, str, list[str]]:
    """Return metrics, the markdown report, and any Stage C errors.

    Stored Stage C rows are reused. When `client` is set, missing candidates are classified,
    and `predictions_out` receives one CSV row per item classified in this call.
    """
    rows = read_sheet(path)
    items = gold_items(rows)
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION, cfg.prompts_dir)
    model_name = model or cfg.settings.llm.small_model
    stage_a, stage_b, candidates = preview_funnel(items, cfg, encoder)
    ambiguous = {
        row["item_id"]
        for row in rows
        if (row.get("retrieval_type") or "").strip().lower() == AMBIGUOUS
    }
    labels: dict[str, str] = {}
    if factory is not None:
        with session_scope(factory) as session:
            labels.update(
                stored_stage_c(session, candidates, prompt_id=prompt.id, model=model_name)
            )
    errors: list[str] = []
    if client is not None:
        pending = [
            item
            for item in items
            if item.item_id in candidates
            and item.item_id not in labels
            and item.item_id not in ambiguous
        ]
        classified, errors, stopped = classify_pending(
            client,
            prompt,
            pending,
            model_name,
            batching=cfg.settings.relevance,
            max_item_chars=cfg.settings.llm.max_input_chars,
            workers=workers,
        )
        if stopped:
            errors.append(stopped)
        for item, outcome in classified:
            labels[item.item_id] = outcome.result.retrieval_type.value
        if predictions_out is not None:
            write_predictions(predictions_out, rows, classified)
    labels = {item_id: label for item_id, label in labels.items() if item_id in candidates}
    metrics = evaluate_gold(rows, stage_a_ids=stage_a, stage_b_ids=stage_b, stage_c_labels=labels)
    text = render_metrics(
        metrics,
        tau=cfg.settings.relevance.semantic_margin_threshold,
        prompt_version=prompt.id,
        model=model_name,
    )
    return metrics, text, errors


PREDICTION_COLUMNS = (
    "item_id",
    "source",
    "gold",
    "predicted",
    "vague_memory_relevance",
    "excluded_topic",
    "excluded_topic_blocks_retrieval",
    "confidence",
    "rationale",
)


def write_predictions(
    path: Path,
    rows: list[dict[str, str]],
    classified: list[tuple[WorkItem, Classified]],
) -> None:
    gold = {row["item_id"]: (row.get("retrieval_type") or "").strip().lower() for row in rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREDICTION_COLUMNS)
        writer.writeheader()
        for item, outcome in sorted(classified, key=lambda pair: pair[0].item_id):
            result = outcome.result
            writer.writerow(
                {
                    "item_id": item.item_id,
                    "source": item.source,
                    "gold": gold.get(item.item_id, ""),
                    "predicted": result.retrieval_type.value,
                    "vague_memory_relevance": result.vague_memory_relevance,
                    "excluded_topic": topic_name(result.excluded_topic) or "",
                    "excluded_topic_blocks_retrieval": (
                        ""
                        if result.excluded_topic_blocks_retrieval is None
                        else result.excluded_topic_blocks_retrieval
                    ),
                    "confidence": result.confidence,
                    "rationale": result.rationale,
                }
            )


def save_report(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_prompt_for(cfg: AppConfig) -> Prompt:
    return load_prompt(PROMPT_NAME, PROMPT_VERSION, cfg.prompts_dir)
