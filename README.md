# Google Photos Discovery Engine

Turns public Google Photos feedback (Play Store, App Store, a Reddit/Community Google Sheet, and
Help Community threads) into a ranked, evidence-backed list of *vaguely remembered photo
retrieval* opportunity areas. See [`Docs/`](Docs/) for the problem statement, architecture, and
implementation plan.

**Status:** Phase 1 (Data Ingestion). `ingest` and `quality` are live; later stages are stubs
that record a `skipped` run.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.11 automatically).

```bash
uv sync                      # create .venv and install dependencies
uv run playwright install chromium   # browser for the Help Community crawler
cp .env.example .env         # fill in GROQ_API_KEY, AUTHOR_HASH_SALT (and DATABASE_URL for Postgres)
uv run discovery init-db     # create tables (local SQLite at data/discovery.db by default)
```

## Commands

```bash
uv run discovery --help
uv run discovery ingest --source all|play_store|app_store|google_sheet|google_community
uv run discovery ingest --source play_store --since last   # only reviews newer than the stored ones
uv run discovery ingest --source google_community --limit 20
uv run discovery quality     # counts, date ranges, missing-field rates per source (--format json)
uv run discovery prep | classify | extract | cluster | score | eval
uv run discovery export --format csv|sheets|pdf
uv run discovery run-all     # every stage under one run id
uv run discovery runs        # recent pipeline_runs history
uv run discovery llm-check   # Groq demo: same call twice, second served from cache
uv run discovery llm-check --burst 40   # rate-limit check: 40 uncached calls
```

`--run-id` groups several stage commands under one run (used by GitHub Actions).

## Configuration

| File | Purpose |
|---|---|
| `config/settings.yaml` | Sources, Groq models, rate limits, pricing, budget ceiling, thresholds |
| `config/taxonomy.yaml` | Categories and enums (kept in sync with `models/schemas.py` by tests) |
| `config/scoring_weights.yaml` | Opportunity score weights and bands (PM-editable) |
| `config/keywords.yaml` | Stage A prefilter lexicon and Stage B seed exemplars |
| `prompts/*_vN.md` | Versioned prompts; bump the version on any change (it is part of the cache key) |

Secrets live in `.env` locally, GitHub Actions secrets for the pipeline, and Streamlit secrets
for the dashboard. `DATABASE_URL` selects the database: unset = local SQLite; a Postgres URL
(use the provider's *pooled* connection string) = hosted Postgres.

### Ingestion

Each source writes the latest payload to `raw_items`, an immutable JSONL snapshot to
`data/raw/{source}/{run_id}/items.jsonl` (local only, never committed), and normalized rows to
`items`. A failing source is logged as `failed`/`partial` in `pipeline_runs`
(`ingest:{source}`) and never stops the others. Author names are never stored, only a salted
hash.

Every request sends an identifying User-Agent, is paced (1 request/second per host with
jitter; 2-3 s per Help Community page), and is checked against the host's `robots.txt`:

| Source | Method | robots.txt |
|---|---|---|
| Play Store | `google-play-scraper` | Disallowed (`/_`). Recorded PM exception in `ingest.robots_exceptions` |
| App Store | `apps.apple.com` reviews page | Allowed. Shows ~10 recent reviews per storefront, so `app_store_daily.yml` accumulates them |
| Google Sheet | CSV export of every tab | Allowed |
| Help Community | Thread list over HTTP, threads in headless Chromium | Allowed. Resumes where the last run stopped (`max_threads_per_run`) |

The iTunes RSS feed (`sources.app_store.method: rss`) is disallowed by robots.txt and only runs
if `app_store` is added to `ingest.robots_exceptions`.

In GitHub Actions, **Pipeline** (`pipeline.yml`, manual) runs ingestion against hosted
Postgres, and **App Store daily** runs every day. Both need the `DATABASE_URL` and
`AUTHOR_HASH_SALT` secrets and write a Data Quality summary to the run page.

### Groq rate limits

`llm.rate_limits` defaults to Groq's free-tier limits with headroom (28 RPM, 7.5K TPM). The
free tier also caps GPT-OSS models at 1K requests/day, which is too low for a full run
(~4,500 calls), so use the Developer plan for full runs and raise `requests_per_minute`,
`tokens_per_minute`, and `max_concurrency` to match the account's
[Limits page](https://console.groq.com/settings/limits).

## Tests and linting

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Database tests run on SQLite, and also on Postgres when `TEST_DATABASE_URL` is set (CI does
this with a Postgres service container).

## Hosted Postgres (one-time)

1. Create a free Neon or Supabase database and copy the pooled connection string.
2. Add `DATABASE_URL`, `GROQ_API_KEY`, and `AUTHOR_HASH_SALT` as GitHub Actions secrets.
3. Run the **Initialize database** workflow (or `DATABASE_URL=... uv run discovery init-db`).
