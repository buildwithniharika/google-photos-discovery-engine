from __future__ import annotations

from discovery.config import load_config
from discovery.prep.clean import (
    analysis_text,
    clean,
    contains_phrase,
    find_pii,
    phrase_pattern,
    scrub_usernames,
)

cfg = load_config()
PREP = cfg.settings.prep
RETRIEVAL = phrase_pattern(cfg.keywords.lexicon.retrieval_intent)
MAX_CHARS = cfg.settings.llm.max_input_chars


def _clean(text: str, **kwargs: object):
    return clean(
        text,
        prep=kwargs.pop("prep", PREP),  # type: ignore[arg-type]
        retrieval=RETRIEVAL,
        max_chars=kwargs.pop("max_chars", MAX_CHARS),  # type: ignore[arg-type]
        strip_boilerplate=kwargs.pop("strip_boilerplate", False),  # type: ignore[arg-type]
    )


def test_markup_is_stripped_and_quotes_removed():
    text = (
        "Search is <b>broken</b> &amp; slow\n\n"
        "> the parent post should not survive\n\n"
        "**Still** can't find it"
    )
    result = _clean(text)
    assert "broken" in result.clean_text
    assert "<b>" not in result.clean_text
    assert "&amp;" not in result.clean_text
    assert "parent post" not in result.clean_text
    assert "Still can't find it" in result.clean_text.replace("\n", " ")


def test_short_text_with_a_retrieval_keyword_stays_eligible():
    result = _clean("can't find screenshots")
    assert result.word_count < PREP.min_words
    assert result.ai_eligible
    assert result.exclude_reason is None


def test_short_text_without_a_keyword_is_excluded_but_cleaned():
    result = _clean("Nice app")
    assert result.ai_eligible is False
    assert result.exclude_reason == "min_words"
    assert result.clean_text == "Nice app"


def test_substring_of_a_keyword_does_not_count():
    result = _clean("findings")
    assert not contains_phrase("findings", RETRIEVAL)
    assert result.exclude_reason == "min_words"


def test_emoji_only_is_excluded_and_mixed_emoji_is_described():
    only = _clean("😡😡😡")
    assert only.exclude_reason == "emoji_only"
    assert only.word_count == 0
    assert "pouting face" in only.clean_text

    hearts = _clean("❤️" * 20)
    assert hearts.exclude_reason == "emoji_only"
    assert hearts.word_count == 0
    assert "heavy black heart" in hearts.clean_text
    assert "variation selector" not in hearts.clean_text

    mixed = _clean("😡 search is broken")
    assert "pouting face" in mixed.clean_text
    assert "search is broken" in mixed.clean_text
    assert mixed.word_count == 3
    assert mixed.ai_eligible


def test_pii_is_redacted_and_versions_and_dates_are_kept():
    text = (
        "Email ada@example.com or call +1 415-555-2671 or 9876543210. "
        "Aadhaar 1234 5678 9012, id 123456789012, passport A1234567. "
        "See https://photos.google.com/share/abc?token=secret. "
        "Broke after 6.12.0.123 on 2023-10-05."
    )
    result = _clean(text)
    assert "ada@example.com" not in result.clean_text
    assert "415-555-2671" not in result.clean_text
    assert "9876543210" not in result.clean_text
    assert "1234 5678 9012" not in result.clean_text
    assert "123456789012" not in result.clean_text
    assert "A1234567" not in result.clean_text
    assert "token=secret" not in result.clean_text
    assert "[EMAIL]" in result.clean_text
    assert "[PHONE]" in result.clean_text
    assert "[ID]" in result.clean_text
    assert "[URL]" in result.clean_text
    assert "6.12.0.123" in result.clean_text
    assert "2023-10-05" in result.clean_text
    assert find_pii(result.clean_text) == []


def test_code_mixed_text_is_flagged_and_not_sent_to_ai():
    result = _clean("photo dhundh nahi pa raha search mein")
    assert result.code_mixed
    assert result.language == "code_mixed"
    assert result.exclude_reason == "code_mixed"
    assert result.ai_eligible is False


def test_short_ascii_defaults_to_english():
    result = _clean("Good photos")
    assert result.language == "en"


def test_devanagari_is_not_english():
    result = _clean("मुझे मेरी शादी की फोटो नहीं मिल रही है खोजने पर")
    assert result.language == "hi"
    assert result.exclude_reason == "language"


def test_spanish_is_detected_when_the_text_is_long_enough():
    result = _clean(
        "No puedo encontrar mis fotos de la boda del año pasado en la aplicación porque "
        "la búsqueda no muestra nada"
    )
    assert result.language == "es"
    assert result.exclude_reason == "language"


def test_page_boilerplate_is_stripped():
    text = (
        "Menu\n\nSign in now\n\nSkip to content\n\n"
        "[News](https://example.com/news)\n\n"
        "I cannot find the wedding photos from last year after the restore."
    )
    result = _clean(text, strip_boilerplate=True)
    assert "Sign in" not in result.clean_text
    assert "wedding photos" in result.clean_text


def test_long_text_is_truncated_but_keeps_a_later_keyword_sentence():
    prep = PREP.model_copy(update={"truncation_head_chars": 40})
    filler = "The gallery view looks different now. " * 30
    text = filler + "I still can't find the receipt from the clinic visit last March."
    result = _clean(text, prep=prep, max_chars=120)
    assert result.truncated
    assert len(result.clean_text) <= 120
    assert "can't find" in result.clean_text


def test_analysis_text_includes_title_and_op_followups_only():
    text = analysis_text(
        "Where is the café photo?",
        "I remember the trip but not the date.",
        ["It was somewhere in Goa."],
    )
    assert text.startswith("Where is the café photo?")
    assert "[Update from original poster] It was somewhere in Goa." in text


def test_usernames_are_removed_from_metadata():
    cleaned, removed = scrub_usernames(
        {"review_id": "abc", "author": "Ada Lovelace", "replies": [{"username": "ada"}]}
    )
    assert removed == ["Ada Lovelace", "ada"]
    assert "author" not in cleaned
    assert cleaned["replies"] == [{}]
    assert cleaned["review_id"] == "abc"
