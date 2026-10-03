"""Run cleaning and dedup over the `items` table and write the results back."""

from __future__ import annotations

import csv
import hashlib
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import bindparam, delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from discovery.config import AppConfig
from discovery.db import session_scope
from discovery.ingest.base import hash_author
from discovery.models.orm import (
    EmbeddingRow,
    InsightRow,
    ItemRow,
    ItemSourceRow,
    OpportunityEvidenceRow,
    RawItemRow,
    RelevanceRow,
)
from discovery.models.schemas import Platform, RawItem, SourceName
from discovery.prep.clean import (
    CleanResult,
    analysis_text,
    clean,
    find_pii,
    phrase_pattern,
    scrub_usernames,
)
from discovery.prep.dedup import DedupItem, DedupResult, Merge, deduplicate
from discovery.prep.normalize import SkipItem, _new_row, item_id_for, normalize
from discovery.runs import StageRun

log = logging.getLogger(__name__)

_CHILD_TABLES = (RelevanceRow, InsightRow, EmbeddingRow, OpportunityEvidenceRow)
_WRITE_CHUNK = 500

# Core UPDATE so a batch of rows is one executemany, not 20k round trips.
_ITEMS = ItemRow.__table__
_UPDATE_ITEMS = (
    update(_ITEMS)
    .where(_ITEMS.c.item_id == bindparam("b_id"))
    .values(
        clean_text=bindparam("b_clean"),
        language=bindparam("b_lang"),
        author_hash=bindparam("b_author"),
        is_spam=bindparam("b_spam"),
        similar_count=bindparam("b_similar"),
        date=bindparam("b_date"),
        metadata=bindparam("b_meta"),
    )
)


@dataclass
class PrepReport:
    items_in: int = 0
    remaining: int = 0
    ai_eligible: int = 0
    pii_redactions: int = 0
    pii_remaining: int = 0
    usernames_dropped: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    languages: dict[str, int] = field(default_factory=dict)
    dedup: dict[str, int] = field(default_factory=dict)
    merges: list[Merge] = field(default_factory=list)

    def as_counts(self) -> dict[str, object]:
        return {
            "items_in": self.items_in,
            "remaining": self.remaining,
            "ai_eligible": self.ai_eligible,
            "excluded": self.excluded,
            "languages": self.languages,
            "pii_redactions": self.pii_redactions,
            "pii_remaining": self.pii_remaining,
            "usernames_dropped": self.usernames_dropped,
            "dedup": self.dedup,
        }


def render_prep_report(report: PrepReport) -> str:
    excluded = ", ".join(f"{k}={v}" for k, v in sorted(report.excluded.items())) or "none"
    languages = ", ".join(f"{k}={v}" for k, v in sorted(report.languages.items())) or "none"
    dedup = report.dedup
    lines = [
        f"Items in: {report.items_in:,}    remaining: {report.remaining:,}    "
        f"ready for AI: {report.ai_eligible:,}",
        f"Excluded: {excluded}",
        f"Languages: {languages}",
        f"PII redactions: {report.pii_redactions:,}    "
        f"items still containing email or phone: {report.pii_remaining:,}",
        "Dedup: "
        f"exact={dedup.get('exact_merged', 0):,} near={dedup.get('near_merged', 0):,} "
        f"review_id={dedup.get('review_id_merged', 0):,} "
        f"cross_source={dedup.get('cross_source_merged', 0):,} "
        f"spam={dedup.get('spam', 0):,} "
        f"short_kept_separate={dedup.get('short_kept_separate', 0):,}",
    ]
    return "\n".join(lines)


def write_merge_sample(path: Path, merges: list[Merge], limit: int) -> int:
    """A sample of merges for the manual ≥95% check. Returns how many rows were written.

    At most three rows come from any one kept item, and exact, near, and review-id merges
    are interleaved, so one large cluster cannot fill the sheet.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = _diverse_merges(merges, limit)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "reason",
                "jaccard",
                "canonical_item_id",
                "merged_item_id",
                "canonical_source",
                "merged_source",
                "canonical_text",
                "merged_text",
            ],
        )
        writer.writeheader()
        for merge in sample:
            writer.writerow(
                {
                    "reason": merge.reason,
                    "jaccard": "" if merge.jaccard is None else f"{merge.jaccard:.3f}",
                    "canonical_item_id": merge.canonical_id,
                    "merged_item_id": merge.merged_id,
                    "canonical_source": merge.canonical_source,
                    "merged_source": merge.merged_source,
                    "canonical_text": merge.canonical_text,
                    "merged_text": merge.merged_text,
                }
            )
    return len(sample)


def _diverse_merges(merges: list[Merge], limit: int) -> list[Merge]:
    per_canonical: Counter[str] = Counter()
    buckets: dict[str, list[Merge]] = defaultdict(list)
    for merge in merges:
        if per_canonical[merge.canonical_id] >= 3:
            continue
        per_canonical[merge.canonical_id] += 1
        buckets[merge.reason].append(merge)
    sample: list[Merge] = []
    cursors = {reason: 0 for reason in ("near", "exact", "review_id")}
    while len(sample) < limit:
        added = False
        for reason, cursor in cursors.items():
            bucket = buckets.get(reason, [])
            if cursor >= len(bucket):
                continue
            sample.append(bucket[cursor])
            cursors[reason] = cursor + 1
            added = True
            if len(sample) >= limit:
                break
        if not added:
            break
    return sample


def run_prep(
    cfg: AppConfig, factory: sessionmaker[Session], run: StageRun, *, salt: str | None
) -> PrepReport:
    prep = cfg.settings.prep
    retrieval = phrase_pattern(cfg.keywords.lexicon.retrieval_intent)
    max_chars = cfg.settings.llm.max_input_chars
    dayfirst = cfg.settings.sources.google_sheet.dayfirst

    with session_scope(factory) as session:
        restored = expand_prior_merges(session, dayfirst=dayfirst)
    if restored:
        log.info("Restored %s items absorbed by an earlier prep run", restored)

    with session_scope(factory) as session:
        rows = list(session.scalars(select(ItemRow)))
        session.expunge_all()

    report = PrepReport(items_in=len(rows))
    if not rows:
        run.counts.update(report.as_counts())
        return report

    cleaned: list[tuple[ItemRow, CleanResult, dict, str | None]] = []
    dedup_items: list[DedupItem] = []
    for row in rows:
        meta, removed = scrub_usernames(dict(row.metadata_ or {}))
        report.usernames_dropped += len(removed)
        author = row.author_hash
        if author is None and removed and salt:
            author = hash_author(removed[0], salt)
        followups = meta.get("op_followups") or []
        text = analysis_text(row.title, row.original_text, followups)
        boilerplate = row.platform == "Web Forum" or bool(meta.get("scraped_page"))
        result = clean(
            text,
            prep=prep,
            retrieval=retrieval,
            max_chars=max_chars,
            strip_boilerplate=boilerplate,
        )
        report.pii_redactions += result.pii_redactions
        if find_pii(result.clean_text):
            report.pii_remaining += 1
            run.error("clean_text still contains an email or phone number", item_id=row.item_id)
        cleaned.append((row, result, meta, author))
        dedup_items.append(
            DedupItem(
                item_id=row.item_id,
                source=row.primary_source_name,
                author_hash=author,
                clean_text=result.clean_text,
                dedup_text=result.dedup_text,
                words=tuple(result.dedup_text.split()),
                date=row.date,
                review_id=_review_key(row.primary_source_name, row.platform, meta),
                has_retrieval=bool(retrieval.search(result.dedup_text)),
                ai_eligible=result.ai_eligible,
                exclude_reason=result.exclude_reason,
            )
        )

    log.info("Deduplicating %s cleaned items", len(dedup_items))
    dedup_result = deduplicate(
        dedup_items,
        short_words=prep.short_text_words,
        jaccard_threshold=prep.near_dup_jaccard,
        shingle_size=prep.near_dup_shingle_size,
        spam_min_authors=prep.spam_min_authors,
    )
    report.dedup = dedup_result.stats
    report.merges = dedup_result.merges
    _persist(factory, cleaned, dedup_items, dedup_result)

    languages_by_id = {row.item_id: result.language for row, result, _, _ in cleaned}
    languages: Counter[str] = Counter()
    excluded: Counter[str] = Counter()
    eligible = 0
    for item in dedup_items:
        if item.item_id in dedup_result.dropped:
            continue
        languages[languages_by_id.get(item.item_id, "unknown")] += 1
        if item.ai_eligible:
            eligible += 1
        elif item.exclude_reason:
            excluded[item.exclude_reason] += 1
    report.remaining = dedup_result.stats.get("remaining", 0)
    report.ai_eligible = eligible
    report.languages = dict(languages)
    report.excluded = dict(excluded)
    run.counts.update(report.as_counts())
    log.info("Prep finished: %s", render_prep_report(report).replace("\n", " | "))
    return report


def expand_prior_merges(session: Session, *, dayfirst: bool) -> int:
    """Put back items an earlier prep run deleted, then point each raw row at its own item.

    Dedup removes the shorter copy. Re-running prep has to see those copies again, which
    means rebuilding them from `raw_items`. Sources that were never merged (or that have
    no raw payload) are left alone.
    """
    sources = list(session.scalars(select(ItemSourceRow)))
    moved = [src for src in sources if item_id_for(src.raw_id) != src.item_id]
    if not moved:
        return 0
    raws = _load_raws(session, [src.raw_id for src in moved])
    actionable = [src for src in moved if src.raw_id in raws]
    if not actionable:
        return 0

    expected_ids = [item_id_for(src.raw_id) for src in actionable]
    existing: set[str] = set()
    for chunk in _chunks(expected_ids, _WRITE_CHUNK):
        existing.update(session.scalars(select(ItemRow.item_id).where(ItemRow.item_id.in_(chunk))))

    restored = 0
    repoint: list[ItemSourceRow] = []
    for src in actionable:
        expected = item_id_for(src.raw_id)
        if expected not in existing:
            try:
                item = normalize(_as_raw(raws[src.raw_id], src), dayfirst=dayfirst)
            except SkipItem:
                log.warning("Skipped restoring %s: raw payload has no text", src.raw_id)
                continue
            session.add(_new_row(item))
            existing.add(expected)
            restored += 1
        repoint.append(src)
    # Captured before sources are pointed back at the restored items.
    canonical_ids = {src.item_id for src in actionable}
    session.flush()
    for src in repoint:
        src.item_id = item_id_for(src.raw_id)

    _reset_canonicals(session, sources, canonical_ids, dayfirst=dayfirst)
    return restored


def _reset_canonicals(
    session: Session, sources: list[ItemSourceRow], canonical_ids: set[str], *, dayfirst: bool
) -> None:
    """Drop a stale merge list and put the kept item's own date back.

    The kept row's date was overwritten with the earliest date in the cluster. After the
    other copies are restored, that date belongs to the kept item only until dedup runs again.
    """
    owners = [
        src
        for src in sources
        if src.item_id in canonical_ids and item_id_for(src.raw_id) == src.item_id
    ]
    raws = _load_raws(session, [src.raw_id for src in owners])
    rows: dict[str, ItemRow] = {}
    for chunk in _chunks(list(canonical_ids), _WRITE_CHUNK):
        for row in session.scalars(select(ItemRow).where(ItemRow.item_id.in_(chunk))):
            rows[row.item_id] = row
    for src in owners:
        row = rows.get(src.item_id)
        if row is None:
            continue
        meta = dict(row.metadata_ or {})
        prep = meta.get("prep")
        if isinstance(prep, dict) and prep.get("merged_from"):
            prep = dict(prep)
            prep["merged_from"] = []
            meta["prep"] = prep
            row.metadata_ = meta
        raw = raws.get(src.raw_id)
        if raw is None:
            continue
        try:
            item = normalize(_as_raw(raw, src), dayfirst=dayfirst)
        except SkipItem:
            continue
        row.date = item.date


def _load_raws(session: Session, raw_ids: list[str]) -> dict[str, RawItemRow]:
    found: dict[str, RawItemRow] = {}
    for chunk in _chunks(raw_ids, _WRITE_CHUNK):
        for row in session.scalars(select(RawItemRow).where(RawItemRow.raw_id.in_(chunk))):
            found[row.raw_id] = row
    return found


def _as_raw(raw: RawItemRow, src: ItemSourceRow) -> RawItem:
    fetched: datetime = raw.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return RawItem(
        raw_id=raw.raw_id,
        source_name=SourceName(src.source_name),
        platform=Platform(src.platform),
        source_url=src.source_url,
        fetched_at=fetched,
        run_id=raw.run_id,
        payload=raw.payload or {},
    )


def _review_key(source: str, platform: str, meta: dict) -> str | None:
    """Shared key for a store review that also appears as a Sheet row (DD-05)."""
    if source == "play_store" and meta.get("review_id"):
        return f"play_store:{meta['review_id']}"
    if source == "app_store" and meta.get("review_id"):
        return f"app_store:{meta['review_id']}"
    sheet_id = meta.get("sheet_id_value")
    if source == "google_sheet" and sheet_id:
        if platform == "Android":
            return f"play_store:{sheet_id}"
        if platform == "iOS":
            return f"app_store:{sheet_id}"
    return None


def _persist(
    factory: sessionmaker[Session],
    cleaned: list[tuple[ItemRow, CleanResult, dict, str | None]],
    dedup_items: list[DedupItem],
    dedup_result: DedupResult,
) -> None:
    by_id = {it.item_id: it for it in dedup_items}
    absorbed: dict[str, list[str]] = {}
    for merged_id, canonical_id in dedup_result.dropped.items():
        absorbed.setdefault(canonical_id, []).append(merged_id)

    with session_scope(factory) as session:
        if dedup_result.dropped:
            _repoint_sources(session, dedup_result.dropped)
            session.flush()
            for loser_ids in _chunks(list(dedup_result.dropped), _WRITE_CHUNK):
                for model in _CHILD_TABLES:
                    session.execute(delete(model).where(model.item_id.in_(loser_ids)))
                session.execute(delete(ItemRow).where(ItemRow.item_id.in_(loser_ids)))

        payloads = []
        for row, result, meta, author in cleaned:
            item = by_id[row.item_id]
            if row.item_id in dedup_result.dropped:
                continue
            previous = meta.get("prep") if isinstance(meta.get("prep"), dict) else {}
            merged_from = list(previous.get("merged_from") or [])
            merged_from.extend(absorbed.get(row.item_id, []))
            meta["prep"] = {
                "ai_eligible": item.ai_eligible,
                "exclude_reason": item.exclude_reason,
                "code_mixed": result.code_mixed,
                "truncated": result.truncated,
                "word_count": result.word_count,
                "pii_redactions": result.pii_redactions,
                "dedup_hash": hashlib.sha256(result.dedup_text.encode()).hexdigest(),
                "merged_from": merged_from,
            }
            payloads.append(
                {
                    "b_id": row.item_id,
                    "b_clean": result.clean_text,
                    "b_lang": result.language,
                    "b_author": author,
                    "b_spam": item.is_spam,
                    "b_similar": item.similar_count,
                    "b_date": dedup_result.earliest_date.get(row.item_id, row.date),
                    "b_meta": meta,
                }
            )
        log.info("Writing %s cleaned items", len(payloads))
        for start in range(0, len(payloads), _WRITE_CHUNK):
            session.execute(
                _UPDATE_ITEMS,
                payloads[start : start + _WRITE_CHUNK],
                execution_options={"synchronize_session": None},
            )


def _repoint_sources(session: Session, dropped: dict[str, str]) -> None:
    """Point every raw source of a merged item at the canonical item."""
    for loser_ids in _chunks(list(dropped), _WRITE_CHUNK):
        source_rows = session.scalars(
            select(ItemSourceRow).where(ItemSourceRow.item_id.in_(loser_ids))
        )
        for source in source_rows:
            source.item_id = dropped[source.item_id]


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def pii_scan(factory: sessionmaker[Session]) -> list[str]:
    """Item ids whose `clean_text` still matches an email or phone pattern."""
    with session_scope(factory) as session:
        rows = session.execute(select(ItemRow.item_id, ItemRow.clean_text)).all()
    return [item_id for item_id, text in rows if text and find_pii(text)]
