"""Stage A keyword prefilter (architecture Section 7).

Keep an item when it contains a retrieval-intent phrase, or a search-feature phrase
together with a rating at or below the configured maximum. Precision is not the goal;
recall on retrieval items is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from discovery.config import Keywords
from discovery.prep.clean import contains_phrase, phrase_pattern

NO_SIGNAL = "no_signal"
RETRIEVAL_INTENT = "retrieval_intent"
SEARCH_FEATURE_LOW_RATING = "search_feature_low_rating"


@dataclass(frozen=True)
class StageA:
    intent: re.Pattern[str]
    features: re.Pattern[str]
    negative_rating_max: int

    @classmethod
    def from_keywords(cls, keywords: Keywords) -> StageA:
        return cls(
            intent=phrase_pattern(keywords.lexicon.retrieval_intent),
            features=phrase_pattern(keywords.lexicon.search_features),
            negative_rating_max=keywords.rules.negative_rating_max,
        )

    def decide(self, text: str, rating: int | None) -> str:
        if contains_phrase(text or "", self.intent):
            return RETRIEVAL_INTENT
        low_rating = rating is not None and rating <= self.negative_rating_max
        if low_rating and contains_phrase(text or "", self.features):
            return SEARCH_FEATURE_LOW_RATING
        return NO_SIGNAL

    def keep(self, text: str, rating: int | None) -> bool:
        return self.decide(text, rating) != NO_SIGNAL
