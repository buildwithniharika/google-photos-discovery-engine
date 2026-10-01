# Problem Statement: AI-Powered Discovery Engine for Vaguely Remembered Photo Retrieval

## Table of Contents

1. [Project Context](#project-context)
2. [Phase 1 Objective](#phase-1-objective)
3. [Core Problem](#core-problem)
4. [Mandatory Data Sources](#mandatory-data-sources)
5. [Data Collection Requirements](#data-collection-requirements)
6. [What the Discovery Engine Must Analyze](#what-the-discovery-engine-must-analyze)
7. [Retrieval Problem Categories](#retrieval-problem-categories)
8. [Required Dashboard Output](#required-dashboard-output)
9. [Opportunity Scoring Framework](#opportunity-scoring-framework)
10. [MVP Scope for Phase 1](#mvp-scope-for-phase-1)
11. [Example Output](#example-output)
12. [Success Criteria](#success-criteria)
13. [Non-Goals](#non-goals)
14. [Final Build Prompt](#final-build-prompt)

---

## Project Context

Google Photos users accumulate thousands of photos, videos, screenshots, documents, receipts, memes, travel memories, and other visual information over years of usage.

When users know exactly what they are looking for, search is relatively straightforward. They may search by a person, place, object, date, text, or album. However, retrieval becomes much harder when the user remembers that a photo exists but cannot clearly remember the exact details needed to find it.

For example, a user may remember:

- "That small café we went to during our Goa trip."
- "The picture of the medicine I took when I was sick last year."
- "The screenshot of a recipe someone sent me."
- "That document I photographed, but I don't remember the name."
- "The photo from a trip, but I don't remember which month it was."

In these situations, the user has a memory of the photo, but the memory is incomplete. They may not remember when it was taken, where it was taken, what album it belongs to, who was in it, or the exact words needed to search for it.

> **Strategic goal:** Increase the percentage of users who successfully retrieve a photo they remember but cannot precisely describe when they start searching.

The challenge is **not** to improve search in general. The challenge is to understand how people remember old visual information, where retrieval breaks down when memory is vague, and which opportunity areas can meaningfully improve successful retrieval.

---

## Phase 1 Objective

The first phase of this project is to build an **AI-powered discovery engine** that analyzes public user feedback and conversations about Google Photos retrieval at scale.

Before proposing any product solution, the system should help uncover real user problems, behavior patterns, and opportunity areas related to vaguely remembered photo retrieval.

The discovery engine should **collect, clean, classify, analyze, and synthesize** user-generated feedback from specified public sources. It should help a Product Manager understand:

- What users are trying to retrieve
- What they remember
- What they forget
- How they search
- Where the current retrieval experience fails

The final output should **not** be a generic sentiment report. It should be an evidence-backed product discovery system that helps identify and compare specific retrieval problems.

---

## Core Problem

Users often struggle to retrieve old photos, screenshots, documents, or visual memories when they cannot describe them precisely.

The issue is not always that users do not know what they want. In many cases, users know the photo exists, but their memory is based on partial, contextual, emotional, or approximate cues.

| Users may remember... | Example |
|---|---|
| A situation | "when I was sick" |
| A place vaguely | "that café in Goa" |
| A person involved | "with my college friends" |
| A purpose | "the bill I needed for reimbursement" |
| A visual detail | "the blue medicine bottle" |
| A time range | "sometime last year" |
| A source | "a screenshot from WhatsApp" |
| A life event | "my first office party" |
| A feeling | "that funny photo" |

But they may forget:

- Exact date
- Exact location
- Album name
- File type
- Exact text visible in the image
- Who sent it
- Whether it was a photo, screenshot, video, or document
- The right search keyword

Because of this **mismatch between how users remember and how they search**, users may fail to retrieve photos even when those photos exist in their library.

---

## Mandatory Data Sources

For Phase 1, the discovery engine must scrape, ingest, and analyze data from the following specified sources.

### 1. Google Play Store Reviews

- **Source:** <https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN>
- The engine should scrape public reviews for the Google Photos Android app. It should capture review text, rating, date, and any available metadata.
- The analysis should identify retrieval-related complaints, search failures, frustration around finding old photos, and Android-specific patterns.

### 2. Apple App Store Reviews

- **Source:** <https://apps.apple.com/us/app/google-photos-backup-edit/id962194608?see-all=reviews&platform=iphone>
- The engine should scrape public reviews for the Google Photos iPhone app. It should capture review text, rating, date, and any available metadata.
- The analysis should identify search-related issues, retrieval pain points, and iOS-specific expectations or frustrations.

### 3. Additional Dataset (Reddit / Google Community / Play Store Reviews)

- **Source:** <https://docs.google.com/spreadsheets/d/1lrAFUCIkTOCN9uje8u_nlxshmWO8yKe3ygSAlwRW28M/edit?usp=sharing>
- The engine should ingest Reddit conversations from the provided Google Sheet.
- These discussions should be treated as qualitative user conversations that may reveal deeper context around failed photo retrieval, vague memory, screenshots, documents, missing photos, and search behavior.

### 4. Google Photos Community / Support Discussions

- **Source:** <https://support.google.com/photos/threads?hl=en&thread_filter=(category:photos_restore)&sjid=15402027034214155333-NC>
- The engine should scrape public Google Photos Help Community threads from the specified category.
- These discussions should be analyzed to understand user-reported retrieval issues, restore-related confusion, missing photo concerns, and cases where users believe a photo exists but cannot locate it.

---

## Data Collection Requirements

The discovery engine should prioritize **only the four specified data sources** in Phase 1.

For every scraped or ingested item, the system should store:

| Field | Notes |
|---|---|
| Source name | |
| Source URL | |
| Platform | Android, iOS, Reddit, or Google Community |
| Original user text | Preserve original language |
| Date | If available |
| Rating | If available |
| User-reported issue | |
| Extracted retrieval problem | |
| Relevance to vaguely remembered photo retrieval | |
| Evidence quote | |
| AI confidence score | |

The system should preserve the original user language wherever possible so that insights remain grounded in real evidence.

---

## What the Discovery Engine Must Analyze

For each user review, comment, post, or discussion, the AI system should identify:

1. Is this about Google Photos?
2. Is this about photo retrieval?
3. Is this about vaguely remembered photo retrieval?
4. What is the user trying to find?
5. What type of content is involved?
6. What does the user remember?
7. What has the user forgotten?
8. What did the user search or try?
9. Where did the retrieval journey break down?
10. What emotion or frustration is expressed?
11. Which retrieval problem category does this belong to?
12. How strong is the evidence?
13. Is this useful for product opportunity discovery?

The system should separate relevant retrieval problems from unrelated complaints.

> Complaints about **storage, pricing, backup, sync, sharing, or deletion** should only be included if they directly affect the user's ability to find or retrieve a remembered photo.

---

## Retrieval Problem Categories

The discovery engine should classify user feedback into meaningful problem categories. Suggested categories include:

| # | Category | Description | Example |
|---|---|---|---|
| 1 | **Context-Based Retrieval Failure** | Users remember the situation around the photo but not the exact searchable details. | "The café we went to on that Goa trip." |
| 2 | **Time-Based Memory Gap** | Users remember an approximate time but not the exact date, month, or year. | "I took it sometime last year." |
| 3 | **Screenshot and Document Retrieval Failure** | Users struggle to find screenshots, receipts, prescriptions, IDs, bills, documents, or notes. | "I can't find the screenshot of the payment receipt." |
| 4 | **Object or Visual Detail Search Failure** | Users remember an object, color, product, or scene, but search does not surface the right result. | "The photo with the blue medicine bottle." |
| 5 | **People and Event Association Failure** | Users remember who was involved or the event context but cannot find the image. | "That photo from my friend's birthday." |
| 6 | **Location Ambiguity** | Users remember a place vaguely but not the exact location name. | "Some restaurant near the beach in Goa." |
| 7 | **Life-Event Retrieval** | Users remember the personal meaning of the photo but not the metadata. | "The day I moved into my first apartment." |
| 8 | **Search Trust Breakdown** | Users believe the photo exists but lose trust because search results feel incomplete, inconsistent, or irrelevant. | "I know the photo is there, but Google Photos won't show it." |

---

## Required Dashboard Output

The discovery engine should produce a dashboard or structured report that helps the Product Manager compare retrieval opportunity areas.

### Each opportunity area should include

- Opportunity name
- User problem summary
- Source breakdown
- Type of photo or visual content involved
- What users remembered
- What users forgot
- Common search attempts
- Breakdown point in the retrieval journey
- Representative user quotes
- Source links or references
- Frequency estimate
- Severity estimate
- Evidence quality score
- Strategic fit score
- Product opportunity score
- Suggested follow-up research questions

### The dashboard should allow filtering by

- Source
- Platform
- Problem category
- Severity
- Relevance to vague memory retrieval
- Content type
- Confidence score

---

## Opportunity Scoring Framework

Each retrieval problem area should be scored using the following dimensions:

| # | Dimension | Question it answers |
|---|---|---|
| 1 | **Frequency** | How often does this problem appear across the data sources? |
| 2 | **Severity** | How frustrating, painful, or important does the problem appear to users? |
| 3 | **Strategic Fit** | How closely does this problem connect to the goal of helping users retrieve photos they remember but cannot precisely describe? |
| 4 | **Evidence Quality** | How clear and specific is the user evidence? |
| 5 | **Product Leverage** | Can Google Photos meaningfully improve this problem through AI, search UX, memory understanding, guided retrieval, or better result explanation? |
| 6 | **Research Value** | Would this problem be useful to explore further through interviews, surveys, or concept testing? |

The system should use these scores to **rank opportunity areas** and help the Product Manager decide which problem is worth solving first.

---

## MVP Scope for Phase 1

The first version of the discovery engine should include:

- [ ] Data scraping or ingestion from the four specified sources
- [ ] Data cleaning and deduplication
- [ ] Relevance classification for photo retrieval and vague memory retrieval
- [ ] AI-powered insight extraction
- [ ] Clustering of similar retrieval problems
- [ ] Evidence tables with real user quotes
- [ ] Opportunity scoring
- [ ] A dashboard for comparing problem areas
- [ ] Filters by source, platform, category, and severity
- [ ] Exportable opportunity report in CSV, Google Sheets, or PDF format

---

## Example Output

### Opportunity Area: Screenshot and Document Retrieval Failure

**Problem Summary**

Users often struggle to retrieve screenshots, receipts, prescriptions, documents, and bills because they remember the purpose of the image but not the exact text, date, or app source.

**What Users Remember**

- "payment receipt"
- "medicine prescription"
- "screenshot from WhatsApp"
- "document for work"
- "bill I needed"

**What Users Forget**

- Date taken
- Exact text inside the image
- Sender
- Album location
- Whether it was a screenshot or photo

**Common Search Attempts**

- Searching broad keywords like "receipt," "medicine," "bill," or "screenshot"
- Scrolling through screenshots
- Searching approximate dates
- Looking through WhatsApp media

**Breakdown Point**

Search results are too broad, irrelevant, or fail to understand the user's task-based memory.

**Opportunity Score:** High

**Why This Matters**

This problem directly maps to vague retrieval because users remember the purpose and context of the visual item but not the precise metadata needed to find it.

---

## Success Criteria

The discovery engine will be considered successful if it helps the Product Manager:

- Identify at least **5 to 8 distinct retrieval problem areas**.
- Support each problem area with real user evidence.
- Understand what users remember and forget during failed retrieval.
- Compare opportunity areas using a consistent scoring model.
- Separate vague retrieval problems from general Google Photos complaints.
- Select one strong opportunity area for deeper research or product concept development.
- Generate sharper follow-up questions for user interviews.
- Avoid generic conclusions like "search should be better."

---

## Non-Goals

This phase should **not** focus on:

- Designing the final user-facing solution.
- Improving Google Photos search directly.
- Building a production-ready Google-scale system.
- Analyzing every Google Photos complaint broadly.
- Generic sentiment analysis.
- Generic feature ideation without user evidence.
- Solving backup, storage, pricing, sharing, or deletion issues unless they directly affect retrieval.
- Making claims without traceable user evidence.

---

## Final Build Prompt

Build an AI-powered discovery engine for a Product Manager working on Google Photos.

The goal is to analyze public user feedback and identify real opportunity areas around vaguely remembered photo retrieval. This means cases where users know a photo, screenshot, document, or visual memory exists, but cannot precisely remember the date, location, album, file type, or exact search terms needed to find it.

The engine must scrape, ingest, and analyze data from these four sources:

1. **Google Play Store reviews:** <https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN>
2. **Apple App Store reviews:** <https://apps.apple.com/us/app/google-photos-backup-edit/id962194608?see-all=reviews&platform=iphone>
3. **Additional Dataset (Reddit / Google Community / Play Store reviews):** <https://docs.google.com/spreadsheets/d/1lrAFUCIkTOCN9uje8u_nlxshmWO8yKe3ygSAlwRW28M/edit?usp=sharing>
4. **Google Photos Community discussions:** <https://support.google.com/photos/threads?hl=en&thread_filter=(category:photos_restore)&sjid=15402027034214155333-NC>

For every scraped or ingested item, store the source, platform, original text, date, rating if available, source URL, extracted retrieval issue, relevance to vague memory retrieval, representative quote, and AI confidence score.

The system should clean the data, remove duplicates, classify relevance, extract structured insights, cluster similar issues, and generate opportunity areas.

For each user comment or review, identify what the user was trying to find, what they remembered, what they forgot, what they searched or tried, where the experience broke down, and what emotion or frustration they expressed.

The system should classify problems into categories such as context-based retrieval failure, time-based memory gap, screenshot/document retrieval failure, visual detail search failure, people/event association failure, location ambiguity, life-event retrieval, and search trust breakdown.

Create a dashboard that allows the Product Manager to compare opportunity areas by frequency, severity, evidence quality, strategic fit, product leverage, and research value.

Each opportunity area should include a problem summary, source breakdown, representative quotes, remembered cues, forgotten details, attempted search behaviors, breakdown points, and an opportunity score.

The system should go beyond sentiment analysis and generic review summarization. It should help identify specific, evidence-backed product opportunities that can improve the percentage of users who successfully retrieve a photo they remember but cannot precisely describe when they start searching.
