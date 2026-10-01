"""Pydantic schemas shared by every pipeline stage (architecture Sections 5.5, 7, 8, 9, 10, 13).

`RelevanceResult` and `Insight` double as the JSON contracts for Groq structured output,
so their field descriptions are written for the model as much as for developers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Enumerations (mirror config/taxonomy.yaml) ------------------------------


class SourceName(StrEnum):
    PLAY_STORE = "play_store"
    APP_STORE = "app_store"
    GOOGLE_SHEET = "google_sheet"
    GOOGLE_COMMUNITY = "google_community"


class Platform(StrEnum):
    ANDROID = "Android"
    IOS = "iOS"
    REDDIT = "Reddit"
    GOOGLE_COMMUNITY = "Google Community"
    # Only from the Google Sheet dataset, which also holds YouTube comments and forum pages.
    YOUTUBE = "YouTube"
    WEB_FORUM = "Web Forum"


class RetrievalType(StrEnum):
    NOT_RETRIEVAL = "not_retrieval"
    GENERAL_RETRIEVAL = "general_retrieval"
    VAGUE_MEMORY_RETRIEVAL = "vague_memory_retrieval"


class ExcludedTopic(StrEnum):
    STORAGE = "storage"
    PRICING = "pricing"
    BACKUP = "backup"
    SYNC = "sync"
    SHARING = "sharing"
    DELETION = "deletion"
    OTHER = "other"


class Category(StrEnum):
    CONTEXT_BASED_RETRIEVAL_FAILURE = "context_based_retrieval_failure"
    TIME_BASED_MEMORY_GAP = "time_based_memory_gap"
    SCREENSHOT_DOCUMENT_RETRIEVAL_FAILURE = "screenshot_document_retrieval_failure"
    VISUAL_DETAIL_SEARCH_FAILURE = "visual_detail_search_failure"
    PEOPLE_EVENT_ASSOCIATION_FAILURE = "people_event_association_failure"
    LOCATION_AMBIGUITY = "location_ambiguity"
    LIFE_EVENT_RETRIEVAL = "life_event_retrieval"
    SEARCH_TRUST_BREAKDOWN = "search_trust_breakdown"
    OTHER_EMERGENT = "other_emergent"


class ContentType(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    SCREENSHOT = "screenshot"
    RECEIPT_OR_BILL = "receipt_or_bill"
    PRESCRIPTION_OR_MEDICAL = "prescription_or_medical"
    ID_OR_OFFICIAL_DOCUMENT = "id_or_official_document"
    GENERAL_DOCUMENT = "general_document"
    TICKET_OR_BOOKING = "ticket_or_booking"
    RECIPE_OR_INFO_CARD = "recipe_or_info_card"
    CHAT_MEDIA = "chat_media"
    MEME_OR_FUNNY = "meme_or_funny"
    UNKNOWN = "unknown"


class CueType(StrEnum):
    SITUATION = "situation"
    PLACE_VAGUE = "place_vague"
    PERSON = "person"
    PURPOSE_TASK = "purpose_task"
    VISUAL_DETAIL = "visual_detail"
    TIME_RANGE = "time_range"
    SOURCE_APP = "source_app"
    LIFE_EVENT = "life_event"
    FEELING = "feeling"
    TRIP_OR_EVENT = "trip_or_event"
    TEXT_FRAGMENT = "text_fragment"


class ForgottenDetail(StrEnum):
    EXACT_DATE = "exact_date"
    MONTH_OR_YEAR = "month_or_year"
    LOCATION_NAME = "location_name"
    ALBUM_NAME = "album_name"
    FILE_TYPE = "file_type"
    EXACT_TEXT_IN_IMAGE = "exact_text_in_image"
    SENDER = "sender"
    MEDIA_KIND = "media_kind"
    SEARCH_KEYWORD = "search_keyword"
    PEOPLE_NAMES = "people_names"


class BreakdownPoint(StrEnum):
    QUERY_FORMULATION = "query_formulation"
    NO_RESULTS = "no_results"
    RESULTS_IRRELEVANT_OR_TOO_BROAD = "results_irrelevant_or_too_broad"
    TARGET_NOT_SURFACED = "target_not_surfaced"
    WRONG_METADATA = "wrong_metadata"
    BROWSE_FATIGUE = "browse_fatigue"
    ITEM_APPEARS_MISSING = "item_appears_missing"
    TRUST_BREAKDOWN_GAVE_UP = "trust_breakdown_gave_up"
    NOT_STATED = "not_stated"


class AttemptType(StrEnum):
    KEYWORD_SEARCH = "keyword_search"
    DATE_SEARCH = "date_search"
    LOCATION_SEARCH = "location_search"
    PEOPLE_SEARCH = "people_search"
    TEXT_OCR_SEARCH = "text_ocr_search"
    NATURAL_LANGUAGE_QUERY = "natural_language_query"
    ALBUM_BROWSE = "album_browse"
    MANUAL_SCROLL = "manual_scroll"
    EXTERNAL_APP_CHECK = "external_app_check"
    ASKED_SUPPORT = "asked_support"
    NOT_STATED = "not_stated"


class Emotion(StrEnum):
    FRUSTRATED = "frustrated"
    ANGRY = "angry"
    ANXIOUS = "anxious"
    SAD_LOSS = "sad_loss"
    CONFUSED = "confused"
    RESIGNED = "resigned"
    NEUTRAL = "neutral"


class Outcome(StrEnum):
    NOT_FOUND = "not_found"
    FOUND_WITH_DIFFICULTY = "found_with_difficulty"
    FOUND = "found"
    NOT_STATED = "not_stated"


class AreaStatus(StrEnum):
    ACTIVE = "active"
    MERGED = "merged"
    ARCHIVED = "archived"


class ScoreBand(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class OverrideTarget(StrEnum):
    ITEM = "item"
    AREA = "area"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    PUBLISHED = "published"
    SKIPPED = "skipped"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Ingestion ---------------------------------------------------------------


class RawItem(BaseModel):
    """Immutable raw payload written by a connector before any processing (Section 5.5)."""

    model_config = ConfigDict(frozen=True)

    raw_id: str = Field(description="'{source}:{source-native id}', unique across sources")
    source_name: SourceName
    platform: Platform
    source_url: str
    fetched_at: datetime = Field(default_factory=_utcnow)
    run_id: str
    payload: dict[str, Any]


class Item(BaseModel):
    """Canonical, deduplicated unit of analysis (Section 13.2 `items`)."""

    item_id: str
    primary_source_name: SourceName
    platform: Platform
    source_url: str
    title: str | None = None
    original_text: str
    clean_text: str | None = None
    language: str | None = None
    date: datetime | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    engagement: dict[str, int] = Field(default_factory=dict)
    author_hash: str | None = None
    content_hash: str | None = None
    is_spam: bool = False
    similar_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def analysis_text(self) -> str:
        """Title + body (+ the original poster's follow-ups for threads): the text the AI
        stages analyze, before cleaning. `original_text` itself is never modified."""
        parts: list[str] = []
        if self.title and not self.original_text.startswith(self.title.strip()):
            parts.append(self.title.strip())
        parts.append(self.original_text)
        parts += [
            f"[Update from original poster] {t}" for t in self.metadata.get("op_followups", [])
        ]
        return "\n\n".join(p for p in parts if p)


# --- LLM outputs -------------------------------------------------------------


class RelevanceResult(BaseModel):
    """Stage C relevance classification (Section 7)."""

    is_google_photos: bool = Field(description="Is the text about Google Photos?")
    is_retrieval: bool = Field(description="Is the user trying to find or get back a photo/video?")
    retrieval_type: RetrievalType
    vague_memory_relevance: float = Field(
        ge=0,
        le=1,
        description="How strongly this matches 'remembers it exists but can't describe it "
        "precisely enough to find it'. 0 = not at all, 1 = textbook case.",
    )
    excluded_topic: ExcludedTopic | None = Field(
        description="Out-of-scope topic the text is mainly about, or null"
    )
    excluded_topic_blocks_retrieval: bool | None = Field(
        description="If excluded_topic is set: does it block finding a remembered item? "
        "Null when excluded_topic is null."
    )
    rationale: str = Field(description="One sentence explaining the decision")
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _consistent(self) -> RelevanceResult:
        if self.excluded_topic is None:
            self.excluded_topic_blocks_retrieval = None
        if not self.is_retrieval and self.retrieval_type != RetrievalType.NOT_RETRIEVAL:
            raise ValueError("retrieval_type must be 'not_retrieval' when is_retrieval is false")
        return self


class RememberedCue(BaseModel):
    cue: str = Field(description="The user's own words for what they remember")
    cue_type: CueType


class SearchAttempt(BaseModel):
    attempt: str = Field(description="What the user searched for or tried, in their words")
    attempt_type: AttemptType


class Insight(BaseModel):
    """Per-item insight extraction (Section 8.1). Only stated facts; never guessed."""

    trying_to_find: str
    content_type: ContentType
    remembered_cues: list[RememberedCue]
    forgotten_details: list[ForgottenDetail]
    search_attempts: list[SearchAttempt]
    breakdown_point: BreakdownPoint
    outcome: Outcome
    emotion: Emotion
    frustration_intensity: int = Field(ge=1, le=5)
    primary_category: Category
    secondary_categories: list[Category]
    high_stakes: bool = Field(
        description="True for medical, financial, legal/ID, or irreplaceable memories"
    )
    evidence_quote: str | None = Field(
        description="Verbatim substring of the user's text that best evidences the problem"
    )
    evidence_strength: int = Field(ge=1, le=5)
    useful_for_discovery: bool
    user_reported_issue: str = Field(description="The issue in the user's terms, one sentence")
    problem_statement: str = Field(description="Normalized one-line retrieval problem")
    confidence: float = Field(ge=0, le=1)


# --- Synthesis, scoring, curation --------------------------------------------


class Cluster(BaseModel):
    cluster_id: str
    run_id: str
    area_id: str | None = None
    label: str
    summary: str
    mapped_category: Category
    size: int = Field(ge=0)


class OpportunityArea(BaseModel):
    area_id: str
    run_id: str
    name: str
    category: Category
    is_emergent: bool = False
    problem_summary: str
    aggregates: dict[str, Any] = Field(default_factory=dict)
    research_questions: list[str] = Field(default_factory=list)
    status: AreaStatus = AreaStatus.ACTIVE


Score = Annotated[float, Field(ge=1, le=5)]


class OpportunityScore(BaseModel):
    area_id: str
    run_id: str
    frequency: Score
    severity: Score
    strategic_fit: Score
    evidence_quality: Score
    product_leverage: Score
    research_value: Score
    composite: Score
    band: ScoreBand
    inputs: dict[str, Any] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    low_evidence_flag: bool = False


class PMOverride(BaseModel):
    override_id: str | None = None
    target_type: OverrideTarget
    target_id: str
    field: str
    ai_value: Any = None
    override_value: Any
    note: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
