# Relevance evaluation

Prompt `relevance_v2` is current. The measured Stage C numbers below are from `relevance_v1` on a partial gold run (2026-10-01). Groq's free tier stopped that run, and a v2 resume has not finished.

Gold rows scored: 298 (ambiguous excluded: 2). Stage B threshold τ = -0.080.

## Targets

- Stage A recall: 100% (95/95) (target ≥ 95%, met)
- Stages A+B recall: 96% (91/95) (target ≥ 90%, met)
- Stage C precision (vague memory), prompt v1, partial: 75% (12/16) (target ≥ 80%, open)
- Stage C recall (vague memory), prompt v1, partial: 46% (12/26) (target ≥ 75%, open)

122 of 213 Stage C candidates were classified with v1 before `retry-after` 830s. The saved copy is `eval/relevance_v1_report.md`.

## Result

Stage A and Stages A+B meet the Section 17.2 targets. Stage C does not. The full corpus has not been classified. `discovery eval --llm` resumes from the LLM cache once Groq quota is available. `discovery classify` then fills the `relevance` table.
