"""Exact, near-duplicate, and cross-source dedup (architecture Section 6.3).

Short texts (under `short_text_words`) are merged only when the author and the source
both match. The same short complaint from different people stays as separate items, each
with a `similar_count`. Near-duplicate matching (exact Jaccard on word shingles) runs on
long texts only, and only against the kept text: a chain of similar posts is not collapsed
into one item. The longest text is kept; the earliest date is kept with it.
"""

from __future__ import annotations

import itertools
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

_DF_MAX = 40
_PREFIX_WORDS = 6
_PREFIX_MAX = 80
_NEIGHBOR_WINDOW = 8


@dataclass
class DedupItem:
    item_id: str
    source: str
    author_hash: str | None
    clean_text: str
    dedup_text: str
    words: tuple[str, ...]
    date: datetime | None
    review_id: str | None
    has_retrieval: bool
    ai_eligible: bool
    exclude_reason: str | None
    is_spam: bool = False
    similar_count: int = 0


@dataclass(frozen=True)
class Merge:
    canonical_id: str
    merged_id: str
    reason: str
    jaccard: float | None
    canonical_source: str
    merged_source: str
    canonical_text: str
    merged_text: str


@dataclass
class DedupResult:
    """`dropped` maps a removed item id to the canonical item that absorbed it."""

    dropped: dict[str, str] = field(default_factory=dict)
    merges: list[Merge] = field(default_factory=list)
    earliest_date: dict[str, datetime] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return shared / (len(a) + len(b) - shared)


def shingles(words: tuple[str, ...] | list[str], n: int) -> set[str]:
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def deduplicate(
    items: list[DedupItem],
    *,
    short_words: int,
    jaccard_threshold: float,
    shingle_size: int,
    spam_min_authors: int,
) -> DedupResult:
    """Mutates `is_spam`, `similar_count`, and (when spam) eligibility on `items`."""
    result = DedupResult()
    if not items:
        return result

    spam_texts = _spam_texts(items, spam_min_authors)
    uf = _UnionFind(len(items))
    _union_exact(uf, items, short_words)
    _union_review_ids(uf, items)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(items)):
        groups[uf.find(i)].append(i)

    exact_merged = near_merged = review_merged = cross_source = 0
    for member_ids in groups.values():
        if len(member_ids) == 1:
            continue
        canonical_i = _pick_canonical(member_ids, items)
        canonical = items[canonical_i]
        _remember_earliest(result, canonical, [items[i].date for i in member_ids])
        for i in member_ids:
            if i == canonical_i:
                continue
            other = items[i]
            reason, score = _merge_reason(canonical_i, i, items)
            # Equality and shared review ids are transitive. A member that matches
            # neither the canonical text nor its review id got here through a review id.
            if reason == "near":
                reason, score = "review_id", None
            _record_merge(result, canonical, other, reason, score)
            if reason == "exact":
                exact_merged += 1
            else:
                review_merged += 1
            if other.source != canonical.source:
                cross_source += 1

    active = [i for i in range(len(items)) if items[i].item_id not in result.dropped]
    for canonical_i, other_i, score in _greedy_near(
        items,
        active,
        shingle_size=shingle_size,
        threshold=jaccard_threshold,
        short_words=short_words,
    ):
        canonical = items[canonical_i]
        other = items[other_i]
        _remember_earliest(result, canonical, [other.date])
        _record_merge(result, canonical, other, "near", score)
        near_merged += 1
        if other.source != canonical.source:
            cross_source += 1

    _flatten(result, items)
    dropped = set(result.dropped)
    survivors = [it for it in items if it.item_id not in dropped]
    _apply_spam(survivors, spam_texts)
    short_kept = _apply_similar_counts(survivors, short_words)

    result.stats = {
        "exact_merged": exact_merged,
        "near_merged": near_merged,
        "review_id_merged": review_merged,
        "cross_source_merged": cross_source,
        "spam": sum(it.is_spam for it in survivors),
        "short_kept_separate": short_kept,
        "remaining": len(survivors),
    }
    return result


def _flatten(result: DedupResult, items: list[DedupItem]) -> None:
    """Point every removed item at the item that is actually kept.

    An exact duplicate can be merged into a copy that a near-duplicate pass then
    merges again. The source rows have to follow the chain to the last item, or
    deleting the middle copy breaks the foreign key.
    """
    by_id = {it.item_id: it for it in items}
    original = dict(result.dropped)

    def final(item_id: str) -> str:
        seen: set[str] = set()
        while item_id in original and item_id not in seen:
            seen.add(item_id)
            item_id = original[item_id]
        return item_id

    result.dropped = {loser: final(canonical) for loser, canonical in original.items()}
    dates: dict[str, datetime] = {}
    for item_id, when in result.earliest_date.items():
        dest = final(item_id)
        if dest not in dates or when < dates[dest]:
            dates[dest] = when
    result.earliest_date = {dest: when for dest, when in dates.items() if by_id[dest].date != when}
    rewritten: list[Merge] = []
    for merge in result.merges:
        dest = final(merge.canonical_id)
        kept = by_id[dest]
        rewritten.append(
            Merge(
                canonical_id=dest,
                merged_id=merge.merged_id,
                reason=merge.reason,
                jaccard=merge.jaccard,
                canonical_source=kept.source,
                merged_source=merge.merged_source,
                canonical_text=kept.clean_text,
                merged_text=merge.merged_text,
            )
        )
    result.merges = rewritten


def _spam_texts(items: list[DedupItem], spam_min_authors: int) -> set[str]:
    """Identical text from many authors, with no retrieval keyword (DD-06)."""
    groups: dict[str, list[DedupItem]] = defaultdict(list)
    for it in items:
        if it.dedup_text and not it.has_retrieval:
            groups[it.dedup_text].append(it)
    flagged = set()
    for text, group in groups.items():
        authors = {it.author_hash or it.item_id for it in group}
        if len(authors) >= spam_min_authors:
            flagged.add(text)
    return flagged


def _chain(uf: _UnionFind, indexes: list[int]) -> None:
    if len(indexes) < 2:
        return
    head = indexes[0]
    for other in indexes[1:]:
        uf.union(head, other)


def _union_exact(uf: _UnionFind, items: list[DedupItem], short_words: int) -> None:
    by_text: dict[str, list[int]] = defaultdict(list)
    for i, it in enumerate(items):
        if it.dedup_text:
            by_text[it.dedup_text].append(i)
    for indexes in by_text.values():
        long = [i for i in indexes if len(items[i].words) >= short_words]
        _chain(uf, long)
        buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i in indexes:
            if len(items[i].words) >= short_words or items[i].author_hash is None:
                continue
            buckets[(items[i].author_hash, items[i].source)].append(i)
        for bucket in buckets.values():
            _chain(uf, bucket)


def _union_review_ids(uf: _UnionFind, items: list[DedupItem]) -> None:
    by_review: dict[str, list[int]] = defaultdict(list)
    for i, it in enumerate(items):
        if it.review_id:
            by_review[it.review_id].append(i)
    for indexes in by_review.values():
        _chain(uf, indexes)


def _remember_earliest(
    result: DedupResult, canonical: DedupItem, dates: list[datetime | None]
) -> None:
    known = [d for d in dates if d is not None]
    if canonical.date is not None:
        known.append(canonical.date)
    prior = result.earliest_date.get(canonical.item_id)
    if prior is not None:
        known.append(prior)
    if not known:
        return
    earliest = min(known)
    if earliest != canonical.date:
        result.earliest_date[canonical.item_id] = earliest


def _record_merge(
    result: DedupResult,
    canonical: DedupItem,
    other: DedupItem,
    reason: str,
    score: float | None,
) -> None:
    result.dropped[other.item_id] = canonical.item_id
    result.merges.append(
        Merge(
            canonical_id=canonical.item_id,
            merged_id=other.item_id,
            reason=reason,
            jaccard=score,
            canonical_source=canonical.source,
            merged_source=other.source,
            canonical_text=canonical.clean_text,
            merged_text=other.clean_text,
        )
    )


def _greedy_near(
    items: list[DedupItem],
    active: list[int],
    *,
    shingle_size: int,
    threshold: float,
    short_words: int,
) -> list[tuple[int, int, float]]:
    """Merge each long text into the longest text it actually matches.

    Pairs are not chained: if A matches B and B matches C, C is kept unless it also
    matches A. That stops a run of similar complaints from collapsing into one item.
    """
    long = [i for i in active if len(items[i].words) >= short_words]
    neighbors: dict[int, dict[int, float]] = defaultdict(dict)
    for a, b, score in _near_pairs(items, long, shingle_size=shingle_size, threshold=threshold):
        neighbors[a][b] = score
        neighbors[b][a] = score

    ordered = sorted(long, key=lambda i: _canonical_key(items[i]))
    assigned: set[int] = set()
    merges: list[tuple[int, int, float]] = []
    for i in ordered:
        if i in assigned:
            continue
        assigned.add(i)
        for j, score in neighbors.get(i, {}).items():
            if j in assigned:
                continue
            assigned.add(j)
            merges.append((i, j, score))
    return merges


def _canonical_key(it: DedupItem) -> tuple:
    ts = it.date.timestamp() if it.date is not None else float("inf")
    return (-len(it.clean_text), ts, it.item_id)


def _near_pairs(
    items: list[DedupItem],
    long: list[int],
    *,
    shingle_size: int,
    threshold: float,
) -> list[tuple[int, int, float]]:
    if len(long) < 2:
        return []
    shingles_of = {i: shingles(items[i].words, shingle_size) for i in long}
    candidates: set[tuple[int, int]] = set()

    ordered = sorted(long, key=lambda i: items[i].dedup_text)
    for pos, i in enumerate(ordered):
        for j in ordered[pos + 1 : pos + 1 + _NEIGHBOR_WINDOW]:
            candidates.add((i, j) if i < j else (j, i))

    index: dict[str, list[int]] = defaultdict(list)
    for i in long:
        for sh in shingles_of[i]:
            index[sh].append(i)
    for ids in index.values():
        if 2 <= len(ids) <= _DF_MAX:
            for a, b in itertools.combinations(sorted(set(ids)), 2):
                candidates.add((a, b))

    prefixes: dict[str, list[int]] = defaultdict(list)
    for i in long:
        prefixes[" ".join(items[i].words[:_PREFIX_WORDS])].append(i)
    for ids in prefixes.values():
        uniq = sorted(set(ids))
        if 2 <= len(uniq) <= _PREFIX_MAX:
            for a, b in itertools.combinations(uniq, 2):
                candidates.add((a, b))

    log.info("Near-duplicate candidates: %s pairs among %s long items", len(candidates), len(long))
    found: list[tuple[int, int, float]] = []
    for a, b in candidates:
        score = jaccard(shingles_of[a], shingles_of[b])
        if score >= threshold:
            found.append((a, b, round(score, 4)))
    return found


def _pick_canonical(member_ids: list[int], items: list[DedupItem]) -> int:
    # Longest text, then earliest date, then stable id (DD-07).
    return min(member_ids, key=lambda i: _canonical_key(items[i]))


def _merge_reason(
    canonical_i: int, other_i: int, items: list[DedupItem]
) -> tuple[str, float | None]:
    canonical, other = items[canonical_i], items[other_i]
    if canonical.review_id and canonical.review_id == other.review_id:
        return "review_id", None
    if canonical.dedup_text == other.dedup_text:
        return "exact", None
    return "near", None


def _apply_spam(survivors: list[DedupItem], spam_texts: set[str]) -> None:
    for it in survivors:
        if it.dedup_text in spam_texts:
            it.is_spam = True
            if it.ai_eligible:
                it.ai_eligible = False
                it.exclude_reason = "spam"


def _apply_similar_counts(survivors: list[DedupItem], short_words: int) -> int:
    groups: dict[str, list[DedupItem]] = defaultdict(list)
    for it in survivors:
        if it.dedup_text and len(it.words) < short_words:
            groups[it.dedup_text].append(it)
    kept_separate = 0
    for group in groups.values():
        extra = len(group) - 1
        for it in group:
            it.similar_count = extra
        if extra > 0:
            kept_separate += len(group)
    return kept_separate
