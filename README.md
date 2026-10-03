# Google Photos Discovery Engine

Turns public Google Photos feedback (Play Store, App Store, a Reddit/Community Google Sheet, and
Help Community threads) into a ranked, evidence-backed list of *vaguely remembered photo
retrieval* opportunity areas. See [`Docs/`](Docs/) for the problem statement, architecture, and
implementation plan.

**Status:** Phases 0–8 are in the repo. Phase 9 readout is ready for the stakeholder meeting: selected area, PDF, and interview guide are in [`Docs/discovery-readout.md`](Docs/discovery-readout.md). `ingest` through `export` and `run-all` are live. Streamlit Community Cloud deploy and private viewers (Phase 7) are still open. How to deploy and operate the scheduled pipeline is in [`Docs/deployment-runbook.md`](Docs/deployment-runbook.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.11 automatically).

```bash
uv sync                      # create .venv and install dependencies
uv run playwright install chromium   # browser for the Help Community crawler
cp .env.example .env         # fill in ANTHROPIC_API_KEY, AUTHOR_HASH_SALT (and DATABASE_URL for Postgres)
uv run discovery init-db     # create tables (local SQLite at data/discovery.db by default)
```

## Commands

```bash
uv run discovery --help
uv run discovery ingest --source all|play_store|app_store|google_sheet|google_community
uv run discovery ingest --source play_store --since last   # only reviews newer than the stored ones
uv run discovery ingest --source google_community --limit 20
uv run discovery quality     # counts, date ranges, missing-field rates per source (--format json)
uv run discovery prep        # clean, redact PII, deduplicate (original_text is kept)
uv run discovery prep --review-out eval/dedup_review.csv   # sample of merges to check by hand
uv run discovery gold-set    # write the unlabeled ~300-item labeling sheet
uv run discovery gold-set --agree eval/gold_set.csv        # agreement once two people have labeled
uv run discovery classify   # Stages A–C; writes eval/funnel_report.md and the relevance table
uv run discovery classify --limit 50
uv run discovery eval       # Stage A and A+B recall on eval/gold_set.csv
uv run discovery eval --llm # also run Stage C on the gold items the funnel sends on
uv run discovery eval-extract --estimate  # free cost estimate for the gold-set extraction eval
uv run discovery eval-extract             # extraction metrics on the gold set
uv run discovery extract --estimate       # free cost estimate for the corpus
uv run discovery extract                  # writes the insights table, report, and review queue
uv run discovery extract-review           # 50-item sample sheet for Gate G3
uv run discovery cluster --no-llm         # free preview: clusters and areas, heuristic names
uv run discovery cluster --estimate       # free cost estimate for labels and synthesis
uv run discovery cluster                  # writes clusters, areas, evidence, report, G4 sheet
uv run discovery curate-area oa-1a2b3c4d --rename "..."   # also --merge-into, --split-cluster, --archive
uv run discovery review-areas             # Gate G4: score the filled review sheet (top-8 coherence ≥ 4)
uv run discovery score --estimate         # free cost estimate for the rubric
uv run discovery score --no-llm           # repeatable scores with a heuristic rubric; does not publish
uv run discovery score                    # six scores, rank, explanations; publishes the run
uv run discovery override-score oa-1a2b3c4d --product-leverage 4 --note "..."
uv run discovery export --format csv|pdf|sheets
uv run discovery export --format pdf --audience external
uv run discovery run-all --since last   # incremental pipeline; publishes only if checks pass
uv run discovery purge --older-than 90d
uv run discovery cost-report
uv run discovery eval-regression        # refuse a prompt or model change that is not accepted
uv run discovery runs        # recent pipeline_runs history
uv run discovery llm-check   # LLM demo: same call twice, second served from cache
uv run discovery llm-check --burst 40   # rate-limit check: 40 uncached calls
```

`--run-id` groups several stage commands under one run (used by GitHub Actions).

## Configuration

| File | Purpose |
|---|---|
| `config/settings.yaml` | Sources, LLM provider and models, rate limits, pricing, spend limits, thresholds |
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

### Prep and the gold set

`discovery prep` writes `clean_text` (markup stripped, PII redacted) and never changes
`original_text`. English items with at least 4 words go on to the AI stages, plus shorter
items that contain a retrieval keyword ("can't find screenshots" is kept). Other languages
and code-mixed text are flagged and counted, and left out of the AI stages.

Exact duplicates of long text are merged, including the same review collected from Play and
from the Google Sheet. Short complaints are not merged across authors; each keeps a
`similar_count`. Near-duplicates (Jaccard ≥ 0.85 on word shingles) are merged for long text
only, and only when the text matches the copy that is kept. Emoji are described in
`clean_text` for the model, and they are not counted as words. Identical non-retrieval text
from many authors is flagged `is_spam`.

`discovery gold-set` draws about 300 items, spread across the four sources and three strata
(likely relevant, borderline, likely irrelevant). How to fill the sheet is in
[`eval/labeling_guide.md`](eval/labeling_guide.md). The labeled sheet is `eval/gold_set.csv`.

### Relevance funnel

`discovery classify` runs three stages on items prep marked ready for AI:

1. **Stage A** keeps a review that has a retrieval-intent phrase, or a search-feature phrase and a rating of 3 or lower (`config/keywords.yaml`).
2. **Stage B** embeds `clean_text` with the local model in `clustering.embedding_model` and keeps it when the best similarity to a positive seed exemplar exceeds the best similarity to a negative exemplar by more than `relevance.semantic_margin_threshold`. Five percent of the rejections are still sent to the classifier so missed relevant items can be estimated.
3. **Stage C** asks the small model (`llm.small_model`, Claude Sonnet 5.5), with the current `prompts/relevance_vN.md`, for the JSON relevance schema. Items go up to 8 per call, and the system prompt is read from Anthropic's prompt cache after the first call. Storage, pricing, backup, sync, sharing, and deletion stay in scope only when the model sets `excluded_topic_blocks_retrieval` to true. Calls stay inside `llm.max_concurrency` and the configured rate limits. A later run reuses Stage C rows for the same model and prompt.

`discovery eval` scores Stage A and Stage A+B on the gold sheet. `discovery eval --llm` also scores Stage C precision and recall for `vague_memory_retrieval`. The report is `eval/relevance_report.md`.

### Insight extraction

`discovery extract` runs on items Stage C labeled general or vague memory retrieval. It uses the current `prompts/extraction_vN.md` (v2) and the settings under `extraction:` in `config/settings.yaml`:

- Short items go to Sonnet, up to 6 per call. Texts over 1,500 characters go to Opus one at a time, and Sonnet results with confidence below 0.6 are re-run on Opus.
- Each `evidence_quote` must match `clean_text` at 95% or more. If it doesn't, the item is retried once. If it still fails, the quote is cleared and evidence strength is capped at 2.
- With 100 or more items pending, the run uses Anthropic's Message Batches API (half price; results can take up to an hour). If the wait runs out, re-run with the `--batch-id` that was printed.
- Items stored for the current prompt version are skipped on later runs, so a run that stops on the spend guard can be repeated.

The command writes `eval/extraction_report.md` and `eval/extraction_review_queue.csv` (confidence below 0.5 or an ungrounded quote). `discovery extract-review` writes the 50-item Gate G3 sheet. `discovery eval-extract` scores category top-1/top-2, content type, `not_stated` correctness, and quote grounding on the gold set. Add `--estimate` to `extract` or `eval-extract` to price a run without calling the model.

### Clustering and opportunity areas

`discovery cluster` uses the settings under `clustering:` in `config/settings.yaml`:

- It embeds each insight's problem statement, what the user was looking for, and the breakdown point, then clusters with UMAP + HDBSCAN. Insights marked not useful for discovery are left out. `--sweep` compares parameter settings.
- The large model names each cluster from 20 representative items and maps it to a taxonomy category or `other_emergent` (`prompts/cluster_label_vN.md`). Same-category clusters with centroid cosine ≥ 0.93 become one area.
- Counts, cues, attempts, breakdown points, and 5-8 representative quotes are computed without the LLM. The large model then writes the summary, why it matters, and research questions (`prompts/opportunity_synthesis_vN.md`). Every sentence must cite evidence items; one without a valid citation is flagged ⚠.
- A cluster close to one from the last LLM-labeled run keeps its area ID, so PM decisions made with `discovery curate-area` stay applied.

It writes `eval/opportunity_areas.md` and the Gate G4 sheet `eval/opportunity_review.csv`. `--no-llm` previews the areas for free, and `--estimate` prices the LLM calls.

For Gate G4, fill in `coherence_1_5` (1-5), `specific_not_generic` (yes/no), and optionally `action` (keep, rename, merge, split, archive) with `action_detail` in the sheet. Then run `discovery review-areas`. It checks the sheet against the latest cluster run, computes the average coherence of the top 8 active areas (target ≥ 4), stores the scores in `pm_overrides`, and writes `eval/opportunity_review_report.md`. The report includes each area's embedding coherence as a second signal and the `curate-area` commands for the actions you marked.

### Opportunity scoring

`discovery score` ranks the active areas from the latest cluster run (or `--run-id`) and writes `opportunity_scores` plus `eval/opportunity_scores.md`.

- **Frequency** is the share of all vague-retrieval items in the area, blended with a source-balanced share so one high-volume source cannot dominate, times a small log-scaled engagement boost. One calendar day can contribute at most 10% of an area's vague items. The share is mapped to 1-5 by quantiles across areas (fixed cutoffs when there are fewer than 5 areas).
- **Severity** is frustration intensity, the high-stakes share, and the share of ratings at 2 stars or below. Sources with no ratings drop that term and re-weight the other two.
- **Strategic fit** is `5 × mean vague-memory relevance × the share of vague items in the area`, clamped to 1-5.
- **Evidence quality** is quote strength, how many platforms are represented (saturated at 4), and sample size (saturated at 50).
- **Product leverage** and **research value** come from the large model (`prompts/scoring_rubric_v1.md`), one call per area, cached until the brief changes. `discovery override-score` replaces either score; the model score is kept next to it. `--no-llm` uses a heuristic and does not publish.
- The composite uses `config/scoring_weights.yaml`. Areas with fewer than 10 items or evidence quality below 2.5 are labeled emerging and ranked below every adequately evidenced area. The report says whether the top 3 moves when any weight changes by 0.05.

A finished model run sets `published_run` to this cluster run, in the same transaction as the scores. A skipped run (no vague-retrieval items, or no areas) leaves the previous published run in place. `--estimate` prices the rubric without calling the model.

The published ranking is `eval/opportunity_scores.md` (run `2026-10-02T102736_d4e5bd`). The weights in `config/scoring_weights.yaml` were signed off on 2026-10-02 (decision D8). The report's sensitivity section shows that a 0.05 change in several weights moves second and third place; first place stays the milestone-photos area.

### Exports

`discovery export` writes the published run (or `--run-id`) as CSV, PDF, or a Google Sheet. Every file includes the run id, the date, the scoring weights, and the active filters (`--filters some.json`). CSV and Sheets default to an internal audience. PDF defaults to external: high-stakes and grief quotes are left out, and phrases like "my sister Priya" become "my sister [name]". The dashboard Export page builds the same files in memory and does not write them to disk.

Sheets needs a service account (`GOOGLE_SERVICE_ACCOUNT_FILE` or `[gcp_service_account]` in Streamlit secrets). Share the target spreadsheet with that account as an Editor, and pass `--spreadsheet-id` or `SHEETS_SPREADSHEET_ID`. If the account cannot open it, the error says which email to share with.

### Pipeline, publish, and retention

`discovery run-all` runs ingest, prep, classify, extract, cluster, and score under one run id, then a publish check:

- Every stage finished (`ingest` may be partial when one source fails).
- There is at least one active area, each with scores and a representative quote.
- Evidence volume did not fall by more than 90% from the published run.

If a check fails, the previous published run stays on the dashboard and the reasons are stored on the `run_all` row for the Data Quality page. A skipped score (nothing new to rank) leaves the pointer alone and exits successfully.

Overlapping runs are refused. GitHub Actions uses one `discovery-pipeline` concurrency group for the weekly pipeline and the daily App Store ingest, and the commands take a database lock (a Postgres advisory lock, or a file lock on SQLite).

`discovery run-all` also refuses to start when `prompts/` or `llm.small_model` / `llm.large_model` differ from `eval/regression_baseline.json`. Run the gold-set eval, then `discovery eval-regression --accept`, before the next real run. `--skip-regression` is for experiments only.

`discovery purge --older-than 90d` deletes old raw payloads, the LLM cache, and unpublished run outputs. It never deletes the published run. `--dry-run` prints the counts first.

`discovery cost-report` writes runtime, token, and cost totals for a run and compares them with the rate limits and the project budget.

In GitHub Actions, **Pipeline** (`pipeline.yml`) runs `discovery run-all` every Monday and on demand. **App Store daily** still runs every day. Both need `DATABASE_URL`, `AUTHOR_HASH_SALT`, and (for the weekly run) `ANTHROPIC_API_KEY`. A failed job shows on the Data Quality page and, with GitHub's Actions notifications on, sends email. The dashboard does not need a redeploy to show a newly published run.

### LLM provider, limits, and spend

The LLM is Anthropic Claude (`llm.provider: anthropic`; key in `ANTHROPIC_API_KEY`). Groq is
still supported by setting `llm.provider: groq` and `GROQ_API_KEY`.

`llm.rate_limits` starts conservative (45 RPM, 30K tokens/min). Raise it, and
`max_concurrency`, to match the account's
[Limits page](https://console.anthropic.com/settings/limits).

Two spend guards run before every call, using that call's worst-case cost:
`llm.max_cost_usd_per_run` (per command) and `llm.project_budget_usd` (all LLM spend recorded in
`pipeline_runs`, currently the PM's $9.00 cap). A stage that hits either one stops cleanly.
Spend is recorded when a run ends, so an LLM command refuses to start while another LLM stage
is still running (`llm.block_concurrent_runs_hours`).
Finished calls are cached, so nothing is paid twice. `uv run discovery runs` shows spend per
stage. Set a monthly spend limit in the Anthropic Console as a backstop.

## Dashboard

```bash
uv sync --group dev
uv run streamlit run dashboard/app.py
```

Opens http://localhost:8501. It reads the published run from `DATABASE_URL` (`.streamlit/secrets.toml`, then `.env`, then `data/discovery.db`). Sidebar filters apply on every page, including Export. Corrections go to `pm_overrides` and stay in front of the AI values. How to use the six pages is in [`Docs/dashboard-walkthrough.md`](Docs/dashboard-walkthrough.md). Deploying the private app and operating the schedule is in [`Docs/deployment-runbook.md`](Docs/deployment-runbook.md).

## Troubleshooting

| What you see | What to do |
|---|---|
| `No published run` | Run `discovery score` (or `discovery run-all`) against a database that has areas. The dashboard only reads `published_run`. |
| A newer run failed and the pages look unchanged | That is the publish check. Open Data Quality for the reason. Fix it and run again. The previous run stays up. |
| `Another pipeline run holds the lock` | Wait for the weekly or daily GitHub Actions job to finish. Do not start a second `run-all`. |
| `Prompt … is not the accepted` | The prompt or model changed. Run `discovery eval` and `discovery eval-extract`, then `discovery eval-regression --accept` if the metrics hold. |
| Sheets export says the service account cannot open the spreadsheet | Share the sheet with the `client_email` in the service account JSON, as Editor. |
| `ANTHROPIC_API_KEY secret is not set` on GitHub Actions | Add the key under Settings → Secrets and variables → Actions, then re-run the workflow. |
| Dashboard is empty on Streamlit Community Cloud | Secrets need the pooled `DATABASE_URL`. The app reads only the published run. See the runbook. |
| Spend guard stops a stage | `discovery runs` shows the cost. Raising `llm.project_budget_usd` needs PM approval. Cached calls are free on a re-run. |

## Tests and linting

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Database tests run on SQLite, and also on Postgres when `TEST_DATABASE_URL` is set (CI does
this with a Postgres service container).

## Hosted Postgres (one-time)

1. Create a free Neon or Supabase database and copy the pooled connection string.
2. Add `DATABASE_URL`, `ANTHROPIC_API_KEY`, and `AUTHOR_HASH_SALT` as GitHub Actions secrets.
3. Run the **Initialize database** workflow (or `DATABASE_URL=... uv run discovery init-db`).
