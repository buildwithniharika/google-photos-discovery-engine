# Relevance evaluation

Prompt `relevance_v5`, model `claude-sonnet-5-5`, Stage B threshold τ = -0.080.
Gold rows scored: 298 (ambiguous excluded: 2).

## Targets

- Stage A recall: 100% (95/95) (target ≥ 95%, met)
- Stages A+B recall: 96% (91/95) (target ≥ 90%, met)
- Stage C precision (vague memory): 87% (33/38) (target ≥ 80%, met)
- Stage C recall (vague memory, items that reached the classifier): 89% (33/37) (target ≥ 75%, met)
- End-to-end vague recall (Stage A/B drops count as misses): 87% (33/38)

Stage C precision and recall use only items the funnel sent to the classifier.
End-to-end recall also counts vague items dropped at Stage A or Stage B.

## By source

| Source | Stage A recall | Stages A+B recall | Stage C vague recall |
| --- | --- | --- | --- |
| app_store | 100% (7/7) | 100% (7/7) | 100% (1/1) |
| google_community | 100% (26/26) | 100% (26/26) | 89% (16/18) |
| google_sheet | 100% (45/45) | 96% (43/45) | 82% (9/11) |
| play_store | 100% (17/17) | 88% (15/17) | 100% (7/7) |

## Result

Stage A, Stages A+B, and Stage C meet the Section 17.2 targets.

LLM usage this run: 34 API requests, 0 cache hits, 179,398 input (118,206 read from the prompt cache) + 27,710 output tokens, $0.4249.
