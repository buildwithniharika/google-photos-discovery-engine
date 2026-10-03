# Opportunity scores

Run: `2026-10-02T102736_d4e5bd`. Rubric: `scoring_rubric_v1` on `claude-opus-5-5` (llm).
Vague-retrieval items in the denominator: 121. Items outside the analysis window, dropped from areas: 0.

Weights (decision D8, signed off 2026-10-02): frequency 0.20, severity 0.20, strategic_fit 0.20, evidence_quality 0.15, product_leverage 0.15, research_value 0.10.

Top 3: oa-6e0c776d, oa-69e09da1, oa-af73e0ca. The top 3 changes under at least one ±0.05 weight change; see below.

## Ranking

| Rank | Area | Composite | Band | Items | Vague | Flag |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Irreplaceable milestone photos of loved ones appear missing | 3.45 | Medium | 15 | 11 |  |
| 2 | Edited, saved, or new photos missing from main view | 3.17 | Medium | 69 | 21 |  |
| 3 | Backed-up photo blocks vanish after updates or phone changes | 3.13 | Medium | 105 | 47 |  |
| 4 | Recently downloaded or restored photos filed under old dates | 3.12 | Medium | 14 | 6 |  |
| 5 | Exact keyword and name searches broken by AI search | 3.10 | Medium | 72 | 6 |  |
| 6 | Photos shared by family and friends unreachable | 2.83 | Low | 10 | 5 |  |
| 7 | Locked Folder photos missing after moves, updates, resets | 2.56 | Low | 11 | 2 |  |
| 8 | Face groups lost, split, or incomplete break person search | 2.49 | Low | 21 | 1 |  |
| 9 | Missing day and month dividers break date-based browsing | 2.38 | Low | 32 | 1 |  |
| 10 | Finding photos inside large, scattered album and folder collections | 2.37 | Low | 22 | 1 |  |
| 11 | Mixed-size grid thumbnails disrupt scroll-based photo finding | 2.19 | Low | 13 | 0 |  |

## Why each area ranks here

### 1. Irreplaceable milestone photos of loved ones appear missing

`oa-6e0c776d` · life_event_retrieval · Medium

- Frequency 4.60 = quantile of adjusted share 0.2046 (raw 9% = 11/121 vague items, source-balanced 32%, engagement ×1.01).
- Severity 4.67 = intensity 4.33, 100% high-stakes, 100% ≤2-star (9/9 rated).
- Strategic fit 1.97 = 5 × mean vague relevance 0.54 × vague share 73% (11/15).
- Evidence quality 4.01 = mean strength 3.67, 4 platforms (Android, Google Community, Reddit, iOS), sample 15 items.
- Product leverage 2.00 = llm. The evidence centers on backed-up milestone photos and videos that appear gone after setup, account recovery, updates, gallery merges, or storage policy changes, and trash, archive, and website checks found nothing. Better search or memory understanding would not bring these items back, though clearer explanations of item state (deleted, hidden, unsynced, unplayable) could help a minority of cases.
- Research value 3.00 = llm. The stakes are real (late parents, deceased pets, weddings) and there are open unknowns about what triggered each search, what users try, and whether an unplayable copy counts as found. However, the 15 items mostly describe data-loss events that are already specific, so a study would largely confirm a backup and sync problem rather than surface retrieval behavior to test.
- Composite 3.45 = 0.20×4.60 + 0.20×4.67 + 0.20×1.97 + 0.15×4.01 + 0.15×2.00 + 0.10×3.00.

### 2. Edited, saved, or new photos missing from main view

`oa-69e09da1` · context_based_retrieval_failure · Medium

- Frequency 4.20 = quantile of adjusted share 0.1525 (raw 17% = 21/121 vague items, source-balanced 13%, engagement ×1.01).
- Severity 2.25 = intensity 3.07, 0% high-stakes, 71% ≤2-star (37/52 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.17 × vague share 30% (21/69). Raw 0.25, clamped to 1.00.
- Evidence quality 4.50 = mean strength 3.74, 4 platforms (Android, Google Community, Reddit, YouTube), sample 69 items.
- Product leverage 4.00 = llm. Most cases are edited copies, downloads, uploads, and shared-library saves that exist but fail to surface in the main timeline or share pickers. Users can still reach them through Recently added or device folders, so better surfacing by recency and action, plus clearer result explanations, would address the main cases. Some cases sit outside search, such as the unbacked-up device folder and older backed-up photos missing from the app's main view after updates.
- Research value 4.00 = llm. There are 69 items from three sources, involving recruitable users with time-sensitive tasks like posting to Facebook, Vinted, or eBay. Clear gaps remain around how often each action causes trouble, where users expect edited or shared-library saves to land, and whether they believe items are lost. The problem is already fairly specific and partly mixes in sync and display issues, so a study would partly confirm what is known.
- Composite 3.17 = 0.20×4.20 + 0.20×2.25 + 0.20×1.00 + 0.15×4.50 + 0.15×4.00 + 0.10×4.00.

### 3. Backed-up photo blocks vanish after updates or phone changes

`oa-af73e0ca` · context_based_retrieval_failure · Medium

- Frequency 5.00 = quantile of adjusted share 0.3427 (raw 39% = 47/121 vague items, source-balanced 29%, engagement ×1.00).
- Severity 3.09 = intensity 3.52, 37% high-stakes, 78% ≤2-star (38/49 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.29 × vague share 45% (47/105). Raw 0.65, clamped to 1.00.
- Evidence quality 4.10 = mean strength 3.70, 3 platforms (Android, Google Community, Reddit), sample 105 items.
- Product leverage 2.00 = llm. The 105 items describe whole albums and year spans (before 2023, 5 years, 4000+ photos) that disappear after updates, phone changes, or transfers and are not found in trash, archive, Drive, or Takeout, so the problem is mostly sync and data that appears gone. Clearer status signals could help in cases with leftover traces, such as album counts that won't open or storage usage suggesting the photos still exist, but better search would not bring the items back.
- Research value 4.00 = llm. There is a recruitable group across three sources (Play Store 41, Google Community 36, Google Sheet 28) with real stakes, such as photos of a late father, and clear gaps: how users work out which ranges are missing, what triggered the loss, and what would restore their trust. The cases cluster around one data-loss pattern rather than spanning several contexts.
- Composite 3.13 = 0.20×5.00 + 0.20×3.09 + 0.20×1.00 + 0.15×4.10 + 0.15×2.00 + 0.10×4.00.

### 4. Recently downloaded or restored photos filed under old dates

`oa-a8abbac6` · context_based_retrieval_failure · Medium

- Frequency 3.80 = quantile of adjusted share 0.0432 (raw 5% = 6/121 vague items, source-balanced 4%, engagement ×1.01).
- Severity 2.36 = intensity 3.21, 0% high-stakes, 75% ≤2-star (9/12 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.21 × vague share 43% (6/14). Raw 0.44, clamped to 1.00.
- Evidence quality 3.59 = mean strength 3.64, 3 platforms (Android, Google Community, Reddit), sample 14 items.
- Product leverage 5.00 = llm. The items are present but filed by embedded capture or file dates, while users remember when or how they arrived (download, backup, restore). Surfacing items by arrival recency, searching on arrival cues, and explaining where an item was placed would directly address the scrolling through years of images that the quotes describe.
- Research value 4.00 = llm. Across 14 items from Play Store and sheet sources, the problem is consistent and the users (people who download, restore, or upload) are recruitable. Frequency, which origins cause the most misplaced items, the task behind the search, and abandonment remain unknown, but the core breakdown is already fairly specific.
- Composite 3.12 = 0.20×3.80 + 0.20×2.36 + 0.20×1.00 + 0.15×3.59 + 0.15×5.00 + 0.10×4.00.

### 5. Exact keyword and name searches broken by AI search

`oa-53a15320` · search_trust_breakdown · Medium

- Frequency 3.40 = quantile of adjusted share 0.0427 (raw 5% = 6/121 vague items, source-balanced 3%, engagement ×1.01).
- Severity 2.49 = intensity 3.42, 1% high-stakes, 76% ≤2-star (32/42 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.05 × vague share 8% (6/72). Raw 0.02, clamped to 1.00.
- Evidence quality 4.50 = mean strength 3.75, 4 platforms (Android, Google Community, Reddit, iOS), sample 72 items.
- Product leverage 5.00 = llm. The breakdowns are entirely within search: object keywords like "yellow truck" and "police" return partial or zero results, user-written descriptions and exact text are ignored, and classic search returns complete results. Better retrieval and result explanations would address this directly.
- Research value 3.00 = llm. The problem is already specific and well evidenced across 72 items, with consistent keyword-recall failures compared against classic search. Open gaps remain on how common exact-match intent is versus descriptive intent and on the real cost of failures, but only 6 items are vague memory retrieval and the high-stakes share is low, so a study would mostly confirm what is known.
- Composite 3.10 = 0.20×3.40 + 0.20×2.49 + 0.20×1.00 + 0.15×4.50 + 0.15×5.00 + 0.10×3.00.

### 6. Photos shared by family and friends unreachable

`oa-5fa72a6b` · context_based_retrieval_failure · Low

- Frequency 3.00 = quantile of adjusted share 0.0354 (raw 4% = 5/121 vague items, source-balanced 3%, engagement ×1.00).
- Severity 2.55 = intensity 3.10, 0% high-stakes, 100% ≤2-star (6/6 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.28 × vague share 50% (5/10). Raw 0.71, clamped to 1.00.
- Evidence quality 3.49 = mean strength 3.70, 3 platforms (Android, Google Community, Reddit), sample 10 items.
- Product leverage 4.00 = llm. Most items are retrieval and navigation failures that search and UX changes could address: removed "sharing" entry points after an update, shared albums missing from title or location search despite appearing in the albums list, and a tagged gallery reachable only by URL. A few cases sit outside retrieval, such as photos vanishing after the sharer deleted them and a shared link returning a deleted or invalid error.
- Research value 4.00 = llm. The 10 items show a recruitable group who remember the sharer (dad, son, husband, friend) more than the content. The brief notes real gaps: which cue users rely on, what they tried and in what order, and how they separate shared items from device folders. Stakes and frequency are undescribed, and some sharing and ownership confusion may extend beyond retrieval.
- Composite 2.83 = 0.20×3.00 + 0.20×2.55 + 0.20×1.00 + 0.15×3.49 + 0.15×4.00 + 0.10×4.00.

### 7. Locked Folder photos missing after moves, updates, resets

`oa-a340b4dd` · context_based_retrieval_failure · Low

- Frequency 2.60 = quantile of adjusted share 0.0146 (raw 2% = 2/121 vague items, source-balanced 1%, engagement ×1.00).
- Severity 3.57 = intensity 3.91, 55% high-stakes, 80% ≤2-star (4/5 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.10 × vague share 18% (2/11). Raw 0.09, clamped to 1.00.
- Evidence quality 3.54 = mean strength 3.73, 3 platforms (Android, Google Community, Reddit), sample 11 items.
- Product leverage 2.00 = llm. Most items describe Locked Folder content that appears gone after resets, re-sign-ins, updates, or with backup disabled, which is data loss or sync failure that search and memory features would not bring back. Only the case of items moved out with a forgotten origin, where file-name search failed, is a retrieval problem, along with perhaps a clearer status for the missing entry point.
- Research value 3.00 = llm. There are real stakes and clear unknowns about how users understand Locked Folder backup and where they expect moved items to land, across 11 items from three sources. However, only 2 items involve vague memory, and the problem is already fairly specific as a backup and sync failure, so a study would mostly confirm it rather than open retrieval concepts to test.
- Composite 2.56 = 0.20×2.60 + 0.20×3.57 + 0.20×1.00 + 0.15×3.54 + 0.15×2.00 + 0.10×3.00.

### 8. Face groups lost, split, or incomplete break person search

`oa-bf3f5f4c` · people_event_association_failure · Low

- Frequency 2.20 = quantile of adjusted share 0.0076 (raw 1% = 1/121 vague items, source-balanced 1%, engagement ×1.00).
- Severity 2.21 = intensity 3.24, 0% high-stakes, 59% ≤2-star (10/17 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.03 × vague share 5% (1/21). Raw 0.01, clamped to 1.00.
- Evidence quality 3.38 = mean strength 3.67, 2 platforms (Android, Reddit), sample 21 items.
- Product leverage 4.00 = llm. The failures sit in face detection, grouping, merging, and manual tagging inside Google Photos' own memory understanding. Examples include clearly visible faces not detected, one person split into groups, places labeled as people, and groups lost after updates, so better person recognition and correction UX would directly address most cases. Some loss tied to updates or resets may be system state rather than retrieval.
- Research value 3.00 = llm. The breakdown is already concrete and consistent across 21 items, mostly Play Store complaints, but only 1 is a vague-memory item and the high-stakes share is zero. The open gaps, such as which cues users hold besides the person, how they fall back, and what counts as a complete set, are real but narrow, so a study would mostly confirm the grouping failures.
- Composite 2.49 = 0.20×2.20 + 0.20×2.21 + 0.20×1.00 + 0.15×3.38 + 0.15×4.00 + 0.10×3.00.

### 9. Missing day and month dividers break date-based browsing

`oa-260980f7` · other_emergent · Low

- Frequency 1.60 = quantile of adjusted share 0.0069 (raw 1% = 1/121 vague items, source-balanced 1%, engagement ×1.00).
- Severity 2.32 = intensity 3.09, 0% high-stakes, 77% ≤2-star (24/31 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.02 × vague share 3% (1/32). Raw 0.00, clamped to 1.00.
- Evidence quality 3.34 = mean strength 3.16, 2 platforms (Android, Reddit), sample 32 items.
- Product leverage 4.00 = llm. The breakdown is a UX layout change (missing day/month headers, month-only zoomed-out grids, tapping items to learn dates), which timeline and browsing design can directly address; the rare wrong-date metadata case from a single evening sits partly outside browse UX.
- Research value 3.00 = llm. With 32 items (31 general retrieval, only 1 vague memory) describing a specific, consistent complaint about removed date dividers, a study would mostly confirm it, though open gaps remain on memory precision, zoom-level needs, and day-level selection tasks; stakes are unstated.
- Composite 2.38 = 0.20×1.60 + 0.20×2.32 + 0.20×1.00 + 0.15×3.34 + 0.15×4.00 + 0.10×3.00.

### 10. Finding photos inside large, scattered album and folder collections

`oa-fcedbf51` · other_emergent · Low

- Frequency 1.60 = quantile of adjusted share 0.0069 (raw 1% = 1/121 vague items, source-balanced 1%, engagement ×1.00).
- Severity 2.36 = intensity 3.23, 0% high-stakes, 75% ≤2-star (15/20 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.02 × vague share 5% (1/22). Raw 0.01, clamped to 1.00.
- Evidence quality 3.21 = mean strength 3.18, 2 platforms (Android, Reddit), sample 22 items.
- Product leverage 4.00 = llm. The breakdowns are album-title search, in-album filtering, grouping and sort order, and scattering caused by auto-sorting and navigation changes. These are search and browse UX problems Google Photos controls directly, though the vanishing album titles may partly reflect a download or sync issue.
- Research value 3.00 = llm. With 22 items, only 1 of them vague-memory retrieval, the complaints are already specific feature gaps such as album name search and sort order, so a study would mostly confirm them. Open unknowns remain around frequency, time cost, and how users' organizing schemes relate to how they later retrieve items.
- Composite 2.37 = 0.20×1.60 + 0.20×2.36 + 0.20×1.00 + 0.15×3.21 + 0.15×4.00 + 0.10×3.00.

### 11. Mixed-size grid thumbnails disrupt scroll-based photo finding

`oa-4ff23301` · other_emergent · Low

- Frequency 1.00 = quantile of adjusted share 0.0000 (raw 0% = 0/121 vague items, source-balanced 0%, engagement ×1.00).
- Severity 2.54 = intensity 3.38, 0% high-stakes, 85% ≤2-star (11/13 rated).
- Strategic fit 1.00 = 5 × mean vague relevance 0.00 × vague share 0% (0/13). Raw 0.00, clamped to 1.00.
- Evidence quality 2.53 = mean strength 2.92, 1 platform (Android), sample 13 items. One platform, so diversity is low.
- Product leverage 4.00 = llm. All 13 general-retrieval items describe a grid-layout UX problem (enlarged thumbnails, the lost uniform grid option, obscured chronological order) that a browse UX change would directly address. Some of the complaints, such as multi-select confusion and overlays covering the view, go beyond retrieval, and none involve vague memory.
- Research value 3.00 = llm. The complaint is already specific and consistent across 10 Play Store and 3 sheet items, so a study would mostly confirm the uniform-grid preference. Real unknowns remain about how often users scroll versus search and how they rely on spatial and chronological memory, but stakes appear low and there are no vague-memory cases.
- Composite 2.19 = 0.20×1.00 + 0.20×2.54 + 0.20×1.00 + 0.15×2.53 + 0.15×4.00 + 0.10×3.00.

## Weight sensitivity

Scenarios where the ordered top 3 changed:

- frequency +0.05 → oa-6e0c776d, oa-af73e0ca, oa-69e09da1
- frequency -0.05 → oa-6e0c776d, oa-69e09da1, oa-53a15320
- severity +0.05 → oa-6e0c776d, oa-af73e0ca, oa-69e09da1
- severity -0.05 → oa-6e0c776d, oa-69e09da1, oa-a8abbac6
- evidence_quality -0.05 → oa-6e0c776d, oa-a8abbac6, oa-69e09da1
- product_leverage +0.05 → oa-6e0c776d, oa-a8abbac6, oa-69e09da1
- product_leverage -0.05 → oa-6e0c776d, oa-af73e0ca, oa-69e09da1
- research_value -0.05 → oa-6e0c776d, oa-69e09da1, oa-53a15320
