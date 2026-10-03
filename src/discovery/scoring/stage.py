"""Score the latest opportunity areas and publish the run (Phase 6).

Counts are computed from the items in each area. Product leverage and research value come
from the large model, unless `--no-llm` asks for the heuristic. Scores are stored on the
cluster run's id, so they join the areas. The published-run pointer moves only after a
complete model scoring; a heuristic preview and a stopped run leave the previous pointer.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from discovery.ai.llm_client import (
    LLMBatchPending,
    LLMClient,
    LLMConfigError,
)
from discovery.ai.prompts import Prompt, load_prompt
from discovery.ai.synthesis import LLMCall, StepEstimate, estimate_step, run_calls
from discovery.config import AppConfig
from discovery.db import session_scope
from discovery.models.orm import (
    InsightRow,
    ItemRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    OpportunityScoreRow,
    PMOverrideRow,
    PublishedRunRow,
    RelevanceRow,
)
from discovery.models.schemas import AreaRubric, OpportunityScore, RunStatus, ScoreBand
from discovery.runs import StageRun, save_counts
from discovery.scoring.dimensions import (
    DIMENSIONS,
    DimensionScore,
    ScoredItem,
    adjusted_share,
    frequency_explanation,
    in_window,
    score_evidence_quality,
    score_frequency,
    score_severity,
    score_strategic_fit,
    window_start,
)
from discovery.scoring.ranker import (
    AreaRanking,
    apply_ranks,
    band_for,
    composite_explanation,
    emerging_label,
    normalize_weights,
    weighted_sum,
)
from discovery.scoring.rubric import (
    PROMPT_NAME,
    PROMPT_VERSION,
    heuristic_rubric,
    rubric_dimension,
    rubric_input,
)

log = logging.getLogger(__name__)

STAGE = "score"
OVERRIDE_FIELDS = ("product_leverage", "research_value")
# Expected output tokens for the cost estimate (the budget guard uses the max instead).
EST_RUBRIC_OUTPUT = 180


@dataclass
class ScoreReport:
    run_id: str
    areas: list[AreaRanking] = field(default_factory=list)
    sensitivity: dict[str, Any] = field(default_factory=dict)
    vague_items: int = 0
    excluded_outside_window: int = 0
    rubric_source: str = "heuristic"
    published: bool = False
    skipped_reason: str | None = None
    stopped_early: str | None = None
    prompt_id: str | None = None
    model: str | None = None
    estimate: StepEstimate | None = None
    batch_price_factor: float = 1.0
    written: bool = False

    def as_counts(self) -> dict[str, Any]:
        return {
            "areas": len(self.areas),
            "vague_items": self.vague_items,
            "excluded_outside_window": self.excluded_outside_window,
            "rubric_source": self.rubric_source,
            "published": self.published,
            "top3_stable": self.sensitivity.get("top3_stable"),
            "baseline_top3": self.sensitivity.get("baseline_top3", []),
            "sensitivity_changes": len(self.sensitivity.get("changes", [])),
            "low_evidence": sum(1 for area in self.areas if area.low_evidence),
            "prompt": self.prompt_id,
            "model": self.model,
            "stopped_early": self.stopped_early,
            "skipped_reason": self.skipped_reason,
        }


# --- loading -----------------------------------------------------------------


def load_items(session: Session) -> dict[str, ScoredItem]:
    """Every insight joined to its item and relevance row, keyed by item id."""
    rows = session.execute(
        select(
            InsightRow,
            ItemRow.primary_source_name,
            ItemRow.platform,
            ItemRow.rating,
            ItemRow.engagement,
            ItemRow.date,
            ItemRow.is_spam,
            RelevanceRow.retrieval_type,
            RelevanceRow.vague_memory_relevance,
        )
        .join(ItemRow, ItemRow.item_id == InsightRow.item_id)
        .outerjoin(RelevanceRow, RelevanceRow.item_id == InsightRow.item_id)
        .order_by(InsightRow.item_id)
    ).all()
    items = {}
    for ins, source, platform, rating, engagement, date, spam, rtype, relevance in rows:
        items[ins.item_id] = ScoredItem(
            item_id=ins.item_id,
            source=source,
            platform=platform,
            retrieval_type=rtype,
            vague_memory_relevance=relevance,
            frustration_intensity=ins.frustration_intensity,
            high_stakes=ins.high_stakes,
            evidence_strength=ins.evidence_strength,
            rating=rating,
            engagement=dict(engagement or {}),
            date=date,
            is_spam=bool(spam),
            problem_statement=ins.problem_statement or "",
            trying_to_find=ins.trying_to_find or "",
            breakdown_point=ins.breakdown_point,
            evidence_quote=ins.evidence_quote,
            quote_grounded=ins.quote_grounded,
        )
    return items


@dataclass
class AreaMembers:
    area_id: str
    name: str
    category: str
    problem_summary: str
    research_questions: list[Any]
    items: list[ScoredItem]
    representative_ids: list[str]
    excluded_outside_window: int


def load_areas(
    session: Session,
    run_id: str,
    items: Mapping[str, ScoredItem],
    *,
    start: datetime | None,
) -> tuple[list[AreaMembers], int]:
    """Active areas and their in-scope members. Returns areas and how many members were too old."""
    area_rows = session.scalars(
        select(OpportunityAreaRow)
        .where(OpportunityAreaRow.run_id == run_id, OpportunityAreaRow.status == "active")
        .order_by(OpportunityAreaRow.area_id)
    ).all()
    evidence = session.execute(
        select(
            OpportunityEvidenceRow.area_id,
            OpportunityEvidenceRow.item_id,
            OpportunityEvidenceRow.is_representative,
            OpportunityEvidenceRow.rank,
        ).where(OpportunityEvidenceRow.run_id == run_id)
    ).all()
    members: dict[str, list[tuple[str, bool, int | None]]] = defaultdict(list)
    for area_id, item_id, representative, rank in evidence:
        members[area_id].append((item_id, bool(representative), rank))

    areas: list[AreaMembers] = []
    excluded = 0
    for area in area_rows:
        kept: list[ScoredItem] = []
        quotes: list[tuple[int, str]] = []
        dropped = 0
        for item_id, representative, rank in members.get(area.area_id, []):
            item = items.get(item_id)
            if item is None or item.is_spam:
                continue
            if not in_window(item, start):
                dropped += 1
                continue
            kept.append(item)
            if representative:
                quotes.append((rank if rank is not None else 10**6, item_id))
        excluded += dropped
        kept.sort(key=lambda it: it.item_id)
        areas.append(
            AreaMembers(
                area_id=area.area_id,
                name=area.name,
                category=area.category,
                problem_summary=area.problem_summary or "",
                research_questions=list(area.research_questions or []),
                items=kept,
                representative_ids=[item_id for _, item_id in sorted(quotes)],
                excluded_outside_window=dropped,
            )
        )
    return areas, excluded


def load_overrides(
    session: Session, area_ids: set[str]
) -> dict[tuple[str, str], tuple[float, str | None]]:
    """Latest PM value for product leverage and research value, with the note."""
    if not area_ids:
        return {}
    rows = session.scalars(
        select(PMOverrideRow)
        .where(
            PMOverrideRow.target_type == "area",
            PMOverrideRow.target_id.in_(area_ids),
            PMOverrideRow.field.in_(OVERRIDE_FIELDS),
        )
        .order_by(PMOverrideRow.override_id)
    ).all()
    latest: dict[tuple[str, str], tuple[float, str | None]] = {}
    for row in rows:
        try:
            value = float(row.override_value)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= 5:
            latest[(row.target_id, row.field)] = (value, row.note)
    return latest


def corpus_vague(items: Mapping[str, ScoredItem], start: datetime | None) -> list[ScoredItem]:
    """Every in-scope vague-retrieval item, clustered or not. This is the frequency denominator."""
    found = [
        it
        for it in items.values()
        if it.retrieval_type == "vague_memory_retrieval" and in_window(it, start)
    ]
    return sorted(found, key=lambda it: it.item_id)


# --- scoring ------------------------------------------------------------------


def _override_value(
    overrides: Mapping[tuple[str, str], tuple[float, str | None]], area_id: str, field_name: str
) -> float | None:
    stored = overrides.get((area_id, field_name))
    return None if stored is None else stored[0]


def score_loaded_areas(
    areas: Sequence[AreaMembers],
    vague: Sequence[ScoredItem],
    rubrics: Mapping[str, AreaRubric],
    overrides: Mapping[tuple[str, str], tuple[float, str | None]],
    cfg: AppConfig,
    *,
    rubric_source: str,
) -> list[AreaRanking]:
    """Turn loaded areas into ranked scores. Areas with no in-scope items are left out."""
    settings = cfg.settings.scoring
    weights = normalize_weights(cfg.scoring.weights.model_dump())
    present = [area for area in areas if area.items]
    shares = []
    share_detail = []
    for area in present:
        share, detail = adjusted_share(
            area.items,
            vague,
            day_cap=settings.day_contribution_cap,
            engagement_cap=settings.engagement_cap,
            engagement_alpha=settings.engagement_alpha,
            raw_weight=settings.frequency_raw_weight,
        )
        shares.append(share)
        share_detail.append(detail)
    freq_scores, mapping = score_frequency(
        shares,
        min_areas=settings.quantile_min_areas,
        thresholds=settings.frequency_fixed_thresholds,
    )

    ranked: list[AreaRanking] = []
    for area, freq, detail in zip(present, freq_scores, share_detail, strict=True):
        detail = {**detail, "mapping": mapping}
        frequency = DimensionScore(freq, frequency_explanation(freq, detail, mapping), detail)
        severity = score_severity(area.items)
        fit = score_strategic_fit(area.items)
        quality = score_evidence_quality(area.items)
        rubric = rubrics[area.area_id]
        leverage = rubric_dimension(
            rubric.product_leverage,
            label="Product leverage",
            override=_override_value(overrides, area.area_id, "product_leverage"),
            source=rubric_source,
        )
        research = rubric_dimension(
            rubric.research_value,
            label="Research value",
            override=_override_value(overrides, area.area_id, "research_value"),
            source=rubric_source,
        )
        dimensions = {
            "frequency": frequency,
            "severity": severity,
            "strategic_fit": fit,
            "evidence_quality": quality,
            "product_leverage": leverage,
            "research_value": research,
        }
        effective = {key: dim.score for key, dim in dimensions.items()}
        ai_scores = dict(effective)
        ai_scores["product_leverage"] = float(leverage.detail["ai_score"])
        ai_scores["research_value"] = float(research.detail["ai_score"])
        composite = weighted_sum(effective, weights)
        composite_ai = weighted_sum(ai_scores, weights)
        vague_n = detail["vague_items_before_day_cap"]
        reason = emerging_label(len(area.items), quality.score, settings)
        ranked.append(
            AreaRanking(
                area_id=area.area_id,
                name=area.name,
                category=area.category,
                item_count=len(area.items),
                vague_items=vague_n,
                dimensions=dimensions,
                composite=composite,
                composite_ai=composite_ai,
                band=band_for(composite, cfg.scoring.bands),
                low_evidence_reason=reason,
                weights=weights,
            )
        )
    apply_ranks(ranked, weights)
    return ranked


# --- persistence --------------------------------------------------------------


def _inputs(area: AreaRanking) -> dict[str, Any]:
    explanations = {key: area.dimensions[key].explanation for key in DIMENSIONS}
    explanations["composite"] = composite_explanation(area.scores(), area.weights, area.composite)
    if area.low_evidence_reason:
        explanations["guardrail"] = (
            f"{area.low_evidence_reason} Ranked below every area with adequate evidence."
        )
    return {
        "items": area.item_count,
        "vague_items": area.vague_items,
        "rank": area.rank,
        "rank_ai": area.rank_ai,
        "composite_ai": area.composite_ai,
        "band": area.band,
        "low_evidence_reason": area.low_evidence_reason,
        "explanations": explanations,
        "dimensions": {key: area.dimensions[key].detail for key in DIMENSIONS},
        "sensitivity": area.sensitivity,
    }


def write_scores(
    factory: sessionmaker[Session],
    report: ScoreReport,
    *,
    publish: bool,
) -> None:
    """Replace this run's scores, and move the published-run pointer, in one transaction."""
    now = datetime.now(UTC)
    with session_scope(factory) as session:
        session.execute(
            delete(OpportunityScoreRow).where(OpportunityScoreRow.run_id == report.run_id)
        )
        for area in report.areas:
            payload = OpportunityScore(
                area_id=area.area_id,
                run_id=report.run_id,
                frequency=area.dimensions["frequency"].score,
                severity=area.dimensions["severity"].score,
                strategic_fit=area.dimensions["strategic_fit"].score,
                evidence_quality=area.dimensions["evidence_quality"].score,
                product_leverage=area.dimensions["product_leverage"].score,
                research_value=area.dimensions["research_value"].score,
                composite=area.composite,
                band=ScoreBand(area.band),
                inputs=_inputs(area),
                weights=area.weights,
                low_evidence_flag=area.low_evidence,
            )
            session.add(
                OpportunityScoreRow(
                    area_id=payload.area_id,
                    run_id=payload.run_id,
                    frequency=payload.frequency,
                    severity=payload.severity,
                    strategic_fit=payload.strategic_fit,
                    evidence_quality=payload.evidence_quality,
                    product_leverage=payload.product_leverage,
                    research_value=payload.research_value,
                    composite=payload.composite,
                    band=payload.band.value,
                    inputs=payload.inputs,
                    weights=payload.weights,
                    low_evidence_flag=payload.low_evidence_flag,
                )
            )
        if publish:
            row = session.get(PublishedRunRow, 1)
            if row is None:
                session.add(PublishedRunRow(id=1, published_run_id=report.run_id, published_at=now))
            else:
                row.published_run_id = report.run_id
                row.published_at = now
        report.written = True
        report.published = publish


# --- the stage ---------------------------------------------------------------


def run_scoring(
    factory: sessionmaker[Session],
    cfg: AppConfig,
    run: StageRun,
    *,
    client: LLMClient | None = None,
    client_factory: Callable[[], LLMClient] | None = None,
    use_llm: bool = True,
    use_batch_api: bool | None = None,
    batch_id: str | None = None,
    estimate_only: bool = False,
    include_outside_window: bool = False,
    as_of: datetime | None = None,
    rubrics: Mapping[str, AreaRubric] | None = None,
) -> ScoreReport:
    """Score `run.run_id`, which must be a cluster run. Writes scores on success."""
    settings = cfg.settings.scoring
    llm = cfg.settings.llm
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION, cfg.prompts_dir)
    model = settings.resolve_model(llm)
    report = ScoreReport(run_id=run.run_id, prompt_id=prompt.id, model=model)
    start = (
        None
        if include_outside_window
        else window_start(as_of or datetime.now(UTC), settings.analysis_window_days)
    )

    with session_scope(factory) as session:
        items = load_items(session)
        areas, report.excluded_outside_window = load_areas(session, run.run_id, items, start=start)
        overrides = load_overrides(session, {area.area_id for area in areas})
    vague = corpus_vague(items, start)
    report.vague_items = len(vague)

    if not areas:
        report.skipped_reason = (
            f"No active opportunity areas on run {run.run_id}. Run `discovery cluster` first."
        )
        run.skip(report.skipped_reason)
        run.counts.update(report.as_counts())
        return report
    if not vague:
        report.skipped_reason = (
            "No vague-retrieval items in scope. Scoring skipped; the published run is unchanged."
        )
        run.skip(report.skipped_reason)
        run.counts.update(report.as_counts())
        return report

    scorable = [area for area in areas if area.items]
    if not scorable:
        report.skipped_reason = (
            "Every area's items are outside the analysis window."
            if report.excluded_outside_window
            else "No in-scope items in the active areas."
        )
        run.skip(report.skipped_reason)
        run.counts.update(report.as_counts())
        return report

    briefs = {
        area.area_id: rubric_input(
            name=area.name,
            category=area.category,
            summary=area.problem_summary,
            questions=area.research_questions,
            items=area.items,
            representative_ids=area.representative_ids,
            quote_limit=settings.rubric_items,
        )
        for area in scorable
    }
    if estimate_only:
        report.estimate = estimate_step(
            prompt,
            list(briefs.values()),
            model,
            EST_RUBRIC_OUTPUT,
            settings.rubric_max_output_tokens,
            llm,
        )
        if llm.use_batch_api_for_backfill and len(briefs) >= settings.batch_api_min_calls:
            report.batch_price_factor = llm.batch_price_factor
        run.skip("cost estimate only")
        run.counts.update(report.as_counts())
        return report

    resolved, source, stopped = _resolve_rubrics(
        scorable,
        briefs,
        prompt,
        model,
        cfg,
        run,
        factory,
        client=client,
        client_factory=client_factory,
        use_llm=use_llm,
        use_batch_api=use_batch_api,
        batch_id=batch_id,
        provided=rubrics,
    )
    report.rubric_source = source
    if stopped:
        report.stopped_early = stopped
        run.error(stopped)
        run.counts.update(report.as_counts())
        return report
    missing = [area.area_id for area in scorable if area.area_id not in resolved]
    if missing:
        report.stopped_early = f"No rubric for {', '.join(missing)}"
        run.error(report.stopped_early)
        run.counts.update(report.as_counts())
        return report

    report.areas = score_loaded_areas(
        scorable, vague, resolved, overrides, cfg, rubric_source=source
    )
    report.sensitivity = report.areas[0].sensitivity if report.areas else {}
    # A heuristic preview is repeatable and useful, and it is not a publishable judgment.
    publish = source == "llm" and not estimate_only
    write_scores(factory, report, publish=publish)
    if publish:
        run.final_status = RunStatus.PUBLISHED
    run.counts.update(report.as_counts())
    return report


def _resolve_rubrics(
    areas: Sequence[AreaMembers],
    briefs: Mapping[str, str],
    prompt: Prompt,
    model: str,
    cfg: AppConfig,
    run: StageRun,
    factory: sessionmaker[Session],
    *,
    client: LLMClient | None,
    client_factory: Callable[[], LLMClient] | None,
    use_llm: bool,
    use_batch_api: bool | None,
    batch_id: str | None,
    provided: Mapping[str, AreaRubric] | None,
) -> tuple[dict[str, AreaRubric], str, str | None]:
    """(rubrics, source, stop reason). Source is llm, heuristic, or provided."""
    if provided is not None:
        return dict(provided), "llm", None
    if not use_llm:
        return (
            {
                area.area_id: heuristic_rubric(
                    category=area.category,
                    name=area.name,
                    summary=area.problem_summary,
                    vague_items=sum(
                        1 for it in area.items if it.retrieval_type == "vague_memory_retrieval"
                    ),
                    item_count=len(area.items),
                    question_count=len(area.research_questions),
                )
                for area in areas
            },
            "heuristic",
            None,
        )

    if client is None and client_factory is not None:
        client = client_factory()
    if client is None:
        raise LLMConfigError("Scoring with the rubric needs an LLM client.")
    settings = cfg.settings.scoring
    llm = cfg.settings.llm
    batch = llm.use_batch_api_for_backfill if use_batch_api is None else use_batch_api
    if len(areas) < settings.batch_api_min_calls:
        batch = False
    calls = [
        LLMCall(area.area_id, briefs[area.area_id], model, settings.rubric_max_output_tokens)
        for area in areas
    ]

    def on_submit(new_id: str) -> None:
        run.counts["batch_id"] = new_id
        save_counts(factory, run)

    try:
        result = run_calls(
            client,
            prompt,
            calls,
            AreaRubric,
            use_batch_api=batch and client.provider == "anthropic",
            workers=llm.max_concurrency,
            poll_seconds=settings.batch_poll_seconds,
            max_wait_seconds=settings.batch_max_wait_minutes * 60,
            batch_id=batch_id,
            on_submit=on_submit,
        )
    except LLMBatchPending as exc:
        run.counts["batch_id"] = exc.batch_id
        return {}, "llm", str(exc)
    finally:
        usage = client.usage
        run.add_llm_usage(usage.total_tokens, usage.cost_usd)
        run.counts["llm_requests"] = usage.requests
        run.counts["llm_cache_hits"] = usage.cache_hits
    rubrics: dict[str, AreaRubric] = {}
    for key, value in result.values.items():
        if isinstance(value, AreaRubric):
            rubrics[key] = value
    for key, message in sorted(result.errors.items()):
        run.error(f"rubric {key}: {message}")
    if result.stopped:
        return rubrics, "llm", result.stopped
    if result.errors:
        failed = ", ".join(sorted(result.errors))
        return rubrics, "llm", f"Rubric failed for {failed}"
    return rubrics, "llm", None


def record_score_override(
    factory: sessionmaker[Session],
    area_id: str,
    field_name: str,
    value: float,
    *,
    note: str | None = None,
) -> tuple[int, str]:
    """Store a PM score for product leverage or research value. Returns (override id, run id)."""
    from discovery.ai.cluster_stage import latest_cluster_run

    if field_name not in OVERRIDE_FIELDS:
        raise ValueError(f"Unknown score field {field_name!r}. Use {', '.join(OVERRIDE_FIELDS)}.")
    if not 1 <= value <= 5:
        raise ValueError(f"{field_name} must be between 1 and 5 (got {value}).")
    with session_scope(factory) as session:
        run_id = latest_cluster_run(session, labeled_only=False)
        if run_id is None:
            raise ValueError("No cluster run yet. Run `discovery cluster` first.")
        area = session.get(OpportunityAreaRow, (area_id, run_id))
        if area is None:
            raise ValueError(f"Area {area_id} is not in the latest cluster run {run_id}.")
        existing = session.scalars(
            select(OpportunityScoreRow).where(
                OpportunityScoreRow.run_id == run_id,
                OpportunityScoreRow.area_id == area_id,
            )
        ).first()
        ai_value = None
        if existing is not None:
            detail = (existing.inputs or {}).get("dimensions", {}).get(field_name, {})
            ai_value = detail.get("ai_score", getattr(existing, field_name))
        row = PMOverrideRow(
            target_type="area",
            target_id=area_id,
            field=field_name,
            ai_value=ai_value,
            override_value=round(float(value), 2),
            note=note,
        )
        session.add(row)
        session.flush()
        return row.override_id, run_id


# --- report -------------------------------------------------------------------


def render_score_report(report: ScoreReport, weights: Mapping[str, float]) -> str:
    """Ranked list, one explanation block per area, and the weight-sensitivity result."""
    lines = [
        "# Opportunity scores",
        "",
        f"Run: `{report.run_id}`. Rubric: `{report.prompt_id}` on `{report.model}` "
        f"({report.rubric_source}).",
        f"Vague-retrieval items in the denominator: {report.vague_items}. "
        f"Items outside the analysis window, dropped from areas: {report.excluded_outside_window}.",
        "",
        "Weights (decision D8, signed off 2026-10-02): "
        + ", ".join(f"{key} {weights[key]:.2f}" for key in DIMENSIONS)
        + ".",
        "",
    ]
    if report.skipped_reason:
        lines.append(report.skipped_reason)
        return "\n".join(lines) + "\n"
    if report.estimate is not None:
        est = report.estimate
        factor = report.batch_price_factor
        lines.append(
            f"Rubric: {est.calls} calls, ~{est.input_tokens:,} input + {est.output_tokens:,} "
            f"output tokens, ~${est.usd:.3f} with normal calls "
            f"(~${est.usd * factor:.3f} with Message Batches)."
        )
        lines.append(
            f"The budget guard reserves up to ${est.worst_case_usd:.3f} "
            f"(~${est.worst_case_usd * factor:.3f} with batches) before sending."
        )
        return "\n".join(lines) + "\n"
    sensitivity = report.sensitivity
    stable = sensitivity.get("top3_stable")
    top = ", ".join(sensitivity.get("baseline_top3") or []) or "none"
    lines.append(
        f"Top 3: {top}. "
        + (
            "The top 3 does not change when any weight moves by 0.05."
            if stable
            else "The top 3 changes under at least one ±0.05 weight change; see below."
        )
    )
    if sensitivity.get("overrides_change_top3"):
        ai = ", ".join(sensitivity.get("baseline_top3_ai") or [])
        lines.append(f"Without PM overrides the top 3 would be: {ai}.")
    lines.extend(["", "## Ranking", ""])
    lines.append("| Rank | Area | Composite | Band | Items | Vague | Flag |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for area in sorted(report.areas, key=lambda a: a.rank):
        flag = "low evidence" if area.low_evidence else ""
        lines.append(
            f"| {area.rank} | {area.name} | {area.composite:.2f} | {area.band} | "
            f"{area.item_count} | {area.vague_items} | {flag} |"
        )
    lines.extend(["", "## Why each area ranks here", ""])
    for area in sorted(report.areas, key=lambda a: a.rank):
        lines.append(f"### {area.rank}. {area.name}")
        lines.append("")
        lines.append(f"`{area.area_id}` · {area.category} · {area.band}")
        if area.low_evidence_reason:
            lines.append("")
            lines.append(
                area.low_evidence_reason + " It is not ranked above a better-evidenced area."
            )
        lines.append("")
        for key in DIMENSIONS:
            lines.append(f"- {area.dimensions[key].explanation}")
        lines.append(f"- {composite_explanation(area.scores(), area.weights, area.composite)}")
        if area.composite != area.composite_ai:
            lines.append(
                f"- AI-only composite {area.composite_ai:.2f} (rank {area.rank_ai}), "
                "before the PM override."
            )
        lines.append("")
    changes = sensitivity.get("changes") or []
    lines.extend(["## Weight sensitivity", ""])
    if not changes:
        lines.append(
            f"Checked {sensitivity.get('scenarios_checked', 0)} scenarios "
            "(each weight ±0.05, then re-normalized). The top 3 stayed the same."
        )
    else:
        lines.append("Scenarios where the ordered top 3 changed:")
        lines.append("")
        for change in changes:
            sign = "+" if change["delta"] > 0 else ""
            top3 = ", ".join(change["top3"])
            lines.append(f"- {change['dimension']} {sign}{change['delta']:.2f} → {top3}")
    if not report.published:
        lines.extend(
            [
                "",
                "This run was not published. The dashboard keeps the previous published run.",
            ]
        )
    lines.append("")
    return "\n".join(lines)
