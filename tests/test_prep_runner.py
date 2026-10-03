from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from discovery.models.orm import ItemRow, ItemSourceRow, RawItemRow
from discovery.prep.dedup import Merge
from discovery.prep.normalize import item_id_for
from discovery.prep.runner import run_prep, write_merge_sample
from discovery.runs import StageRun


def _add(session, **kwargs: object) -> None:
    item_id = str(kwargs["item_id"])
    session.add(
        ItemRow(
            item_id=item_id,
            primary_source_name=kwargs.get("source", "play_store"),
            platform=kwargs.get("platform", "Android"),
            source_url=kwargs.get("url", f"https://example.com/{item_id}"),
            title=kwargs.get("title"),
            original_text=str(kwargs["text"]),
            content_hash="ingest-hash",
            author_hash=kwargs.get("author"),
            date=kwargs.get("date"),
            rating=kwargs.get("rating"),
            metadata_=kwargs.get("metadata") or {},
        )
    )
    session.add(
        ItemSourceRow(
            raw_id=kwargs.get("raw_id", f"raw:{item_id}"),
            item_id=item_id,
            source_name=kwargs.get("source", "play_store"),
            platform=kwargs.get("platform", "Android"),
            source_url=kwargs.get("url", f"https://example.com/{item_id}"),
            run_id="ingest-run",
        )
    )


def test_prep_cleans_in_place_and_keeps_short_complaints_separate(cfg, session_factory):
    with session_factory() as session:
        for i in range(20):
            _add(
                session,
                item_id=f"s{i}",
                text="Search doesn't work",
                author=f"author-{i}",
                metadata={"author": f"person-{i}", "review_id": f"r{i}"},
            )
        _add(
            session,
            item_id="pii",
            text="Email me at ada@example.com about version 6.12.0.123",
            author="author-pii",
        )
        session.commit()

    report = run_prep(cfg, session_factory, StageRun("prep-run", "prep"), salt="salt")
    assert report.pii_remaining == 0
    assert report.usernames_dropped == 20

    with session_factory() as session:
        rows = {r.item_id: r for r in session.scalars(select(ItemRow))}
    assert len(rows) == 21
    assert all(r.similar_count == 19 for iid, r in rows.items() if iid.startswith("s"))
    assert rows["s0"].original_text == "Search doesn't work"
    assert rows["s0"].content_hash == "ingest-hash"
    assert "author" not in rows["s0"].metadata_
    assert rows["s0"].metadata_["prep"]["ai_eligible"] is True
    pii = rows["pii"]
    assert "ada@example.com" not in (pii.clean_text or "")
    assert "[EMAIL]" in (pii.clean_text or "")
    assert "6.12.0.123" in (pii.clean_text or "")
    assert pii.original_text.startswith("Email me at ada@example.com")


def test_cross_source_duplicates_keep_every_source(cfg, session_factory):
    text = (
        "I remember a photo of the small cafe from our trip but cannot recall the name "
        "or the month we visited and search failed"
    )
    with session_factory() as session:
        _add(
            session,
            item_id="play",
            text=text,
            source="play_store",
            platform="Android",
            author="play-author",
            date=datetime(2024, 5, 1, tzinfo=UTC),
            metadata={"review_id": "abc-123"},
        )
        _add(
            session,
            item_id="sheet",
            text=text,
            source="google_sheet",
            platform="Android",
            raw_id="raw:sheet",
            author="sheet-author",
            date=datetime(2024, 1, 1, tzinfo=UTC),
            metadata={"sheet_id_value": "abc-123"},
        )
        session.commit()

    run_prep(cfg, session_factory, StageRun("prep-run", "prep"), salt="salt")

    with session_factory() as session:
        items = list(session.scalars(select(ItemRow)))
        sources = list(session.scalars(select(ItemSourceRow)))
    assert len(items) == 1
    assert {s.source_name for s in sources} == {"play_store", "google_sheet"}
    assert {s.item_id for s in sources} == {items[0].item_id}
    # Earliest date is kept. SQLite returns it without a timezone.
    assert items[0].date.replace(tzinfo=UTC) == datetime(2024, 1, 1, tzinfo=UTC)


def test_prep_restores_items_a_previous_run_merged(cfg, session_factory):
    raw_a = "play_store:heart-a"
    raw_b = "play_store:heart-b"
    id_a = item_id_for(raw_a)
    id_b = item_id_for(raw_b)
    with session_factory() as session:
        session.add(
            ItemRow(
                item_id=id_a,
                primary_source_name="play_store",
                platform="Android",
                source_url="https://example.com/a",
                original_text="❤️❤️❤️",
                date=datetime(2020, 1, 1, tzinfo=UTC),
                author_hash="hash-a",
                content_hash="old",
                metadata_={"prep": {"merged_from": [id_b], "word_count": 40}},
            )
        )
        for raw_id, content, author in (
            (raw_a, "❤️❤️❤️", "hash-a"),
            (raw_b, "❤️❤️❤️❤️", "hash-b"),
        ):
            session.add(
                RawItemRow(
                    raw_id=raw_id,
                    source_name="play_store",
                    run_id="ingest-run",
                    fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
                    payload={
                        "content": content,
                        "reviewId": raw_id,
                        "at": "2024-06-01T00:00:00+00:00",
                        "score": 5,
                        "author_hash": author,
                    },
                )
            )
            session.add(
                ItemSourceRow(
                    raw_id=raw_id,
                    item_id=id_a,
                    source_name="play_store",
                    platform="Android",
                    source_url=f"https://example.com/{raw_id}",
                    run_id="ingest-run",
                )
            )
        session.commit()

    run_prep(cfg, session_factory, StageRun("prep-run", "prep"), salt="salt")

    with session_factory() as session:
        rows = {r.item_id: r for r in session.scalars(select(ItemRow))}
        sources = {s.raw_id: s for s in session.scalars(select(ItemSourceRow))}
    assert set(rows) == {id_a, id_b}
    assert sources[raw_a].item_id == id_a
    assert sources[raw_b].item_id == id_b
    assert rows[id_a].metadata_["prep"]["merged_from"] == []
    assert rows[id_a].date.replace(tzinfo=UTC) == datetime(2024, 6, 1, tzinfo=UTC)
    assert rows[id_b].metadata_["prep"]["exclude_reason"] == "emoji_only"


def test_merge_sample_is_spread_across_reasons_and_clusters(tmp_path):
    merges = []
    for i in range(10):
        merges.append(Merge("hearts", f"h{i}", "near", 0.9, "play_store", "play_store", "❤️", "❤️"))
    merges.append(Merge("exact-1", "e1", "exact", None, "play_store", "play_store", "same", "same"))
    merges.append(
        Merge("review-1", "r1", "review_id", None, "play_store", "google_sheet", "long", "short")
    )
    path = tmp_path / "dedup_review.csv"
    written = write_merge_sample(path, merges, limit=5)
    rows = path.read_text(encoding="utf-8").strip().splitlines()[1:]
    assert written == 5
    reasons = [row.split(",")[0] for row in rows]
    assert reasons[:3] == ["near", "exact", "review_id"]
    assert sum(row.split(",")[2] == "hearts" for row in rows) <= 3
