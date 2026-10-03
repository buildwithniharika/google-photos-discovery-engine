# Relevance evaluation

Prompt `relevance_v1`, model `openai/gpt-oss-20b`, Stage B threshold τ = -0.080.
Gold rows scored: 298 (ambiguous excluded: 2).

## Targets

- Stage A recall: 100% (95/95) (target ≥ 95%, met)
- Stages A+B recall: 96% (91/95) (target ≥ 90%, met)
- Stage C precision (vague memory): 75% (12/16) (target ≥ 80%, open)
- Stage C recall (vague memory, items that reached the classifier): 46% (12/26) (target ≥ 75%, open)
- End-to-end vague recall (Stage A/B drops count as misses): 32% (12/38)

Stage C precision and recall use only items the funnel sent to the classifier.
End-to-end recall also counts vague items dropped at Stage A or Stage B.

## By source

| Source | Stage A recall | Stages A+B recall | Stage C vague recall |
| --- | --- | --- | --- |
| app_store | 100% (7/7) | 100% (7/7) | 100% (1/1) |
| google_community | 100% (26/26) | 100% (26/26) | 50% (5/10) |
| google_sheet | 100% (45/45) | 96% (43/45) | 50% (4/8) |
| play_store | 100% (17/17) | 88% (15/17) | 29% (2/7) |

## Result

Below target:
- Stage C precision 75.0% is below 80%
- Stage C recall 46.2% is below 75%
