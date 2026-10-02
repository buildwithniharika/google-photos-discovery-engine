"""Pre-aggregated views so pages never load the whole corpus into memory (P7.10).

`v_published_areas` is one row per scored area on the published run.
`v_published_evidence` is one row per evidence item on that same run, joined to the
item, relevance, and insight columns the filters need. Both views follow the
`published_run` pointer, so a half-finished run is not visible.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Engine

_AREAS = """
CREATE VIEW v_published_areas AS
SELECT
    a.area_id AS area_id,
    a.run_id AS run_id,
    a.name AS name,
    a.category AS category,
    a.is_emergent AS is_emergent,
    a.problem_summary AS problem_summary,
    a.status AS status,
    a.aggregates AS aggregates,
    a.research_questions AS research_questions,
    s.frequency AS frequency,
    s.severity AS severity,
    s.strategic_fit AS strategic_fit,
    s.evidence_quality AS evidence_quality,
    s.product_leverage AS product_leverage,
    s.research_value AS research_value,
    s.composite AS composite,
    s.band AS band,
    s.inputs AS inputs,
    s.weights AS weights,
    s.low_evidence_flag AS low_evidence_flag
FROM opportunity_areas AS a
JOIN opportunity_scores AS s
    ON s.area_id = a.area_id AND s.run_id = a.run_id
JOIN published_run AS p
    ON p.id = 1 AND p.published_run_id = a.run_id
"""

_EVIDENCE = """
CREATE VIEW v_published_evidence AS
SELECT
    e.run_id AS run_id,
    e.area_id AS area_id,
    a.name AS area_name,
    e.item_id AS item_id,
    e.is_representative AS is_representative,
    e.rank AS quote_rank,
    i.primary_source_name AS source,
    i.platform AS platform,
    i.source_url AS source_url,
    i.clean_text AS clean_text,
    i.language AS language,
    i.date AS date,
    i.rating AS rating,
    i.is_spam AS is_spam,
    r.retrieval_type AS retrieval_type,
    r.vague_memory_relevance AS vague_memory_relevance,
    r.confidence AS relevance_confidence,
    ins.content_type AS content_type,
    ins.primary_category AS primary_category,
    ins.frustration_intensity AS frustration_intensity,
    ins.high_stakes AS high_stakes,
    ins.confidence AS insight_confidence,
    ins.evidence_quote AS evidence_quote,
    ins.quote_grounded AS quote_grounded,
    ins.problem_statement AS problem_statement,
    ins.user_reported_issue AS user_reported_issue,
    ins.trying_to_find AS trying_to_find,
    ins.breakdown_point AS breakdown_point,
    ins.emotion AS emotion,
    ins.outcome AS outcome
FROM opportunity_evidence AS e
JOIN published_run AS p
    ON p.id = 1 AND p.published_run_id = e.run_id
JOIN opportunity_areas AS a
    ON a.area_id = e.area_id AND a.run_id = e.run_id
JOIN items AS i
    ON i.item_id = e.item_id
LEFT JOIN relevance AS r
    ON r.item_id = e.item_id
LEFT JOIN insights AS ins
    ON ins.item_id = e.item_id
"""


@dataclass(frozen=True)
class DashboardViews:
    areas: Table
    evidence: Table


def ensure_views(engine: Engine) -> None:
    """Drop and recreate so a schema tweak is picked up on the next app start."""
    with engine.begin() as conn:
        conn.execute(text("DROP VIEW IF EXISTS v_published_evidence"))
        conn.execute(text("DROP VIEW IF EXISTS v_published_areas"))
        conn.execute(text(_AREAS))
        conn.execute(text(_EVIDENCE))


def load_views(engine: Engine) -> DashboardViews:
    ensure_views(engine)
    meta = MetaData()
    return DashboardViews(
        areas=Table("v_published_areas", meta, autoload_with=engine),
        evidence=Table("v_published_evidence", meta, autoload_with=engine),
    )
