# Discovery readout: vaguely remembered photo retrieval

**Date:** 2026-10-03  
**Frozen run:** `2026-10-02T102736_d4e5bd` (published 2026-10-02 11:34 UTC)  
**PDF:** [`eval/discovery_readout.pdf`](../eval/discovery_readout.pdf) (internal export of that run: ranked table, one page per top area, methodology)  
**Evidence review:** [`eval/readout_deep_dive.md`](../eval/readout_deep_dive.md)  
**Next step:** interviews, using [`interview-guide.md`](interview-guide.md)

## Decision

Pursue **edited, saved, or new photos missing from the main view** (`oa-69e09da1`) as the retrieval problem to research first.

People edit, crop, download, upload, or save a copy, then open the main library, the Camera folder, or the share picker in another app and the photo is not there. Often it is still in Recently added, Pictures, the website, or another folder. The memory they bring is the action they just took and the task (post, attach, send), not a date or a caption.

This is rank 2 of 11, composite **3.17, Medium**. It stays inside the top 3 under every ±0.05 weight change. The item-level read supports the failure mode: all 28 items reviewed are `target_not_surfaced`.

The composite winner stays in the readout as a separate finding. **Irreplaceable milestone photos of loved ones appear missing** (`oa-6e0c776d`, 3.45) is first in every weight scenario. Those 15 items are apparent loss of backed-up files. Product leverage is 2.00 and research value is 3.00: interviews there would mostly confirm a backup and recovery failure. That work belongs with backup and account recovery, which this phase sets aside. The same is true of rank 3, **backed-up photo blocks that vanish after updates or phone changes** (`oa-af73e0ca`, 3.13), and third place is not stable.

Gate G6 should agree that the next step is the interview guide, not a concept test. The cluster mixes several triggers, and we do not yet know where people expect a just-saved photo to land.

## Corpus and method

| Stage | Result on the frozen run |
| --- | --- |
| Ingestion | 21,721 public items from Play Store, the App Store reviews page, a Reddit and community Google Sheet, and Help Community threads |
| Prep | 21,457 items after clean and dedupe; 8,716 eligible for the funnel |
| Relevance | 121 vague-memory retrieval, 469 general retrieval, 8,126 not retrieval |
| Extraction | 590 retrieval items; quote grounding 100% on the corpus |
| Clustering | 400 insights clustered into 11 active areas (PM curation merged the two “photos vanished” areas and split shared albums out) |
| Scoring | Rubric `scoring_rubric_v1` on Claude Opus 5.5; D8 weights |

A fresh GitHub Actions run was not started. This published run is the latest scored run. The only later pipeline row is the Gate G4 review, which does not change scores. Recorded LLM spend is **$8.90 of the $9.00** project cap, so a full re-run would cross the budget. The run id above is the one this readout uses. See [`eval/readout_run.json`](../eval/readout_run.json).

Vague-memory retrieval and general retrieval were labeled separately and stay visible on every area. Storage, pricing, backup, sharing, and deletion stay out of the funnel unless the person is blocked from finding a photo. Several large areas are still mostly general retrieval; the vague count is on each row below.

## What people remember, and what they forget

When a forgotten detail is actually stated, it is almost always the exact date (23 items in the vanished-blocks area, a handful elsewhere). Most items never name something the person forgot. They name the cue they still have.

| Area | What they still have | What the text says they forgot |
| --- | --- | --- |
| Milestone photos missing | The person, pet, or occasion, and a rough span (“years ago”, “2012–2026”) | Not stated |
| Edited / saved / new, missing from the main view | The action (edit, crop, download, upload, save) and the task (post, attach, send). 57 of 69 items have a cue; situation is the most common | Not stated. The location the app chose is often unknown to them |
| Year-blocks missing after a phone change or update | A time boundary (“before 2023”, “2013 to 2018”) and the event that preceded the gap | Exact date, on 23 items |
| Downloads filed under old capture dates | When or how the file arrived, not when it was taken | Exact date, rarely |
| Exact keyword search | A concrete word: “yellow truck”, a file name, text they typed on the photo | Exact date, rarely. The query itself is precise; the result set is partial or empty |

That last row is a different problem from vague memory. People remember a specific keyword and the product returns too little. It is rank 5, with only 6 of 72 items labeled vague-memory retrieval.

## How the 11 areas compare

Nothing in this run reaches the High band (3.8). Medium starts at 3.0. Strategic fit is clamped to 1.0 for almost every area, because it multiplies vague-memory share by mean vague relevance. Frequency and severity therefore do most of the ranking. Product leverage is what marks a retrieval follow-up, and it is 15% of the composite. The signed-off weights (decision D8, 2026-10-02) stay as they are. This readout uses them, then applies the problem statement: backup and deletion are in scope only when they block retrieval.

| Rank | Area | Composite | Items (vague) | Leverage | What the evidence is |
| --- | --- | --- | --- | --- | --- |
| 1 | Irreplaceable milestone photos appear missing | 3.45 | 15 (11) | 2 | Files of a person, pet, or occasion look gone |
| 2 | Edited, saved, or new photos missing from the main view | 3.17 | 69 (21) | 4 | The item still exists in a side view or another app |
| 3 | Backed-up blocks vanish after updates or phone changes | 3.13 | 105 (47) | 2 | Whole year spans look gone |
| 4 | Downloads and restores filed under old capture dates | 3.12 | 14 (6) | 5 | People remember arrival; the timeline uses capture date |
| 5 | Exact keyword and name search returns too little | 3.10 | 72 (6) | 5 | A known word returns a partial set or nothing |
| 6 | Photos shared by family and friends are unreachable | 2.83 | 10 (5) | 4 | People remember who shared it |
| 7 | Locked Folder photos missing after moves or resets | 2.56 | 11 (2) | 2 | Mostly backup and sync |
| 8 | Face groups lost or split, so person search fails | 2.49 | 21 (1) | 4 | The person is the cue; the group is wrong or gone |
| 9 | Missing day and month dividers break date browsing | 2.38 | 32 (1) | 4 | People know the day and cannot see the boundary |
| 10 | Finding photos inside large album collections | 2.37 | 22 (1) | 4 | People know the album and cannot search it |
| 11 | Mixed-size thumbnails disrupt scrolling | 2.19 | 13 (0) | 4 | A grid-layout complaint, with no vague-memory items |

Full score arithmetic is in [`eval/opportunity_scores.md`](../eval/opportunity_scores.md). Source links for every area are in [`eval/opportunity_areas.md`](../eval/opportunity_areas.md) and in the PDF.

**Weight check.** First place does not move. `oa-69e09da1` stays in the top 3 in all 12 scenarios and trades second and third with the vanished-blocks area. Vanished blocks drop out in five of the eight scenarios that change the order. The areas that enter are old capture dates (`oa-a8abbac6`) or keyword search (`oa-53a15320`). Detail is in the evidence review.

## What we checked in the top 3

Method and item tables: [`eval/readout_deep_dive.md`](../eval/readout_deep_dive.md). Seed `20261003`. The milestone area was read in full (15 items). The other two were 8 representative quotes plus 20 other items each.

| Area | Summary | What to carry into the decision |
| --- | --- | --- |
| Milestone photos | Holds. 15/15 look missing. One item is a trash delete of a wedding video. One video still exists and will not play. | Severity is real. Recorded search attempts are few. This is a recovery finding. |
| Edited / saved / new | The failure mode holds (28/28 not surfaced). The title covers the representative quotes and 7 of the 20 random items. The rest are screenshots, web-only albums, hidden items, and a timeline that skips photos. | One mechanism, several triggers. G4 coherence was 4 and “specific” was no. Interviews have to separate the triggers. |
| Vanished year-blocks | The year-boundary story is the majority, and it overlaps milestone loss. Seven of 20 random items are a neighboring problem (SD card, someone else deleted them, albums replaced by suggestions, or the account still has them and the app does not show them). | Coherence 3 at G4. Third place moves when weights move. |

## Why this area

| Dimension | Score | Reading |
| --- | --- | --- |
| Frequency | 4.20 | 21 of 121 vague items (17% raw). Source-balanced share 13%. |
| Severity | 2.25 | Mean frustration 3.07. No high-stakes items. 71% of rated reviews are 1–2 stars (37 of 52). |
| Strategic fit | 1.00 | Clamped. Raw value 0.25, because 21 of 69 items are vague-memory retrieval. |
| Evidence quality | 4.50 | Mean strength 3.74. Four platforms (Android, Google Community, Reddit, YouTube). 69 items. |
| Product leverage | 4.00 | Most items are copies, downloads, uploads, and shared-library saves that exist and fail to show in the main timeline or a share picker. |
| Research value | 4.00 | The people are recruitable, the task is often time-sensitive, and we do not know which action causes the most trouble or where they expect the file to land. |
| Composite | 3.17 | 0.20×4.20 + 0.20×2.25 + 0.20×1.00 + 0.15×4.50 + 0.15×4.00 + 0.10×4.00. Band: Medium. |

Sources: Play Store 38, Google Sheet 28, Help Community 3. Platforms: Android 52, Reddit 13, Google Community 3, YouTube 1. Content types: 56 photos, 7 screenshots, 2 videos, 4 unknown.

Representative evidence, with links:

- “Editing a photo and saving a copy is useless, as it won't even show up in the app.” [Play Store](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `268a4406`
- “now anytime I crop or edit a picture in anyway, it no longer shows up on my camera, recent photos, or any other album in other apps to post.” [Play Store](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=8457344b-60bb-46aa-bfe6-533241125099) · `87811740`
- “I can't see my photos in the main photos album but I can see them in the recently added folder.” [Play Store](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=28989f59-8f51-4c4c-ad34-f7a0aee322e1) · `a65cf50d`
- “it never show recently downloaded photos on top.” [Play Store](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=23dacf60-656a-44e6-8d7b-fd50ef90f132) · `d8abe474`

The specific claim for the next study: **a photo the person just edited, saved, or downloaded does not appear in the surface they open to use it, while another surface sometimes still has it.**

If interviews show those triggers are different problems, the next area to open is **recently downloaded or restored photos filed under old capture dates** (`oa-a8abbac6`). It is rank 4, composite 3.12, product leverage 5, G4 coherence 5, and it enters the top 3 when severity, evidence quality, or product leverage moves by 0.05. It has 14 items, so it is the follow-on, not the first study.

## Limitations

- The corpus is public posts in English, not a sample of Google Photos users. Play Store is most of the volume. The App Store contributed 78 eligible items and 1 vague-memory item, because collection stays on the allowed reviews page.
- Rank 2’s strategic fit is at the floor. Most of its 69 items are general retrieval of a recent action, not a vague memory of an old photo. The vague-memory slice is the notification and “I remember that I saved it” cases. Interviews have to count how often the cue is only the action.
- The area name is sharper than the cluster. Screenshot filing, web-versus-app albums, and hidden items share the “not surfaced” label.
- External PDF exports drop high-stakes and grief items. The PDF attached here is the internal export, so those quotes remain, with names redacted. Author names are never shown.
- No area scored High. A Medium composite is the top of this corpus under the signed-off weights.
- This readout does not propose a search design.

## Next steps

1. **G6.** Walk stakeholders through this note and the PDF. Ask them to confirm interviews as the next step for `oa-69e09da1`.
2. **Recruit** with the screener in [`interview-guide.md`](interview-guide.md). Target 8–12 people. At least six whose incident is an edit, crop, download, or save.
3. **Run the interviews** before any concept test. The guide’s job is to learn the expected landing place, the order of attempts, and whether “I can see it somewhere else” means the person trusts that the photo is safe.
4. **Bring milestone loss and vanished year-blocks** to whoever owns backup and recovery, with the quotes in the evidence review. They are the highest-severity findings, and they are a different project.

## Learnings for the next phase

**Taxonomy**

- Context-based retrieval failure absorbed five areas and 209 items. Time-based memory gap had 83 extracted items and no active area of its own; the date-divider cluster was labeled `other_emergent` even though most of its items were extracted as time-based. The next taxonomy pass should decide whether “I know the day and the timeline will not show the boundary” is time-based memory or a layout complaint.
- Screenshot and document retrieval, object and visual-detail search, and location ambiguity produced no cluster. They remain in the extraction counts (10, 13, and 1 items) and are too thin to be areas.
- Milestone loss and vanished year-blocks overlap. `c74cd14d` and `3a306f65` are milestone photos inside a vanished block. A later clustering pass can keep one parent for “the file appears gone” and leave “the file is in another view” as its own area.
- Split `oa-69e09da1` if interviews confirm separate triggers: just-saved copies, web-versus-app library, and screenshot or album filing.

**Sources**

- iOS is still thin. Another storefront pass will not fix that while collection stays on the allowed reviews page; a licensed source or a user panel would.
- YouTube appears once, inside the selected area. A small, deliberate YouTube comments pull on “edited photo not showing” would test whether that platform adds new triggers.
- Shared-library saves are called out in the research questions and are only a couple of items. Partner sharing and shared libraries are worth a targeted Help Community query on the next ingest.

**Prompts and scoring**

- Forgotten-detail extraction is usually empty because people do not say what they forgot. Keep the “only what is stated” rule. Use interviews to fill that field, and do not loosen the prompt to invent a forgotten date.
- Strategic fit sits on the floor for 10 of 11 areas, so it barely ranks them. A later weight review can raise product leverage only after a new gold-set regression, and only with an explicit D8 change. Until then, read leverage beside the composite when the question is which retrieval problem to study.
- Synthesis of `oa-69e09da1` should be prompted to separate sub-themes in the summary. The cluster label is one sentence, and the random sample shows three situations under it.
- Representative quotes are sometimes a short fragment (“They have disappeared”) while the life-event lives in the extracted cues. The readout pairs the quote with the cue. A future synthesis pass can prefer the sentence that carries both the cue and the breakdown, still only when that sentence is in the text.
