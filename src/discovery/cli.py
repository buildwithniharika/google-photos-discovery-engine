"""`discovery` CLI. The same commands run locally and in GitHub Actions."""

from __future__ import annotations

import logging
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
from discovery.runs import new_run_id, track_stage

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
    for noisy in ("httpx", "httpcore", "groq"):
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
    outcomes = run_ingest(
        cfg, factory, run_id, sources, since=since, limit=limit, salt=cfg.author_hash_salt
    )
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
def prep(run_id: str | None = RunIdOption) -> None:
    """Normalize, clean, redact PII, and deduplicate."""
    _stub("prep", 2, run_id)


@app.command()
def classify(run_id: str | None = RunIdOption) -> None:
    """Relevance funnel: keyword prefilter, semantic filter, LLM classifier."""
    _stub("classify", 3, run_id)


@app.command()
def extract(run_id: str | None = RunIdOption) -> None:
    """Extract structured insights from retrieval items."""
    _stub("extract", 4, run_id)


@app.command()
def cluster(run_id: str | None = RunIdOption) -> None:
    """Embed, cluster, label, and synthesize opportunity areas."""
    _stub("cluster", 5, run_id)


@app.command()
def score(run_id: str | None = RunIdOption) -> None:
    """Score and rank opportunity areas."""
    _stub("score", 6, run_id)


@app.command()
def export(
    format: ExportFormat = typer.Option(ExportFormat.csv, "--format", "-f"),
    filters: Path | None = typer.Option(None, exists=True, dir_okay=False),
    run_id: str | None = RunIdOption,
) -> None:
    """Export the opportunity report (CSV, Google Sheets, or PDF)."""
    _stub("export", 8, run_id, format=format.value, filters=filters)


@app.command("run-all")
def run_all(run_id: str | None = RunIdOption) -> None:
    """Run every stage in order under one run id."""
    _, factory = _context()
    run_id = run_id or new_run_id()
    stages = [ingest, prep, classify, extract, cluster, score]
    with track_stage(factory, run_id, "run_all") as run:
        for stage_fn in stages:
            if stage_fn is ingest:
                stage_fn(source=SourceChoice.all, since=None, limit=None, run_id=run_id)
            else:
                stage_fn(run_id=run_id)
        with session_scope(factory) as s:
            rows = s.scalars(select(PipelineRunRow).where(PipelineRunRow.run_id == run_id)).all()
            statuses = {r.stage: r.status for r in rows if r.stage != "run_all"}
        run.counts["stages"] = statuses
        if all(st == RunStatus.SKIPPED for st in statuses.values()):
            run.skip("all stages are stubs")
    typer.echo(f"run-all finished. Run id: {run_id}")


@app.command("eval")
def evaluate(run_id: str | None = RunIdOption) -> None:
    """Evaluate the AI stages against the gold set."""
    _stub("eval", 3, run_id)


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
    """Groq demo: one structured call twice (second is served from cache), optional burst."""
    from discovery.ai.llm_client import LLMClient, LLMError
    from discovery.ai.prompts import load_prompt

    cfg, factory = _context()
    run_id = new_run_id()
    try:
        client = LLMClient(cfg.settings.llm, factory, api_key=cfg.groq_api_key)
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
