# Opportunity areas (draft)

Run: `2026-10-02T102736_d4e5bd`. Prompts: `cluster_label_v1`, `opportunity_synthesis_v1`. Label model: `claude-opus-5-5`. Synthesis model: `claude-opus-5-5`.
Clustering: UMAP n_neighbors=15, n_components=10; HDBSCAN min_cluster_size=10, min_samples=3, method=eom; area merge cosine ≥ 0.93.

## Summary

- Insights: 590; clustered input: 400 (left out as not useful for discovery: 190)
- Clusters: 13; unclustered (noise): 16 (4%)
- Active areas: 11 (0 emerging with < 10 items; 3 emergent category)
- Matched to the previous run `2026-10-02T092729_aa73f2`: 13 of 13 clusters kept their area
- PM curation applied: `oa-af73e0ca` merged in oa-76a5bc8f; `oa-5fa72a6b` split from oa-69e09da1 (override 23); `oa-76a5bc8f` merged into oa-af73e0ca
- Synthesis sentences without a valid citation (flagged ⚠): 0

## Taxonomy check

Does the data support the 8 starting categories? Clusters are mapped to a category by the label model; `other_emergent` marks problems the taxonomy missed.

| Category | Active areas | Clusters | Items in areas | Items (extraction category) |
| --- | --- | --- | --- | --- |
| Context-Based Retrieval Failure | 5 | 6 | 209 | 150 |
| Time-Based Memory Gap | 0 | 1 | 0 | 83 |
| Screenshot and Document Retrieval Failure | 0 | 0 | 0 | 10 |
| Object or Visual Detail Search Failure | 0 | 0 | 0 | 13 |
| People and Event Association Failure | 1 | 1 | 21 | 29 |
| Location Ambiguity | 0 | 0 | 0 | 1 |
| Life-Event Retrieval | 1 | 1 | 15 | 14 |
| Search Trust Breakdown | 1 | 1 | 72 | 65 |
| Emergent / Other (surfaced by clustering) | 3 | 3 | 67 | 35 |

No cluster maps to: Screenshot and Document Retrieval Failure, Object or Visual Detail Search Failure, Location Ambiguity.

Clusters whose category differs from the per-item extraction majority:

- `c05` Timeline update removed date headers, browsing by day fails: label `other_emergent`, extraction majority `time_based_memory_gap` (23/32)

## Areas

### 1. Opportunity Area: Backed-up photo blocks vanish after updates or phone changes

`oa-af73e0ca` · Context-Based Retrieval Failure · 105 items (47 vague memory, 58 general)

**Problem Summary**

Users look for large sets of previously backed-up photos, often whole albums or entire year spans, that stop appearing after an app update, phone change, transfer, or storage action. [`2e18c3f3`, `62dcd9bc`, `72b459e4`, `499cbe59`, `af8d138d`, `c8fb0ac7`]
What they remember is a time boundary or range (such as before 2023, 2014 to 2022, 2013 to 2018) and the event that preceded the loss, rather than exact dates or individual images. [`2e18c3f3`, `e38f95be`, `db606b73`, `bdf56f12`, `b5db2e4e`, `707f3499`]
They check trash, archive, albums, On this device, other accounts, Google Drive, Takeout, and device galleries, and some contact support, but find nothing. [`e38f95be`, `eef82213`, `db606b73`, `499cbe59`, `f7081a5c`]
Retrieval breaks down because the items appear missing altogether, sometimes with leftover traces like album counts that won't open or metadata without images, leaving users unsure whether the photos still exist. [`d43fdac1`, `a001b557`, `6fef8001`, `f1c6b666`, `a79dca81`]
Some losses involve highly sentimental content such as photos of a late father, a first child, or meeting a spouse. [`c74cd14d`, `db606b73`, `91983997`]

**Sub-themes**

- Backed-up photos vanish after updates, resets, phone changes (69 items, `c02`)
- Whole year ranges of photos vanish from library (36 items, `c04`)

**Source breakdown:** play_store 41 (39%), google_community 36 (34%), google_sheet 28 (27%). Platforms: Android 49 (47%), Google Community 40 (38%), Reddit 16 (15%).

**Content types:** photo 92 (88%), unknown 6 (6%), video 5 (5%), chat_media 1 (1%), screenshot 1 (1%).

**What Users Remember**

Cue types (items): situation 75, time_range 51, source_app 18, person 9, feeling 5, life_event 5, trip_or_event 5, purpose_task 3. Items with any cue: 101 of 105.

- "my son" (3 items)
- "2013 to 2018"
- "2017 pictures"
- "2017 to 2019"
- "2020 through mid 2026"
- "2021 pictures on my phone"

**What Users Forget**

exact_date 23

**Common Search Attempts**

- external_app_check (22 items): "logged into Google Photos website and old phone, photos visible"; "tried Google takeout"
- album_browse (21 items): "checked Trash/Locked Folder"; "checked Google Photos carefully, including the Trash/Bin and other sections"
- asked_support (10 items): "official guide "Restore recently deleted photos & videos""; "Request to recover a deleted Google photos album"
- keyword_search (7 items): "simple searches"; "searching by resolution, and MP size"
- manual_scroll (6 items): "looked on google photos"; "looked through the Google Photos app"

**Breakdown Point**

Dominant: **item_appears_missing**. All: item_appears_missing 96, target_not_surfaced 3, trust_breakdown_gave_up 2, browse_fatigue 1, not_stated 1, query_formulation 1, wrong_metadata 1.

**Representative Quotes**

> "since i changed my phone a few months back, i lost all my photos before 2023."  
> — google_community, Google Community, strength 5 · [source](https://support.google.com/photos/thread/470539066?hl=en) · `2e18c3f3`

> "now Google Photos only shows photos from 2023 onwards"  
> — google_community, Google Community, strength 5 · [source](https://support.google.com/photos/thread/467058819?hl=en) · `e38f95be`

> "Missing 4000+ photos from Camera album and also from phone gallery"  
> — google_community, Google Community, strength 5 · [source](https://support.google.com/photos/thread/470812262?hl=en) · `eef82213`

> "5 years of photos have vanished"  
> — google_community, Google Community, strength 4 · [source](https://support.google.com/photos/thread/466739076?hl=en) · `62dcd9bc`

> "since this latest new update I have lost thousands of photos and memories that I had put into albums on to my device"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=406498dc-5ac8-43e1-830d-8a05b1b062dd) · `c74cd14d`

> "I see photos from 2019 to 2021. But I do not see any photos from 2013 to 2018."  
> — google_sheet, Reddit, strength 5 · [source](https://www.reddit.com/r/googlephotos/comments/1l977mm/google_automatically_deleted_old_photos/) · `db606b73`

> "Metadata for the image such as ISO location and what the image was taken on is still present but just no image."  
> — google_sheet, Reddit, strength 5 · [source](https://www.reddit.com/r/googlephotos/comments/1od023s/possibly_saving_old_photos/) · `d43fdac1`

> "All the photos from before December 2023 in my Google Photos account disappeared at once."  
> — google_community, Google Community, strength 4 · [source](https://support.google.com/photos/thread/468377180?hl=en) · `bdf56f12`

**Why This Matters**

This area only partly maps to vague memory retrieval: 58 of 105 items are general retrieval about apparent data loss, though users consistently identify targets by time range, situation, or person rather than specifics. [`e38f95be`, `db606b73`, `72b459e4`, `707f3499`, `c8fb0ac7`]

**Follow-up Research Questions**

1. When you first noticed the photos were missing, what were you trying to find and what did you remember about it?  
   _Gap:_ Unclear whether users discover the gap while seeking a specific memory or by noticing the library looks shorter. (`91983997`, `f7081a5c`, `c8fb0ac7`)
2. How did you work out which years or albums were missing?  
   _Gap:_ Feedback states ranges but not how users determined the boundaries. (`2e18c3f3`, `db606b73`, `46db5d0e`)
3. What did you check first, and in what order, when the photos weren't where you expected?  
   _Gap:_ Attempts are listed but not their sequence or which felt most promising. (`e38f95be`, `eef82213`, `db606b73`)
4. What would you need to see to feel confident your photos were found or still safe?  
   _Gap:_ No evidence on what counts as 'found' or what restores trust. (`6fef8001`, `a79dca81`, `2e18c3f3`)
5. What happened on your device or account in the days before the photos disappeared?  
   _Gap:_ Triggers are reported loosely, with many users saying they don't know how it happened. (`c2780729`, `707f3499`, `5d98226c`)
6. How important were the missing photos to you, and were there specific moments you most wanted back?  
   _Gap:_ Stakes are clear for some items but unknown for most. (`c74cd14d`, `db606b73`, `655d7e90`)
7. What did support or help resources tell you, and what did you do afterward?  
   _Gap:_ Outcomes after asking support and whether users gave up are not described. (`db606b73`, `499cbe59`)

Severity inputs: mean frustration 3.5238, high-stakes share 0.3714, low-rating share 0.7755 (49 rated). Mean evidence strength 3.6952.

---

### 2. Opportunity Area: Exact keyword and name searches broken by AI search

`oa-53a15320` · Search Trust Breakdown · 72 items (6 vague memory, 66 general)

**Problem Summary**

Users look for photos they know exist using simple object keywords such as yellow truck, police, monkey, cat, or basketballs, and the AI-driven search returns only a fraction of matches, unrelated photos, or nothing at all. [`6705cda2`, `c550affa`, `7991262b`, `7f39a16a`, `383bc0e6`]
Exact text cues users remember, such as words they typed into descriptions, meme text, file names, and people's names, are ignored or mixed with loose visual matches. [`4ef88d9d`, `d3da875d`, `a8a96b3d`, `9137d33d`]
Users contrast this with earlier search, where the same queries reliably returned many matching photos, and some switch to classic search to get complete results. [`7a121bc4`, `cb3eaba2`, `dc7e4e7e`, `5b5d8689`]
Breakdowns also include follow-up queries losing context, misread address queries, missing albums, people, and months, and search being unavailable while photos are organized. [`29752f63`, `c1ed2cfe`, `01ae76c7`, `88e3610a`]
Repeated failures lead users to call search a stumbling block and give up, saying it feels nearly impossible to find anything. [`afbfe304`, `db04a962`, `a2571983`, `0964c4e1`]

**Sub-themes**

- AI search replaced keyword search, results degraded (72 items, `c01`)

**Source breakdown:** google_sheet 45 (62%), play_store 26 (36%), app_store 1 (1%). Platforms: Android 41 (57%), Reddit 28 (39%), Google Community 2 (3%), iOS 1 (1%).

**Content types:** photo 51 (71%), unknown 14 (19%), meme_or_funny 3 (4%), screenshot 2 (3%), general_document 1 (1%), video 1 (1%).

**What Users Remember**

Cue types (items): visual_detail 18, text_fragment 13, person 8, situation 7, time_range 7, purpose_task 3, place_vague 2, life_event 1. Items with any cue: 45 of 72.

- "ABC2022 in file name"
- "any person's photo"
- "banana yellow truck and nothing else in the image"
- "Beautiful tree"
- "Bob and Sue in the same photo"
- "by name"

**What Users Forget**

exact_date 2

**Common Search Attempts**

- keyword_search (35 items): "specific keywords in quotation marks"; "Best match tab"
- natural_language_query (19 items): "used the AI search"; "searches needing five or six clarifications"
- date_search (6 items): "searched the current day's date in quotes"; "search by date range"
- people_search (4 items): "looked for 'Search by People' option"; "face tags"
- text_ocr_search (4 items): "searched for text on images in Android app"; "searched for text in web browser"

**Breakdown Point**

Dominant: **results_irrelevant_or_too_broad**. All: results_irrelevant_or_too_broad 32, no_results 20, trust_breakdown_gave_up 9, query_formulation 6, target_not_surfaced 3, browse_fatigue 2.

**Representative Quotes**

> "I ask for "yellow truck" and it finds 2 out of the 10 different days I had taken pictures of yellow trucks."  
> — google_sheet, Reddit, strength 5 · [source](https://www.reddit.com/r/googlephotos/comments/1rihxp2/sorry_if_this_has_been_asked_a_hundred_times_but/) · `6705cda2`

> "I copy / paste this keyword and search again and again it is not found!"  
> — google_sheet, Google Community, strength 5 · [source](https://support.google.com/photos/thread/88651366/is-there-a-reliable-way-to-do-a-text-search-in-a-photo-description) · `4ef88d9d`

> "Now, when I search for something like; tyre, home, or cat, it does not properly display anything from my collection."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=fab83449-0c0b-41d3-8f08-530ea54462bd) · `c550affa`

> "I search for police in my Google photos and nothing shows up! Zero photos."  
> — google_sheet, Reddit, strength 5 · [source](https://www.reddit.com/r/googlephotos/comments/1o4p08y/cant_find_police/) · `7991262b`

> "it gave me 2 pics. It did show I could click to "see more", but clicking on that gave a screen that said "no more results"."  
> — google_sheet, Reddit, strength 4 · [source](https://www.reddit.com/r/googlephotos/comments/1kucfki/google_photos_new_ai_search_sucks/) · `7a121bc4`

> "now with this bullshit ai it tells me it can't "help me with that""  
> — google_sheet, Reddit, strength 5 · [source](https://www.reddit.com/r/googlephotos/comments/1syjrhs/i_hate_the_ai_in_my_search/) · `7f39a16a`

> "I search up human, animal, painting, rollercoaster, all things that ARE IN MY CAMERA ROLL and all things I've looked up before."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=a2bee9ea-3dfd-431d-aa09-fbbf49e52c3a) · `5b5d8689`

> "if I search for any photos, then issue a follow up query, it will completely ignore the initial search results and describe some random photo from my library that has nothing to do with what I was searching for."  
> — app_store, iOS, strength 3 · [source](https://apps.apple.com/gb/app/google-photos-backup-edit/id962194608?see-all=reviews&platform=iphone) · `29752f63`

**Why This Matters**

This area is mostly general retrieval rather than vague memory: of 72 items, 66 are general retrieval, and users usually remember a precise keyword, name, or description, so the problem is lost trust in exact-match search. That loss of trust affects whether users would rely on search when their memory is vague. [`6705cda2`, `4ef88d9d`, `afbfe304`, `dc7e4e7e`, `7f39a16a`]

**Follow-up Research Questions**

1. When you last searched for a photo you knew existed, what exactly did you type, and what did you see in the results?  
   _Gap:_ The feedback gives few concrete query-to-result examples, so we cannot tell how far typical results fall from what users expect. (`d76b4be1`, `cb3eaba2`, `7f0f6546`)
2. How often do you search by a single exact word, such as an object, a name, or a description you wrote, compared with describing a scene in a sentence?  
   _Gap:_ It is unclear how common exact-match intent is compared with descriptive intent. (`4ef88d9d`, `d3da875d`, `9137d33d`)
3. When search returns some but not all matching photos, how do you decide whether what you got is enough?  
   _Gap:_ We do not know whether users need complete sets of matching photos or a single item. (`6705cda2`, `7a121bc4`, `dc7e4e7e`)
4. After a search fails, what do you do next to find the photo?  
   _Gap:_ Fallback behaviors such as classic search, scrolling, or giving up are only partly described. (`7a121bc4`, `afbfe304`, `a2571983`)
5. Tell me about a time a failed search had consequences for you, for work or otherwise.  
   _Gap:_ The high-stakes share is very low, so the real cost of these failures is unclear. (`7991262b`, `6705cda2`)
6. What made you stop trusting search results, and what would you need to see before you rely on them again?  
   _Gap:_ The feedback shows trust breaking down but not what signals users use to judge reliability. (`db04a962`, `0964c4e1`, `7f0f6546`)
7. How do you use descriptions, names, or labels you add to photos, and what do you expect to happen when you search for them?  
   _Gap:_ It is unclear how widespread user annotation is and what retrieval users expect from it. (`4ef88d9d`, `a8a96b3d`, `9137d33d`)

Severity inputs: mean frustration 3.4167, high-stakes share 0.0139, low-rating share 0.7619 (42 rated). Mean evidence strength 3.75.

---

### 3. Opportunity Area: Edited, saved, or new photos missing from main view

`oa-69e09da1` · Context-Based Retrieval Failure · 69 items (21 vague memory, 48 general)

**Problem Summary**

Users look for photos they just edited, saved as a copy, downloaded, uploaded, or saved from a shared library, and they remember the action they took rather than anything about the photo's content. [`268a4406`, `d8abe474`, `e51d96e9`, `c6a9ca53`, `4ca61254`]
These items fail to surface in the main timeline, the Camera folder, or the pickers of other apps such as Facebook, Lightroom, Vinted, and eBay, which blocks posting, attaching, or sending them. [`a65cf50d`, `87811740`, `1a5c9439`, `03616256`, `c1a327a3`]
Users fall back on side views such as Recently added, device folders, the Files app, or even the delete flow, and some only find items at the bottom of the library after hunting. [`a65cf50d`, `d8abe474`, `7a18899e`, `e51d96e9`, `263320f6`, `71594310`]
A second group knows that older backed-up photos exist because they appear on photos.google.com, in albums, or in memories, but after updates the app's main view shows only on-device photos. [`cbf8db58`, `83747224`, `48bcfacb`, `172225d5`, `e4f99e01`]
Even a user who remembers a specific Photos notification showing kids' photos from a couple of years ago could not locate them in the app, on PC, or in trash or archive. [`df1af45c`]

**Sub-themes**

- Edited, saved, or new photos missing from main view (58 items, `c03`)
- Cloud photos visible on web but missing in app (11 items, `c12`)

**Source breakdown:** play_store 38 (55%), google_sheet 28 (41%), google_community 3 (4%). Platforms: Android 52 (75%), Reddit 13 (19%), Google Community 3 (4%), YouTube 1 (1%).

**Content types:** photo 56 (81%), screenshot 7 (10%), unknown 4 (6%), video 2 (3%).

**What Users Remember**

Cue types (items): situation 39, source_app 19, time_range 13, purpose_task 7, text_fragment 2, visual_detail 2, person 1, place_vague 1. Items with any cue: 57 of 69.

- "added a bunch of photos to this album"
- "added photos from my PC"
- "albums and memories from that period work"
- "albums from events spanning many years and locations"
- "appear when I try to upload them using other applications"
- "apps on my phone"

**What Users Forget**

Not stated.

**Common Search Attempts**

- album_browse (17 items): "looked under recently uploaded"; "looked in the screenshot collection"
- external_app_check (15 items): "used older app version and PC web interface"; "checked the web browser version of google photo"
- manual_scroll (5 items): "spent over 1 hr trying to find a photo"; "hunting for new photos"
- keyword_search (3 items): "search for the original photo"; "checked their file names and upload dates in google photos"
- asked_support (2 items): "contacted Google One support"; "tried the help links"

**Breakdown Point**

Dominant: **target_not_surfaced**. All: target_not_surfaced 68, browse_fatigue 1.

**Representative Quotes**

> "I went to find those photos earlier this week, and they don't seem to be anywhere."  
> — google_sheet, Reddit, strength 5 · [source](https://www.reddit.com/r/googlephotos/comments/1gs76kk/cant_find_the_pictures_that_were_in_a/) · `df1af45c`

> "I can't see my photos in the main photos album but I can see them in the recently added folder."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=28989f59-8f51-4c4c-ad34-f7a0aee322e1) · `a65cf50d`

> "Editing a photo and saving a copy is useless, as it won't even show up in the app"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `268a4406`

> "it never show recently downloaded photos on top"  
> — play_store, Android, strength 5 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=23dacf60-656a-44e6-8d7b-fd50ef90f132) · `d8abe474`

> "some photos I have already uploaded are not showing on the main screen of my Photos app"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=d21462ad-6201-41ba-83b0-f32633a56d76) · `7a6ab34e`

> "now anytime I crop or edit a picture in anyway, it no longer shows up on my camera, recent photos, or any other album in other apps to post."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=8457344b-60bb-46aa-bfe6-533241125099) · `87811740`

> "When i edited my photos they used to stay in photos, now i can't find them."  
> — google_sheet, YouTube, strength 4 · [source](https://www.youtube.com/watch?v=4DNQp3jgT8c) · `35b72493`

> "The photos still appear in Pictures but I didn't find a way to make them reappear in the Camera folder."  
> — google_community, Google Community, strength 4 · [source](https://support.google.com/photos/thread/470825684?hl=en) · `1a5c9439`

**Why This Matters**

Most items here (48 of 69) are general retrieval of recently acted-on or cloud items rather than vague memory, but users consistently recall the situation or source app (edit, download, save) instead of content details, so a failure to surface the item leaves them without a way to search. [`268a4406`, `d8abe474`, `35b72493`, `c6a9ca53`, `4ca61254`]

**Follow-up Research Questions**

1. How often do you edit, download, or save a photo and then have trouble finding it again?  
   _Gap:_ Feedback does not tell us how often this happens or for which actions. (`35b72493`, `7a18899e`, `263320f6`)
2. After you save an edited copy, where do you look for it first, and why there?  
   _Gap:_ Users' expected location for edited copies, and their first search step, is unclear. (`e51d96e9`, `c6a9ca53`, `268a4406`)
3. What were you trying to do with the photo when you could not find it, such as posting, attaching, or sending it?  
   _Gap:_ We don't know how often the task is time-sensitive or what it costs the user when the item is missing. (`d8abe474`, `87811740`, `03616256`)
4. When a photo shows up in Recently added or another folder but not in the main view, what do you conclude has happened?  
   _Gap:_ Evidence doesn't say whether users trust the item is safe or believe it is lost. (`a65cf50d`, `1a5c9439`, `b6cc4836`, `71594310`)
5. When you saved photos from a shared library, where did you expect them to appear?  
   _Gap:_ Users' mental model of where items saved from shared libraries end up is unknown. (`4ca61254`, `ac3aec2a`)
6. What made you check the website or other apps when you couldn't see your older photos in the app?  
   _Gap:_ We don't know what triggers cross-surface checking or how long users search before checking elsewhere. (`cbf8db58`, `172225d5`, `e4f99e01`)
7. Describe the last time a photo you remembered, such as one from a notification, could not be found anywhere. What did you remember about it?  
   _Gap:_ Cues users hold for older items, and whether those items ever reappear, are rarely captured. (`df1af45c`, `48bcfacb`)

Severity inputs: mean frustration 3.0725, high-stakes share 0.0, low-rating share 0.7115 (52 rated). Mean evidence strength 3.7391.

---

### 4. Opportunity Area: Missing day and month dividers break date-based browsing

`oa-260980f7` · Emergent / Other (surfaced by clustering) · 32 items (1 vague memory, 31 general) · **Emergent category**

**Problem Summary**

After a layout update, users who find photos and videos by scrolling to a particular day or month report that the timeline became one undifferentiated block with no clear date separators. [`5da121c1`, `fabae7f8`, `cc49cd4e`, `1c3e4f46`, `3f6c37a7`, `8b219c5b`]
Their remembered cue is mostly time: a certain date, a specific day, or which month a picture belongs to, and they relied on day and month boundaries to orient themselves. [`c0f17881`, `be13eab0`, `f9d69912`, `0d90783a`, `3f62d012`]
Without visible headers, users scroll through clutter or tap individual items just to learn their dates, and floating or vague month labels do not show where one day or month ends and the next begins. [`8958778d`, `e60d3081`, `98d46d4a`, `be13eab0`, `0d90783a`]
Zoomed-out grids show only month grouping, and day-level selection becomes awkward without day separators. [`aed33fea`, `5be1c20c`, `31d45799`]
In one report, photos from a single evening carried dates months apart, so time-based browsing could not bring them together. [`0d66aa77`]

**Sub-themes**

- Timeline update removed date headers, browsing by day fails (32 items, `c05`)

**Source breakdown:** play_store 22 (69%), google_sheet 10 (31%). Platforms: Android 31 (97%), Reddit 1 (3%).

**Content types:** photo 27 (84%), unknown 3 (9%), video 2 (6%).

**What Users Remember**

Cue types (items): time_range 8, situation 3, text_fragment 1. Items with any cue: 12 of 32.

- "a certain date"
- "each month is just lumped together"
- "exactly what I wrote for a description"
- "find my pictures sorted by month"
- "find pictures by where one month ends and the next begins"
- "images displayed date-wise"

**What Users Forget**

exact_date 2

**Common Search Attempts**

- manual_scroll (11 items): "relying on the floating months on top"; "scroll through my 1500 videos"
- keyword_search (2 items): "search by videos"; "typing exactly what I wrote for a description"
- date_search (1 items): "searching year by year"

**Breakdown Point**

Dominant: **browse_fatigue**. All: browse_fatigue 29, wrong_metadata 2, no_results 1.

**Representative Quotes**

> "my photos aren't organized into different days anymore and everything is just bunched up"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `5da121c1`

> "The lack of separation from date to date makes it difficult to find anything in my camera roll."  
> — google_sheet, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `fabae7f8`

> "trying to find a video from a different day now just gets lost in the clutter"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `8958778d`

> "I don't like the photos being all together and not categorized, it makes it harder to find."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=d595c43d-54df-48ca-8b83-42b99f260c58) · `aeb6b12c`

> "recent update mix all dates photos and we have to click to a particular photo or video to check its date"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=9d51dede-c7bb-4950-a8a5-c74708fb2b88) · `e60d3081`

> "I can't see the new and old photos separately anymore, or even by month and days."  
> — google_sheet, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `a7f6ac3a`

> "This makes things really cluttered and hard to see/ find photos."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=e7d9c87e-257d-4e84-9d1f-edead3e7f658) · `01143ce9`

> "Now it is exceptionally difficult to find a specific day."  
> — google_sheet, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `c0f17881`

**Why This Matters**

Nearly all items (31 of 32) are general retrieval: users usually know roughly when something happened and browse by date, so this area concerns time-anchored browsing more than vague memory, though approximate-date recall depends on visible date structure. [`c0f17881`, `be13eab0`, `0d90783a`, `3f62d012`, `0d66aa77`]

**Follow-up Research Questions**

1. When you last looked for a photo by scrolling, what did you remember about when it was taken, and how precise was that memory?  
   _Gap:_ The feedback rarely says whether users know an exact day, a rough month, or only a season when they start browsing. (`be13eab0`, `f9d69912`, `0d90783a`)
2. How often do you scroll the main timeline to find a specific photo or video, compared with using search or albums?  
   _Gap:_ There is no data on how frequently date browsing is the primary retrieval method or whether alternatives are tried. (`8958778d`, `c0f17881`, `3f62d012`)
3. Walk me through what you did the last time you couldn't tell which day you were looking at while scrolling.  
   _Gap:_ Workarounds such as tapping items to check dates are mentioned, but the full sequence of steps and the point of giving up are unknown. (`e60d3081`, `98d46d4a`)
4. What kinds of photos or videos were you trying to find when the missing date dividers got in your way, and how important were they to you?  
   _Gap:_ Most items do not name the target content or its stakes, and the high-stakes share is zero. (`5da121c1`, `8958778d`, `3d71e196`)
5. Besides finding a single photo, what else do you do with photos grouped by day, such as selecting or sharing a whole day?  
   _Gap:_ Some users mention day-level selection, but the range of tasks that depend on day grouping is unclear. (`5be1c20c`)
6. At what zoom level do you usually browse your library, and what information do you need to see at that level?  
   _Gap:_ One report says zooming out removes individual dates, but it is unknown how many users browse that way or what they need there. (`aed33fea`, `31d45799`)
7. Have you noticed photos from the same event showing up under different dates, and how did you find them?  
   _Gap:_ Wrong-date metadata appears only rarely, so its prevalence and effect on retrieval are unknown. (`0d66aa77`)

Severity inputs: mean frustration 3.0938, high-stakes share 0.0, low-rating share 0.7742 (31 rated). Mean evidence strength 3.1562.

---

### 5. Opportunity Area: Finding photos inside large, scattered album and folder collections

`oa-fcedbf51` · Emergent / Other (surfaced by clustering) · 22 items (1 vague memory, 21 general) · **Emergent category**

**Problem Summary**

Users who organize photos into many albums or folders cannot search album titles or contents, for example to find an album by name when adding photos or to find a remodeling job album by its address. [`20d24c5a`, `b9fc9b10`, `3de4ed48`, `f8cf06c2`]
Inside large or shared albums, users report no way to filter, group, or zoom out, and sort orders such as chronological or oldest-first are not respected, so they have to scroll item by item. [`8327ccb2`, `ee4136aa`, `ed90e160`, `0952f94b`, `2d70de41`]
Automatic sorting and redesigned navigation scatter photos, downloads, and screenshots across many folders and sections, so items are no longer where users expect them. [`74818209`, `a6317d8a`, `f81c150e`, `1dde2b3a`, `92c6adf0`, `91af86d2`, `c45e7413`]
Retrieval mostly breaks down as browse fatigue: users fall back to scanning thousands of files, opening folder after folder, or switching to the phone's native gallery. [`1fa388c6`, `3054d606`, `7428964e`, `d9d73e35`]

**Sub-themes**

- Albums and folders hard to browse, sort, or search (22 items, `c06`)

**Source breakdown:** google_sheet 13 (59%), play_store 9 (41%). Platforms: Android 20 (91%), Reddit 2 (9%).

**Content types:** photo 15 (68%), unknown 6 (27%), screenshot 1 (5%).

**What Users Remember**

Cue types (items): situation 7, source_app 3, purpose_task 2, text_fragment 2, life_event 1, time_range 1. Items with any cue: 14 of 22.

- "address"
- "big, shared albums"
- "everything is not where it used to be"
- "I sort my photos into albums"
- "it used to be so simple and straight forward"
- "jobsite photos"

**What Users Forget**

Not stated.

**Common Search Attempts**

- manual_scroll (5 items): "sorting through thousands of files"; "going one by one"
- album_browse (4 items): "selected oldest first"; "open different folders to find a photo"
- external_app_check (2 items): "use phone's native photo app instead"; "compared album ordering on Google Photos online"
- keyword_search (2 items): "search"; "put an address in the search field"
- natural_language_query (1 items): "AI search for abstracted photos"

**Breakdown Point**

Dominant: **browse_fatigue**. All: browse_fatigue 19, no_results 1, results_irrelevant_or_too_broad 1, target_not_surfaced 1.

**Representative Quotes**

> "I also can't search for the name of the album, at the time I'm trying to add the photos."  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `20d24c5a`

> "the album titles vanish and I'm left with sorting through thousands of files to find one because they aren't named except by download numbers"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `1fa388c6`

> "some of my albums are too big, making it difficult to find what I'm looking for"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `8327ccb2`

> "it makes it SO much more difficult to easily find what I'm looking for when they're scattered in 12 different places that don't even make sense together"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=6cf35a27-cd8e-4f3b-a07e-c409316e6f71) · `74818209`

> "Without any search by title/person/place I have to mostly scan through my albums to find things."  
> — google_sheet, Reddit, strength 4 · [source](https://www.reddit.com/r/googlephotos/comments/1hpu2k9/having_trouble_finding_old_shared_albums/) · `b9fc9b10`

> "I have so many photos it takes days to find one photo cause the search sucked."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=da80cc2c-2fba-46b4-bde8-844b1921bc8c) · `3054d606`

> "I can't find anything everything is not where it used to be you keep adding folders you remove folders there's too many folders"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=f971c0e4-7bbd-4857-97c4-a2c5e2cd18a5) · `a6317d8a`

> "Not recommended for big, shared albums (no way to filter, order by, group by, search, etc. inside them)."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=2d723029-22d9-4cad-896d-9a4164c828ab) · `ee4136aa`

**Why This Matters**

This area is mostly general retrieval, with only one of 22 items labeled vague memory, because users usually know which album, folder, or organizing scheme holds the item; the overlap with vague memory is the cue they do recall (album name, address, event, or 'where it used to be') that the app does not let them use. [`20d24c5a`, `3de4ed48`, `f8cf06c2`, `a6317d8a`, `e7cbce8e`]

**Follow-up Research Questions**

1. When you look for a photo you filed in an album, what do you remember first: the album name, the event, the date, or something else?  
   _Gap:_ Feedback names several cues but not which one users rely on at the moment of search. (`20d24c5a`, `f8cf06c2`, `3de4ed48`)
2. How often do you need to find a specific album or a photo inside an album, and how long does it usually take?  
   _Gap:_ There is no data on frequency or time cost. (`8327ccb2`, `3054d606`)
3. Walk me through the last time you searched inside a large or shared album. What did you try first, and what did you try next?  
   _Gap:_ The sequence of attempts and the point where users give up are unclear. (`ee4136aa`, `b9fc9b10`, `0952f94b`)
4. How do you name or structure your albums, for example by address, event, or date, and why?  
   _Gap:_ How users' organizing schemes relate to how they later retrieve items is unknown. (`3de4ed48`, `f8cf06c2`, `1fa388c6`)
5. When photos ended up in different folders or sections than you expected, how did you work out where they had gone?  
   _Gap:_ How users recover from scattered or relocated items is not described. (`74818209`, `a6317d8a`, `1dde2b3a`, `92c6adf0`)
6. Which photos you have looked for in albums mattered most to you, and what happened when you could not find them?  
   _Gap:_ Stakes and consequences are barely described; only work photos are called critical. (`e7cbce8e`, `3de4ed48`)
7. When you switch to another gallery or file app to find something, what does that app let you do that you rely on?  
   _Gap:_ What users value in their workarounds is not explained. (`7428964e`, `71156e87`, `d9d73e35`)

Severity inputs: mean frustration 3.2273, high-stakes share 0.0, low-rating share 0.75 (20 rated). Mean evidence strength 3.1818.

---

### 6. Opportunity Area: Face groups lost, split, or incomplete break person search

`oa-bf3f5f4c` · People and Event Association Failure · 21 items (1 vague memory, 20 general)

**Problem Summary**

Users try to find photos of specific people, themselves, or pets through face groups and People & Pets, remembering mainly who is in the photo rather than when it was taken. [`80646af5`, `bc9cd9c8`, `adc7fd6f`, `ce24cec5`, `f8d0e58c`, `0ec9a598`]
Face groups and face-based albums disappear after app updates, resets, toggling grouping, or for no apparent reason, and previously tagged faces lose their tags over time. [`80646af5`, `0dcf569b`, `9f72435e`, `bebc76db`, `3e3f888b`, `69fadc1e`]
Grouping is also wrong when present: one person is split into duplicate groups that cannot be merged, different people are merged together, places or strangers appear as people, and important people are missing. [`bc9cd9c8`, `adc7fd6f`, `225c227d`, `6036ad8d`, `580e37d0`]
Clearly visible faces and backed-up photos often go undetected, so they never appear under the person even when they show in Recently Added or keyword search, and users cannot add them manually. [`73792a42`, `40871ee0`, `6caf6d39`, `ce24cec5`, `e784bc70`, `f8d0e58c`, `0ec9a598`]
When person search fails, users fall back to scrolling without a known date, searching keywords, or contacting support, and some cannot filter for unnamed faces at all in large libraries. [`ce24cec5`, `52b859b3`, `03ce9130`, `0dcf569b`, `48305714`]

**Sub-themes**

- Face groups lost, split, or incomplete; person search fails (21 items, `c07`)

**Source breakdown:** play_store 16 (76%), google_sheet 5 (24%). Platforms: Android 17 (81%), Reddit 4 (19%).

**Content types:** photo 20 (95%), unknown 1 (5%).

**What Users Remember**

Cue types (items): person 14, visual_detail 4, situation 3, source_app 2, purpose_task 1, time_range 1. Items with any cue: 17 of 21.

- "album labeled Jess"
- "anyone that matters (lot of pics)"
- "appear in Recently Added"
- ""Available to add" tag"
- "my detected face"
- "face albums"

**What Users Forget**

exact_date 1

**Common Search Attempts**

- people_search (8 items): "searched the name of the person's face"; "new search shows people who are not me"
- keyword_search (3 items): "keyword search replaced by chatty AI"; "using search, even suggested searches"
- asked_support (2 items): "I contacted Google one support"; "chatted with tech support"
- manual_scroll (2 items): "manually clicking through 85,000 photos"; "scroll to find them"
- natural_language_query (1 items): "You can't search, filter or even use the AI Ask photos feature"

**Breakdown Point**

Dominant: **wrong_metadata**. All: wrong_metadata 14, target_not_surfaced 3, item_appears_missing 2, no_results 1, query_formulation 1.

**Representative Quotes**

> "the new update removed all the face groups I had, now I can't even find the people I want in the pictures"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=26604a83-3bcf-43f1-a740-d411407e9331) · `80646af5`

> "I can't find anyone that matters (lot of pics) but only those with few pics and places are somehow labeled as people"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `bc9cd9c8`

> "pictures with very clear faces aren't getting grouped and aren't getting registered as faces so I can't even manually add them"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=fc669de5-7c8c-4bd8-a274-f7b51d5377d0) · `73792a42`

> "some pictures have no faces detected and yet there's literally a person in the picture"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=829193c1-5ec9-4238-965b-6cfcb129ad7a) · `40871ee0`

> "It hardly finds anyone you love or care about & seems more focussed on picking out blurred strangers in the distance."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=8daacf56-2570-4a54-9478-8f2be70d44e3) · `adc7fd6f`

> "after month or two, all photos that were previously tagged with faces lose their tags and never regain them."  
> — google_sheet, Reddit, strength 4 · [source](https://www.reddit.com/r/googlephotos/comments/bhdtfa/google_photos_has_such_great_potential_but_its/) · `0dcf569b`

> "face detection automatically reset i cannot see a familar face. App is telling me to set new face."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=6cd8a38c-946a-464d-ae9d-5ec8e2a867a7) · `9f72435e`

> "all the previous created album with faces has been deleted, and now it's not even showing option to add face on any picture"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=8369e4a7-c689-4eee-90e6-a87a862a43ae) · `bebc76db`

**Why This Matters**

Most items here are general retrieval rather than vague memory, but they matter because a person is often the strongest cue users retain when they forget dates, so broken face groups remove that fallback path. [`ce24cec5`, `3e3f888b`, `80646af5`, `0ec9a598`]

**Follow-up Research Questions**

1. Tell me about the last time you looked for photos of a specific person; what did you remember about those photos when you started?  
   _Gap:_ Feedback states the person but rarely what other cues users held at search time. (`ce24cec5`, `0ec9a598`, `80646af5`)
2. When a person did not show up where you expected, what did you do next?  
   _Gap:_ Few items describe fallback strategies or how long they took. (`ce24cec5`, `03ce9130`, `f8d0e58c`)
3. How often do you search for photos by person compared with other ways of searching?  
   _Gap:_ Frequency of person-based retrieval is unknown. (`80646af5`, `48305714`)
4. How did you notice that face groups had changed or disappeared, and what were you trying to do at that moment?  
   _Gap:_ Unclear whether loss is discovered during an actual retrieval task. (`9f72435e`, `3e3f888b`, `69fadc1e`, `bebc76db`)
5. What would make you confident that you had found all the photos of someone?  
   _Gap:_ We do not know what users accept as complete or 'found'. (`bc9cd9c8`, `6caf6d39`, `e784bc70`)
6. Which people or pets matter most to you to find, and why?  
   _Gap:_ High-stakes share is zero; the importance of these retrievals is unclear. (`adc7fd6f`, `bc9cd9c8`, `f8d0e58c`)
7. Describe a time you tried to correct how faces were grouped; what happened?  
   _Gap:_ Correction attempts and their outcomes are only partially described. (`adc7fd6f`, `6036ad8d`, `580e37d0`, `73792a42`)

Severity inputs: mean frustration 3.2381, high-stakes share 0.0, low-rating share 0.5882 (17 rated). Mean evidence strength 3.6667.

---

### 7. Opportunity Area: Irreplaceable milestone photos of loved ones appear missing

`oa-6e0c776d` · Life-Event Retrieval · 15 items (11 vague memory, 4 general)

**Problem Summary**

Users look for long-held backed-up photos and videos tied to meaningful moments, such as late parents, a relative singing, deceased pets, and weddings, and find them gone from the library. [`36b03a9e`, `c02bc3ae`, `ef36576f`, `b766003b`, `bae40b3d`, `fbdbf6e8`, `87de847b`, `1dce3c4e`]
What they remember is the life event, the person or pet, and a rough time span like "years ago" or "2012-2026", rather than precise details of the files. [`295ae9ed`, `5239f80e`, `16702342`, `fbdbf6e8`, `b6af135e`]
Several users tie the disappearance to a specific situation such as an app setup, an account recovery, a system update, a gallery merge, or a storage policy change, and insist they never deleted the items. [`36b03a9e`, `295ae9ed`, `ef36576f`, `b766003b`, `16702342`, `bae40b3d`]
The few reported search attempts were checking trash and archive, scrolling the app, checking the website, and following support steps, and none of them surfaced the items. [`295ae9ed`, `5239f80e`, `b766003b`, `d8d95c08`]
Some videos still exist in some form but will not play, which leaves users unsure whether the item is lost, hidden, or never synced. [`295ae9ed`, `b766003b`, `1dce3c4e`, `b6af135e`]

**Sub-themes**

- Irreplaceable old and milestone photos vanish from library (15 items, `c08`)

**Source breakdown:** play_store 8 (53%), google_community 4 (27%), google_sheet 2 (13%), app_store 1 (7%). Platforms: Android 8 (53%), Google Community 5 (33%), Reddit 1 (7%), iOS 1 (7%).

**Content types:** photo 10 (67%), video 5 (33%).

**What Users Remember**

Cue types (items): life_event 8, time_range 8, situation 7, person 6, feeling 2, visual_detail 2. Items with any cue: 15 of 15.

- "2012 -2026"
- "always appeared in my photo collection"
- "artistic nude photos taken about 15 years ago"
- "my baby boy"
- "backed up just like all of my other photos for years"
- "collection of classic vehicles"

**What Users Forget**

Not stated.

**Common Search Attempts**

- album_browse (2 items): "checked trash"; "checked archives"
- asked_support (2 items): "tried to reach Google, only AI chat bots and self help articles"; "tried the suggested steps"
- external_app_check (1 items): "Went on the website"
- manual_scroll (1 items): "went on the app to look"

**Breakdown Point**

Dominant: **item_appears_missing**. All: item_appears_missing 15.

**Representative Quotes**

> "while trying to find some old pics which already uploaded a long time ago, I can't find them now."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=7f1adf10-627a-4212-9e0a-5c9b69d73e2f) · `8853a34a`

> "Once it properly downloaded it deleted over 1000 of my photos and videos and I do not know how to get them back."  
> — app_store, iOS, strength 4 · [source](https://apps.apple.com/ca/app/google-photos-backup-edit/id962194608?see-all=reviews&platform=iphone) · `36b03a9e`

> "Now i cant find the same video on my google photos anywhere like its been deleted but i never deleted it."  
> — google_community, Google Community, strength 4 · [source](https://support.google.com/photos/thread/462563766?hl=en) · `295ae9ed`

> "a huge chunk of my old photos are missing"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=1e928442-696c-4c02-8e3c-1cd1d3abb036) · `ccfde137`

> "I go in and find that a lot of the photos and videos including very important and special occasions iny life."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=8d06824c-7271-4fe7-9ff8-d00261c0a138) · `c02bc3ae`

> "They have disappeared. How do I get them back?"  
> — google_community, Google Community, strength 4 · [source](https://support.google.com/photos/thread/467174777?hl=en) · `5239f80e`

> "I've got my Google account back and I'm still missing over a thousand pictures of my dead mother and father and my family and my kids"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=1bdb56c5-415c-4129-ba48-6f08d024dc3d) · `ef36576f`

> "When I was looking for a certain video, I couldn't find it anywhere."  
> — google_sheet, Reddit, strength 4 · [source](https://www.reddit.com/r/googlephotos/comments/1k8s7pn/missing_photos_and_videos_help/) · `b766003b`

**Why This Matters**

Most items are vague memory retrieval in which users recall the person, pet, or occasion and its emotional weight but cannot locate the file, though a few are general reports of old photos going missing, and every item is high-stakes because the content is irreplaceable. [`8853a34a`, `36b03a9e`, `ccfde137`, `ef36576f`, `16702342`, `d8d95c08`, `fbdbf6e8`, `b6af135e`]

**Follow-up Research Questions**

1. When you noticed the photos or videos were missing, what were you trying to find and what did you remember about it?  
   _Gap:_ Most items do not say what triggered the search or which cues the user had at the moment of search. (`8853a34a`, `ccfde137`, `c02bc3ae`, `fbdbf6e8`)
2. What steps did you take, in order, to look for the missing items before concluding they were gone?  
   _Gap:_ Most items state no search attempts, so we do not know what users try first or how thoroughly they look. (`36b03a9e`, `c02bc3ae`, `ef36576f`, `bae40b3d`)
3. What happened on your phone or account shortly before the items disappeared?  
   _Gap:_ Some users suspect a specific cause, but we do not know how reliably users can connect a disappearance to an event. (`36b03a9e`, `ef36576f`, `b766003b`, `16702342`, `bae40b3d`)
4. How did you decide whether the items were deleted, hidden, archived, or never backed up?  
   _Gap:_ Users are unsure which of these states their items are in. (`295ae9ed`, `b766003b`, `1dce3c4e`)
5. If you found a version of the item that would not play or looked different, would you consider it found? Why or why not?  
   _Gap:_ Unplayable or expired copies exist, but we do not know what users would accept as a successful retrieval. (`295ae9ed`, `b766003b`, `b6af135e`)
6. How often have you gone looking for an older meaningful photo or video and been unable to find it?  
   _Gap:_ There is no information on how frequently this happens. (`8853a34a`, `ccfde137`, `d8d95c08`)
7. Where else, if anywhere, do copies of these important memories exist outside Google Photos?  
   _Gap:_ We do not know whether the library is the only copy, which affects how much is at stake. (`36b03a9e`, `ef36576f`, `fbdbf6e8`, `87de847b`)

Severity inputs: mean frustration 4.3333, high-stakes share 1.0, low-rating share 1.0 (9 rated). Mean evidence strength 3.6667.

---

### 8. Opportunity Area: Recently downloaded or restored photos filed under old dates

`oa-a8abbac6` · Context-Based Retrieval Failure · 14 items (6 vague memory, 8 general)

**Problem Summary**

Users look for photos and videos they recently downloaded, backed up, restored, or uploaded, and what they remember is when or how the item arrived, not when it was originally captured. [`cbcda3c8`, `7bed222f`, `4f7e72e7`, `ca8c85d4`, `06c807fa`, `7a7ef8c7`]
The timeline places these items by embedded capture or file dates, or by dates that look arbitrary, so a picture downloaded on 27 July can show up in May 2012 and family photos land in the year they were taken. [`cbcda3c8`, `7bed222f`, `0f3106a8`, `aeb391c1`]
Editing, sharing to another app, or changing date and time can also create copies with new dates, which breaks the chronological order users rely on. [`7d3bef24`, `aeb391c1`]
Users then scroll through hundreds of videos or years of images, or sift through Collections, because they cannot view items by when they were added. [`4f7e72e7`, `b804be56`, `ca8c85d4`, `99746e46`, `7a7ef8c7`]
In some cases even date or AI search returns nothing for manually uploaded photos, and a Memories notification about a seven-year-old photo does not lead to the photo itself. [`2a76b54a`, `f1282758`]

**Sub-themes**

- Downloaded or uploaded photos buried under wrong dates (14 items, `c09`)

**Source breakdown:** google_sheet 7 (50%), play_store 7 (50%). Platforms: Android 12 (86%), Google Community 1 (7%), Reddit 1 (7%).

**Content types:** photo 13 (93%), video 1 (7%).

**What Users Remember**

Cue types (items): situation 7, source_app 7, time_range 6, person 1, purpose_task 1. Items with any cue: 12 of 14.

- "6 yr old photos"
- "download video on july 20"
- "downloaded at 12:55am on 27th July"
- "downloaded images"
- "downloaded images/videos"
- "downloaded on july 20"

**What Users Forget**

exact_date 2

**Common Search Attempts**

- manual_scroll (4 items): "scrolling through things until found them"; "scrolling through 2026 to 2020 images"
- album_browse (1 items): "sift through Collections"
- date_search (1 items): "searched by date"
- natural_language_query (1 items): "searched with AI"

**Breakdown Point**

Dominant: **wrong_metadata**. All: wrong_metadata 8, browse_fatigue 3, no_results 1, query_formulation 1, target_not_surfaced 1.

**Representative Quotes**

> "I downloaded a pic at 12:55am on the 27th of July. Then I go to Photos and find it saved all the way in 5:32pm on the 5th of May, 2012."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=b5a7131a-d9f5-472a-9177-96470a749006) · `cbcda3c8`

> "can't find family photos downloaded cause they end up being the year it was taken"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `7bed222f`

> "How can I easily find photos or videos that I most recently backed up, even if their time stamp (when they were taken) is from years ago?"  
> — google_sheet, Reddit, strength 4 · [source](https://www.reddit.com/r/googlephotos/comments/1ousytw/how_can_i_easily_find_photos_or_videos_that_i/) · `4f7e72e7`

> "it comes in by the date on the file so sometimes you upload a picture and you never see it again"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=04926827-f070-4a86-8304-6589751c01d9) · `0f3106a8`

> "Downloaded images should have a sort by download date option so you don't have a dig for years to find a photo from 2021."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=c436a612-4b31-4bd4-8824-6528ba767e60) · `b804be56`

> "every time you edit or share to another app, it saves it AND changes the date"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `7d3bef24`

> "i must scroll to hundreds video just to find my newly downloaded."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=5aa4cef4-f3d2-48b9-9981-33136527b77b) · `ca8c85d4`

> "It doesn't read an image metadata date - downloaded something from 2011 today? well todat is the date now."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=39e310ad-035f-4666-b47d-579dc2936fb0) · `99746e46`

**Why This Matters**

This area is only partly vague memory: 6 of the 14 items show users remembering the situation or arrival time rather than the capture date that the timeline uses, while the other 8 are general complaints about date ordering. [`cbcda3c8`, `7bed222f`, `4f7e72e7`, `ca8c85d4`, `7a7ef8c7`, `f1282758`, `0f3106a8`, `11b10cb1`]

**Follow-up Research Questions**

1. How often do you look for a photo or video soon after downloading, restoring, or uploading it, and how long does finding it usually take?  
   _Gap:_ Frequency and time cost are not reported; items describe the problem but not how often it happens. (`b804be56`, `ca8c85d4`, `7a7ef8c7`)
2. When you go looking for a recently added item, what do you remember about it at that moment: when you added it, where it came from, what is in it, or something else?  
   _Gap:_ Most items name the arrival event, but it is unclear which cues are actually available in the user's memory at search time. (`cbcda3c8`, `4f7e72e7`, `06c807fa`)
3. What do you try first when a recently added photo is not where you expected it in the timeline?  
   _Gap:_ Search attempts are rarely stated; scrolling dominates, and other strategies are mostly unreported. (`7bed222f`, `0f3106a8`, `99746e46`, `2a76b54a`)
4. What were you planning to do with the item once you found it, such as editing, sharing, or keeping it?  
   _Gap:_ Only one item states the purpose (editing), so the task behind the retrieval and its urgency are unknown. (`7a7ef8c7`, `7bed222f`)
5. Where do the items that end up misplaced usually come from, such as web downloads, messaging apps, SD cards, restores, or edits?  
   _Gap:_ Sources vary across items, and it is unclear which origins cause the most misplaced items. (`7d3bef24`, `99746e46`, `aeb391c1`, `2a76b54a`)
6. Have you ever stopped looking for a recently added item without finding it, and what happened afterward?  
   _Gap:_ The feedback does not show whether these searches end in abandonment or in lost trust. (`0f3106a8`, `2a76b54a`, `f1282758`)
7. What would let you say with confidence that you had found the exact item you just added?  
   _Gap:_ Users' criteria for a successful find, including telling originals apart from duplicates, are not described. (`7d3bef24`, `4f7e72e7`)

Severity inputs: mean frustration 3.2143, high-stakes share 0.0, low-rating share 0.75 (12 rated). Mean evidence strength 3.6429.

---

### 9. Opportunity Area: Mixed-size grid thumbnails disrupt scroll-based photo finding

`oa-4ff23301` · Emergent / Other (surfaced by clustering) · 13 items (0 vague memory, 13 general) · **Emergent category**

**Problem Summary**

Users scroll their main photo grid to find specific pictures, but randomly enlarged thumbnails and the loss of a uniform grid option make visual scanning slow and jarring. [`dc48a46d`, `4bc92db6`, `76e747f0`, `94017710`]
Users with large libraries or looking for older photos report that the uneven layout makes quick scanning harder, with one user describing a random photo six times the size of the rest every few rows among over 20,000 photos. [`b0539341`, `94017710`, `7edfb9c0`]
Enlarged items also obscure chronological order and disrupt orientation in the library, and one user says constant rearranging makes photos hard to find. [`64f1efb8`, `7edfb9c0`, `5f1ff6a9`]
Beyond finding photos, the irregular layout makes multi-selecting items confusing, and floating overlays further cover the browsing view. [`7d7b0a68`, `8aa0c909`]

**Sub-themes**

- Variable-size grid thumbnails hinder browsing for photos (13 items, `c10`)

**Source breakdown:** play_store 10 (77%), google_sheet 3 (23%). Platforms: Android 13 (100%).

**Content types:** photo 12 (92%), unknown 1 (8%).

**What Users Remember**

Cue types (items): situation 1. Items with any cue: 1 of 13.

- "previous layout"

**What Users Forget**

Not stated.

**Common Search Attempts**

- manual_scroll (10 items): "scanning the grid"; "scrolling the grid of all backed up files"

**Breakdown Point**

Dominant: **browse_fatigue**. All: browse_fatigue 13.

**Representative Quotes**

> "I can even find te pictures I am looking for half the because the pictures are all different sizes."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=ed6871e6-cf0f-4938-89ff-4a4b89ab4c0a) · `dc48a46d`

> "now it's making problems to find the picture...some pictures are shown in large & some in small it really creates a mess"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=3826c223-3784-4da9-8cbf-2765a02c9fbf) · `11241c4c`

> "This change makes it harder to quickly scan and find photos due to the uneven layout"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=38de3da7-e3d4-4cd3-9bea-9bba05e9c1f1) · `4bc92db6`

> "which is not only hard to view in a neat and organized way, it also makes it really confusing when trying to select multiple items"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=ccdb2c13-6d25-46eb-a516-e04cc5491779) · `7d7b0a68`

> "It is now much harder to quickly scan and find older photos"  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=2af991b1-0548-4866-bf3b-75196eddff30) · `b0539341`

> "it makes it really jarring to scroll through searching for a particular photo"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=bfb0d9d2-17f9-416a-83ab-abe1255ae5b6) · `76e747f0`

> "now even browsing your pictures, the UI is covered with needless floating menus and mismatched image sizes making it hard to find pictures"  
> — google_sheet, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `8aa0c909`

> "the latest update seems to arbitrarily choose a photo or video to enlarge compared to everything else in the grid layout of all backed up files"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=5c249e55-6c05-4b78-b39f-8b83e11b24c4) · `64f1efb8`

**Why This Matters**

All 13 items describe general browse-based retrieval rather than vague memory retrieval; users rely on scrolling and chronological position, so this area matters for vague memory only to the extent that scrolling through time is the fallback when users cannot describe a photo. [`76e747f0`, `64f1efb8`, `94017710`, `7edfb9c0`]

**Follow-up Research Questions**

1. When you scroll your library to find a photo, what do you know about it at the start, such as roughly when it was taken or what it looks like?  
   _Gap:_ Items state what users were looking for but almost never what they remembered about it. (`dc48a46d`, `76e747f0`, `94017710`)
2. How often do you scroll the main grid to find a photo, compared with using search?  
   _Gap:_ Frequency of manual scrolling as a retrieval method and its relation to search is unknown. (`76e747f0`, `c40f1300`, `94017710`)
3. How do you use dates or the order of photos to tell where you are in your library while scrolling?  
   _Gap:_ Users say chronological order is obscured but not how they navigate by time. (`64f1efb8`, `7edfb9c0`)
4. Tell me about a recent time you scrolled for a photo and gave up or took longer than expected. What happened?  
   _Gap:_ Evidence reports frustration but not whether searches fail outright or just take longer. (`dc48a46d`, `94017710`, `8132e93e`)
5. What do you do when you can't find a photo by scrolling?  
   _Gap:_ Fallback behaviors are not described, beyond one user switching to an older app version. (`b0539341`, `8132e93e`)
6. How do you remember where a particular photo sits in your library, and what happens when that position changes?  
   _Gap:_ One item suggests spatial memory matters, but how users rely on it is unclear. (`5f1ff6a9`)
7. How important were the photos you were trying to find in these situations?  
   _Gap:_ No items were high stakes, so the importance of the items being browsed for is unknown. (`11241c4c`, `8aa0c909`, `8132e93e`)

Severity inputs: mean frustration 3.3846, high-stakes share 0.0, low-rating share 0.8462 (13 rated). Mean evidence strength 2.9231.

---

### 10. Opportunity Area: Locked Folder photos missing after moves, updates, resets

`oa-a340b4dd` · Context-Based Retrieval Failure · 11 items (2 vague memory, 9 general)

**Problem Summary**

Users look for photos and videos they stored in, or moved into or out of, the Locked Folder, and find them missing from folders, trash, and the Locked Folder itself. [`006ff018`, `36c8cc55`, `6dd6c3cf`, `50ac66f5`, `f42c8b0c`, `58af0dd2`]
What users remember is the situation around the loss, such as an app update, a device reset and restore, an app crash mid-move, or re-signing into an account, rather than details of the items themselves. [`36c8cc55`, `eda86da0`, `6dd6c3cf`, `50ac66f5`, `f42c8b0c`, `5e67efd3`]
Users mostly try to recover the items by browsing: checking the Locked Folder, Collections, settings, deleted files, and even the device file manager. One user also asked for support without success. [`006ff018`, `eda86da0`, `6dd6c3cf`, `5e67efd3`]
In one case the Locked Folder entry point itself disappeared after an update. Another user had disabled backup and later found the folder empty with no recovery path. [`eda86da0`, `66b894fc`]
One user moved items out of the Locked Folder and forgot where they originally came from, and searching by file name did not surface them. [`12b4baa5`]

**Sub-themes**

- Locked Folder photos vanish or become inaccessible (11 items, `c11`)

**Source breakdown:** play_store 5 (45%), google_community 4 (36%), google_sheet 2 (18%). Platforms: Android 5 (45%), Google Community 4 (36%), Reddit 2 (18%).

**Content types:** photo 9 (82%), unknown 2 (18%).

**What Users Remember**

Cue types (items): situation 10, life_event 1, purpose_task 1. Items with any cue: 11 of 11.

- "after the recent Google Photos update"
- "after update"
- "app just crashed"
- "my family memories"
- "fixed my old phone after buying a new one just to get my photos back"
- "in locked folder"

**What Users Forget**

album_name 1

**Common Search Attempts**

- album_browse (5 items): "checked deleted files"; "logged back in and looked in locked folder"
- asked_support (1 items): "got help but it doesn't work"
- external_app_check (1 items): "checked every single folder and file manager"
- keyword_search (1 items): "searching by file name"

**Breakdown Point**

Dominant: **item_appears_missing**. All: item_appears_missing 10, target_not_surfaced 1.

**Representative Quotes**

> "I lost my photo when I moved it out from locked folder, I checked every single folder and even from my file manager."  
> — google_community, Google Community, strength 5 · [source](https://support.google.com/photos/thread/470197126?hl=en) · `006ff018`

> "now when i have logged back in so I don't find any photo in locked folder even if they were all backed up"  
> — google_community, Google Community, strength 5 · [source](https://support.google.com/photos/thread/466834854?hl=en) · `36c8cc55`

> "Locked Folder is missing from my Google Photos app."  
> — play_store, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=c3d63140-a872-4b55-8a04-7747712af335) · `eda86da0`

> "My mobile is reset few day ago and then I backup my google photos everything okay but my locked folder photos are not there"  
> — google_community, Google Community, strength 4 · [source](https://support.google.com/photos/thread/470430325?hl=en) · `6dd6c3cf`

> "Now last week suddenly I found my locked folder empty and all my data disappeared."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=ef181a04-622f-4a32-b252-6530236da671) · `66b894fc`

> "I moved around 4000 photos from my google photos to a locked folder (an option within the app) then my app just crashed I opened it again and now I can't find any of my photos."  
> — google_sheet, Reddit, strength 4 · [source](https://www.reddit.com/r/googlephotos/comments/1esvr8y/i_cant_find_my_gallery/) · `50ac66f5`

> "all my picture in locked folder was missing after update."  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=c0bf1c6a-d918-4585-887f-e344c310b29c) · `f42c8b0c`

> "Unfortunately I lost my locked folder photo and videos."  
> — google_community, Google Community, strength 3 · [source](https://support.google.com/photos/thread/470026409?hl=en) · `58af0dd2`

**Why This Matters**

Most of these 11 items (9 general retrieval) are about items appearing lost from the Locked Folder rather than vague memory, and only two involve users who remember a purpose or a move but not the details. Still, the stakes are high, because users describe irreplaceable family memories and thousands of photos. [`50ac66f5`, `58af0dd2`, `5e67efd3`, `12b4baa5`]

**Follow-up Research Questions**

1. Walk me through the last time photos in your Locked Folder seemed to disappear: what happened just before, and what did you check first?  
   _Gap:_ Most items do not describe the sequence of events or what users tried first. (`66b894fc`, `50ac66f5`, `f42c8b0c`, `58af0dd2`)
2. When you moved photos out of the Locked Folder, where did you expect them to show up?  
   _Gap:_ It is unclear what users expect about where items land after leaving the Locked Folder. (`006ff018`, `12b4baa5`)
3. What did you understand about how the Locked Folder relates to backup before you started using it?  
   _Gap:_ Users' understanding of backup for Locked Folder items, and how that shapes their expectations, is unknown. (`36c8cc55`, `6dd6c3cf`, `66b894fc`)
4. What kinds of photos or videos do you keep in the Locked Folder, and how important are they to you?  
   _Gap:_ Beyond family memories and content for a video, the evidence does not show the types or value of stored items. (`58af0dd2`, `5e67efd3`)
5. When you look for items you moved, what details about them do you still remember, such as date, album, or content?  
   _Gap:_ Only one item notes a forgotten detail, so the cues available at search time are unknown. (`12b4baa5`)
6. How often have you had trouble finding photos after an app update, device reset, or signing back into your account?  
   _Gap:_ The frequency of these losses is not known. (`36c8cc55`, `eda86da0`, `6dd6c3cf`, `f42c8b0c`)
7. What would you need to see to feel confident your missing photos were found or truly gone?  
   _Gap:_ It is unclear what users would accept as a resolution. (`006ff018`, `6dd6c3cf`, `50ac66f5`)

Severity inputs: mean frustration 3.9091, high-stakes share 0.5455, low-rating share 0.8 (5 rated). Mean evidence strength 3.7273.

---

### 11. Opportunity Area: Photos shared by family and friends unreachable

`oa-5fa72a6b` · Context-Based Retrieval Failure · 10 items (5 vague memory, 5 general)

**Problem Summary**

Users look for photos and albums that a specific person shared with them, such as a dad's trip photos, a son's photos and videos, a husband's many albums, or a friend's gallery where they are tagged, and they remember who shared it more than what it contains. [`addb4ba5`, `532e1d65`, `f25d8212`, `0afb9276`]
Several users report that after an app update the entry points they relied on, such as the "sharing" and "for you" links, were gone, so they no longer know where shared items live. [`addb4ba5`, `f25d8212`]
When users search by words from an album title or location, some shared albums do not appear in results even though they are visible in the albums list, and browsing collections shows device folders instead of the remembered album. [`532e1d65`, `394dad57`]
Retrieval also breaks when the only route back is a link: a tagged gallery cannot be found without its URL, and a shared album link returns a deleted or invalid error. [`0afb9276`, `ca1e1999`]
Users also struggle to find photos they sent to others, and shared album photos disappear after the sharer deletes them from their phone. [`19c7883a`, `90a92dbd`]

**Sub-themes**

- Shared albums and partner-shared photos unreachable (10 items, `c13`)

**Source breakdown:** google_sheet 9 (90%), play_store 1 (10%). Platforms: Android 6 (60%), Reddit 3 (30%), Google Community 1 (10%).

**Content types:** photo 9 (90%), unknown 1 (10%).

**What Users Remember**

Cue types (items): situation 5, source_app 5, person 4, purpose_task 2, trip_or_event 2, text_fragment 1, time_range 1. Items with any cue: 10 of 10.

- "albums you made on your device"
- "backed up from an old phone, 2023 or earlier"
- "deleted them from their phone"
- "even after I backed up the photos"
- "Facebook"
- "family and holidays travel album"

**What Users Forget**

Not stated.

**Common Search Attempts**

- album_browse (2 items): "looked in albums and collection in the app"; "Scrolling through the albums page"
- keyword_search (2 items): "searching for any words in the album title"; "trying single words and word combinations, location"
- external_app_check (1 items): "searched for this error online, last post 5 years ago"

**Breakdown Point**

Dominant: **target_not_surfaced**. All: target_not_surfaced 9, browse_fatigue 1.

**Representative Quotes**

> "I just updated the app.And now I can't find the pictures my dad shared with me from his recent trip."  
> — google_sheet, Reddit, strength 4 · [source](https://www.reddit.com/r/googlephotos/comments/1gb1k3v/did_they_remove_sharing_albums_ive_always_shared/) · `addb4ba5`

> "However, when searching for any words in the album title in the search field, some of the albums do not show up in the search results drop-down (including trying single words and word combinations, location, etc.)."  
> — google_sheet, Google Community, strength 5 · [source](https://support.google.com/photos/thread/262562) · `532e1d65`

> "They took away the "sharing" link and now I can't find photos and videos my son has shared."  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `f25d8212`

> "It's impossible to find the gallery of photos I'm tagged in that my friend sent without the URL."  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `0afb9276`

> "Shared album photos went missing after user deleted them from their phone even after I backed up the photos?"  
> — google_sheet, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `90a92dbd`

> "Google photos simply show all the devices folder and i couldn't find my family and travel holiday album from the app"  
> — google_sheet, Android, strength 4 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `394dad57`

> "can't find my photos when I want to put one in Facebook to send to a friend"  
> — play_store, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN&reviewId=c78446e3-b577-4963-84ee-6310f0bf013c) · `d7cebb33`

> "Why is it so difficult to find the photos I've sent other people?"  
> — google_sheet, Android, strength 3 · [source](https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN) · `19c7883a`

**Why This Matters**

Half of the items are vague memory retrieval, where users recall the person who shared, the trip, or the purpose but not where the item sits or what it is called; the other half are general retrieval failures such as broken links, missing recent photos, and title search gaps. [`addb4ba5`, `f25d8212`, `0afb9276`, `394dad57`, `532e1d65`, `ca1e1999`, `db83381d`]

**Follow-up Research Questions**

1. When you look for something someone shared with you, what do you remember about it first?  
   _Gap:_ We do not know which cue, such as the person, the event, or the time, users rely on at the moment of search. (`addb4ba5`, `f25d8212`, `0afb9276`)
2. Walk me through the last time you tried to find a shared photo or album. Where did you look first, and what did you do next?  
   _Gap:_ Most items do not say what users tried or in what order. (`addb4ba5`, `f25d8212`, `0afb9276`, `90a92dbd`, `19c7883a`)
3. How often do you need to go back to photos that family or friends shared with you?  
   _Gap:_ The feedback does not show how often this need comes up. (`addb4ba5`, `532e1d65`, `f25d8212`)
4. How do you tell apart photos others shared with you, photos you shared, and photos on your own device?  
   _Gap:_ It is unclear how users think about ownership and location of shared items versus device folders. (`394dad57`, `19c7883a`, `532e1d65`)
5. What happened the last time a shared link or shared album stopped working for you?  
   _Gap:_ We do not know how users recover, or whether they give up, when a link or the sharer's copy is gone. (`0afb9276`, `90a92dbd`, `ca1e1999`)
6. How important were the shared photos you could not find, and what did you do without them?  
   _Gap:_ The stakes and the consequences of failure are not described. (`addb4ba5`, `f25d8212`, `90a92dbd`)
7. What would you need to see to feel confident you had found the right shared photo or album?  
   _Gap:_ We do not know what users would accept as "found" for shared items. (`532e1d65`, `0afb9276`, `394dad57`)

Severity inputs: mean frustration 3.1, high-stakes share 0.0, low-rating share 1.0 (6 rated). Mean evidence strength 3.7.

---

## Merged areas

- `oa-76a5bc8f`  → `oa-af73e0ca`

## Unclustered items

16 items fit no cluster. They stay in `insights` (Evidence Explorer). By extraction category: context_based_retrieval_failure 11, other_emergent 3, time_based_memory_gap 1, life_event_retrieval 1.
