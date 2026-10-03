"""The gold-set gate that runs before a new prompt or model is accepted."""

from __future__ import annotations

import json

import pytest

from discovery.eval.regression import (
    Baseline,
    accept_baseline,
    acceptance_problems,
    compare_metrics,
    current_prompts,
    load_baseline,
    parse_report,
)

RELEVANCE = """
# Relevance evaluation
Prompt `relevance_v5`, model `claude-sonnet-5-5`.
- Stage A recall: 100% (95/95) (target ≥ 95%, met)
- Stages A+B recall: 96% (91/95) (target ≥ 90%, met)
- Stage C precision (vague memory): 87% (33/38) (target ≥ 80%, met)
- Stage C recall (vague memory, items that reached the classifier): 89% (33/37) (target ≥ 75%, met)
"""

EXTRACTION = """
# Extraction evaluation
Prompt `extraction_v2`, small model `claude-sonnet-5-5`, large model `claude-opus-5-5`.
- Quote grounding rate: 100.0% (95/95) (target ≥ 98%, met)
- Primary category accuracy (top-1): 80.0% (76/95) (target ≥ 70%, met)
- Primary category accuracy (top-2): 94.7% (90/95) (target ≥ 85%, met)
- not_stated correctness: 91.6% (87/95) (target ≥ 90%, met)
"""


def test_checked_in_baseline_matches_the_code():
    baseline = load_baseline()
    assert (
        acceptance_problems(
            baseline, small_model="claude-sonnet-5-5", large_model="claude-opus-5-5"
        )
        == []
    )
    assert current_prompts() == {"relevance": "relevance_v5", "extraction": "extraction_v2"}


def test_a_new_prompt_version_is_refused_until_accepted():
    baseline = load_baseline()
    drifted = Baseline(
        prompts={"relevance": "relevance_v4", "extraction": baseline.prompts["extraction"]},
        models=baseline.models,
        metrics=baseline.metrics,
        accepted_at=baseline.accepted_at,
    )
    problems = acceptance_problems(
        drifted, small_model="claude-sonnet-5-5", large_model="claude-opus-5-5"
    )
    assert any("relevance_v5" in problem for problem in problems)


def test_a_metric_drop_is_not_accepted(tmp_path):
    path = tmp_path / "baseline.json"
    saved = accept_baseline(
        path,
        relevance_report=RELEVANCE,
        extraction_report=EXTRACTION,
        small_model="claude-sonnet-5-5",
        large_model="claude-opus-5-5",
    )
    assert saved.prompts["relevance"] == "relevance_v5"
    worse = RELEVANCE.replace(
        "Stage C precision (vague memory): 87%",
        "Stage C precision (vague memory): 70%",
    )
    with pytest.raises(ValueError, match="regressed"):
        accept_baseline(
            path,
            relevance_report=worse,
            extraction_report=EXTRACTION,
            small_model="claude-sonnet-5-5",
            large_model="claude-opus-5-5",
        )
    stored = json.loads(path.read_text(encoding="utf-8"))["metrics"]["stage_c_precision"]
    assert stored == pytest.approx(0.87)


def test_parse_report_reads_the_target_lines():
    metrics = parse_report(RELEVANCE + EXTRACTION)
    problems = compare_metrics(metrics, load_baseline())
    assert problems == []
