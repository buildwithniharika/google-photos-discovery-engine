"""Gold-set regression gate (P8.8).

A new prompt or model is accepted only when this check passes. The accepted versions
and metrics live in eval/regression_baseline.json. `discovery run-all` refuses to
start when the code or config has moved past that file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from discovery.ai.extraction import PROMPT_VERSION as EXTRACTION_VERSION
from discovery.ai.relevance import PROMPT_VERSION as RELEVANCE_VERSION
from discovery.config import PROJECT_ROOT
from discovery.eval.extraction_eval import (
    CATEGORY_TOP1_TARGET,
    CATEGORY_TOP2_TARGET,
    GROUNDING_TARGET,
    NOT_STATED_TARGET,
)
from discovery.eval.metrics import (
    STAGE_A_RECALL_TARGET,
    STAGE_AB_RECALL_TARGET,
    STAGE_C_PRECISION_TARGET,
    STAGE_C_RECALL_TARGET,
)

DEFAULT_BASELINE = PROJECT_ROOT / "eval" / "regression_baseline.json"

# A parsed percent may round to one decimal. 0.5 points is the largest slip we ignore.
TOLERANCE = 0.005

TARGETS = {
    "stage_a_recall": STAGE_A_RECALL_TARGET,
    "stage_ab_recall": STAGE_AB_RECALL_TARGET,
    "stage_c_precision": STAGE_C_PRECISION_TARGET,
    "stage_c_recall": STAGE_C_RECALL_TARGET,
    "grounding": GROUNDING_TARGET,
    "category_top1": CATEGORY_TOP1_TARGET,
    "category_top2": CATEGORY_TOP2_TARGET,
    "not_stated": NOT_STATED_TARGET,
}

_LINE = {
    "stage_a_recall": re.compile(r"Stage A recall:\s*([\d.]+)%", re.I),
    "stage_ab_recall": re.compile(r"Stages A\+B recall:\s*([\d.]+)%", re.I),
    "stage_c_precision": re.compile(r"Stage C precision.*?:\s*([\d.]+)%", re.I),
    "stage_c_recall": re.compile(r"Stage C recall.*?:\s*([\d.]+)%", re.I),
    "grounding": re.compile(r"Quote grounding rate:\s*([\d.]+)%", re.I),
    "category_top1": re.compile(r"Primary category accuracy \(top-1\):\s*([\d.]+)%", re.I),
    "category_top2": re.compile(r"Primary category accuracy \(top-2\):\s*([\d.]+)%", re.I),
    "not_stated": re.compile(r"not_stated correctness:\s*([\d.]+)%", re.I),
}
_PROMPT = re.compile(r"Prompt `([a-z0-9_]+)`")


@dataclass(frozen=True)
class Baseline:
    prompts: dict[str, str]
    models: dict[str, str]
    metrics: dict[str, float]
    accepted_at: str


def load_baseline(path: Path = DEFAULT_BASELINE) -> Baseline:
    if not path.is_file():
        raise FileNotFoundError(f"No regression baseline at {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Baseline(
        prompts=dict(raw["prompts"]),
        models=dict(raw["models"]),
        metrics={key: float(value) for key, value in raw["metrics"].items()},
        accepted_at=str(raw.get("accepted_at") or ""),
    )


def current_prompts() -> dict[str, str]:
    return {
        "relevance": f"relevance_v{RELEVANCE_VERSION}",
        "extraction": f"extraction_v{EXTRACTION_VERSION}",
    }


def acceptance_problems(baseline: Baseline, *, small_model: str, large_model: str) -> list[str]:
    """Differences that must be cleared with a gold-set run before the next pipeline."""
    problems = []
    prompts = current_prompts()
    for name, prompt_id in prompts.items():
        accepted = baseline.prompts.get(name)
        if prompt_id != accepted:
            problems.append(
                f"Prompt {prompt_id} is not the accepted {name} prompt ({accepted}). "
                "Run the gold-set evaluation, then `discovery eval-regression --accept`."
            )
    if small_model != baseline.models.get("small"):
        problems.append(
            f"llm.small_model is {small_model}, not the accepted {baseline.models.get('small')}. "
            "Run the gold-set check before using it on a real run."
        )
    if large_model != baseline.models.get("large"):
        problems.append(
            f"llm.large_model is {large_model}, not the accepted {baseline.models.get('large')}. "
            "Run the gold-set check before using it on a real run."
        )
    return problems


def parse_report(text: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for key, pattern in _LINE.items():
        match = pattern.search(text)
        if match:
            found[key] = float(match.group(1)) / 100
    return found


def parse_prompt(text: str) -> str | None:
    match = _PROMPT.search(text)
    return match.group(1) if match else None


def compare_metrics(current: dict[str, float], baseline: Baseline) -> list[str]:
    """Metrics below the absolute target, or below the accepted baseline, are regressions."""
    problems = []
    for key, target in TARGETS.items():
        value = current.get(key)
        if value is None:
            problems.append(f"Missing metric {key} in the evaluation report.")
            continue
        if value + TOLERANCE < target:
            problems.append(f"{key} {value:.1%} is below the {target:.0%} target.")
        accepted = baseline.metrics.get(key)
        if accepted is not None and value + TOLERANCE < accepted:
            problems.append(
                f"{key} {value:.1%} regressed from the accepted {accepted:.1%}. "
                "Do not accept this prompt."
            )
    return problems


def accept_baseline(
    path: Path,
    *,
    relevance_report: str,
    extraction_report: str,
    small_model: str,
    large_model: str,
) -> Baseline:
    """Write a new baseline when both reports meet the targets and do not fall below it."""
    metrics = {**parse_report(relevance_report), **parse_report(extraction_report)}
    previous = load_baseline(path) if path.is_file() else None
    reference = previous or Baseline(prompts={}, models={}, metrics={}, accepted_at="")
    problems = compare_metrics(metrics, reference)
    if previous is None:
        problems = [item for item in problems if "regressed" not in item]
    relevance_prompt = parse_prompt(relevance_report) or current_prompts()["relevance"]
    extraction_prompt = parse_prompt(extraction_report) or current_prompts()["extraction"]
    if relevance_prompt != current_prompts()["relevance"]:
        problems.append(
            f"The relevance report is {relevance_prompt}, but the code loads "
            f"{current_prompts()['relevance']}."
        )
    if extraction_prompt != current_prompts()["extraction"]:
        problems.append(
            f"The extraction report is {extraction_prompt}, but the code loads "
            f"{current_prompts()['extraction']}."
        )
    if problems:
        raise ValueError(
            "Refusing to accept this prompt.\n" + "\n".join(f"- {item}" for item in problems)
        )
    baseline = Baseline(
        prompts={"relevance": relevance_prompt, "extraction": extraction_prompt},
        models={"small": small_model, "large": large_model},
        metrics={key: round(metrics[key], 4) for key in TARGETS},
        accepted_at=datetime.now(UTC).date().isoformat(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "accepted_at": baseline.accepted_at,
                "prompts": baseline.prompts,
                "models": baseline.models,
                "metrics": baseline.metrics,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return baseline
