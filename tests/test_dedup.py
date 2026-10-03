from __future__ import annotations

from datetime import UTC, datetime, timedelta

from discovery.prep.clean import normalize_for_dedup, phrase_pattern
from discovery.prep.dedup import DedupItem, deduplicate, jaccard, shingles

RETRIEVAL = phrase_pattern(
    ["find", "finding", "can't find", "search", "searching", "lost", "missing"]
)

LONG = (
    "i remember a photo of the small cafe from our trip but cannot recall the name "
    "or the month we visited and search failed"
)


def _item(
    item_id: str,
    text: str,
    *,
    author: str | None = None,
    source: str = "play_store",
    date: datetime | None = None,
    review_id: str | None = None,
    eligible: bool = True,
) -> DedupItem:
    dedup_text = normalize_for_dedup(text)
    words = tuple(dedup_text.split())
    return DedupItem(
        item_id=item_id,
        source=source,
        author_hash=author,
        clean_text=text,
        dedup_text=dedup_text,
        words=words,
        date=date,
        review_id=review_id,
        has_retrieval=bool(RETRIEVAL.search(dedup_text)),
        ai_eligible=eligible,
        exclude_reason=None if eligible else "min_words",
    )


def _run(items: list[DedupItem], **kwargs: object):
    return deduplicate(
        items,
        short_words=kwargs.get("short_words", 12),  # type: ignore[arg-type]
        jaccard_threshold=kwargs.get("jaccard_threshold", 0.85),  # type: ignore[arg-type]
        shingle_size=5,
        spam_min_authors=kwargs.get("spam_min_authors", 5),  # type: ignore[arg-type]
    )


def test_identical_short_complaints_from_different_authors_stay_separate():
    items = [_item(f"s{i}", "Search doesn't work", author=f"author-{i}") for i in range(20)]
    result = _run(items)
    assert result.dropped == {}
    assert all(it.similar_count == 19 for it in items)
    assert result.stats["short_kept_separate"] == 20
    assert all(not it.is_spam for it in items)


def test_same_author_and_source_short_text_is_merged():
    items = [
        _item("a", "ok thanks", author="same"),
        _item("b", "ok thanks", author="same"),
        _item("c", "ok thanks", author="other"),
    ]
    result = _run(items)
    assert set(result.dropped) == {"b"} or set(result.dropped) == {"a"}
    assert "c" not in result.dropped
    survivors = [it for it in items if it.item_id not in result.dropped]
    assert len(survivors) == 2
    assert all(it.similar_count == 1 for it in survivors)


def test_anonymous_short_text_is_not_merged():
    items = [_item("a", "Nice app", author=None), _item("b", "Nice app", author=None)]
    result = _run(items)
    assert result.dropped == {}


def test_long_exact_duplicates_merge_across_sources_and_keep_the_earliest_date():
    early = datetime(2024, 1, 1, tzinfo=UTC)
    late = early + timedelta(days=10)
    items = [
        _item("play", LONG, author="p", source="play_store", date=late),
        _item("sheet", LONG, author="s", source="google_sheet", date=early),
    ]
    result = _run(items)
    assert len(result.dropped) == 1
    canonical_id = next(iter(result.dropped.values()))
    canonical = next(it for it in items if it.item_id == canonical_id)
    kept = result.earliest_date.get(canonical_id, canonical.date)
    assert result.stats["cross_source_merged"] == 1
    assert result.merges[0].reason == "exact"
    assert kept == early


def test_near_duplicates_keep_the_longest_text():
    longer = LONG + " completely"
    assert jaccard(shingles(LONG.split(), 5), shingles(longer.split(), 5)) >= 0.85
    early = datetime(2024, 1, 1, tzinfo=UTC)
    items = [
        _item("short", LONG, date=early),
        _item("long", longer, date=early + timedelta(days=3)),
    ]
    result = _run(items)
    assert result.dropped == {"short": "long"}
    assert result.merges[0].reason == "near"
    assert result.earliest_date["long"] == early


def test_dissimilar_long_texts_are_not_merged():
    other = (
        "backup was switched off last year and every picture from the holiday is gone "
        "from the library which is a storage problem rather than search"
    )
    items = [_item("a", LONG), _item("b", other)]
    other_shingles = shingles(normalize_for_dedup(other).split(), 5)
    assert jaccard(shingles(LONG.split(), 5), other_shingles) < 0.85
    result = _run(items)
    assert result.dropped == {}


def test_matching_review_id_merges_across_sources_even_when_wording_differs():
    sheet = "Search is useless on the first try and sometimes never returns a photo at all today"
    items = [
        _item("play", LONG, source="play_store", review_id="play_store:abc"),
        _item("sheet", sheet, source="google_sheet", review_id="play_store:abc"),
    ]
    result = _run(items)
    assert len(result.dropped) == 1
    assert result.merges[0].reason == "review_id"
    assert result.stats["cross_source_merged"] == 1
    assert result.dropped["sheet"] == "play"  # the Play text is longer


def test_heart_only_reviews_from_different_authors_are_not_merged():
    from discovery.config import load_config
    from discovery.prep.clean import clean

    prep = load_config().settings.prep
    hearts = "❤️" * 12
    items = []
    for i in range(6):
        result = clean(hearts, prep=prep, retrieval=RETRIEVAL, max_chars=6000)
        assert result.word_count < 12
        items.append(
            DedupItem(
                item_id=f"h{i}",
                source="play_store",
                author_hash=f"author-{i}",
                clean_text=result.clean_text,
                dedup_text=result.dedup_text,
                words=tuple(result.dedup_text.split()),
                date=None,
                review_id=None,
                has_retrieval=False,
                ai_eligible=result.ai_eligible,
                exclude_reason=result.exclude_reason,
            )
        )
    assert _run(items).dropped == {}


def test_exact_copy_merged_onward_by_near_dup_points_at_the_final_item():
    longer = LONG + " completely"
    early = datetime(2024, 1, 1, tzinfo=UTC)
    items = [
        _item("copy", LONG, date=early),
        _item("middle", LONG, date=early + timedelta(days=2)),
        _item("kept", longer, date=early + timedelta(days=3)),
    ]
    result = _run(items)
    assert result.dropped == {"copy": "kept", "middle": "kept"}
    assert {m.canonical_id for m in result.merges} == {"kept"}
    assert result.earliest_date["kept"] == early


def test_a_near_duplicate_chain_does_not_absorb_the_far_end():
    # A two-word shift stays above 0.85. Two shifts fall below it, so C must stay.
    a = " ".join(f"w{i:02d}" for i in range(40)) + " extra extra"
    b = " ".join(f"w{i:02d}" for i in range(2, 42))
    c = " ".join(f"w{i:02d}" for i in range(4, 44))
    a_words, b_words, c_words = a.split(), b.split(), c.split()
    assert jaccard(shingles(a_words, 5), shingles(b_words, 5)) >= 0.85
    assert jaccard(shingles(b_words, 5), shingles(c_words, 5)) >= 0.85
    assert jaccard(shingles(a_words, 5), shingles(c_words, 5)) < 0.85
    result = _run([_item("a", a), _item("b", b), _item("c", c)])
    assert result.dropped.get("b") == "a"
    assert "c" not in result.dropped


def test_templated_text_from_many_authors_is_spam_but_retrieval_text_is_not():
    promo = "Download this casino now please friends today"
    spam = [_item(f"p{i}", promo, author=f"a{i}") for i in range(5)]
    result = _run(spam)
    assert result.dropped == {}
    assert all(it.is_spam and not it.ai_eligible and it.exclude_reason == "spam" for it in spam)

    complaints = [_item(f"c{i}", "Search doesn't work", author=f"a{i}") for i in range(5)]
    _run(complaints)
    assert all(not it.is_spam for it in complaints)
