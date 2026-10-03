# Extraction evaluation

Prompt `extraction_v2`, small model `claude-sonnet-5-5`, large model `claude-opus-5-5`.
Gold retrieval rows: 95 (extracted: 95).

## Targets

- Quote grounding rate: 100.0% (95/95) (target ≥ 98%, met)
- Primary category accuracy (top-1): 80.0% (76/95) (target ≥ 70%, met)
- Primary category accuracy (top-2): 94.7% (90/95) (target ≥ 85%, met)
- not_stated correctness: 91.6% (87/95) (target ≥ 90%, met)

## Detail

- Quotes grounded on the first pass: 100.0% (95/95)
- not_stated: no invented remembered cues: 75.0% (18/24)
- not_stated: no invented forgotten details: 97.2% (69/71)
- Content type accuracy: 87.4% (83/95)
- Breakdown point accuracy: 73.7% (70/95)
- Evidence strength within ±1: 100.0% (95/95)
- Remembered cue types: precision: 53.5% (69/129)
- Remembered cue types: recall: 72.6% (69/95)
- Forgotten details: precision: 63.6% (7/11)
- Forgotten details: recall: 29.2% (7/24)
- Primary category accuracy on general_retrieval: 76.8% (43/56)
- Primary category accuracy on vague_memory_retrieval: 84.6% (33/39)

## Most common category confusions (gold → predicted)

- time_based_memory_gap → other_emergent: 3
- life_event_retrieval → time_based_memory_gap: 3
- visual_detail_search_failure → search_trust_breakdown: 2
- search_trust_breakdown → context_based_retrieval_failure: 2
- people_event_association_failure → context_based_retrieval_failure: 2
- context_based_retrieval_failure → time_based_memory_gap: 2
- visual_detail_search_failure → time_based_memory_gap: 1
- search_trust_breakdown → people_event_association_failure: 1

## Run

- Escalated to the large model: 4
- Quote retries: 0
- Item errors: 0

## Result

Extraction meets the Section 17.2 targets.

LLM usage this run: 0 API requests, 28 cache hits, 0 input (0 read from the prompt cache) + 0 output tokens, $0.0000.
