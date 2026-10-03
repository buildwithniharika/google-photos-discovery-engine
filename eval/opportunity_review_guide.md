# Gate G4 review guide: `eval/opportunity_review.csv`

You are checking whether each opportunity area is **one coherent, specific retrieval problem**.
Your scores decide the Phase 5 criterion: **the average coherence of areas 1-8 must be at least 4 out of 5.**

Time needed: about 30-45 minutes.

## What you edit

Fill in only the last five columns. Leave every other column, the header row, and the row order unchanged.

| Column | Required? | What to enter |
| --- | --- | --- |
| `coherence_1_5` | **Yes for rows 1-8** (rows 9-11 are recommended) | A whole number from 1 to 5 (rubric below) |
| `specific_not_generic` | Yes for rows 1-8 | `yes` or `no` (rubric below) |
| `action` | Optional; blank means keep | One of `keep`, `rename`, `merge`, `split`, `archive` |
| `action_detail` | Only for rename, merge, split | See "Actions" below |
| `pm_notes` | Optional | Anything worth remembering, such as why you scored low |

## Steps

1. Open `eval/opportunity_areas.md` next to the sheet. Each row in the sheet is one area section there, and rank 1 in the sheet is "### 1." in the report.
2. For each area, read the name, the **Problem Summary**, the **Sub-themes**, and **all 7-8 Representative Quotes**. Skim the cues and search attempts.
3. Ask yourself: *"Would one interview study about one problem cover all of these users?"* Then score `coherence_1_5`.
4. Ask yourself: *"Does the name point at a concrete thing that breaks, or could it be 'search should be better'?"* Then fill in `specific_not_generic`.
5. If an area should change, fill in `action` and `action_detail`.
6. Save the file as CSV in UTF-8. If you use Excel, choose "CSV UTF-8". In Google Sheets, use File > Download > CSV.
7. Run `uv run discovery review-areas`.

## Coherence rubric (`coherence_1_5`)

Judge **the grouping**. Don't judge how well the summary is written, how important the problem is (that is Phase 6 scoring), or whether it is vague-memory or general retrieval.

| Score | Meaning |
| --- | --- |
| 5 | Every quote and sub-theme describes the same retrieval problem. |
| 4 | Clearly one problem; one or two quotes are a bit off, or a sub-theme is a close neighbour. |
| 3 | A recognizable core, but two problems are noticeably mixed. You would want to split or rename it. |
| 2 | Two or more different problems are lumped together. |
| 1 | There is no common problem. |

## Specificity rubric (`specific_not_generic`)

- `yes`: the area names what users try to find, the cue they remember, or where finding it fails. Example: "Face groups lost, split, or incomplete break person search".
- `no`: it could be restated as "search should be better" or "photos go missing", with nothing concrete to research.

## Actions (`action` and `action_detail`)

`review-areas` does not change anything by itself. It writes the matching `discovery curate-area` commands into `eval/opportunity_review_report.md` for you to run.

| `action` | When | `action_detail` |
| --- | --- | --- |
| `keep` (or blank) | The area is fine as it is | blank |
| `rename` | The grouping is right but the name is weak | The new name, such as `Edited copies missing from timeline` |
| `merge` | This area is the same problem as another area | The **area ID** to merge into, from the `area_id` column, such as `oa-76a5bc8f` |
| `split` | One sub-theme doesn't belong | The cluster IDs to move out, from the `sub_themes` column, such as `c13` |
| `archive` | Not a retrieval problem, or not worth tracking | blank |

Rules:
- **Split** only works on an area with more than one sub-theme. Today that is only rank 1 (`c03`, `c12`, `c13`). At least one cluster must stay in the area. All clusters listed in one row move together into **one** new area. To make `c12` and `c13` two separate areas, list only one here, then split the other after the next run.
- **Merge** keeps the target's ID. Score both areas as they are now, and put `merge` on the area that should disappear.
- An archived area drops out of the top 8 on the next run, and the next-largest area moves up.

## Things worth a close look (not answers)

- **Rank 1** mixes three sub-themes: edited or saved copies missing (`c03`), photos on the web but not in the app (`c12`), and shared albums (`c13`). Are they one problem?
- **Ranks 3, 4, 8 and 11** are all variations of "photos vanished" (after updates, by year range, milestone photos, the Locked Folder). Are they distinct problems, or should some be merged?
- **Ranks 5 and 10** (missing date dividers, mixed-size grid) are layout complaints about browsing. Is each one a retrieval problem worth keeping?
- The **Representative Quotes** are the strongest items. If you are unsure, open a few `source` links to see the typical case.

## Example rows (only the columns you edit)

```text
coherence_1_5,specific_not_generic,action,action_detail,pm_notes
4,yes,split,c13,shared-album items are a different problem
5,yes,,,
3,no,merge,oa-af73e0ca,same "photos vanished" story as rank 4
```

## After you run `discovery review-areas`

- **"Phase 5 coherence criterion met"**: you're done. Tell me, and I'll tick the criterion in the plan.
- **Below target**: run the `curate-area` commands from `eval/opportunity_review_report.md`, then `uv run discovery cluster`. That writes a fresh sheet with blank score columns, so score the changed areas again. Area IDs of unchanged areas stay the same. Re-running costs only for areas whose content changed (about $0.02-0.03 each).
- **Red error lines**: a cell is invalid, such as a score of 4.5 or a merge without an area ID. Fix the cell and run it again; nothing is saved until the sheet is valid.
