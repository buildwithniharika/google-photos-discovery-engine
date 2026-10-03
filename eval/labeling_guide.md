# Gold-set labeling guide

Label about 300 items so later phases can measure whether the AI is finding vague-memory
retrieval, and whether it names the right problem. Budget about 4–6 hours. A second person
should label the rows marked `overlap=yes` (about 50) so we can check agreement.

The sheet is `eval/gold_set.csv`, produced by `discovery gold-set` after `discovery prep`.
Read the `clean_text` column. It is the PII-redacted text the model will see. Leave
`original_text` out of the labeling; it is not in the sheet.

## How to fill a row

Type only the allowed values below. Use lowercase, exactly as written. Separate multiple
cue types or forgotten details with a pipe: `trip_or_event|place_vague`.

If a row is genuinely unclear after reading it twice, write `ambiguous` in `retrieval_type`
and leave the other label columns blank. Ambiguous rows are left out of accuracy scores.

| Column | What to decide |
|---|---|
| `retrieval_type` | Is this about finding a photo or video at all, and is the memory vague? |
| `primary_category` | The main retrieval problem, if it is retrieval. Blank when `not_retrieval`. |
| `content_type` | What they are trying to find. `unknown` if they never say. |
| `remembered_cue_types` | What they still remember. Blank if they don't say. Do not guess. |
| `forgotten_details` | What they say they can't recall. Blank if they don't say. Do not guess. |
| `breakdown_point` | Where finding it broke down. `not_stated` if they don't say. |
| `evidence_strength` | 1–5, using the rubric below. |
| `labeler` | Your name. |
| `notes` | Optional. Use this when a ruling was close. |

The second labeler fills `retrieval_type_2`, `primary_category_2`, and `labeler_2` on the
overlap rows only. Then run:

```bash
uv run discovery gold-set --agree eval/gold_set.csv
```

Agreement on retrieval type should be at least 80%. Disagreeing rows get adjudicated;
anything still unclear is marked `ambiguous`.

Do not copy gold-set text into prompt examples. The two sets must stay separate, or the
evaluation will look better than it is.

## Retrieval type

| Value | Use it when |
|---|---|
| `vague_memory_retrieval` | They know the item exists but can't describe it precisely enough to find it. A place, trip, person, or event is remembered; a date, name, or exact keyword is missing. |
| `general_retrieval` | They are trying to find something and they know what to search for, or the complaint is about search without a memory story. |
| `not_retrieval` | Not about finding an item. Storage, price, backup, or sync complaints stay here unless finding a remembered item is what broke. |
| `ambiguous` | You cannot choose. |

**Ruling.** "Photos vanished after restore, need wedding pics" is `vague_memory_retrieval`.
The wedding is the memory; they cannot point at the photos precisely, and the restore is
only the setting. Category `life_event_retrieval`, content type `photo`, remembered cue
`life_event`, breakdown `item_appears_missing`. Forgotten details stay blank unless the
text says what they can't recall.

A few more rulings (the full table is in `Docs/edge-case.md`, section 2):

- "Search is useless" with no memory described → `general_retrieval`, evidence strength 2 or lower.
- "I searched 'passport' and nothing came up" → `general_retrieval`, unless they also say what they can't recall.
- "Backup was off, lost all my 2019 photos" → `not_retrieval`. The photos are not in the library.
- "Freed up space and now can't find my daughter's first-day photo" → retrieval. The cleanup blocked finding a remembered photo.
- "The screenshot from WhatsApp, can't find it in Photos" → `vague_memory_retrieval`.
- "How do I see only screenshots?" → `general_retrieval`.
- A complaint about a different app, with no Google Photos retrieval → `not_retrieval`.

## Evidence strength

| Score | Meaning |
|---|---|
| 5 | Specific item, specific cues remembered or forgotten, and a specific search that failed. |
| 4 | Clear retrieval failure with at least two of: what was sought, cues, attempt, breakdown. |
| 3 | Clear retrieval failure, but generic ("search never finds anything"). |
| 2 | Retrieval is implied, or mixed in with other complaints. |
| 1 | Barely related. |

## Allowed values

Retrieval type: `not_retrieval`, `general_retrieval`, `vague_memory_retrieval`, `ambiguous`.

Primary category: `context_based_retrieval_failure`, `time_based_memory_gap`,
`screenshot_document_retrieval_failure`, `visual_detail_search_failure`,
`people_event_association_failure`, `location_ambiguity`, `life_event_retrieval`,
`search_trust_breakdown`, `other_emergent`.

Content type: `photo`, `video`, `screenshot`, `receipt_or_bill`, `prescription_or_medical`,
`id_or_official_document`, `general_document`, `ticket_or_booking`, `recipe_or_info_card`,
`chat_media`, `meme_or_funny`, `unknown`.

Remembered cue types: `situation`, `place_vague`, `person`, `purpose_task`, `visual_detail`,
`time_range`, `source_app`, `life_event`, `feeling`, `trip_or_event`, `text_fragment`.

Forgotten details: `exact_date`, `month_or_year`, `location_name`, `album_name`, `file_type`,
`exact_text_in_image`, `sender`, `media_kind`, `search_keyword`, `people_names`.

Breakdown point: `query_formulation`, `no_results`, `results_irrelevant_or_too_broad`,
`target_not_surfaced`, `wrong_metadata`, `browse_fatigue`, `item_appears_missing`,
`trust_breakdown_gave_up`, `not_stated`.
