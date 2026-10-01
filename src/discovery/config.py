"""Typed configuration loaded from config/*.yaml plus environment variables."""

from __future__ import annotations

import math
import os
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


class PlayStoreSettings(_Strict):
    app_id: str
    lang: str = "en"
    countries: list[str]
    max_reviews_per_country: int = Field(gt=0)
    requests_per_second: float = Field(gt=0)


class AppStoreSettings(_Strict):
    app_id: str
    countries: list[str]
    pages: int = Field(ge=1, le=10)
    playwright_fallback: bool = False


class GoogleSheetSettings(_Strict):
    sheet_id: str
    tabs: Literal["auto"] | list[str] = "auto"
    column_map: Literal["auto"] | dict[str, str] = "auto"
    default_platform: str = "Reddit"


class GoogleCommunitySettings(_Strict):
    list_url: str
    max_threads: int = Field(gt=0)
    seconds_between_pages: float = Field(ge=2.0)
    include_replies: bool = True


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


class RelevanceSettings(_Strict):
    semantic_margin_threshold: float
    audit_sample_rate: float = Field(ge=0, le=1)


class RateLimit(_Strict):
    requests_per_minute: float = Field(gt=0)
    tokens_per_minute: float | None = Field(default=None, gt=0)


class ModelPricing(_Strict):
    input: float = Field(ge=0, description="USD per 1M input tokens")
    output: float = Field(ge=0, description="USD per 1M output tokens")


class LLMSettings(_Strict):
    provider: Literal["groq"] = "groq"
    small_model: str
    large_model: str
    structured_output: Literal["json_schema", "json_object"] = "json_schema"
    strict_schema: bool = True
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    temperature: float = Field(default=0, ge=0, le=2)
    max_output_tokens: int = Field(default=2048, gt=0)
    escalate_below_confidence: float = Field(ge=0, le=1)
    max_input_chars: int = Field(gt=0)
    max_attempts: int = Field(default=5, ge=1)
    max_retry_wait_seconds: float = Field(default=120, gt=0)
    max_cost_usd_per_run: float | None = Field(default=None, gt=0)
    max_concurrency: int = Field(ge=1)
    rate_limits: dict[str, RateLimit]
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)
    use_batch_api_for_backfill: bool = True

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
    n_components: int
    n_neighbors: int
    metric: str


class HdbscanSettings(_Strict):
    min_cluster_size: int


class ClusteringSettings(_Strict):
    embedding_model: str
    umap: UmapSettings
    hdbscan: HdbscanSettings


class ScoringSettings(_Strict):
    low_evidence_min_items: int
    low_evidence_min_quality: float


class Settings(_Strict):
    sources: SourcesSettings
    prep: PrepSettings
    relevance: RelevanceSettings
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
    def groq_api_key(self) -> str | None:
        return os.environ.get("GROQ_API_KEY") or None


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
