# Dashboard walkthrough

The PM dashboard reads the published scored run and lets you compare opportunity areas, open the evidence, and correct the AI. It does not run scraping, embeddings, or model calls.

Local pages are in place. Two steps still need your Streamlit Community Cloud account: the first deploy, and inviting viewers. Gate G5 is those three tasks on the **deployed** app.

## Open it on this computer

From the repo root, with `DATABASE_URL` already in `.env` (the same Neon database the pipeline writes to):

```bash
uv sync --group dev
uv run streamlit run dashboard/app.py
```

The app opens at http://localhost:8501. If `.streamlit/secrets.toml` exists, that `DATABASE_URL` wins. Otherwise the app uses `.env`, then `data/discovery.db`.

A copy of the secrets shape is in `.streamlit/secrets.toml.example`. Do not commit the real file.

## How to read the app

The left sidebar is the same on every page: page links, then filters. Filters stay as you move between pages. **Clear all filters** returns to the full published corpus.

Spam is left out of the analysis counts. Category, content type, severity, confidence, and vague-memory filters apply from the retrieval stage onward. Source, platform, date, rating, and language apply to the earlier counts too.

The chip at the top is the published run. If a newer pipeline run is still going or has failed, a banner says so, and the pages keep showing the last published run.

| Page | What it is for |
|---|---|
| Overview | How much feedback made it from raw items to vague-memory retrieval, by source and category |
| Compare | Ranked areas, radar and bubble charts, and weight sliders |
| Detail | One area: why it matters, sources, content types, remembered cues, forgotten details, search attempts, where retrieval breaks down, quotes, scores, research questions |
| Evidence | Searchable quotes and a form to correct one item |
| Quality | Spend, grounding failures, run history, prep and dedup, and the low-confidence queue |
| Export | Placeholder. CSV, Sheets, and PDF are Phase 8. The page already shows the run, weights, and filters a later export will stamp |

A teal **PM** badge means your correction is what the page is showing. The AI value stays in the database next to it.

## Three tasks (Gate G5)

Do these on the deployed app once it is private. The same clicks work locally.

1. **Top 3 for iOS.** Open **Compare**. In the sidebar, set **Platform** to iOS and leave the other filters open. The three cards at the top are the highest-ranked areas that still have at least one matching iOS quote. The table under them is the full filtered ranking.
2. **What users remember, forget, and where it breaks.** Open one of those areas (the select box under the table, or **Detail** in the sidebar). Read **Remembered cues**, **Forgotten details**, and **Where retrieval breaks down**, then the quotes under **What users said**.
3. **Correct a misclassified item.** Open **Evidence**, pick the row, and save a category, retrieval type, or “not a retrieval problem.” Refresh the page. The row still shows the correction, with a PM badge. The same row is still corrected after you close the app and open it again, and after the next pipeline run, because the dashboard reads `pm_overrides` ahead of the AI columns.

## What saves, and when

| Action | When you see it |
|---|---|
| Rename, PM note, product leverage, research value, item correction | Immediately. Stored in `pm_overrides` |
| Weight sliders on Compare | Immediately, in this browser session only. **Reset weights** returns to `config/scoring_weights.yaml`. Nothing is written |
| Merge, split, archive, or restore an area | Saved immediately. The area list changes on the next `discovery cluster` / `discovery score` |

## Deploy to Streamlit Community Cloud

This is the remaining Phase 7 work (P7.0 and P7.12).

1. Push the repo to GitHub if it is not there yet.
2. In [share.streamlit.io](https://share.streamlit.io), create an app from that repo.
3. Main file: `dashboard/app.py`. Python version comes from `runtime.txt` (`python-3.11`). Dependencies come from `dashboard/requirements.txt`, which leaves out the pipeline and ML packages.
4. In the app’s **Secrets**, paste `DATABASE_URL` from `.streamlit/secrets.toml.example`, using the Neon **pooled** connection string (the same one GitHub Actions uses).
5. In the app’s sharing settings, set it to **private** and invite the viewer emails from decision D4b. The app shows user quotes, so it should not be public.

After deploy, run the three Gate G5 tasks on that URL. Page loads after the app is awake should stay under about 3 seconds. The first wake from sleep is slower; later loads use cached queries.
