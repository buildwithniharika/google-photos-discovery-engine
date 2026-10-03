"""`discovery` CLI. The same commands run locally and in GitHub Actions."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import typer
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from discovery.config import AppConfig, load_config
from discovery.db import init_db, make_engine, make_session_factory, redact_url, session_scope
from discovery.models.orm import PipelineRunRow
from discovery.models.schemas import RelevanceResult, RunStatus, SourceName
from discovery.runs import (
    llm_spend_since,
    new_run_id,
    running_stages,
    save_counts,
    track_stage,
)

app = typer.Typer(
    name="discovery",
    help="AI-powered discovery engine for vaguely remembered photo retrieval.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

log = logging.getLogger("discovery")

RunIdOption = typer.Option(None, "--run-id", help="Group stages under one run id.")


class SourceChoice(StrEnum):
    all = "all"
    play_store = "play_store"
    app_store = "app_store"
    google_sheet = "google_sheet"
    google_community = "google_community"


class ExportFormat(StrEnum):
    csv = "csv"
    sheets = "sheets"
    pdf = "pdf"


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpx2", "httpcore", "groq", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@lru_cache(maxsize=1)
def _context() -> tuple[AppConfig, sessionmaker[Session]]:
    try:
        cfg = load_config()
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.secho(f"Configuration error:\n{exc}", fg="red", err=True)
        raise typer.Exit(2) from exc
    engine = make_engine(cfg.database_url)
    init_db(engine)
    return cfg, make_session_factory(engine)


LLM_STAGES = {"classify", "eval", "extract", "eval_extract", "llm_check", "cluster", "score"}


def _llm_client(
    cfg: AppConfig, factory: sessionmaker[Session], settings=None, *, run_id: str | None = None
):
    """Build the LLM client with the spend already recorded toward llm.project_budget_usd.
    Refuses while another LLM run is in progress (`run_id` is this command's own run)."""
    from discovery.ai.llm_client import LLMClient, LLMConfigError

    settings = settings or cfg.settings.llm
    if settings.block_concurrent_runs_hours is not None:
        others = running_stages(
            factory,
            LLM_STAGES,
            within_hours=settings.block_concurrent_runs_hours,
            exclude_run_id=run_id,
        )
        if others:
            other_id, stage, started = others[0]
            raise LLMConfigError(
                f"Another LLM run is in progress: '{stage}' run {other_id}, started "
                f"{started:%Y-%m-%d %H:%M} UTC. Its spend is not recorded until it ends, so a "
                "second run could cross the budget. Wait for it to finish (`discovery runs`). "
                "If it died, it stops blocking after llm.block_concurrent_runs_hours."
            )
    spent = llm_spend_since(factory, settings.budget_since)
    if settings.project_budget_usd is not None:
        log.info(
            "LLM spend so far: $%.4f of the $%.2f project budget",
            spent,
            settings.project_budget_usd,
        )
    return LLMClient(settings, factory, api_key=cfg.llm_api_key, spent_before_usd=spent)


def _stub(stage: str, phase: int, run_id: str | None, **params: object) -> str:
    """Placeholder stage: validates config, records a `skipped` row in pipeline_runs."""
    _, factory = _context()
    run_id = run_id or new_run_id()
    with track_stage(factory, run_id, stage) as run:
        run.counts["params"] = {k: str(v) for k, v in params.items() if v is not None}
        run.skip(f"not implemented yet (Phase {phase})")
    typer.echo(f"[{stage}] stub: implemented in Phase {phase}. Run {run_id} recorded as skipped.")
    return run_id


# --- pipeline stages ---------------------------------------------------------


class QualityFormat(StrEnum):
    markdown = "markdown"
    json = "json"


@app.command()
def ingest(
    source: SourceChoice = typer.Option(SourceChoice.all, "--source", "-s"),
    since: str | None = typer.Option(
        None,
        help="Only items newer than this date (YYYY-MM-DD), or 'last' for the newest stored "
        "item per source minus the overlap window.",
    ),
    limit: int | None = typer.Option(None, min=1, help="Max items per source."),
    run_id: str | None = RunIdOption,
) -> None:
    """Fetch raw data from the four sources into raw_items and items."""
    from discovery.ingest.runner import ALL_SOURCES, parse_since, run_ingest

    cfg, factory = _context()
    if not cfg.author_hash_salt:
        typer.secho(
            "AUTHOR_HASH_SALT is not set. Add a long random string to .env (local) or the "
            "GitHub Actions secrets; it is used to hash author names.",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)
    try:
        parse_since(since)
    except ValueError as exc:
        raise typer.BadParameter(f"--since: {exc}") from exc
    run_id = run_id or new_run_id()
    sources = ALL_SOURCES if source == SourceChoice.all else (SourceName(source.value),)
    from discovery.db import PipelineBusy, pipeline_lock

    try:
        with pipeline_lock(_engine(factory), run_id):
            outcomes = run_ingest(
                cfg, factory, run_id, sources, since=since, limit=limit, salt=cfg.author_hash_salt
            )
    except PipelineBusy as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    for o in outcomes:
        c = o.counts
        typer.echo(
            f"[{o.source.value}] {o.status.value}: fetched={c.get('fetched', 0)} "
            f"new={c.get('items_new', 0)} updated={c.get('items_updated', 0)} "
            f"unchanged={c.get('items_unchanged', 0)} skipped={c.get('skipped', {})}"
        )
    typer.echo(f"Run id: {run_id}")
    if all(o.status == RunStatus.FAILED for o in outcomes):
        raise typer.Exit(1)


@app.command()
def quality(
    format: QualityFormat = typer.Option(QualityFormat.markdown, "--format", "-f"),
    out: Path | None = typer.Option(None, dir_okay=False, help="Also write the report here."),
) -> None:
    """Data Quality summary: counts per source/platform, date ranges, missing fields."""
    from discovery.quality import quality_report, render_json, render_markdown

    _, factory = _context()
    report = quality_report(factory)
    text = render_json(report) if format == QualityFormat.json else render_markdown(report)
    typer.echo(text, nl=False)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")


@app.command()
def prep(
    run_id: str | None = RunIdOption,
    review_out: Path | None = typer.Option(
        None,
        "--review-out",
        dir_okay=False,
        help="Write a sample of dedup merges for the manual correctness check.",
    ),
    review_limit: int = typer.Option(50, min=1, help="How many merges to write with --review-out."),
) -> None:
    """Clean text, redact PII, and deduplicate. original_text is never modified."""
    from discovery.prep.runner import render_prep_report, run_prep, write_merge_sample

    # run-all calls this as a plain function, so Typer's Option defaults may arrive unparsed.
    if not isinstance(review_out, Path | str):
        review_out = None
    if not isinstance(review_limit, int):
        review_limit = 50

    cfg, factory = _context()
    run_id = run_id or new_run_id()
    with track_stage(factory, run_id, "prep") as run:
        report = run_prep(cfg, factory, run, salt=cfg.author_hash_salt)
    typer.echo(render_prep_report(report))
    if review_out is not None:
        path = Path(review_out)
        written = write_merge_sample(path, report.merges, review_limit)
        typer.echo(f"Wrote {written} dedup merges to {path}")
    typer.echo(f"Run id: {run_id}")
    if report.pii_remaining:
        raise typer.Exit(1)


@app.command()
def classify(
    run_id: str | None = RunIdOption,
    limit: int | None = typer.Option(
        None, min=1, help="Classify at most this many eligible items."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-run Stage C even when a matching row exists."
    ),
    report: Path = typer.Option(
        Path("eval/funnel_report.md"),
        "--report",
        dir_okay=False,
        help="Where to write the funnel counts.",
    ),
) -> None:
    """Relevance funnel: keyword prefilter, semantic filter, LLM classifier."""
    from discovery.ai.funnel import render_funnel_report, run_funnel
    from discovery.ai.llm_client import LLMClient, LLMConfigError, LLMError
    from discovery.eval.relevance_eval import save_report

    cfg, factory = _context()
    run_id = run_id or new_run_id()

    def client_factory() -> LLMClient:
        return _llm_client(cfg, factory, run_id=run_id)

    try:
        with track_stage(factory, run_id, "classify") as run:
            funnel_report = run_funnel(
                factory,
                cfg,
                run,
                client_factory=client_factory,
                limit=limit,
                force=force,
            )
    except (LLMConfigError, LLMError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    text = render_funnel_report(funnel_report, tau=cfg.settings.relevance.semantic_margin_threshold)
    typer.echo(text)
    if funnel_report.eligible:
        save_report(report, text)
        typer.echo(f"Wrote {report}")
    typer.echo(f"Run id: {run_id}")
    if funnel_report.stopped_early:
        raise typer.Exit(1)


BatchApiOption = typer.Option(
    None,
    "--batch-api/--no-batch-api",
    help="Use the Anthropic Message Batches API (50% off, asynchronous). Default: on for "
    "backfills of extraction.batch_api_min_items or more (llm.use_batch_api_for_backfill).",
)
BatchIdOption = typer.Option(
    None, "--batch-id", help="Collect an earlier Message Batch instead of submitting a new one."
)


@app.command()
def extract(
    run_id: str | None = RunIdOption,
    limit: int | None = typer.Option(None, min=1, help="Extract at most this many items."),
    force: bool = typer.Option(
        False, "--force", help="Re-extract items that already have a row for this prompt."
    ),
    batch_api: bool | None = BatchApiOption,
    batch_id: str | None = BatchIdOption,
    estimate: bool = typer.Option(
        False, "--estimate", help="Print the pending count and estimated cost; make no calls."
    ),
    report: Path = typer.Option(
        Path("eval/extraction_report.md"), "--report", dir_okay=False, help="Run summary."
    ),
    queue: Path = typer.Option(
        Path("eval/extraction_review_queue.csv"),
        "--queue",
        dir_okay=False,
        help="Low-confidence and ungrounded-quote queue for PM review.",
    ),
) -> None:
    """Extract structured insights from retrieval items."""
    from discovery.ai.insight_stage import (
        render_extraction_report,
        run_extraction,
        write_review_queue,
    )
    from discovery.ai.llm_client import LLMClient, LLMConfigError, LLMError
    from discovery.eval.relevance_eval import save_report

    cfg, factory = _context()
    run_id = run_id or new_run_id()

    def client_factory() -> LLMClient:
        return _llm_client(cfg, factory, run_id=run_id)

    try:
        with track_stage(factory, run_id, "extract") as run:
            result = run_extraction(
                factory,
                cfg,
                run,
                client_factory=client_factory,
                limit=limit,
                force=force,
                use_batch_api=batch_api,
                batch_id=batch_id,
                estimate_only=estimate,
            )
            if estimate:
                run.skip("cost estimate only")
    except (LLMConfigError, LLMError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    text = render_extraction_report(result)
    typer.echo(text)
    if not estimate and result.in_scope:
        save_report(report, text)
        typer.echo(f"Wrote {report}")
        queued = write_review_queue(
            queue, factory, below=cfg.settings.extraction.review_below_confidence
        )
        typer.echo(f"Wrote {queued} items to {queue}")
    typer.echo(f"Run id: {run_id}")
    if result.stopped_early:
        raise typer.Exit(1)


@app.command("extract-review")
def extract_review(
    out: Path = typer.Option(Path("eval/extraction_review_sample.csv"), "--out", dir_okay=False),
    size: int = typer.Option(50, min=1, help="How many insights to sample."),
    seed: int = typer.Option(0, help="Sample seed. The same seed redraws the same sheet."),
) -> None:
    """Gate G3 sheet: sampled insights with the user's text next to each extracted field."""
    from discovery.ai.insight_stage import write_review_sample

    _, factory = _context()
    written = write_review_sample(out, factory, size=size, seed=seed)
    if not written:
        typer.secho("No insights yet. Run `discovery extract` first.", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"Wrote {written} insights to {out}")


@app.command()
def cluster(
    run_id: str | None = RunIdOption,
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Preview without LLM calls: heuristic cluster names, no summaries or questions.",
    ),
    estimate: bool = typer.Option(
        False, "--estimate", help="Cluster, print the LLM cost estimate, write nothing."
    ),
    sweep: bool = typer.Option(
        False, "--sweep", help="Try a grid of UMAP/HDBSCAN settings and print the results."
    ),
    batch_api: bool | None = typer.Option(
        None,
        "--batch-api/--no-batch-api",
        help="Send label and synthesis calls through the Message Batches API (50% off). "
        "Default: on with llm.use_batch_api_for_backfill, from clustering.batch_api_min_calls.",
    ),
    batch_id: str | None = BatchIdOption,
    report: Path = typer.Option(
        Path("eval/opportunity_areas.md"), "--report", dir_okay=False, help="Draft area report."
    ),
    review: Path = typer.Option(
        Path("eval/opportunity_review.csv"),
        "--review",
        dir_okay=False,
        help="Gate G4 review sheet (one row per area).",
    ),
) -> None:
    """Embed, cluster, label, and synthesize opportunity areas."""
    from discovery.ai.cluster_stage import (
        load_evidence,
        render_cluster_report,
        run_clustering,
        write_review_sheet,
    )
    from discovery.ai.llm_client import LLMClient, LLMConfigError, LLMError
    from discovery.eval.relevance_eval import save_report

    cfg, factory = _context()
    if sweep:
        from discovery.ai.clustering import render_sweep
        from discovery.ai.clustering import sweep as run_sweep
        from discovery.ai.embeddings import SentenceEncoder, insight_text

        settings = cfg.settings.clustering
        with session_scope(factory) as session:
            items, _ = load_evidence(session, only_useful=settings.only_useful_for_discovery)
        if not items:
            typer.secho("No insights yet. Run `discovery extract` first.", fg="red", err=True)
            raise typer.Exit(1)
        vectors = SentenceEncoder(settings.embedding_model).embed(
            [insight_text(i.problem_statement, i.trying_to_find, i.breakdown_point) for i in items]
        )
        typer.echo(f"{len(items)} insights\n")
        typer.echo(render_sweep(run_sweep(vectors, settings), settings))
        return

    run_id = run_id or new_run_id()

    def client_factory() -> LLMClient:
        return _llm_client(cfg, factory, run_id=run_id)

    try:
        with track_stage(factory, run_id, "cluster") as run:
            result = run_clustering(
                factory,
                cfg,
                run,
                client_factory=client_factory,
                use_llm=not no_llm,
                use_batch_api=batch_api,
                batch_id=batch_id,
                estimate_only=estimate,
            )
            if estimate:
                run.skip("cost estimate only")
    except (LLMConfigError, LLMError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    counts = result.as_counts()
    typer.echo(
        f"Clustered {counts['clustered_input']} of {counts['insights_total']} insights "
        f"({counts['excluded_not_useful']} not useful for discovery): {counts['clusters']} "
        f"clusters, {counts['noise']} unclustered, {counts['areas_active']} active areas "
        f"({counts['areas_emerging']} emerging)."
    )
    if estimate:
        if result.estimate is not None:
            typer.echo(result.estimate.render())
        return
    if result.written:
        save_report(report, render_cluster_report(result, cfg.taxonomy.categories))
        rows = write_review_sheet(review, result)
        typer.echo(f"Wrote {report} and {review} ({rows} areas)")
    if result.stopped_early:
        typer.secho(f"Stopped early: {result.stopped_early}", fg="yellow", err=True)
    typer.echo(f"Run id: {run_id}")
    if result.stopped_early:
        raise typer.Exit(1)


@app.command("curate-area")
def curate_area(
    area_id: str = typer.Argument(
        ..., help="Area id from the latest cluster run, e.g. oa-1a2b3c4d."
    ),
    rename: str | None = typer.Option(None, "--rename", help="New area name."),
    merge_into: str | None = typer.Option(None, "--merge-into", help="Merge into this area."),
    unmerge: bool = typer.Option(False, "--unmerge", help="Undo an earlier --merge-into."),
    split: list[str] | None = typer.Option(
        None, "--split-cluster", help="Move this cluster (e.g. c03) to a new area. Repeatable."
    ),
    archive: bool = typer.Option(False, "--archive", help="Hide the area from the active list."),
    restore: bool = typer.Option(False, "--restore", help="Undo --archive."),
    note: str | None = typer.Option(None, "--note", help="Why (stored with the decision)."),
) -> None:
    """Gate G4: rename, merge, split, or archive an opportunity area.

    Decisions go to pm_overrides and are re-applied on every `discovery cluster` run, which
    matches new clusters to earlier ones. Run `discovery cluster` again to see the result.
    """
    from discovery.ai.cluster_stage import record_curation

    changes: list[tuple[str, object]] = []
    if rename:
        changes.append(("name", rename))
    if merge_into:
        changes.append(("merge_into", merge_into))
    if unmerge:
        changes.append(("merge_into", None))
    if split:
        changes.append(("split_clusters", list(split)))
    if archive and restore:
        raise typer.BadParameter("Use either --archive or --restore.")
    if archive or restore:
        changes.append(("status", "archived" if archive else "active"))
    if not changes:
        raise typer.BadParameter("Nothing to do: pass --rename, --merge-into, --split-cluster, ...")
    _, factory = _context()
    for field_name, value in changes:
        try:
            override_id, run_id = record_curation(factory, area_id, field_name, value, note=note)
        except ValueError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"Recorded {field_name} for {area_id} (override {override_id}, run {run_id}).")
    typer.echo("Run `discovery cluster` to apply. Unchanged LLM calls are served from the cache.")


@app.command("review-areas")
def review_areas_cmd(
    sheet: Path = typer.Option(
        Path("eval/opportunity_review.csv"),
        "--sheet",
        exists=True,
        dir_okay=False,
        help="The Gate G4 sheet with the PM's coherence_1_5, specific_not_generic, action.",
    ),
    report: Path = typer.Option(
        Path("eval/opportunity_review_report.md"), "--report", dir_okay=False
    ),
    run_id: str | None = RunIdOption,
) -> None:
    """Gate G4: score the PM's review sheet (average coherence of the top 8 areas ≥ 4).

    Stores the scores in pm_overrides and writes a report with the `curate-area` commands
    for the actions marked in the sheet. Exit code 1 if the sheet has invalid cells.
    """
    from discovery.eval.area_review import record_review, render_review, review_areas
    from discovery.eval.relevance_eval import save_report

    cfg, factory = _context()
    run_id = run_id or new_run_id()
    with track_stage(factory, run_id, "area_review") as run:
        try:
            summary = review_areas(factory, sheet, cfg.settings.clustering.embedding_model)
        except ValueError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
        recorded = record_review(factory, summary) if not summary.problems else 0
        run.counts.update(summary.as_counts(), recorded=recorded)
        for problem in summary.problems:
            run.error(problem)
    save_report(report, render_review(summary))
    mean = summary.mean_top
    typer.echo(
        f"Top {len(summary.top)} areas: {len(summary.scored_top)} scored, average coherence "
        f"{'-' if mean is None else f'{mean:.2f}'} (target ≥ {summary.target:g})."
    )
    if summary.problems:
        for problem in summary.problems:
            typer.secho(f"- {problem}", fg="red", err=True)
        typer.secho(f"Fix the sheet and re-run. Report: {report}", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"Recorded {recorded} new scores in pm_overrides. Report: {report}")
    if summary.passed:
        typer.secho("Phase 5 coherence criterion met.", fg="green")
    elif summary.complete:
        typer.secho("Below target: curate the low-scoring areas and re-run.", fg="yellow")
    else:
        typer.secho("Not every top area has a coherence score yet.", fg="yellow")


@app.command()
def score(
    run_id: str | None = RunIdOption,
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Heuristic rubric scores only. Writes scores and does not publish.",
    ),
    estimate: bool = typer.Option(
        False, "--estimate", help="Print the rubric cost and write nothing."
    ),
    include_outside_window: bool = typer.Option(
        False,
        "--include-outside-window",
        help="Score items older than scoring.analysis_window_days too.",
    ),
    batch_api: bool | None = typer.Option(
        None,
        "--batch-api/--no-batch-api",
        help="Send rubric calls through the Message Batches API (50% off). "
        "Default: on when there are at least scoring.batch_api_min_calls areas.",
    ),
    batch_id: str | None = BatchIdOption,
    report: Path = typer.Option(
        Path("eval/opportunity_scores.md"),
        "--report",
        dir_okay=False,
        help="Ranked list with explanations and the weight-sensitivity result.",
    ),
) -> None:
    """Score and rank opportunity areas, then publish the run."""
    from discovery.ai.cluster_stage import latest_cluster_run
    from discovery.ai.llm_client import LLMConfigError, LLMError
    from discovery.eval.relevance_eval import save_report
    from discovery.scoring.stage import render_score_report, run_scoring

    cfg, factory = _context()
    if run_id is None:
        with session_scope(factory) as session:
            run_id = latest_cluster_run(session)
        if run_id is None:
            run_id = new_run_id()

    def client_factory() -> object:
        return _llm_client(cfg, factory, run_id=run_id)

    try:
        with track_stage(factory, run_id, "score") as stage:
            result = run_scoring(
                factory,
                cfg,
                stage,
                client_factory=client_factory,
                use_llm=not no_llm,
                use_batch_api=batch_api,
                batch_id=batch_id,
                estimate_only=estimate,
                include_outside_window=include_outside_window,
            )
    except (LLMConfigError, LLMError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    if result.skipped_reason:
        typer.secho(result.skipped_reason, fg="yellow")
        return
    if estimate and result.estimate is not None:
        typer.echo(render_score_report(result, cfg.scoring.weights.model_dump()))
        return
    if result.written:
        save_report(report, render_score_report(result, result.areas[0].weights))
        typer.echo(f"Scored {len(result.areas)} areas. Report: {report}")
    for area in sorted(result.areas, key=lambda a: a.rank)[:3]:
        flag = " (low evidence)" if area.low_evidence else ""
        typer.echo(f"  {area.rank}. {area.name}: {area.composite:.2f} {area.band}{flag}")
    stable = result.sensitivity.get("top3_stable")
    if stable is True:
        typer.echo("Top 3 is stable under ±0.05 weight changes.")
    elif stable is False:
        typer.echo("Top 3 changes under at least one ±0.05 weight change. See the report.")
    if result.published:
        typer.secho(f"Published run {run_id}.", fg="green")
    elif result.written:
        typer.echo("Not published (heuristic rubric). Re-run without --no-llm to publish.")
    if result.stopped_early:
        typer.secho(f"Stopped early: {result.stopped_early}", fg="yellow", err=True)
        raise typer.Exit(1)
    typer.echo(f"Run id: {run_id}")


@app.command("override-score")
def override_score(
    area_id: str = typer.Argument(..., help="Area id from the latest cluster run."),
    product_leverage: float | None = typer.Option(
        None, "--product-leverage", min=1, max=5, help="PM score, 1-5."
    ),
    research_value: float | None = typer.Option(
        None, "--research-value", min=1, max=5, help="PM score, 1-5."
    ),
    note: str | None = typer.Option(None, "--note", help="Why (stored with the override)."),
) -> None:
    """Override product leverage or research value. Re-run `discovery score` to apply it."""
    from discovery.scoring.stage import record_score_override

    changes = []
    if product_leverage is not None:
        changes.append(("product_leverage", product_leverage))
    if research_value is not None:
        changes.append(("research_value", research_value))
    if not changes:
        raise typer.BadParameter("Pass --product-leverage and/or --research-value.")
    _, factory = _context()
    for field_name, value in changes:
        try:
            override_id, run_id = record_score_override(
                factory, area_id, field_name, value, note=note
            )
        except ValueError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.echo(
            f"Recorded {field_name}={value:g} for {area_id} (override {override_id}, run {run_id})."
        )
    typer.echo("Run `discovery score` to apply. Unchanged rubric calls are served from the cache.")


def _engine(factory: sessionmaker[Session]):
    return getattr(factory, "bind", None) or factory.kw["bind"]


@app.command()
def export(
    format: ExportFormat = typer.Option(ExportFormat.csv, "--format", "-f"),
    filters: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help="JSON file of active filters."
    ),
    run_id: str | None = RunIdOption,
    out: Path = typer.Option(
        Path("data/exports"),
        "--out",
        file_okay=False,
        help="Directory for CSV and PDF files. Nothing is written by the dashboard.",
    ),
    audience: str | None = typer.Option(
        None,
        "--audience",
        help="internal or external. PDF defaults to external (sensitive quotes omitted).",
    ),
    spreadsheet_id: str | None = typer.Option(
        None,
        "--spreadsheet-id",
        help="Write into this Google Sheet. Otherwise a new sheet is created.",
    ),
    top: int = typer.Option(8, min=1, help="How many areas get their own PDF page."),
) -> None:
    """Export the opportunity report (CSV, Google Sheets, or PDF)."""
    import json

    from discovery.export.bundle import ExportError, ExportFilters, load_bundle, resolve_run_id
    from discovery.export.csv_export import csv_bytes
    from discovery.export.pdf_report import build_pdf

    if audience not in (None, "internal", "external"):
        raise typer.BadParameter("--audience must be internal or external.")
    chosen = audience or ("external" if format == ExportFormat.pdf else "internal")
    cfg, factory = _context()
    payload = ExportFilters(audience=chosen)
    if filters is not None:
        try:
            raw = json.loads(filters.read_text(encoding="utf-8"))
            if audience is not None:
                raw["audience"] = audience
            elif "audience" not in raw:
                raw["audience"] = chosen
            payload = ExportFilters.from_json(raw)
        except (json.JSONDecodeError, ExportError, TypeError, ValueError) as exc:
            raise typer.BadParameter(f"--filters: {exc}") from exc
    with session_scope(factory) as session:
        try:
            resolved = resolve_run_id(session, run_id)
            bundle = load_bundle(session, resolved, payload, bands=cfg.scoring.bands)
        except ExportError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
    with track_stage(factory, bundle.run_id, "export") as stage:
        stage.counts["format"] = format.value
        stage.counts["areas"] = len(bundle.areas)
        stage.counts["evidence"] = len(bundle.evidence)
        stage.counts["filters"] = bundle.filters_summary
        if format == ExportFormat.csv:
            opportunities, evidence = csv_bytes(bundle)
            out.mkdir(parents=True, exist_ok=True)
            (out / "opportunities.csv").write_bytes(opportunities)
            (out / "evidence.csv").write_bytes(evidence)
            typer.echo(f"Wrote {out / 'opportunities.csv'} and {out / 'evidence.csv'}")
        elif format == ExportFormat.pdf:
            out.mkdir(parents=True, exist_ok=True)
            path = out / "opportunity_report.pdf"
            path.write_bytes(build_pdf(bundle, top_n=top))
            typer.echo(f"Wrote {path}")
        else:
            from discovery.export.sheets_export import SheetsError, export_to_sheets

            sheet = spreadsheet_id or os.environ.get("SHEETS_SPREADSHEET_ID", "").strip() or None
            try:
                url = export_to_sheets(bundle, spreadsheet_id=sheet)
            except SheetsError as exc:
                typer.secho(str(exc), fg="red", err=True)
                raise typer.Exit(1) from exc
            typer.echo(url)
        if bundle.note:
            typer.echo(bundle.note)
    typer.echo(f"Run id: {bundle.run_id}")


@app.command("run-all")
def run_all(
    run_id: str | None = RunIdOption,
    since: str | None = typer.Option(
        None,
        "--since",
        help="Passed to ingest. Use 'last' for an incremental run.",
    ),
    limit: int | None = typer.Option(None, min=1, help="Max items per source."),
    skip_regression: bool = typer.Option(
        False,
        "--skip-regression",
        help="Start even if the prompt or model is not the accepted gold-set baseline.",
    ),
) -> None:
    """Run every stage, then publish only if the sanity checks pass."""
    from discovery.db import PipelineBusy, pipeline_lock
    from discovery.eval.regression import acceptance_problems, load_baseline
    from discovery.pipeline import decide_publish, render_cost_report, snapshot_published

    cfg, factory = _context()
    run_id = run_id or new_run_id()
    if since is not None:
        from discovery.ingest.runner import parse_since

        try:
            parse_since(since)
        except ValueError as exc:
            raise typer.BadParameter(f"--since: {exc}") from exc
    if not skip_regression:
        try:
            problems = acceptance_problems(
                load_baseline(),
                small_model=cfg.settings.llm.small_model,
                large_model=cfg.settings.llm.large_model,
            )
        except FileNotFoundError as exc:
            problems = [str(exc)]
        if problems:
            with track_stage(factory, run_id, "run_all") as run:
                for problem in problems:
                    run.error(problem)
                run.final_status = RunStatus.FAILED
            for problem in problems:
                typer.secho(problem, fg="red", err=True)
            raise typer.Exit(1)
    try:
        with pipeline_lock(_engine(factory), run_id):
            previous = snapshot_published(factory)
            stage_failed = False
            with track_stage(factory, run_id, "run_all") as run:
                for name, stage_fn, kwargs in _run_all_stages(run_id, since, limit):
                    try:
                        stage_fn(**kwargs)
                    except (typer.Exit, SystemExit) as exc:
                        code = getattr(exc, "exit_code", getattr(exc, "code", 1)) or 0
                        if code:
                            stage_failed = True
                            run.error(f"{name} exited {code}")
                            break
                with session_scope(factory) as session:
                    rows = session.scalars(
                        select(PipelineRunRow).where(PipelineRunRow.run_id == run_id)
                    ).all()
                    run.counts["stages"] = {
                        row.stage: row.status for row in rows if row.stage != "run_all"
                    }
                outcome = decide_publish(factory, run_id, previous, stage_failed=stage_failed)
                run.counts["publish"] = outcome.status
                for reason in outcome.reasons:
                    if outcome.published:
                        continue
                    if outcome.status == "unchanged":
                        run.counts["publish_note"] = reason
                    else:
                        run.error(reason)
                if outcome.status == "published":
                    run.final_status = RunStatus.PUBLISHED
                elif outcome.status == "failed":
                    run.final_status = RunStatus.FAILED
                elif outcome.status == "partial":
                    run.final_status = RunStatus.PARTIAL
            report = render_cost_report(factory, cfg, run_id=run_id)
            report_path = Path(os.environ.get("DISCOVERY_COST_REPORT", "eval/cost_report.md"))
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report, encoding="utf-8")
            typer.echo(report)
    except PipelineBusy as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    if outcome.status in ("failed", "partial"):
        typer.secho("Not published. The dashboard keeps the previous run.", fg="red", err=True)
        raise typer.Exit(1)
    if outcome.published:
        typer.secho(f"Published run {run_id}.", fg="green")
    else:
        typer.echo("Published run unchanged.")
    typer.echo(f"run-all finished. Run id: {run_id}")


def _run_all_stages(run_id: str, since: str | None, limit: int | None) -> list[tuple]:
    return [
        (
            "ingest",
            ingest,
            {"source": SourceChoice.all, "since": since, "limit": limit, "run_id": run_id},
        ),
        ("prep", prep, {"run_id": run_id, "review_out": None, "review_limit": 50}),
        (
            "classify",
            classify,
            {
                "run_id": run_id,
                "limit": None,
                "force": False,
                "report": Path("eval/funnel_report.md"),
            },
        ),
        (
            "extract",
            extract,
            {
                "run_id": run_id,
                "limit": None,
                "force": False,
                "batch_api": None,
                "batch_id": None,
                "estimate": False,
                "report": Path("eval/extraction_report.md"),
                "queue": Path("eval/extraction_review_queue.csv"),
            },
        ),
        (
            "cluster",
            cluster,
            {
                "run_id": run_id,
                "no_llm": False,
                "estimate": False,
                "sweep": False,
                "batch_api": None,
                "batch_id": None,
                "report": Path("eval/opportunity_areas.md"),
                "review": Path("eval/opportunity_review.csv"),
            },
        ),
        (
            "score",
            score,
            {
                "run_id": run_id,
                "no_llm": False,
                "estimate": False,
                "include_outside_window": False,
                "batch_api": None,
                "batch_id": None,
                "report": Path("eval/opportunity_scores.md"),
            },
        ),
    ]


@app.command()
def purge(
    older_than: str = typer.Option(..., "--older-than", help="90d, 12w, or YYYY-MM-DD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be deleted."),
) -> None:
    """Delete raw payloads and unpublished runs older than the cutoff. The published run stays."""
    from discovery.pipeline import parse_older_than, purge_older_than

    cfg, factory = _context()
    try:
        cutoff = parse_older_than(older_than)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    raw_dir = Path(cfg.settings.ingest.raw_dir)
    report = purge_older_than(factory, cutoff, raw_dir=raw_dir, dry_run=dry_run)
    prefix = "Would delete" if dry_run else "Deleted"
    typer.echo(prefix + ":\n" + report.lines())


@app.command("cost-report")
def cost_report(
    run_id: str | None = RunIdOption,
    out: Path = typer.Option(Path("eval/cost_report.md"), "--out", dir_okay=False),
) -> None:
    """Cost, runtime, and rate-limit notes for a run, compared with architecture Section 19."""
    from discovery.pipeline import render_cost_report

    cfg, factory = _context()
    text = render_cost_report(factory, cfg, run_id=run_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(text)


@app.command("eval-regression")
def eval_regression(
    relevance: Path | None = typer.Option(
        None, "--relevance", exists=True, dir_okay=False, help="Relevance evaluation report."
    ),
    extraction: Path | None = typer.Option(
        None, "--extraction", exists=True, dir_okay=False, help="Extraction evaluation report."
    ),
    accept: bool = typer.Option(
        False, "--accept", help="Record these reports as the baseline if they do not regress."
    ),
    baseline: Path = typer.Option(
        Path("eval/regression_baseline.json"), "--baseline", dir_okay=False
    ),
) -> None:
    """Check the gold-set baseline before a new prompt or model is accepted."""
    from discovery.eval.regression import (
        accept_baseline,
        acceptance_problems,
        compare_metrics,
        load_baseline,
        parse_report,
    )

    cfg, _factory = _context()
    if accept:
        if relevance is None or extraction is None:
            raise typer.BadParameter("--accept needs both --relevance and --extraction reports.")
        try:
            saved = accept_baseline(
                baseline,
                relevance_report=relevance.read_text(encoding="utf-8"),
                extraction_report=extraction.read_text(encoding="utf-8"),
                small_model=cfg.settings.llm.small_model,
                large_model=cfg.settings.llm.large_model,
            )
        except (FileNotFoundError, ValueError) as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.secho(
            f"Accepted {saved.prompts['relevance']} and {saved.prompts['extraction']}.",
            fg="green",
        )
        return
    try:
        current = load_baseline(baseline)
    except FileNotFoundError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    problems = acceptance_problems(
        current,
        small_model=cfg.settings.llm.small_model,
        large_model=cfg.settings.llm.large_model,
    )
    if relevance or extraction:
        text = ""
        if relevance:
            text += relevance.read_text(encoding="utf-8")
        if extraction:
            text += "\n" + extraction.read_text(encoding="utf-8")
        problems.extend(compare_metrics(parse_report(text), current))
    if problems:
        for problem in problems:
            typer.secho(f"- {problem}", fg="red", err=True)
        raise typer.Exit(1)
    typer.secho("Gold-set baseline matches the configured prompts and models.", fg="green")


@app.command("gold-set")
def gold_set_cmd(
    out: Path = typer.Option(
        Path("eval/gold_set.csv"),
        "--out",
        dir_okay=False,
        help="Where to write the unlabeled labeling sheet.",
    ),
    size: int = typer.Option(300, min=1, help="How many items to sample."),
    per_stratum: int = typer.Option(100, min=1, help="Target items in each relevance stratum."),
    seed: int = typer.Option(0, help="Sample seed. The same seed redraws the same sheet."),
    agree: Path | None = typer.Option(
        None,
        "--agree",
        exists=True,
        dir_okay=False,
        help="Measure labeler agreement on a filled sheet instead of drawing a new sample.",
    ),
) -> None:
    """Draw the ~300-item gold set, or measure agreement between two labelers."""
    from discovery.eval.gold_set import (
        agreement,
        draw_sample,
        exemplar_overlap,
        read_sheet,
        write_sheet,
    )

    if agree is not None:
        stats = agreement(read_sheet(agree))
        for field_name, result in stats.items():
            rate = result["agreement"]
            shown = "n/a" if rate is None else f"{rate:.0%}"
            typer.echo(
                f"{field_name}: agreement={shown} compared={result['compared']} "
                f"ambiguous_excluded={result['ambiguous_excluded']} "
                f"unlabeled={result['unlabeled']}"
            )
        retrieval = stats["retrieval_type"]["agreement"]
        if retrieval is not None and retrieval < 0.8:
            typer.secho("Retrieval-type agreement is below 80%.", fg="yellow", err=True)
            raise typer.Exit(1)
        return

    cfg, factory = _context()
    sample = draw_sample(factory, cfg, size=size, per_stratum=per_stratum, seed=seed)
    if sample.pool == 0:
        typer.secho(
            "No items are ready to sample. Run `discovery prep` first.",
            fg="red",
            err=True,
        )
        raise typer.Exit(1)
    write_sheet(out, sample)
    exemplars = [*cfg.keywords.seed_exemplars.positive, *cfg.keywords.seed_exemplars.negative]
    leaked = exemplar_overlap([it.clean_text for it in sample.items], exemplars)
    typer.echo(
        f"Wrote {len(sample.items)} items to {out} "
        f"(pool={sample.pool}, overlap rows={sample.overlap}, seed={seed})"
    )
    typer.echo("By stratum: " + ", ".join(f"{k}={v}" for k, v in sorted(sample.by_stratum.items())))
    typer.echo("By source: " + ", ".join(f"{k}={v}" for k, v in sorted(sample.by_source.items())))
    if leaked:
        typer.secho(
            f"{len(leaked)} seed exemplar(s) overlap the gold set. "
            "Keep few-shot examples separate.",
            fg="yellow",
            err=True,
        )


@app.command("eval")
def evaluate(
    gold: Path = typer.Option(
        Path("eval/gold_set.csv"),
        "--gold",
        exists=True,
        dir_okay=False,
        help="Labeled gold sheet.",
    ),
    report: Path = typer.Option(
        Path("eval/relevance_report.md"),
        "--report",
        dir_okay=False,
        help="Where to write the metrics.",
    ),
    llm: bool = typer.Option(False, "--llm", help="Run Stage C on gold items the funnel sends on."),
    model: str | None = typer.Option(None, help="Stage C model (default: llm.small_model)."),
    predictions: Path | None = typer.Option(
        None,
        "--predictions",
        dir_okay=False,
        help="With --llm, write each classified gold item (label, confidence, rationale) here.",
    ),
    max_wait: float | None = typer.Option(
        None,
        "--max-wait",
        min=1,
        help="Seconds to wait out a provider rate limit before stopping "
        "(default: llm.max_retry_wait_seconds).",
    ),
    run_id: str | None = RunIdOption,
) -> None:
    """Evaluate the relevance funnel against the gold set."""
    from discovery.ai.embeddings import SentenceEncoder
    from discovery.ai.llm_client import LLMError
    from discovery.eval.relevance_eval import evaluate_sheet, save_report

    cfg, factory = _context()
    run_id = run_id or new_run_id()
    client = None
    if llm:
        settings = cfg.settings.llm
        if max_wait is not None:
            settings = settings.model_copy(update={"max_retry_wait_seconds": max_wait})
        try:
            client = _llm_client(cfg, factory, settings, run_id=run_id)
        except LLMError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
    encoder = SentenceEncoder(cfg.settings.clustering.embedding_model)
    with track_stage(factory, run_id, "eval") as run:
        try:
            metrics, text, errors = evaluate_sheet(
                gold,
                cfg,
                encoder,
                factory=factory,
                client=client,
                model=model,
                workers=cfg.settings.llm.max_concurrency,
                predictions_out=predictions if llm else None,
            )
        finally:
            if client is not None:
                run.add_llm_usage(client.usage.total_tokens, client.usage.cost_usd)
        for message in errors:
            run.error(message)
        run.counts["stage_a_recall"] = metrics.stage_a_recall.value
        run.counts["stage_ab_recall"] = metrics.stage_ab_recall.value
        run.counts["stage_c_precision"] = metrics.stage_c_precision.value
        run.counts["stage_c_recall"] = metrics.stage_c_recall.value
        if client is not None:
            usage = client.usage
            run.counts["llm_requests"] = usage.requests
            run.counts["llm_cache_hits"] = usage.cache_hits
            text += (
                f"\nLLM usage this run: {usage.requests} API requests, {usage.cache_hits} cache "
                f"hits, {usage.input_tokens:,} input ({usage.cached_input_tokens:,} read from "
                f"the prompt cache) + {usage.output_tokens:,} output tokens, "
                f"${usage.cost_usd:.4f}.\n"
            )
            if errors:
                text += "\nStage C errors:\n" + "".join(f"- {m}\n" for m in errors)
    save_report(report, text)
    if predictions is not None and llm:
        typer.echo(f"Wrote {predictions}")
    typer.echo(text)
    typer.echo(f"Wrote {report}")
    if metrics.misses_targets():
        raise typer.Exit(1)


@app.command("eval-extract")
def evaluate_extraction_cmd(
    gold: Path = typer.Option(
        Path("eval/gold_set.csv"), "--gold", exists=True, dir_okay=False, help="Labeled gold sheet."
    ),
    model: str | None = typer.Option(None, help="Small model (default: llm.small_model)."),
    report: Path | None = typer.Option(
        None,
        "--report",
        dir_okay=False,
        help="Default: eval/extraction_v{N}_{model}_report.md",
    ),
    predictions: Path | None = typer.Option(
        None,
        "--predictions",
        dir_okay=False,
        help="Default: eval/extraction_v{N}_{model}_predictions.csv",
    ),
    batch_api: bool = typer.Option(
        False, "--batch-api/--no-batch-api", help="First pass through the Message Batches API."
    ),
    batch_id: str | None = BatchIdOption,
    estimate: bool = typer.Option(
        False, "--estimate", help="Print the estimated cost; make no calls."
    ),
    run_id: str | None = RunIdOption,
) -> None:
    """Evaluate insight extraction against the gold set (P4.7)."""
    from discovery.ai.extraction import PROMPT_NAME, PROMPT_VERSION, estimate_cost
    from discovery.ai.llm_client import LLMError
    from discovery.ai.prompts import load_prompt
    from discovery.eval.extraction_eval import (
        gold_items,
        render_extraction_metrics,
        run_extraction_eval,
        write_extraction_predictions,
    )
    from discovery.eval.gold_set import read_sheet
    from discovery.eval.relevance_eval import save_report

    cfg, factory = _context()
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION, cfg.prompts_dir)
    small = model or cfg.settings.llm.small_model
    if estimate:
        items = gold_items(read_sheet(gold))
        cost = estimate_cost(
            items, prompt, settings=cfg.settings.extraction, llm=cfg.settings.llm, small_model=small
        )
        typer.echo(cost.render())
        return
    stem = f"eval/extraction_v{PROMPT_VERSION}_{small.replace('/', '-')}"
    report = report or Path(f"{stem}_report.md")
    predictions = predictions or Path(f"{stem}_predictions.csv")
    run_id = run_id or new_run_id()
    try:
        client = _llm_client(cfg, factory, run_id=run_id)
    except LLMError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc

    with track_stage(factory, run_id, "eval_extract") as run:

        def on_submit(new_batch_id: str) -> None:
            run.counts["batch_id"] = new_batch_id
            save_counts(factory, run)

        try:
            metrics, outcome, rows = run_extraction_eval(
                gold,
                cfg,
                client,
                prompt,
                model=small,
                use_batch_api=batch_api or batch_id is not None,
                batch_id=batch_id,
                on_batch_submit=on_submit,
            )
        except LLMError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from exc
        finally:
            run.add_llm_usage(client.usage.total_tokens, client.usage.cost_usd)
        for message in outcome.errors:
            run.error(message)
        run.counts.update(
            grounding=metrics.grounding.value,
            category_top1=metrics.category_top1.value,
            category_top2=metrics.category_top2.value,
            not_stated=metrics.not_stated.value,
            llm_requests=client.usage.requests,
            llm_cache_hits=client.usage.cache_hits,
        )
    text = render_extraction_metrics(
        metrics,
        prompt_version=prompt.id,
        model=small,
        large_model=cfg.settings.llm.large_model,
        run=outcome,
    )
    usage = client.usage
    text += (
        f"\nLLM usage this run: {usage.requests} API requests, {usage.cache_hits} cache hits, "
        f"{usage.input_tokens:,} input ({usage.cached_input_tokens:,} read from the prompt "
        f"cache) + {usage.output_tokens:,} output tokens, ${usage.cost_usd:.4f}.\n"
    )
    if outcome.errors:
        text += "\nItem errors:\n" + "".join(f"- {m}\n" for m in outcome.errors)
    save_report(report, text)
    write_extraction_predictions(
        predictions, rows, {item_id: ex for item_id, (_, ex) in outcome.found.items()}
    )
    typer.echo(text)
    typer.echo(f"Wrote {report} and {predictions}")
    if metrics.misses_targets() or outcome.stopped:
        raise typer.Exit(1)


# --- operations --------------------------------------------------------------


@app.command("init-db")
def init_db_cmd() -> None:
    """Create all tables in the configured database (idempotent)."""
    cfg, factory = _context()
    tables = init_db(factory.kw["bind"])
    typer.echo(f"Database: {redact_url(cfg.database_url)}")
    typer.echo(f"{len(tables)} tables ready: {', '.join(tables)}")


SAMPLE_TEXT = (
    "I know there's a photo of a tiny café from our Goa trip, maybe 2023? I searched 'Goa "
    "cafe' and scrolled forever but it only shows beach photos. It's in there somewhere!"
)


@app.command("llm-check")
def llm_check(
    model: str | None = typer.Option(None, help="Model id (default: llm.small_model)."),
    text: str = typer.Option(SAMPLE_TEXT, help="Feedback text to classify."),
    burst: int = typer.Option(0, min=0, help="Also send N uncached calls to test rate limits."),
) -> None:
    """LLM demo: one structured call twice (second is served from cache), optional burst."""
    from discovery.ai.llm_client import LLMError
    from discovery.ai.prompts import load_prompt

    cfg, factory = _context()
    run_id = new_run_id()
    try:
        client = _llm_client(cfg, factory, run_id=run_id)
    except LLMError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc
    prompt = load_prompt("smoke_test", 1, cfg.prompts_dir)

    try:
        with track_stage(factory, run_id, "llm_check") as run:
            result = None
            for i in (1, 2):
                result = client.complete(
                    prompt=prompt, user_input=text, response_model=RelevanceResult, model=model
                )
                typer.echo(
                    f"Call {i}: model={result.model} cached={result.cached} "
                    f"tokens_in={result.input_tokens} tokens_out={result.output_tokens} "
                    f"cost=${result.cost_usd:.6f}"
                )
            assert result is not None
            typer.echo(result.value.model_dump_json(indent=2))

            if burst:
                started = time.monotonic()
                failures = 0
                workers = cfg.settings.llm.max_concurrency
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        pool.submit(
                            client.complete,
                            prompt=prompt,
                            user_input=f"{text} (burst {i})",
                            response_model=RelevanceResult,
                            model=model,
                            use_cache=False,
                        )
                        for i in range(burst)
                    ]
                    for fut in as_completed(futures):
                        try:
                            fut.result()
                        except LLMError as exc:
                            failures += 1
                            run.error(str(exc))
                elapsed = time.monotonic() - started
                typer.echo(
                    f"Burst: {burst} calls, {failures} failed, {elapsed:.1f}s "
                    f"({burst / elapsed * 60:.1f} req/min)"
                )
                run.counts["burst_failures"] = failures

            u = client.usage
            run.counts.update(requests=u.requests, cache_hits=u.cache_hits)
            run.add_llm_usage(u.total_tokens, u.cost_usd)
            typer.echo(
                f"Total: {u.requests} API requests, {u.cache_hits} cache hits, "
                f"{u.total_tokens} tokens, ${u.cost_usd:.6f}"
            )
    except LLMError as exc:
        typer.secho(f"LLM check failed: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc


@app.command()
def runs(limit: int = typer.Option(15, min=1, help="Rows to show.")) -> None:
    """Show recent pipeline_runs rows."""
    _, factory = _context()
    with session_scope(factory) as s:
        rows = s.scalars(
            select(PipelineRunRow).order_by(PipelineRunRow.started_at.desc()).limit(limit)
        ).all()
    if not rows:
        typer.echo("No pipeline runs recorded yet.")
        return
    for r in rows:
        dur = f"{(r.finished_at - r.started_at).total_seconds():.1f}s" if r.finished_at else "-"
        typer.echo(
            f"{r.started_at:%Y-%m-%d %H:%M:%S}  {r.run_id:<26} {r.stage:<10} {r.status:<9} "
            f"{dur:>7}  tokens={r.llm_tokens} cost=${r.llm_cost_usd:.4f} errors={len(r.errors)}"
        )


if __name__ == "__main__":
    app()
