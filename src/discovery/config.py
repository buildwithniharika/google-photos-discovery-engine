"""Typed configuration loaded from config/*.yaml plus environment variables."""

from __future__ import annotations

import math
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_PROMPTS_DIR = PROJECT_ROOT / "prompts"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- settings.yaml -----------------------------------------------------------


class HttpSettings(_Strict):
    user_agent: str
    timeout_seconds: float = Field(default=30, gt=0)
    max_attempts: int = Field(default=4, ge=1)
    min_interval_seconds: float = Field(default=1.0, ge=0)
    jitter_seconds: float = Field(default=0.5, ge=0)


class IngestSettings(_Strict):
    raw_dir: str = "data/raw"
    since_overlap_days: int = Field(default=3, ge=0)
    robots_exceptions: dict[str, str] = Field(default_factory=dict)

    @property
    def raw_path(self) -> Path:
        p = Path(self.raw_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p


class PlayStoreSettings(_Strict):
    app_id: str
    lang: str = "en"
    countries: list[str]
    max_reviews_per_country: int = Field(gt=0)
    requests_per_second: float = Field(gt=0)
    batch_size: int = Field(default=200, ge=1, le=200)


class AppStoreSettings(_Strict):
    app_id: str
    app_slug: str = "app"
    countries: list[str]
    method: Literal["web", "rss"] = "web"
    pages: int = Field(default=10, ge=1, le=10)


class GoogleSheetSettings(_Strict):
    sheet_id: str
    tabs: Literal["auto"] | list[str] = "auto"
    column_map: Literal["auto"] | dict[str, str] = "auto"
    default_platform: str = "Reddit"
    dayfirst: bool = True


class GoogleCommunitySettings(_Strict):
    list_url: str
    list_page_size: int = Field(default=200, ge=20)
    max_threads: int = Field(gt=0)
    max_threads_per_run: int = Field(gt=0)
    seconds_between_pages: float = Field(ge=2.0)
    include_replies: bool = True
    max_replies: int = Field(default=10, ge=0)
    selectors: dict[str, str] = Field(default_factory=dict)


class SourcesSettings(_Strict):
    play_store: PlayStoreSettings
    app_store: AppStoreSettings
    google_sheet: GoogleSheetSettings
    google_community: GoogleCommunitySettings


class PrepSettings(_Strict):
    languages: list[str]
    min_words: int = Field(ge=1)
    short_text_words: int = Field(ge=1)
    near_dup_jaccard: float = Field(gt=0, le=1)
    near_dup_shingle_size: int = Field(default=5, ge=2, le=8)
    spam_min_authors: int = Field(default=5, ge=2)
    # CLN-04: trust a detector only past this length and confidence.
    # Shorter ASCII text defaults to English.
    language_min_words: int = Field(default=6, ge=1)
    language_confidence: float = Field(default=0.85, gt=0, le=1)
    # CLN-10: when clean_text exceeds the LLM input cap, keep this many leading characters
    # plus any later sentences that contain a retrieval keyword.
    truncation_head_chars: int = Field(default=1500, ge=200)


class RelevanceSettings(_Strict):
    semantic_margin_threshold: float
    audit_sample_rate: float = Field(ge=0, le=1)
    # Stage C sends several items per call so the system prompt is paid once per batch.
    batch_max_items: int = Field(default=8, ge=1)
    batch_max_chars: int = Field(default=8000, gt=0)
    batch_max_output_tokens: int = Field(default=4096, gt=0)


class ExtractionSettings(_Strict):
    # Short items share a call (P4.2); an item longer than long_text_chars gets its own call.
    batch_max_items: int = Field(default=6, ge=1)
    batch_max_chars: int = Field(default=6000, gt=0)
    batch_max_output_tokens: int = Field(default=5000, gt=0)
    single_max_output_tokens: int = Field(default=1500, gt=0)
    # P4.4: longer texts go straight to llm.large_model. null keeps them on the small model.
    long_text_chars: int | None = Field(default=1500, gt=0)
    # P4.4: re-run on llm.large_model below llm.escalate_below_confidence. false disables.
    escalate_low_confidence: bool = True
    review_below_confidence: float = Field(default=0.5, ge=0, le=1)
    quote_match_threshold: float = Field(default=95, gt=0, le=100)
    # P4.8: with llm.use_batch_api_for_backfill, use the Message Batches API from this many
    # pending items up; smaller incremental runs use normal calls.
    batch_api_min_items: int = Field(default=100, ge=1)
    batch_poll_seconds: float = Field(default=30, gt=0)
    batch_max_wait_minutes: float = Field(default=180, gt=0)
    # Cost estimate only (--estimate): expected output tokens per extracted item.
    est_output_tokens_per_item: int = Field(default=450, gt=0)


class RateLimit(_Strict):
    requests_per_minute: float = Field(gt=0)
    tokens_per_minute: float | None = Field(default=None, gt=0)


class ModelPricing(_Strict):
    input: float = Field(ge=0, description="USD per 1M input tokens")
    output: float = Field(ge=0, description="USD per 1M output tokens")
    cache_write: float | None = Field(
        default=None, ge=0, description="USD per 1M prompt-cache write tokens (default: input)"
    )
    cache_read: float | None = Field(
        default=None, ge=0, description="USD per 1M prompt-cache read tokens (default: input)"
    )


LLM_API_KEY_ENV = {"groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


class LLMSettings(_Strict):
    provider: Literal["groq", "anthropic"] = "anthropic"
    small_model: str
    large_model: str
    # Groq only: Anthropic always uses JSON Schema structured output.
    structured_output: Literal["json_schema", "json_object"] = "json_schema"
    strict_schema: bool = True
    # Groq: reasoning_effort for gpt-oss models. Anthropic: output_config.effort.
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    # Groq only: Anthropic models with adaptive thinking reject sampling parameters.
    temperature: float = Field(default=0, ge=0, le=2)
    max_output_tokens: int = Field(default=2048, gt=0)
    escalate_below_confidence: float = Field(ge=0, le=1)
    max_input_chars: int = Field(gt=0)
    max_attempts: int = Field(default=5, ge=1)
    max_retry_wait_seconds: float = Field(default=120, gt=0)
    max_cost_usd_per_run: float | None = Field(default=None, gt=0)
    # Hard cap on cumulative LLM spend recorded in pipeline_runs since `budget_since`.
    project_budget_usd: float | None = Field(default=None, gt=0)
    budget_since: datetime | None = None
    # Spend is recorded when a run finishes, so two LLM runs at once could each spend up to
    # the limits. Refuse to start while another LLM stage started this many hours ago is
    # still running (null disables the check).
    block_concurrent_runs_hours: float | None = Field(default=6.0, gt=0)
    max_concurrency: int = Field(ge=1)
    rate_limits: dict[str, RateLimit]
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)
    use_batch_api_for_backfill: bool = True
    # Message Batches API price as a share of the normal price (Anthropic: 50% off).
    batch_price_factor: float = Field(default=0.5, gt=0, le=1)

    @field_validator("rate_limits")
    @classmethod
    def _needs_default(cls, v: dict[str, RateLimit]) -> dict[str, RateLimit]:
        if "default" not in v:
            raise ValueError("llm.rate_limits must include a 'default' entry")
        return v

    def rate_limit_for(self, model: str) -> RateLimit:
        return self.rate_limits.get(model, self.rate_limits["default"])


class DatabaseSettings(_Strict):
    url_env: str = "DATABASE_URL"
    default_url: str = "sqlite:///data/discovery.db"


class UmapSettings(_Strict):
    n_components: int = Field(ge=2)
    n_neighbors: int = Field(ge=2)
    metric: str
    min_dist: float = Field(default=0.0, ge=0)


class HdbscanSettings(_Strict):
    min_cluster_size: int = Field(ge=2)
    min_samples: int | None = Field(default=None, ge=1)
    cluster_selection_method: Literal["eom", "leaf"] = "eom"


class ClusteringSettings(_Strict):
    embedding_model: str
    umap: UmapSettings
    hdbscan: HdbscanSettings
    random_state: int = 42
    # Items the extraction marked useful_for_discovery=false ("all my photos are gone", no
    # cue) only form generic clusters; with this on they stay out of clustering.
    only_useful_for_discovery: bool = True
    # P5.4: same-category clusters whose centroids are at least this close share an area.
    area_merge_cosine: float = Field(default=0.93, gt=0, le=1)
    # P5.9: a cluster this close to a cluster of the previous run keeps that cluster's area.
    match_cosine: float = Field(default=0.9, gt=0, le=1)
    # Below this many items an area is reported as "emerging".
    min_area_items: int = Field(default=10, ge=1)
    # "small" / "large" pick llm.small_model / llm.large_model; anything else is a model id.
    label_model: str = "large"
    synthesis_model: str = "large"
    label_items: int = Field(default=20, ge=3)
    synthesis_items: int = Field(default=25, ge=5)
    min_quotes: int = Field(default=5, ge=1)
    max_quotes: int = Field(default=8, ge=1)
    quote_dedup_ratio: float = Field(default=85, gt=0, le=100)
    label_max_output_tokens: int = Field(default=700, gt=0)
    synthesis_max_output_tokens: int = Field(default=2500, gt=0)
    # Label and synthesis calls go through the Message Batches API (50% off) when the
    # provider is anthropic and at least this many calls are pending.
    batch_api_min_calls: int = Field(default=5, ge=1)
    batch_poll_seconds: float = Field(default=20, gt=0)
    batch_max_wait_minutes: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _quote_range(self) -> ClusteringSettings:
        if self.min_quotes > self.max_quotes:
            raise ValueError("clustering.min_quotes must not exceed clustering.max_quotes")
        return self

    def resolve_model(self, choice: str, llm: LLMSettings) -> str:
        return {"small": llm.small_model, "large": llm.large_model}.get(choice, choice)


class ScoringSettings(_Strict):
    low_evidence_min_items: int = Field(default=10, ge=1)
    low_evidence_min_quality: float = Field(default=2.5, ge=1, le=5)
    # SCOPE-26 / SC-13. Null keeps every dated item. Set days to drop older feedback.
    analysis_window_days: int | None = Field(default=None, ge=1)
    # SC-08: one calendar day can contribute at most this share of an area's vague items.
    day_contribution_cap: float = Field(default=0.10, gt=0, le=1)
    # SC-05: per-item cap before the log, so one viral thread cannot dominate.
    engagement_cap: int = Field(default=100, ge=1)
    # Multiplier added on top of the blended share is at most this (0.15 = +15%).
    engagement_alpha: float = Field(default=0.15, ge=0, le=1)
    # Blend of raw vague share and source-balanced share. 0.5 weights them equally.
    frequency_raw_weight: float = Field(default=0.5, ge=0, le=1)
    # SC-02: quantile mapping needs at least this many areas; otherwise fixed thresholds.
    quantile_min_areas: int = Field(default=5, ge=2)
    # Share cutoffs for the fixed scale: below the first is 1, below the second is 2, ...
    frequency_fixed_thresholds: list[float] = Field(
        default_factory=lambda: [0.02, 0.05, 0.12, 0.25]
    )
    rubric_model: str = "large"  # small | large | a model id
    rubric_items: int = Field(default=5, ge=1)
    rubric_max_output_tokens: int = Field(default=400, gt=0)
    batch_api_min_calls: int = Field(default=5, ge=1)
    batch_poll_seconds: float = Field(default=20, gt=0)
    batch_max_wait_minutes: float = Field(default=30, gt=0)

    @field_validator("frequency_fixed_thresholds")
    @classmethod
    def _four_cutoffs(cls, value: list[float]) -> list[float]:
        ordered = list(value) == sorted(set(value))
        if len(value) != 4 or any(not 0 < x < 1 for x in value) or not ordered:
            raise ValueError(
                "scoring.frequency_fixed_thresholds must be 4 increasing values between 0 and 1"
            )
        return value

    def resolve_model(self, llm: LLMSettings) -> str:
        return {"small": llm.small_model, "large": llm.large_model}.get(
            self.rubric_model, self.rubric_model
        )


class Settings(_Strict):
    http: HttpSettings
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    sources: SourcesSettings
    prep: PrepSettings
    relevance: RelevanceSettings
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)
    llm: LLMSettings
    database: DatabaseSettings
    clustering: ClusteringSettings
    scoring: ScoringSettings


# --- scoring_weights.yaml ----------------------------------------------------


class ScoringWeights(_Strict):
    frequency: float = Field(ge=0, le=1)
    severity: float = Field(ge=0, le=1)
    strategic_fit: float = Field(ge=0, le=1)
    evidence_quality: float = Field(ge=0, le=1)
    product_leverage: float = Field(ge=0, le=1)
    research_value: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> ScoringWeights:
        total = sum(self.model_dump().values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"scoring weights must sum to 1.0 (got {total:.4f})")
        return self


class ScoreBands(_Strict):
    high: float
    medium: float

    @model_validator(mode="after")
    def _ordered(self) -> ScoreBands:
        if not self.high > self.medium:
            raise ValueError("bands.high must be greater than bands.medium")
        return self


class ScoringConfig(_Strict):
    weights: ScoringWeights
    bands: ScoreBands


# --- taxonomy.yaml / keywords.yaml -------------------------------------------


class Taxonomy(_Strict):
    categories: dict[str, str]
    content_types: list[str]
    cue_types: list[str]
    forgotten_details: list[str]
    breakdown_points: list[str]
    attempt_types: list[str]
    emotions: list[str]
    outcomes: list[str]
    retrieval_types: list[str]
    excluded_topics: list[str]


class KeywordRules(_Strict):
    negative_rating_max: int = Field(ge=1, le=5)


class Lexicon(_Strict):
    retrieval_intent: list[str]
    vague_memory_cues: list[str]
    content_types: list[str]
    search_features: list[str]


class SeedExemplars(_Strict):
    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class Keywords(_Strict):
    rules: KeywordRules
    lexicon: Lexicon
    seed_exemplars: SeedExemplars


# --- top-level ---------------------------------------------------------------


class AppConfig(BaseModel):
    settings: Settings
    scoring: ScoringConfig
    taxonomy: Taxonomy
    keywords: Keywords
    config_dir: Path
    prompts_dir: Path

    @property
    def database_url(self) -> str:
        return resolve_database_url(self.settings.database)

    @property
    def llm_api_key_env(self) -> str:
        return LLM_API_KEY_ENV[self.settings.llm.provider]

    @property
    def llm_api_key(self) -> str | None:
        return os.environ.get(self.llm_api_key_env, "").strip() or None

    @property
    def author_hash_salt(self) -> str | None:
        return os.environ.get("AUTHOR_HASH_SALT", "").strip() or None


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data


def resolve_database_url(db: DatabaseSettings) -> str:
    """DATABASE_URL from the environment, else local SQLite under the project root."""
    url = os.environ.get(db.url_env, "").strip()
    if not url:
        url = db.default_url
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith(prefix + "/") and ":memory:" not in url:
        url = prefix + str(PROJECT_ROOT / url[len(prefix) :])
    return url


def load_config(config_dir: Path | None = None, prompts_dir: Path | None = None) -> AppConfig:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    config_dir = Path(config_dir or os.environ.get("DISCOVERY_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    prompts_dir = Path(prompts_dir or DEFAULT_PROMPTS_DIR)
    return AppConfig(
        settings=Settings.model_validate(_read_yaml(config_dir / "settings.yaml")),
        scoring=ScoringConfig.model_validate(_read_yaml(config_dir / "scoring_weights.yaml")),
        taxonomy=Taxonomy.model_validate(_read_yaml(config_dir / "taxonomy.yaml")),
        keywords=Keywords.model_validate(_read_yaml(config_dir / "keywords.yaml")),
        config_dir=config_dir,
        prompts_dir=prompts_dir,
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()
