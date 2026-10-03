# Deployment and operations runbook

How to put the dashboard and the scheduled pipeline in place, rotate secrets, change the LLM, and recover a failed run. This follows architecture Section 21. A fresh checkout can run the pipeline locally by following the README; this document is for the hosted setup.

The dashboard (Streamlit Community Cloud) and the pipeline (GitHub Actions) share one hosted Postgres database. The dashboard reads only the row in `published_run`. A run that fails its checks never replaces that row, so a bad run does not change what people see, and a good run shows up without redeploying the app.

## 1. Deploy

1. Push the repository to GitHub. Private repositories are supported.
2. Create a hosted Postgres database (Neon or Supabase, free tier) and copy its **pooled** connection string.
3. Add these **GitHub Actions secrets** (Settings → Secrets and variables → Actions):
   - `DATABASE_URL` — the pooled Postgres URL
   - `AUTHOR_HASH_SALT` — a long random string, kept stable
   - `ANTHROPIC_API_KEY` — the Claude key
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — only if a workflow should export a Sheet (the dashboard secret below is the one Export uses)
4. Run **Pipeline** once by hand (Actions → Pipeline → Run workflow) so the tables exist and the first full dataset loads. The first run can take a few hours because of the Help Community crawl.
5. In [Streamlit Community Cloud](https://share.streamlit.io), create an app from this repository: branch `main`, main file `dashboard/app.py`. Python is `runtime.txt` (3.11). Dependencies are `dashboard/requirements.txt`.
6. Paste secrets into the app's **Secrets** box, using `.streamlit/secrets.toml.example` as the shape:
   - `DATABASE_URL` — the same pooled URL
   - `[gcp_service_account]` — the service account JSON, if you want Google Sheets export
   - `GITHUB_TOKEN` — optional, a fine-grained token with **actions:write only**, for the "Run pipeline now" button
   - `GITHUB_REPOSITORY` — `owner/name`, if it is not `buildwithniharika/google-photos-discovery-engine`
7. Set the app to **private** and invite viewers by email. The app shows user quotes, so it should not be public.
8. Open the app, change a filter, save a correction on Evidence, reboot the app from the Cloud menu, and confirm the correction is still there.

## 2. Turn on failure email

GitHub emails people with write access when a scheduled workflow fails, if their notification settings allow it.

1. GitHub → Settings → Notifications → Actions (or System → Actions).
2. Enable email, and choose to be notified of failed workflows.
3. Confirm by opening a failed run: the job log is the source of truth, and the Data Quality page in the dashboard repeats the publish-check reason.

The weekly workflow is **Pipeline** (`pipeline.yml`, Mondays 06:23 UTC, and any manual run). **App Store daily** runs at 03:17 UTC. They share one concurrency group, so they wait for each other instead of writing at the same time. The commands also take a database lock, so a second run started from a laptop exits immediately if one is already going.

## 3. Rotate a secret

1. Create the new key, salt, or database password in the provider's console. Do not delete the old one yet.
2. Update the GitHub Actions secret and the Streamlit secret to the new value. Update `.env` on any machine that runs the pipeline locally. Never commit the value.
3. Re-run **Pipeline** with "Run workflow". For `AUTHOR_HASH_SALT`, do not rotate it casually: a new salt changes every author hash.
4. For the Anthropic key, run `uv run discovery llm-check` locally first (a few cents) and confirm a cached second call.
5. Revoke the old key only after that run succeeds.
6. For the Google service account, create a new key in Google Cloud, replace `GOOGLE_SERVICE_ACCOUNT_JSON` and the Streamlit `[gcp_service_account]` block, share the target spreadsheet with the new `client_email`, export once, then delete the old key.

## 4. Change the LLM model or provider

Do this before the next real run. The weekly pipeline refuses to start when the configured model or the relevance/extraction prompt version is not the accepted baseline.

1. Edit `llm.small_model`, `llm.large_model`, or `llm.provider` in `config/settings.yaml`, and the matching API key. Bump a prompt by adding `prompts/<name>_vN.md` and the `PROMPT_VERSION` constant in code. Do not edit a prompt file that has already been accepted; add a new version.
2. Run the gold-set checks. They spend from the project budget:
   ```bash
   uv run discovery eval --llm
   uv run discovery eval-extract
   ```
3. Accept them only if both reports meet the targets and do not fall below `eval/regression_baseline.json`:
   ```bash
   uv run discovery eval-regression \
     --relevance eval/relevance_report.md \
     --extraction eval/extraction_report.md \
     --accept
   ```
4. Commit the baseline with the config change. The next `discovery run-all` (and the Monday workflow) will allow it.
5. If the reports regress, leave the baseline as it is. The pipeline keeps using the accepted prompt until a later report passes.

`--skip-regression` exists for a local experiment. Do not set it on the scheduled workflow.

## 5. When a scheduled run fails

1. Open the failed Actions run. The last lines and the step summary include the cost report and the data-quality counts.
2. Open the dashboard's **Data Quality** page. A banner says a newer run did not finish. The "Runs that were not published" section quotes the check that failed (a stage error, a missing quote, or a volume drop). The ranked pages still show the last good run.
3. Typical causes:
   - A source returned nothing or was blocked. The other sources still land; ingest is marked partial. If **every** source failed, the run is not published.
   - The Anthropic spend cap stopped a stage. `discovery runs` shows the cost. Raising `llm.project_budget_usd` needs a PM decision. Re-running is cheap for calls already in the cache.
   - The gold-set gate refused a prompt change. Follow section 4, or revert the prompt.
   - The database lock or the concurrency group was held. Wait, then re-run the workflow. Do not cancel a run that is mid-write unless you intend to leave it unpublished.
4. Fix the cause and re-run **Pipeline** from Actions (or **Run pipeline now** on the Export page, if the token is set). A successful publish updates `published_run`. Reload the dashboard; no redeploy is required.
5. If the free-tier database is near its storage cap, preview then delete old raw payloads:
   ```bash
   uv run discovery purge --older-than 90d --dry-run
   uv run discovery purge --older-than 90d
   ```
   The published run is kept.

## 6. Local pipeline

```bash
uv sync
uv run playwright install chromium
cp .env.example .env   # ANTHROPIC_API_KEY, AUTHOR_HASH_SALT, DATABASE_URL
uv run discovery init-db
uv run discovery run-all --since last
uv run streamlit run dashboard/app.py
```

`DATABASE_URL` empty uses `data/discovery.db`. Exports from the CLI land in `data/exports/`. The Streamlit Export page never writes files.
