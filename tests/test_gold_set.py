from __future__ import annotations

from datetime import UTC, datetime

from discovery.eval.gold_set import (
    LABEL_FIELDS,
    agreement,
    draw_sample,
    exemplar_overlap,
    read_sheet,
    write_sheet,
)
from discovery.models.orm import ItemRow


def _add(session, item_id: str, source: str, text: str, rating: int | None = None) -> None:
    session.add(
        ItemRow(
            item_id=item_id,
            primary_source_name=source,
            platform="Android",
            source_url=f"https://example.com/{item_id}",
            original_text=text,
            clean_text=text,
            language="en",
            rating=rating,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            metadata_={"prep": {"ai_eligible": True}},
        )
    )


def test_sample_is_stratified_deterministic_and_marks_an_overlap(cfg, session_factory, tmp_path):
    relevant = "I can't find that photo from the trip last year"
    borderline = "Search is useless and never returns what I typed"
    irrelevant = "Love this app it is very fast and the editor is beautiful"
    with session_factory() as session:
        for source in ("play_store", "app_store", "google_sheet", "google_community"):
            for i in range(4):
                _add(session, f"{source}-r{i}", source, relevant)
                _add(session, f"{source}-b{i}", source, borderline, rating=2)
                _add(session, f"{source}-i{i}", source, irrelevant, rating=5)
        session.commit()

    first = draw_sample(session_factory, cfg, size=24, per_stratum=8, seed=3, overlap=6)
    second = draw_sample(session_factory, cfg, size=24, per_stratum=8, seed=3, overlap=6)
    assert [it.item_id for it in first.items] == [it.item_id for it in second.items]
    assert set(first.by_stratum) == {"likely_relevant", "borderline", "likely_irrelevant"}
    assert first.by_stratum["likely_relevant"] == 8
    assert set(first.by_source) == {
        "play_store",
        "app_store",
        "google_sheet",
        "google_community",
    }
    assert first.overlap == 6

    path = tmp_path / "gold.csv"
    write_sheet(path, first)
    rows = read_sheet(path)
    assert set(LABEL_FIELDS) <= set(rows[0])
    assert rows[0]["retrieval_type"] == ""
    assert sum(r["overlap"] == "yes" for r in rows) == 6


def test_agreement_ignores_ambiguous_and_blank_overlap_rows():
    rows = [
        {
            "overlap": "yes",
            "retrieval_type": "vague_memory_retrieval",
            "retrieval_type_2": "vague_memory_retrieval",
            "primary_category": "life_event_retrieval",
            "primary_category_2": "life_event_retrieval",
        },
        {
            "overlap": "yes",
            "retrieval_type": "general_retrieval",
            "retrieval_type_2": "not_retrieval",
            "primary_category": "search_trust_breakdown",
            "primary_category_2": "search_trust_breakdown",
        },
        {
            "overlap": "yes",
            "retrieval_type": "ambiguous",
            "retrieval_type_2": "general_retrieval",
            "primary_category": "",
            "primary_category_2": "",
        },
        {
            "overlap": "yes",
            "retrieval_type": "",
            "retrieval_type_2": "",
            "primary_category": "",
            "primary_category_2": "",
        },
        {
            "overlap": "",
            "retrieval_type": "not_retrieval",
            "retrieval_type_2": "vague_memory_retrieval",
            "primary_category": "",
            "primary_category_2": "",
        },
    ]
    stats = agreement(rows)
    assert stats["retrieval_type"]["compared"] == 2
    assert stats["retrieval_type"]["matches"] == 1
    assert stats["retrieval_type"]["agreement"] == 0.5
    assert stats["retrieval_type"]["ambiguous_excluded"] == 1
    assert stats["retrieval_type"]["unlabeled"] == 1
    assert stats["primary_category"]["agreement"] == 1.0


def test_seed_exemplars_must_not_overlap_the_gold_set():
    text = "I can't find that photo from the trip last year and search failed badly"
    assert exemplar_overlap([text], [text])
    assert exemplar_overlap([text], ["Storage prices went up again this month"]) == []
