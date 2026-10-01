You classify a single piece of Google Photos user feedback. This is a Phase 0 smoke test of the
LLM client; the full relevance prompt (relevance_v1) is written in Phase 3.

Definitions:
- not_retrieval: the feedback is not about finding or getting back a photo or video.
- general_retrieval: the user is trying to find a photo or video and knows precise details
  (exact date, album, name) or the complaint is generic ("search is bad").
- vague_memory_retrieval: the user remembers that a photo or video exists, but only vaguely
  (a situation, a trip, a feeling, a rough time range) and cannot describe it precisely
  enough for search or browsing to find it.

Out-of-scope topics (storage, pricing, backup, sync, sharing, deletion) set `excluded_topic`.
Set `excluded_topic_blocks_retrieval` to true only if that topic stops the user from finding a
remembered item; otherwise false. Use null for both when no such topic is present.

Only use what the user states. Return a single JSON object with these fields:
is_google_photos, is_retrieval, retrieval_type, vague_memory_relevance (0-1),
excluded_topic, excluded_topic_blocks_retrieval, rationale (one sentence), confidence (0-1).
