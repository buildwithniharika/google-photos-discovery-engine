# Relevance evaluation

Prompt `relevance_v4`, model `claude-sonnet-5-5`, Stage B threshold τ = -0.080.
Gold rows scored: 298 (ambiguous excluded: 2).

## Targets

- Stage A recall: 100% (95/95) (target ≥ 95%, met)
- Stages A+B recall: 96% (91/95) (target ≥ 90%, met)
- Stage C precision (vague memory): 64% (36/56) (target ≥ 80%, open)
- Stage C recall (vague memory, items that reached the classifier): 97% (36/37) (target ≥ 75%, met)
- End-to-end vague recall (Stage A/B drops count as misses): 95% (36/38)

Stage C precision and recall use only items the funnel sent to the classifier.
End-to-end recall also counts vague items dropped at Stage A or Stage B.

## By source

| Source | Stage A recall | Stages A+B recall | Stage C vague recall |
| --- | --- | --- | --- |
| app_store | 100% (7/7) | 100% (7/7) | 0% (0/1) |
| google_community | 100% (26/26) | 100% (26/26) | 100% (18/18) |
| google_sheet | 100% (45/45) | 96% (43/45) | 100% (11/11) |
| play_store | 100% (17/17) | 88% (15/17) | 100% (7/7) |

## Result

Below target:
- Stage C precision 64.3% is below 80%

LLM usage this run: 34 API requests, 0 cache hits, 163,520 input (99,680 read from the prompt cache) + 27,408 output tokens, $0.4248.
