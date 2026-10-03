"""Phase 5 stage: cluster insights into opportunity areas and synthesize each area.

1. Embed problem_statement + trying_to_find + breakdown_point per insight (P5.1).
2. UMAP + HDBSCAN (P5.2). Noise items stay unclustered but remain in `insights`.
3. Match each cluster to the previous run's clusters (P5.9); a match keeps its area id.
4. Label each cluster from ~20 representative items on the label model (P5.3).
5. Group same-category clusters with close centroids into areas (P5.4), then apply PM
   curation from `pm_overrides` (rename, archive, merge, split).
6. Aggregates (P5.5) and representative quotes (P5.6), all deterministic.
7. Synthesis per area: summary with citations, why it matters, research questions
   (P5.7-P5.8). Sentences without a valid citation are flagged, not dropped.
8. Replace this run's rows in `clusters`, `opportunity_areas`, `opportunity_evidence`.

Without an LLM (`use_llm=False`, or the budget runs out) clusters get a heuristic label
(majority extraction category, name from the most central item) and areas get no summary.
"""

from __future__ import annotations

import csv
import hashlib
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from discovery.ai.clustering import (
    NOISE,
    ClusterNode,
    PriorCluster,
    Reducer,
    centroid,
    cluster_labels,
    diverse_top,
    group_clusters,
    majority_category,
    match_prior,
)
from discovery.ai.embeddings import (
    INSIGHT_SUFFIX,
    Encoder,
    SentenceEncoder,
    insight_text,
    pack_vector,
    unpack_vector,
)
from discovery.ai.llm_client import LLMBatchPending, LLMClient
from discovery.ai.opportunity import EvidenceItem, aggregates, select_quotes
from discovery.ai.prompts import Prompt, load_prompt
from discovery.ai.synthesis import (
    EST_LABEL_OUTPUT,
    EST_SYNTHESIS_OUTPUT,
    LABEL_PROMPT_NAME,
    LABEL_PROMPT_VERSION,
    SYNTHESIS_PROMPT_NAME,
    SYNTHESIS_PROMPT_VERSION,
    CallResults,
    LLMCall,
    ResolvedSynthesis,
    StepEstimate,
    estimate_step,
    label_input,
    resolve_synthesis,
    run_calls,
    synthesis_input,
)
from discovery.config import AppConfig, ClusteringSettings
from discovery.db import session_scope
from discovery.models.orm import (
    ClusterRow,
    EmbeddingRow,
    InsightRow,
    ItemRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    PipelineRunRow,
    PMOverrideRow,
    RelevanceRow,
)
from discovery.models.schemas import Category, ClusterLabel, OpportunitySynthesis
from discovery.runs import StageRun, llm_spend_since, save_counts

log = logging.getLogger(__name__)

STAGE = "cluster"
_WRITE_CHUNK = 400
HEURISTIC_NAME_CHARS = 70
CURATION_FIELDS = ("name", "status", "merge_into", "split_clusters")


# --- loading -----------------------------------------------------------------


def load_evidence(session: Session, *, only_useful: bool) -> tuple[list[EvidenceItem], int]:
    """Every insight joined with its item and relevance row. Returns the items to cluster
    and how many were left out as not useful for discovery."""
    rows = session.execute(
        select(
            InsightRow,
            ItemRow.primary_source_name,
            ItemRow.platform,
            ItemRow.source_url,
            ItemRow.rating,
            ItemRow.engagement,
            ItemRow.date,
            RelevanceRow.retrieval_type,
            RelevanceRow.vague_memory_relevance,
        )
        .join(ItemRow, ItemRow.item_id == InsightRow.item_id)
        .outerjoin(RelevanceRow, RelevanceRow.item_id == InsightRow.item_id)
        .order_by(InsightRow.item_id)
    ).all()
    items: list[EvidenceItem] = []
    skipped = 0
    for ins, source, platform, url, rating, engagement, date, rtype, relevance in rows:
        if only_useful and ins.useful_for_discovery is False:
            skipped += 1
            continue
        items.append(
            EvidenceItem(
                item_id=ins.item_id,
                source=source,
                platform=platform,
                source_url=url,
                retrieval_type=rtype,
                vague_memory_relevance=relevance,
                problem_statement=ins.problem_statement or "",
                trying_to_find=ins.trying_to_find or "",
                content_type=ins.content_type,
                remembered_cues=list(ins.remembered_cues or []),
                forgotten_details=list(ins.forgotten_details or []),
                search_attempts=list(ins.search_attempts or []),
                breakdown_point=ins.breakdown_point,
                outcome=ins.outcome,
                emotion=ins.emotion,
                frustration_intensity=ins.frustration_intensity,
                primary_category=ins.primary_category,
                high_stakes=ins.high_stakes,
                evidence_quote=ins.evidence_quote,
                evidence_strength=ins.evidence_strength,
                quote_grounded=ins.quote_grounded,
                useful_for_discovery=ins.useful_for_discovery,
                rating=rating,
                engagement=dict(engagement or {}),
                date=date,
            )
        )
    return items, skipped


def latest_cluster_run(
    session: Session, *, exclude: str | None = None, labeled_only: bool = False
) -> str | None:
    """The most recent finished `cluster` run that wrote clusters. `labeled_only` skips
    `--no-llm` previews, whose heuristic grouping must not be inherited."""
    query = (
        select(PipelineRunRow.run_id, PipelineRunRow.counts)
        .where(
            PipelineRunRow.stage == STAGE,
            PipelineRunRow.status.in_(("completed", "partial", "published")),
            PipelineRunRow.run_id.in_(select(ClusterRow.run_id).distinct()),
        )
        .order_by(PipelineRunRow.started_at.desc())
    )
    if exclude is not None:
        query = query.where(PipelineRunRow.run_id != exclude)
    for run_id, counts in session.execute(query):
        if not labeled_only or (counts or {}).get("clusters_labeled_by_llm"):
            return run_id
    return None


def load_prior(session: Session, run_id: str) -> tuple[str | None, list[PriorCluster]]:
    prior_run = latest_cluster_run(session, exclude=run_id, labeled_only=True)
    if prior_run is None:
        return None, []
    rows = session.scalars(select(ClusterRow).where(ClusterRow.run_id == prior_run)).all()
    return prior_run, [
        PriorCluster(r.cluster_id, r.area_id, unpack_vector(r.centroid))
        for r in rows
        if r.area_id and r.centroid
    ]


@dataclass
class Curation:
    """PM decisions on areas, latest first-come order. Re-applied on every run."""

    names: dict[str, str] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    merges: dict[str, str] = field(default_factory=dict)
    # (override id, source area id, {"run_id/cluster_id", ...})
    splits: list[tuple[int, str, set[str]]] = field(default_factory=list)


def load_curation(session: Session) -> Curation:
    rows = session.scalars(
        select(PMOverrideRow)
        .where(PMOverrideRow.target_type == "area", PMOverrideRow.field.in_(CURATION_FIELDS))
        .order_by(PMOverrideRow.created_at, PMOverrideRow.override_id)
    ).all()
    cur = Curation()
    for row in rows:
        value = row.override_value
        if row.field == "split_clusters":
            cur.splits.append((row.override_id, row.target_id, set(value or [])))
            continue
        target = {"name": cur.names, "status": cur.statuses, "merge_into": cur.merges}[row.field]
        if value in (None, ""):
            target.pop(row.target_id, None)
        else:
            target[row.target_id] = str(value)
    return cur


# --- clusters ----------------------------------------------------------------


@dataclass(frozen=True)
class PriorRef:
    run_id: str
    cluster_id: str
    area_id: str
    similarity: float

    @property
    def ref(self) -> str:
        return f"{self.run_id}/{self.cluster_id}"


@dataclass
class ClusterDraft:
    cluster_id: str
    members: list[int]
    centroid: np.ndarray = field(repr=False)
    extraction_categories: Counter[str]
    representatives: list[int]
    label: ClusterLabel | None = None
    labeled_by: str = "heuristic"
    prior: PriorRef | None = None
    area_id: str | None = None
    home_area_id: str | None = None  # before PM merges; stored, so an unmerge takes effect

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def category(self) -> str:
        assert self.label is not None
        return self.label.category.value


def build_clusters(
    items: list[EvidenceItem], vectors: np.ndarray, labels: np.ndarray, label_items: int
) -> list[ClusterDraft]:
    clusters = []
    for c in sorted(set(labels.tolist()) - {NOISE}):
        members = np.flatnonzero(labels == c).tolist()
        center = centroid(vectors[members])
        sims = vectors[members] @ center
        reps = diverse_top(
            [(str(i), items[i].source, float(s)) for i, s in zip(members, sims, strict=True)],
            label_items,
        )
        clusters.append(
            ClusterDraft(
                cluster_id=f"c{c + 1:02d}",
                members=members,
                centroid=center,
                extraction_categories=Counter(items[i].primary_category or "none" for i in members),
                representatives=[int(r) for r in reps],
            )
        )
    return clusters


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split()).rstrip(".")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def heuristic_label(cluster: ClusterDraft, items: list[EvidenceItem]) -> ClusterLabel:
    central = items[cluster.representatives[0]]
    valid = {c.value for c in Category}
    counts = Counter({k: v for k, v in cluster.extraction_categories.items() if k in valid})
    category = counts.most_common(1)[0][0] if counts else Category.OTHER_EMERGENT.value
    return ClusterLabel(
        name=_short(central.problem_statement or central.trying_to_find, HEURISTIC_NAME_CHARS),
        summary=central.problem_statement or central.trying_to_find,
        category=Category(category),
        rationale="Heuristic label: majority extraction category; name from the most central item.",
        confidence=0.0,
    )


# --- areas -------------------------------------------------------------------


def area_id_for(seed: str) -> str:
    return "oa-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]


@dataclass
class AreaDraft:
    area_id: str
    cluster_ids: list[str]
    category: str
    status: str = "active"
    pm_name: str | None = None
    merged_into: str | None = None
    curation: list[str] = field(default_factory=list)
    members: list[int] = field(default_factory=list)
    centroid: np.ndarray | None = field(default=None, repr=False)
    agg: dict[str, Any] = field(default_factory=dict)
    quote_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    synthesis: ResolvedSynthesis | None = None
    fallback_name: str = ""

    @property
    def ai_name(self) -> str:
        return self.synthesis.name if self.synthesis else self.fallback_name

    @property
    def name(self) -> str:
        return self.pm_name or self.ai_name

    @property
    def size(self) -> int:
        return len(self.members)


def build_areas(
    clusters: list[ClusterDraft],
    *,
    run_id: str,
    settings: ClusteringSettings,
    curation: Curation,
) -> tuple[list[AreaDraft], list[AreaDraft]]:
    """Returns the areas (active and archived) and stub rows for areas merged away."""
    fixed = {c.cluster_id: c.prior.area_id for c in clusters if c.prior}
    split_note: dict[str, str] = {}
    for override_id, source, refs in curation.splits:
        new_id = area_id_for(f"split:{override_id}")
        for c in clusters:
            if c.prior and c.prior.ref in refs:
                fixed[c.cluster_id] = new_id
                split_note[new_id] = f"split from {source} (override {override_id})"
    nodes = {
        c.cluster_id: ClusterNode(
            c.cluster_id, c.category, c.centroid, c.size, fixed.get(c.cluster_id)
        )
        for c in clusters
    }
    groups = group_clusters(list(nodes.values()), settings.area_merge_cosine)
    by_id: dict[str, list[str]] = {}
    for g in groups:
        area_id = g.area_id or area_id_for(f"{run_id}:{','.join(sorted(g.keys))}")
        by_id.setdefault(area_id, []).extend(g.keys)

    def final(area_id: str) -> str:
        seen = set()
        while area_id in curation.merges and area_id not in seen:
            seen.add(area_id)
            area_id = curation.merges[area_id]
        return area_id

    by_key = {c.cluster_id: c for c in clusters}
    merged: dict[str, list[str]] = {}
    combined: dict[str, list[str]] = {}
    for area_id, keys in by_id.items():
        for k in keys:
            by_key[k].home_area_id = area_id
        target = final(area_id)
        combined.setdefault(target, []).extend(keys)
        if target != area_id:
            merged.setdefault(target, []).append(area_id)

    areas: list[AreaDraft] = []
    stubs: list[AreaDraft] = []
    for area_id, keys in combined.items():
        members = [nodes[k] for k in keys]
        area = AreaDraft(
            area_id=area_id,
            cluster_ids=sorted(keys, key=lambda k: (-nodes[k].size, k)),
            category=majority_category(members),
            status=curation.statuses.get(area_id, "active"),
            pm_name=curation.names.get(area_id),
        )
        if area_id in split_note:
            area.curation.append(split_note[area_id])
        for source in merged.get(area_id, []):
            area.curation.append(f"merged in {source}")
            stubs.append(
                AreaDraft(
                    area_id=source,
                    cluster_ids=[],
                    category=area.category,
                    status="merged",
                    pm_name=curation.names.get(source),
                    merged_into=area_id,
                    curation=[f"merged into {area_id}"],
                )
            )
        if area.status != "active":
            area.curation.append(f"status {area.status}")
        if area.pm_name:
            area.curation.append("renamed by PM")
        for cid in keys:
            by_key[cid].area_id = area_id
        areas.append(area)
    areas.sort(key=lambda a: (a.status != "active", -sum(nodes[k].size for k in a.cluster_ids)))
    return areas, stubs


def finish_areas(
    areas: list[AreaDraft],
    clusters: list[ClusterDraft],
    items: list[EvidenceItem],
    vectors: np.ndarray,
    settings: ClusteringSettings,
) -> None:
    """Aggregates, quotes, and the evidence items each synthesis call sees."""
    by_id = {c.cluster_id: c for c in clusters}
    for area in areas:
        area.members = sorted({i for cid in area.cluster_ids for i in by_id[cid].members})
        area.centroid = centroid(vectors[area.members])
        sims = {items[i].item_id: float(vectors[i] @ area.centroid) for i in area.members}
        area_items = [items[i] for i in area.members]
        agg = aggregates(area_items)
        agg["sub_themes"] = [
            {
                "cluster_id": cid,
                "label": by_id[cid].label.name,
                "summary": by_id[cid].label.summary,
                "category": by_id[cid].category,
                "labeled_by": by_id[cid].labeled_by,
                "size": by_id[cid].size,
                "item_ids": [items[i].item_id for i in by_id[cid].members],
            }
            for cid in area.cluster_ids
        ]
        agg["emerging"] = area.size < settings.min_area_items
        area.agg = agg
        area.fallback_name = by_id[area.cluster_ids[0]].label.name
        area.quote_ids = select_quotes(
            area_items, sims, max_quotes=settings.max_quotes, dedup_ratio=settings.quote_dedup_ratio
        )
        agg["few_quotes"] = len(area.quote_ids) < settings.min_quotes
        quoted = set(area.quote_ids)
        rest = diverse_top(
            [
                (it.item_id, it.source, sims[it.item_id])
                for it in area_items
                if it.item_id not in quoted
            ],
            max(0, settings.synthesis_items - len(area.quote_ids)),
        )
        area.evidence_ids = area.quote_ids + rest


def synthesis_request(
    area: AreaDraft, items_by_id: dict[str, EvidenceItem], category_names: dict[str, str]
) -> tuple[str, dict[str, str]]:
    return synthesis_input(
        draft_name=area.fallback_name,
        category=area.category,
        category_name=category_names.get(area.category, area.category),
        sub_themes=area.agg["sub_themes"],
        agg=area.agg,
        evidence=[items_by_id[i] for i in area.evidence_ids],
    )


# --- report ------------------------------------------------------------------


@dataclass
class CostEstimate:
    label: StepEstimate
    synthesis: StepEstimate
    batch_price_factor: float
    remaining_usd: float | None

    def render(self) -> str:
        total = self.label.usd + self.synthesis.usd
        worst = self.label.worst_case_usd + self.synthesis.worst_case_usd
        f = self.batch_price_factor
        lines = [
            f"Cluster labels: {self.label.calls} calls, ~{self.label.input_tokens:,} input + "
            f"{self.label.output_tokens:,} output tokens, ~${self.label.usd:.3f}",
            f"Area synthesis: {self.synthesis.calls} calls, ~{self.synthesis.input_tokens:,} input "
            f"+ {self.synthesis.output_tokens:,} output tokens, ~${self.synthesis.usd:.3f}",
            f"Expected total: ~${total:.3f} with normal calls, ~${total * f:.3f} with the Message "
            "Batches API.",
            f"Budget guard reserves up to ${worst:.3f} (normal) / ${worst * f:.3f} (batch) before "
            "sending, so that much room must be left.",
        ]
        if self.remaining_usd is not None:
            lines.append(f"Room left in the project budget: ${self.remaining_usd:.3f}.")
        return "\n".join(lines)


@dataclass
class ClusterReport:
    run_id: str
    label_model: str
    synthesis_model: str
    label_prompt: str
    synthesis_prompt: str
    settings: ClusteringSettings
    insights_total: int = 0
    excluded_not_useful: int = 0
    noise: int = 0
    items: list[EvidenceItem] = field(default_factory=list)
    labels: np.ndarray | None = field(default=None, repr=False)
    clusters: list[ClusterDraft] = field(default_factory=list)
    areas: list[AreaDraft] = field(default_factory=list)
    stubs: list[AreaDraft] = field(default_factory=list)
    prior_run_id: str | None = None
    llm_used: bool = False
    label_errors: list[str] = field(default_factory=list)
    synthesis_errors: list[str] = field(default_factory=list)
    stopped_early: str | None = None
    batch_ids: list[str] = field(default_factory=list)
    estimate: CostEstimate | None = None
    written: bool = False

    @property
    def active(self) -> list[AreaDraft]:
        return [a for a in self.areas if a.status == "active"]

    def as_counts(self) -> dict[str, object]:
        active = self.active
        return {
            "insights_total": self.insights_total,
            "excluded_not_useful": self.excluded_not_useful,
            "clustered_input": len(self.items),
            "clusters": len(self.clusters),
            "noise": self.noise,
            "areas_active": len(active),
            "areas_emerging": sum(1 for a in active if a.agg.get("emerging")),
            "areas_emergent_category": sum(1 for a in active if a.category == "other_emergent"),
            "areas_archived": sum(1 for a in self.areas if a.status == "archived"),
            "areas_merged": len(self.stubs),
            "prior_run_id": self.prior_run_id,
            "clusters_matched_to_prior": sum(1 for c in self.clusters if c.prior),
            "clusters_labeled_by_llm": sum(1 for c in self.clusters if c.labeled_by == "llm"),
            "areas_synthesized": sum(1 for a in self.areas if a.synthesis),
            "uncited_sentences": sum(a.synthesis.uncited for a in self.areas if a.synthesis),
            "label_model": self.label_model,
            "synthesis_model": self.synthesis_model,
            "prompts": [self.label_prompt, self.synthesis_prompt],
            "label_errors": len(self.label_errors),
            "synthesis_errors": len(self.synthesis_errors),
            "batch_ids": self.batch_ids,
            "stopped_early": self.stopped_early,
        }


# --- the stage ---------------------------------------------------------------


def run_clustering(
    factory: sessionmaker[Session],
    cfg: AppConfig,
    run: StageRun,
    *,
    client: LLMClient | None = None,
    client_factory: Callable[[], LLMClient] | None = None,
    encoder: Encoder | None = None,
    reducer: Reducer | None = None,
    use_llm: bool = True,
    use_batch_api: bool | None = None,
    batch_id: str | None = None,
    estimate_only: bool = False,
) -> ClusterReport:
    settings = cfg.settings.clustering
    llm = cfg.settings.llm
    label_prompt = load_prompt(LABEL_PROMPT_NAME, LABEL_PROMPT_VERSION, cfg.prompts_dir)
    synth_prompt = load_prompt(SYNTHESIS_PROMPT_NAME, SYNTHESIS_PROMPT_VERSION, cfg.prompts_dir)
    report = ClusterReport(
        run_id=run.run_id,
        label_model=settings.resolve_model(settings.label_model, llm),
        synthesis_model=settings.resolve_model(settings.synthesis_model, llm),
        label_prompt=label_prompt.id,
        synthesis_prompt=synth_prompt.id,
        settings=settings,
    )

    with session_scope(factory) as session:
        items, report.excluded_not_useful = load_evidence(
            session, only_useful=settings.only_useful_for_discovery
        )
        report.prior_run_id, prior = load_prior(session, run.run_id)
        curation = load_curation(session)
    report.items = items
    report.insights_total = len(items) + report.excluded_not_useful
    if not items:
        run.counts.update(report.as_counts())
        return report

    encoder = encoder or SentenceEncoder(settings.embedding_model)
    vectors = encoder.embed(
        [insight_text(it.problem_statement, it.trying_to_find, it.breakdown_point) for it in items]
    )
    labels = cluster_labels(vectors, settings, reducer=reducer)
    report.labels = labels
    report.noise = int((labels == NOISE).sum())
    clusters = build_clusters(items, vectors, labels, settings.label_items)
    report.clusters = clusters
    matches = match_prior(
        {c.cluster_id: c.centroid for c in clusters}, prior, settings.match_cosine
    )
    for c in clusters:
        c.label = heuristic_label(c, items)
        if c.cluster_id in matches:
            p, sim = matches[c.cluster_id]
            c.prior = PriorRef(report.prior_run_id or "", p.cluster_id, p.area_id, sim)

    label_calls = [
        LLMCall(
            c.cluster_id,
            label_input([items[i] for i in c.representatives], c.extraction_categories, c.size),
            report.label_model,
            settings.label_max_output_tokens,
        )
        for c in clusters
    ]
    items_by_id = {it.item_id: it for it in items}
    category_names = cfg.taxonomy.categories

    if estimate_only:
        areas, _ = build_areas(clusters, run_id=run.run_id, settings=settings, curation=curation)
        finish_areas(areas, clusters, items, vectors, settings)
        report.areas = areas
        synth_inputs = [
            synthesis_request(a, items_by_id, category_names)[0]
            for a in areas
            if a.status == "active"
        ]
        remaining = None
        if llm.project_budget_usd is not None:
            remaining = llm.project_budget_usd - llm_spend_since(factory, llm.budget_since)
        report.estimate = CostEstimate(
            label=estimate_step(
                label_prompt,
                [c.user_input for c in label_calls],
                report.label_model,
                EST_LABEL_OUTPUT,
                settings.label_max_output_tokens,
                llm,
            ),
            synthesis=estimate_step(
                synth_prompt,
                synth_inputs,
                report.synthesis_model,
                EST_SYNTHESIS_OUTPUT,
                settings.synthesis_max_output_tokens,
                llm,
            ),
            batch_price_factor=llm.batch_price_factor,
            remaining_usd=remaining,
        )
        run.counts.update(report.as_counts())
        return report

    store_embeddings(factory, settings.embedding_model + INSIGHT_SUFFIX, items, vectors)

    if use_llm and clusters and client is None and client_factory is not None:
        client = client_factory()
    batch_ok = (
        use_batch_api
        if use_batch_api is not None
        else llm.use_batch_api_for_backfill and llm.provider == "anthropic"
    )
    resume = {"id": batch_id}

    def step(prompt: Prompt, calls: list[LLMCall], model_cls: type) -> CallResults:
        assert client is not None

        def on_submit(new_id: str) -> None:
            report.batch_ids.append(new_id)
            run.counts["batch_ids"] = list(report.batch_ids)
            save_counts(factory, run)
            log.info(
                "Message Batch %s submitted. If this command stops, collect it with "
                "`discovery cluster --batch-id %s`.",
                new_id,
                new_id,
            )

        batch = (batch_ok and len(calls) >= settings.batch_api_min_calls) or resume[
            "id"
        ] is not None
        result = run_calls(
            client,
            prompt,
            calls,
            model_cls,
            use_batch_api=batch,
            workers=llm.max_concurrency,
            poll_seconds=settings.batch_poll_seconds,
            max_wait_seconds=settings.batch_max_wait_minutes * 60,
            batch_id=resume["id"],
            on_submit=on_submit,
        )
        if result.batch_requests:
            resume["id"] = None
            if result.batch_id and result.batch_id not in report.batch_ids:
                report.batch_ids.append(result.batch_id)
        return result

    try:
        if client is not None and use_llm:
            report.llm_used = True
            labels_out = step(label_prompt, label_calls, ClusterLabel)
            for c in clusters:
                value = labels_out.values.get(c.cluster_id)
                if isinstance(value, ClusterLabel):
                    c.label, c.labeled_by = value, "llm"
            for key, message in sorted(labels_out.errors.items()):
                report.label_errors.append(f"{key}: {message}")
                run.error(f"cluster label {key}: {message}")
            if labels_out.stopped:
                report.stopped_early = labels_out.stopped

        areas, stubs = build_areas(
            clusters, run_id=run.run_id, settings=settings, curation=curation
        )
        finish_areas(areas, clusters, items, vectors, settings)
        report.areas, report.stubs = areas, stubs

        if client is not None and use_llm and report.stopped_early is None:
            requests = {
                a.area_id: synthesis_request(a, items_by_id, category_names)
                for a in areas
                if a.status == "active"
            }
            synth_out = step(
                synth_prompt,
                [
                    LLMCall(
                        area_id, text, report.synthesis_model, settings.synthesis_max_output_tokens
                    )
                    for area_id, (text, _) in requests.items()
                ],
                OpportunitySynthesis,
            )
            for area in areas:
                value = synth_out.values.get(area.area_id)
                if isinstance(value, OpportunitySynthesis):
                    area.synthesis = resolve_synthesis(value, requests[area.area_id][1])
            for key, message in sorted(synth_out.errors.items()):
                report.synthesis_errors.append(f"{key}: {message}")
                run.error(f"synthesis {key}: {message}")
            if synth_out.stopped:
                report.stopped_early = synth_out.stopped
    except LLMBatchPending as exc:
        report.stopped_early = str(exc)
        if exc.batch_id not in report.batch_ids:
            report.batch_ids.append(exc.batch_id)
        run.counts.update(report.as_counts())
        return report
    finally:
        if client is not None:
            usage = client.usage
            run.add_llm_usage(usage.total_tokens, usage.cost_usd)
            run.counts["llm_requests"] = usage.requests
            run.counts["llm_cache_hits"] = usage.cache_hits

    if report.stopped_early:
        run.error(f"stopped early: {report.stopped_early}")
    write_results(factory, report)
    report.written = True
    run.counts.update(report.as_counts())
    return report


# --- writes ------------------------------------------------------------------


def store_embeddings(
    factory: sessionmaker[Session], model_key: str, items: list[EvidenceItem], vectors: np.ndarray
) -> None:
    """Replace the insight vectors (recomputed every run: insights can change)."""
    with session_scope(factory) as session:
        session.execute(delete(EmbeddingRow).where(EmbeddingRow.model == model_key))
        rows = [
            EmbeddingRow(item_id=it.item_id, model=model_key, vector=pack_vector(vec))
            for it, vec in zip(items, vectors, strict=True)
        ]
        for start in range(0, len(rows), _WRITE_CHUNK):
            session.add_all(rows[start : start + _WRITE_CHUNK])
            session.flush()


def _area_row(area: AreaDraft, report: ClusterReport) -> OpportunityAreaRow:
    synth = area.synthesis
    named_by = "pm" if area.pm_name else ("llm" if synth else "heuristic")
    agg = {
        **area.agg,
        "ai_name": area.ai_name,
        "named_by": named_by,
        "quote_item_ids": area.quote_ids,
        "curation": area.curation,
        "label_model": report.label_model,
        "synthesis_model": report.synthesis_model if synth else None,
        "prompts": [report.label_prompt, report.synthesis_prompt],
    }
    if area.merged_into:
        agg["merged_into"] = area.merged_into
    if synth is not None:
        agg["summary_sentences"] = synth.summary
        agg["why_it_matters"] = synth.why_it_matters
        agg["uncited_sentences"] = synth.uncited
        agg["unknown_citations"] = synth.unknown_refs
    return OpportunityAreaRow(
        area_id=area.area_id,
        run_id=report.run_id,
        name=area.name,
        category=area.category,
        is_emergent=area.category == Category.OTHER_EMERGENT.value,
        problem_summary=synth.problem_summary if synth else None,
        aggregates=agg,
        research_questions=synth.research_questions if synth else [],
        status=area.status,
    )


def write_results(factory: sessionmaker[Session], report: ClusterReport) -> None:
    """Replace this run's clusters, areas, and evidence in one transaction."""
    run_id = report.run_id
    items = report.items
    with session_scope(factory) as session:
        for table in (OpportunityEvidenceRow, OpportunityAreaRow, ClusterRow):
            session.execute(delete(table).where(table.run_id == run_id))
        session.add_all(
            ClusterRow(
                cluster_id=c.cluster_id,
                run_id=run_id,
                area_id=c.home_area_id or c.area_id,
                label=c.label.name if c.label else None,
                summary=c.label.summary if c.label else None,
                mapped_category=c.category if c.label else None,
                centroid=pack_vector(c.centroid),
                size=c.size,
            )
            for c in report.clusters
        )
        session.add_all(_area_row(a, report) for a in [*report.areas, *report.stubs])
        session.flush()
        evidence = []
        for area in report.areas:
            rank = {item_id: n for n, item_id in enumerate(area.quote_ids, start=1)}
            for i in area.members:
                item_id = items[i].item_id
                evidence.append(
                    OpportunityEvidenceRow(
                        run_id=run_id,
                        area_id=area.area_id,
                        item_id=item_id,
                        is_representative=item_id in rank,
                        rank=rank.get(item_id),
                    )
                )
        for start in range(0, len(evidence), _WRITE_CHUNK):
            session.add_all(evidence[start : start + _WRITE_CHUNK])
            session.flush()


# --- PM curation (Gate G4) ---------------------------------------------------


def record_curation(
    factory: sessionmaker[Session],
    area_id: str,
    field_name: str,
    value: Any,
    *,
    note: str | None = None,
) -> tuple[int, str]:
    """Store one area decision in `pm_overrides`. Returns (override id, cluster run checked).

    Validated against the latest LLM-labeled cluster run, the run later runs inherit area
    ids from (`--no-llm` preview ids do not persist). `split_clusters` takes cluster ids of
    that run (such as "c03") and stores them as "run_id/c03"."""
    if field_name not in CURATION_FIELDS:
        raise ValueError(f"Unknown curation field {field_name!r}")
    with session_scope(factory) as session:
        run_id = latest_cluster_run(session, labeled_only=True)
        if run_id is None:
            hint = (
                " Only `--no-llm` previews exist; their area ids change every run."
                if latest_cluster_run(session) is not None
                else ""
            )
            raise ValueError(
                f"No LLM-labeled cluster run yet.{hint} Run `discovery cluster` first."
            )
        area = session.get(OpportunityAreaRow, (area_id, run_id))
        if area is None:
            raise ValueError(f"Area {area_id} is not in the latest cluster run {run_id}.")
        ai_value: Any = None
        if field_name == "name":
            ai_value = area.aggregates.get("ai_name", area.name)
        elif field_name == "status":
            if value not in ("active", "archived"):
                raise ValueError("status must be 'active' or 'archived'")
            ai_value = "active"
        elif field_name == "merge_into" and value:
            if value == area_id:
                raise ValueError("An area cannot be merged into itself.")
            if session.get(OpportunityAreaRow, (value, run_id)) is None:
                raise ValueError(f"Target area {value} is not in the latest cluster run {run_id}.")
        elif field_name == "split_clusters":
            own = {t["cluster_id"] for t in (area.aggregates or {}).get("sub_themes", [])}
            missing = [c for c in value if c not in own]
            if missing:
                raise ValueError(
                    f"Clusters {', '.join(missing)} are not in area {area_id} (it has "
                    f"{', '.join(sorted(own)) or 'none'})."
                )
            if len(own) - len(set(value)) < 1:
                raise ValueError("Leave at least one cluster in the area; archive it instead.")
            value = [f"{run_id}/{c}" for c in value]
        row = PMOverrideRow(
            target_type="area",
            target_id=area_id,
            field=field_name,
            ai_value=ai_value,
            override_value=value,
            note=note,
        )
        session.add(row)
        session.flush()
        return row.override_id, run_id


# --- report files ------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def _inline(counter: dict[str, int], k: int = 6, *, total: int | None = None) -> str:
    parts = []
    for name, count in list(counter.items())[:k]:
        parts.append(f"{name} {count}" + (f" ({_pct(count, total)})" if total else ""))
    return ", ".join(parts) or "none"


def _md(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def render_cluster_report(report: ClusterReport, category_names: dict[str, str]) -> str:
    s = report.settings
    lines = [
        "# Opportunity areas (draft)",
        "",
        f"Run: `{report.run_id}`. Prompts: `{report.label_prompt}`, `{report.synthesis_prompt}`. "
        f"Label model: `{report.label_model}`. Synthesis model: `{report.synthesis_model}`.",
        f"Clustering: UMAP n_neighbors={s.umap.n_neighbors}, n_components={s.umap.n_components}; "
        f"HDBSCAN min_cluster_size={s.hdbscan.min_cluster_size}, "
        f"min_samples={s.hdbscan.min_samples}, method={s.hdbscan.cluster_selection_method}; "
        f"area merge cosine ≥ {s.area_merge_cosine}.",
        "",
    ]
    if not report.llm_used:
        lines += [
            "> **Preview without the LLM.** Cluster names are the most central item's problem "
            "statement, categories are the extraction majority, and areas have no summary or "
            "research questions.",
            "",
        ]
    if report.stopped_early:
        lines += [f"> **Stopped early:** {report.stopped_early}", ""]
    n = len(report.items)
    active = report.active
    emerging = sum(1 for a in active if a.agg.get("emerging"))
    emergent = sum(1 for a in active if a.category == "other_emergent")
    lines += [
        "## Summary",
        "",
        f"- Insights: {report.insights_total:,}; clustered input: {n:,} (left out as not useful "
        f"for discovery: {report.excluded_not_useful:,})",
        f"- Clusters: {len(report.clusters)}; unclustered (noise): {report.noise:,} "
        f"({_pct(report.noise, n)})",
        f"- Active areas: {len(active)} ({emerging} emerging with < {s.min_area_items} items; "
        f"{emergent} emergent category)",
    ]
    if report.prior_run_id:
        matched = sum(1 for c in report.clusters if c.prior)
        lines.append(
            f"- Matched to the previous run `{report.prior_run_id}`: {matched} of "
            f"{len(report.clusters)} clusters kept their area"
        )
    curated = [a for a in [*report.areas, *report.stubs] if a.curation]
    if curated:
        lines.append(
            "- PM curation applied: "
            + "; ".join(f"`{a.area_id}` {', '.join(a.curation)}" for a in curated)
        )
    if report.llm_used:
        uncited = sum(a.synthesis.uncited for a in report.areas if a.synthesis)
        lines.append(f"- Synthesis sentences without a valid citation (flagged ⚠): {uncited}")
    lines += ["", *_taxonomy_section(report, category_names), ""]

    by_id = {it.item_id: it for it in report.items}
    lines += ["## Areas", ""]
    for rank, area in enumerate(report.areas, start=1):
        lines += _area_section(rank, area, by_id, category_names)
    if report.stubs:
        lines += ["## Merged areas", ""]
        lines += [f"- `{a.area_id}` {a.name or ''} → `{a.merged_into}`" for a in report.stubs]
        lines.append("")
    if report.labels is not None and report.noise:
        noise_cats = Counter(
            it.primary_category or "none"
            for it, lab in zip(report.items, report.labels.tolist(), strict=True)
            if lab == NOISE
        )
        lines += [
            "## Unclustered items",
            "",
            f"{report.noise} items fit no cluster. They stay in `insights` (Evidence Explorer). "
            f"By extraction category: {_inline(dict(noise_cats.most_common()), 9)}.",
            "",
        ]
    errors = report.label_errors + report.synthesis_errors
    if errors:
        lines += ["## Errors", "", *[f"- {e}" for e in errors], ""]
    return "\n".join(lines)


def _taxonomy_section(report: ClusterReport, category_names: dict[str, str]) -> list[str]:
    active = report.active
    clusters = report.clusters
    input_cats = Counter(it.primary_category or "none" for it in report.items)
    lines = [
        "## Taxonomy check",
        "",
        "Does the data support the 8 starting categories? Clusters are mapped to a category by "
        "the label model; `other_emergent` marks problems the taxonomy missed.",
        "",
        "| Category | Active areas | Clusters | Items in areas | Items (extraction category) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key, display in category_names.items():
        areas = [a for a in active if a.category == key]
        lines.append(
            f"| {display} | {len(areas)} | {sum(1 for c in clusters if c.category == key)} | "
            f"{sum(a.size for a in areas)} | {input_cats.get(key, 0)} |"
        )
    missing = [
        category_names[k] for k in category_names if not any(c.category == k for c in clusters)
    ]
    if missing:
        lines += ["", f"No cluster maps to: {', '.join(missing)}."]
    differ = []
    for c in clusters:
        top, count = c.extraction_categories.most_common(1)[0]
        if top != c.category:
            differ.append(
                f"- `{c.cluster_id}` {_md(c.label.name)}: label `{c.category}`, extraction "
                f"majority `{top}` ({count}/{c.size})"
            )
    if differ:
        lines += ["", "Clusters whose category differs from the per-item extraction majority:", ""]
        lines += differ
    return lines


def _area_section(
    rank: int, area: AreaDraft, by_id: dict[str, EvidenceItem], category_names: dict[str, str]
) -> list[str]:
    agg = area.agg
    n = agg["items"]
    flags = []
    if area.status != "active":
        flags.append(area.status.upper())
    if agg.get("emerging"):
        flags.append("Emerging: fewer than the minimum items")
    if area.category == "other_emergent":
        flags.append("Emergent category")
    if agg.get("few_quotes"):
        flags.append(f"Only {len(area.quote_ids)} grounded quotes")
    lines = [
        f"### {rank}. Opportunity Area: {area.name}",
        "",
        f"`{area.area_id}` · {category_names.get(area.category, area.category)} · {n} items "
        f"({agg['vague_items']} vague memory, {agg['general_items']} general)"
        + (f" · **{'; '.join(flags)}**" if flags else ""),
        "",
    ]
    if area.pm_name and area.pm_name != area.ai_name:
        lines += [f"AI name: {area.ai_name}", ""]
    synth = area.synthesis
    lines += ["**Problem Summary**", ""]
    if synth:
        for sent in synth.summary:
            lines.append(_cited(sent))
    else:
        lines.append("_Not synthesized (no LLM call)._")
    lines += ["", "**Sub-themes**", ""]
    lines += [
        f"- {_md(t['label'])} ({t['size']} items, `{t['cluster_id']}`)" for t in agg["sub_themes"]
    ]
    lines += [
        "",
        f"**Source breakdown:** {_inline(agg['sources'], total=n)}. Platforms: "
        f"{_inline(agg['platforms'], total=n)}.",
        "",
        f"**Content types:** {_inline(agg['content_types'], total=n)}.",
        "",
        "**What Users Remember**",
        "",
        f"Cue types (items): {_inline(agg['cue_types'], 8)}. Items with any cue: "
        f"{agg['items_with_cues']} of {n}.",
        "",
    ]
    lines += [
        f'- "{_md(c["examples"][0])}"' + (f" ({c['count']} items)" if c["count"] > 1 else "")
        for c in agg["top_cues"][:6]
    ]
    lines += [
        "",
        "**What Users Forget**",
        "",
        f"{_inline(agg['forgotten'], 8)}" if agg["forgotten"] else "Not stated.",
        "",
        "**Common Search Attempts**",
        "",
    ]
    if agg["attempt_types"]:
        for kind, count in list(agg["attempt_types"].items())[:5]:
            examples = "; ".join(f'"{_md(e)}"' for e in agg["attempt_examples"].get(kind, [])[:2])
            lines.append(f"- {kind} ({count} items)" + (f": {examples}" if examples else ""))
    else:
        lines.append("Not stated.")
    lines += [
        "",
        "**Breakdown Point**",
        "",
        f"Dominant: **{agg['dominant_breakdown']}**. All: {_inline(agg['breakdown'], 9)}.",
        "",
        "**Representative Quotes**",
        "",
    ]
    for item_id in area.quote_ids:
        it = by_id[item_id]
        lines.append(
            f'> "{_md(it.evidence_quote)}"  \n> — {it.source}, {it.platform}, strength '
            f"{it.evidence_strength} · [source]({it.source_url}) · `{item_id[:8]}`"
        )
        lines.append("")
    if not area.quote_ids:
        lines += ["No grounded quotes.", ""]
    lines += ["**Why This Matters**", ""]
    lines.append(_cited(synth.why_it_matters) if synth else "_Not synthesized._")
    lines += ["", "**Follow-up Research Questions**", ""]
    if synth:
        for k, q in enumerate(synth.research_questions, start=1):
            cites = ", ".join(f"`{i[:8]}`" for i in q["item_ids"])
            lines.append(
                f"{k}. {_md(q['question'])}  \n   _Gap:_ {_md(q['evidence_gap'])}"
                + (f" ({cites})" if cites else "")
            )
    else:
        lines.append("_Not synthesized._")
    lines += [
        "",
        f"Severity inputs: mean frustration {agg['mean_frustration']}, high-stakes share "
        f"{agg['high_stakes_share']}, low-rating share {agg['low_rating_share']} "
        f"({agg['rated_items']} rated). Mean evidence strength {agg['mean_evidence_strength']}.",
        "",
        "---",
        "",
    ]
    return lines


def _cited(sentence: dict[str, Any]) -> str:
    cites = ", ".join(f"`{i[:8]}`" for i in sentence["item_ids"])
    flag = " ⚠ _uncited_" if sentence["flagged"] else ""
    return f"{_md(sentence['text'])}" + (f" [{cites}]" if cites else "") + flag


REVIEW_FIELDS = (
    "rank",
    "area_id",
    "name",
    "category",
    "status",
    "items",
    "vague_items",
    "sources",
    "sub_themes",
    "problem_summary",
    "dominant_breakdown",
    "top_cues",
    "quotes",
    "research_questions",
    "coherence_1_5",
    "specific_not_generic",
    "action",
    "action_detail",
    "pm_notes",
)


def _cell(value: object) -> str:
    """CSV-safe text: a leading = + - @ would run as a formula in Sheets or Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def write_review_sheet(path: Path, report: ClusterReport) -> int:
    """Gate G4 sheet: one row per area, with blank columns for the PM's coherence score
    (1-5), specificity verdict, and action (keep / rename / merge / split / archive)."""
    by_id = {it.item_id: it for it in report.items}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REVIEW_FIELDS))
        writer.writeheader()
        for rank, area in enumerate(report.areas, start=1):
            agg = area.agg
            synth = area.synthesis
            writer.writerow(
                {
                    "rank": rank,
                    "area_id": area.area_id,
                    "name": _cell(area.name),
                    "category": area.category,
                    "status": area.status,
                    "items": agg["items"],
                    "vague_items": agg["vague_items"],
                    "sources": _inline(agg["sources"]),
                    "sub_themes": _cell(
                        " | ".join(
                            f"{t['cluster_id']}: {t['label']} ({t['size']})"
                            for t in agg["sub_themes"]
                        )
                    ),
                    "problem_summary": _cell(synth.problem_summary if synth else ""),
                    "dominant_breakdown": agg["dominant_breakdown"],
                    "top_cues": _cell("; ".join(c["examples"][0] for c in agg["top_cues"][:5])),
                    "quotes": _cell(
                        " || ".join(
                            f"{by_id[i].evidence_quote} ({by_id[i].source_url})"
                            for i in area.quote_ids
                        )
                    ),
                    "research_questions": _cell(
                        " | ".join(q["question"] for q in synth.research_questions) if synth else ""
                    ),
                }
            )
    return len(report.areas)
