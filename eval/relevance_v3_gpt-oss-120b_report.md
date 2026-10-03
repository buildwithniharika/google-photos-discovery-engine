# Relevance evaluation

Prompt `relevance_v3`, model `openai/gpt-oss-120b`, Stage B threshold τ = -0.080.
Gold rows scored: 298 (ambiguous excluded: 2).

## Targets

- Stage A recall: 100% (95/95) (target ≥ 95%, met)
- Stages A+B recall: 96% (91/95) (target ≥ 90%, met)
- Stage C precision (vague memory): 69% (18/26) (target ≥ 80%, open)
- Stage C recall (vague memory, items that reached the classifier): 51% (18/35) (target ≥ 75%, open)
- End-to-end vague recall (Stage A/B drops count as misses): 47% (18/38)

Stage C precision and recall use only items the funnel sent to the classifier.
End-to-end recall also counts vague items dropped at Stage A or Stage B.

## By source

| Source | Stage A recall | Stages A+B recall | Stage C vague recall |
| --- | --- | --- | --- |
| app_store | 100% (7/7) | 100% (7/7) | 0% (0/1) |
| google_community | 100% (26/26) | 100% (26/26) | 50% (9/18) |
| google_sheet | 100% (45/45) | 96% (43/45) | 82% (9/11) |
| play_store | 100% (17/17) | 88% (15/17) | 0% (0/5) |

## Result

Below target:
- Stage C precision 69.2% is below 80%
- Stage C recall 51.4% is below 75%
- Stage C incomplete: 2 vague items that passed Stage B have no classifier label

LLM usage this run: 33 API requests, 0 cache hits, 101,233 input + 28,778 output tokens, $0.0325.

Stage C errors:
- 431541b66edaa01a3147cb0c6e7053f6: openai/gpt-oss-120b returned invalid RelevanceBatch twice: 1 validation error for RelevanceBatch
results
  Field required [type=missing, input_value={}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
- 65c91a8bb283618bb63128ba66a40ed2: openai/gpt-oss-120b returned invalid RelevanceBatch twice: 1 validation error for RelevanceBatch
results
  Field required [type=missing, input_value={}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
- 5e67efd34e443256004a34347f3e62cd: openai/gpt-oss-120b returned invalid RelevanceBatch twice: 1 validation error for RelevanceBatch
results
  Field required [type=missing, input_value={}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
- ef47cb2269a28168e1f222a0a90ba43f: openai/gpt-oss-120b returned invalid RelevanceBatch twice: 1 validation error for RelevanceBatch
results
  Field required [type=missing, input_value={}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
- 7ee977572cc60535f73dab6401b629f3: openai/gpt-oss-120b returned invalid RelevanceBatch twice: 1 validation error for RelevanceBatch
results
  Field required [type=missing, input_value={}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
- 520f4637a33c8d99317f3f87187577a3: openai/gpt-oss-120b returned invalid RelevanceBatch twice: 1 validation error for RelevanceBatch
results
  Field required [type=missing, input_value={}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
- 4fa35a1cb957d9536da6cd10495c6eff: openai/gpt-oss-120b returned invalid RelevanceBatch twice: 1 validation error for RelevanceBatch
results
  Field required [type=missing, input_value={}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
- d59e1d5601f06f903c34346d3bc3c316: openai/gpt-oss-120b returned invalid RelevanceBatch twice: 1 validation error for RelevanceBatch
results
  Field required [type=missing, input_value={}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
