# Extraction evaluation

Prompt `extraction_v1`, small model `claude-sonnet-5-5`, large model `claude-opus-5-5`.
Gold retrieval rows: 95 (extracted: 95).

## Targets

- Quote grounding rate: 100.0% (95/95) (target ≥ 98%, met)
- Primary category accuracy (top-1): 72.6% (69/95) (target ≥ 70%, met)
- Primary category accuracy (top-2): 77.9% (74/95) (target ≥ 85%, open)
- not_stated correctness: 94.7% (90/95) (target ≥ 90%, met)

## Detail

- Quotes grounded on the first pass: 100.0% (95/95)
- not_stated: no invented remembered cues: 83.3% (20/24)
- not_stated: no invented forgotten details: 98.6% (70/71)
- Content type accuracy: 86.3% (82/95)
- Breakdown point accuracy: 71.6% (68/95)
- Evidence strength within ±1: 100.0% (95/95)
- Remembered cue types: precision: 59.8% (67/112)
- Remembered cue types: recall: 70.5% (67/95)
- Forgotten details: precision: 83.3% (10/12)
- Forgotten details: recall: 41.7% (10/24)
- Primary category accuracy on general_retrieval: 67.9% (38/56)
- Primary category accuracy on vague_memory_retrieval: 79.5% (31/39)

## Most common category confusions (gold → predicted)

- context_based_retrieval_failure → search_trust_breakdown: 7
- time_based_memory_gap → search_trust_breakdown: 4
- context_based_retrieval_failure → other_emergent: 3
- visual_detail_search_failure → search_trust_breakdown: 2
- people_event_association_failure → search_trust_breakdown: 2
- search_trust_breakdown → other_emergent: 2
- visual_detail_search_failure → other_emergent: 2
- life_event_retrieval → context_based_retrieval_failure: 1

## Run

- Escalated to the large model: 6
- Quote retries: 0
- Item errors: 0

## Result

Below target:
- Primary category top-2 accuracy 77.9% is below 85%

LLM usage this run: 30 API requests, 0 cache hits, 239,430 input (108,345 read from the prompt cache) + 37,505 output tokens, $0.5216.
