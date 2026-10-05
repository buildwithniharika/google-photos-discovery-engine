"""Read the published run and write PM overrides.

Queries are keyed by `published_run_id` (and the latest override id) so a new publish
or a correction refreshes the cache. Opportunity pages read `v_published_*`, which
already exclude every run except the published one.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

import streamlit as st
from sqlalchemy import and_, case, distinct, event, func, or_, select
from sqlalchemy.orm import Session

from dashboard.filters import Filters
from dashboard.labels import DIMENSIONS, LOW_CONFIDENCE, PAGE_SIZE, breakdown_order
from dashboard.views import DashboardViews, load_views
from discovery.config import PROJECT_ROOT, DatabaseSettings, resolve_database_url
from discovery.db import init_db as create_tables
from discovery.db import make_engine, make_session_factory, redact_url, session_scope, wait_for_db
from discovery.models.orm import (
    InsightRow,
    ItemRow,
    PipelineRunRow,
    PMOverrideRow,
    PublishedRunRow,
    RawItemRow,
    RelevanceRow,
)

ITEM_FIELDS = (
    "primary_category",
    "retrieval_type",
    "vague_memory_relevance",
    "marked_irrelevant",
)
AREA_FIELDS = (
    "name",
    "status",
    "merge_into",
    "split_clusters",
    "pm_note",
    "product_leverage",
    "research_value",
)
_VAGUE = "vague_memory_retrieval"
_GENERAL = "general_retrieval"
_RETRIEVAL = (_GENERAL, _VAGUE)


def resolve_url() -> str:
    """`st.secrets` on Community Cloud, else `.env` / the local SQLite file."""
    secret = _secret("DATABASE_URL")
    if secret:
        os.environ["DATABASE_URL"] = secret
    else:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env", override=False)
    return resolve_database_url(DatabaseSettings())


def _secret(key: str) -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        return ""
    return ""


def _limit_postgres(engine) -> None:
    """Give up instead of spinning if Neon or a lock does not answer.

    Applied on the connection, not as arguments to make_engine, because the
    deployed app can import an older copy of that function.
    """
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine, "connect")
    def _set_limits(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET statement_timeout = '20s'")
        cursor.execute("SET lock_timeout = '8s'")
        cursor.close()
        if not getattr(dbapi_conn, "autocommit", True):
            dbapi_conn.commit()


@st.cache_resource(show_spinner=False)
def resources() -> dict[str, Any]:
    """One pooled engine per process, plus the published-run views."""
    url = resolve_url()
    engine = make_engine(url)
    _limit_postgres(engine)
    wait_for_db(engine)
    create_tables(engine)
    return {"engine": engine, "views": load_views(engine), "url": redact_url(url)}


def engine():
    return resources()["engine"]


def views() -> DashboardViews:
    return resources()["views"]


def clear_query_cache() -> None:
    st.cache_data.clear()


def _session():
    return session_scope(make_session_factory(engine()))


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _bool(value: Any) -> bool:
    return bool(value) and value not in ("false", "False", "0")


# --- published run and banner -------------------------------------------------


def read_published(session: Session) -> dict[str, Any] | None:
    row = session.get(PublishedRunRow, 1)
    if row is None or not row.published_run_id:
        return None
    return {
        "run_id": row.published_run_id,
        "published_at": _aware(row.published_at),
    }


def _first_error(stages: list) -> str:
    for stage in stages:
        for error in stage.errors or []:
            message = error.get("message") if isinstance(error, dict) else str(error)
            if message:
                return str(message)[:300]
    return ""


def override_token(session: Session) -> int:
    return int(session.scalar(select(func.max(PMOverrideRow.override_id))) or 0)


def read_banner(session: Session, published: dict[str, Any] | None) -> dict[str, str] | None:
    """A newer run that is still going, or that failed, must not replace the published one."""
    if published is None:
        return {
            "kind": "empty",
            "text": (
                "No published run yet. Score a run with `discovery score` and the "
                "dashboard will show that run only."
            ),
        }
    published_at = published["published_at"]
    rows = session.scalars(select(PipelineRunRow)).all()
    newer: dict[str, list[PipelineRunRow]] = defaultdict(list)
    for row in rows:
        if row.run_id == published["run_id"]:
            continue
        started = _aware(row.started_at)
        if published_at and started and started > published_at:
            newer[row.run_id].append(row)
    if not newer:
        return None

    def _started(run_id: str) -> datetime:
        floor = datetime.min.replace(tzinfo=UTC)
        stamps = [_aware(row.started_at) or floor for row in newer[run_id]]
        return max(stamps)

    latest = max(newer, key=_started)
    stages = newer[latest]
    if any(stage.status == "running" for stage in stages):
        return {
            "kind": "running",
            "text": (
                f"A pipeline run is in progress ({latest}). You are seeing the last "
                "published run. No partial results are shown."
            ),
        }
    if any(stage.status in ("failed", "partial") for stage in stages):
        reason = _first_error(stages)
        text = (
            "A newer pipeline run did not finish. You are still seeing the last "
            "published run. No partial results are shown."
        )
        if reason:
            text = f"{text} {reason}"
        return {"kind": "failed", "text": text}
    if not any(stage.stage == "score" and stage.status == "published" for stage in stages):
        return {
            "kind": "running",
            "text": (
                f"A newer run ({latest}) has not been published. You are seeing the "
                "last published run."
            ),
        }
    return None


# --- overrides ----------------------------------------------------------------


def latest_overrides(session: Session, target_type: str) -> dict[tuple[str, str], dict]:
    """Latest value per target and field. Later rows replace earlier ones."""
    rows = session.scalars(
        select(PMOverrideRow)
        .where(PMOverrideRow.target_type == target_type)
        .order_by(PMOverrideRow.override_id)
    ).all()
    found: dict[tuple[str, str], dict] = {}
    for row in rows:
        found[(row.target_id, row.field)] = {
            "value": row.override_value,
            "ai_value": row.ai_value,
            "note": row.note,
            "override_id": row.override_id,
        }
    return found


def _area_or_raise(session: Session, run_id: str, area_id: str):
    from discovery.models.orm import OpportunityAreaRow

    area = session.get(OpportunityAreaRow, (area_id, run_id))
    if area is None:
        raise ValueError(f"Area {area_id} is not in the published run.")
    return area


def _validate(
    session: Session, run_id: str, target_type: str, target_id: str, field: str, value: Any
) -> Any:
    if target_type == "item":
        if field not in ITEM_FIELDS:
            raise ValueError(f"Items can be corrected on {', '.join(ITEM_FIELDS)}.")
        return value
    if field not in AREA_FIELDS:
        raise ValueError(f"Unknown area field {field}.")
    if field == "merge_into" and value:
        if value == target_id:
            raise ValueError("An area cannot be merged into itself.")
        _area_or_raise(session, run_id, str(value))
    elif field == "status" and value not in ("active", "archived", "", None):
        raise ValueError("Status must be active or archived.")
    elif field == "split_clusters" and value:
        area = _area_or_raise(session, run_id, target_id)
        own = {theme["cluster_id"] for theme in (area.aggregates or {}).get("sub_themes", [])}
        chosen = [str(item) for item in value]
        missing = [item for item in chosen if item.split("/")[-1] not in own and item not in own]
        if missing:
            raise ValueError(
                f"Clusters {', '.join(missing)} are not in this area "
                f"({', '.join(sorted(own)) or 'none'})."
            )
        bare = [item.split("/")[-1] for item in chosen]
        if len(own) - len(set(bare)) < 1:
            raise ValueError("Leave at least one cluster in the area. Archive it instead.")
        return [item if "/" in item else f"{run_id}/{item}" for item in chosen]
    elif field in ("product_leverage", "research_value") and value not in ("", None):
        number = float(value)
        if not 1 <= number <= 5:
            raise ValueError("Scores are between 1 and 5.")
        return round(number, 2)
    elif field == "name" and not str(value or "").strip():
        raise ValueError("The area name cannot be empty.")
    return value


def write_override(
    *,
    target_type: str,
    target_id: str,
    field: str,
    ai_value: Any,
    override_value: Any,
    note: str | None = None,
    run_id: str | None = None,
    clear_cache: bool = True,
    session_factory=None,
) -> int:
    """Append one correction. The AI value stays on the original row; this table wins."""
    if target_type not in ("item", "area"):
        raise ValueError("target_type must be item or area.")
    factory = session_factory or make_session_factory(engine())
    with session_scope(factory) as session:
        published = read_published(session)
        bound_run = run_id or (published["run_id"] if published else None)
        if target_type == "area":
            if not bound_run:
                raise ValueError("There is no published run to correct.")
            override_value = _validate(
                session, bound_run, target_type, target_id, field, override_value
            )
        else:
            override_value = _validate(
                session, bound_run or "", target_type, target_id, field, override_value
            )
        row = PMOverrideRow(
            target_type=target_type,
            target_id=target_id,
            field=field,
            ai_value=ai_value,
            override_value=override_value,
            note=(note or "").strip() or None,
        )
        session.add(row)
        session.flush()
        new_id = int(row.override_id)
    if clear_cache:
        clear_query_cache()
    return new_id


# --- filter clauses -----------------------------------------------------------


def _selected_clause(column, item_column, selected: tuple, field: str, overrides: dict):
    """AI value in the selection, plus items whose override moves them in or out."""
    if not selected:
        return []
    chosen = set(selected)
    include: set[str] = set()
    exclude: set[str] = set()
    for (item_id, name), payload in overrides.items():
        if name != field:
            continue
        if payload["value"] in chosen:
            include.add(item_id)
        else:
            exclude.add(item_id)
    include -= exclude
    clause = column.in_(tuple(selected))
    if include:
        clause = or_(clause, item_column.in_(tuple(include)))
    clauses = [clause]
    if exclude:
        clauses.append(item_column.notin_(tuple(exclude)))
    return clauses


def _irrelevant_ids(overrides: dict, *, show: bool) -> set[str]:
    if show:
        return set()
    return {
        item_id
        for (item_id, field), payload in overrides.items()
        if field == "marked_irrelevant" and payload["value"] is True
    }


def _confidence_clause(rel_col, ins_col, minimum: float):
    """Minimum of the two confidences, so a weak stage cannot hide behind a strong one."""
    if minimum <= 0:
        return None
    lesser = case(
        (and_(rel_col.is_not(None), ins_col.is_not(None), rel_col <= ins_col), rel_col),
        (and_(rel_col.is_not(None), ins_col.is_not(None)), ins_col),
        else_=func.coalesce(rel_col, ins_col),
    )
    return lesser >= minimum


def evidence_clauses(table, filters: Filters, overrides: dict) -> list:
    """WHERE fragments for `v_published_evidence`. Spam is always out."""
    column = table.c
    clauses: list = [column.is_spam.is_(False)]
    clauses += _common_item_clauses(column, filters)
    clauses += _insight_clauses(column, column.item_id, filters, overrides)
    hidden = _irrelevant_ids(overrides, show=filters.show_irrelevant)
    if hidden:
        clauses.append(column.item_id.notin_(tuple(hidden)))
    return clauses


def _common_item_clauses(column, filters: Filters) -> list:
    clauses: list = []
    if filters.sources:
        clauses.append(column.source.in_(filters.sources))
    if filters.platforms:
        clauses.append(column.platform.in_(filters.platforms))
    if filters.languages:
        clauses.append(column.language.in_(filters.languages))
    if filters.date_start:
        clauses.append(func.date(column.date) >= filters.date_start.isoformat())
    if filters.date_end:
        clauses.append(func.date(column.date) <= filters.date_end.isoformat())
    narrowed = filters.rating_min > 1 or filters.rating_max < 5 or not filters.include_unrated
    if narrowed:
        rated = column.rating.between(filters.rating_min, filters.rating_max)
        if filters.include_unrated:
            clauses.append(or_(column.rating.is_(None), rated))
        else:
            clauses.append(rated)
    return clauses


def _scope_clause(retrieval_col, item_col, filters: Filters, overrides: dict):
    if filters.retrieval_scope == "vague":
        chosen = (_VAGUE,)
    elif filters.retrieval_scope == "general":
        chosen = (_GENERAL,)
    else:
        chosen = ()
    return _selected_clause(retrieval_col, item_col, chosen, "retrieval_type", overrides)


def _vague_floor(vague_col, item_col, filters: Filters, overrides: dict):
    if filters.vague_min <= 0:
        return []
    include: set[str] = set()
    exclude: set[str] = set()
    for (item_id, field), payload in overrides.items():
        if field != "vague_memory_relevance":
            continue
        if float(payload["value"]) >= filters.vague_min:
            include.add(item_id)
        else:
            exclude.add(item_id)
    include -= exclude
    clause = vague_col >= filters.vague_min
    if include:
        clause = or_(clause, item_col.in_(tuple(include)))
    clauses = [clause]
    if exclude:
        clauses.append(item_col.notin_(tuple(exclude)))
    return clauses


def _insight_clauses(column, item_col, filters: Filters, overrides: dict) -> list:
    clauses: list = []
    clauses += _scope_clause(column.retrieval_type, item_col, filters, overrides)
    clauses += _vague_floor(column.vague_memory_relevance, item_col, filters, overrides)
    clauses += _selected_clause(
        column.primary_category, item_col, filters.categories, "primary_category", overrides
    )
    clauses += _selected_clause(
        column.content_type, item_col, filters.content_types, "content_type", overrides
    )
    if filters.severity_min > 1 or filters.severity_max < 5:
        clauses.append(
            column.frustration_intensity.between(filters.severity_min, filters.severity_max)
        )
    if filters.high_stakes_only:
        clauses.append(column.high_stakes.is_(True))
    confidence = _confidence_clause(
        column.relevance_confidence, column.insight_confidence, filters.min_confidence
    )
    if confidence is not None:
        clauses.append(confidence)
    return clauses


def _corpus_clauses(filters: Filters, overrides: dict, *, stage: str) -> list:
    """Filters on the items/relevance/insights tables, for the overview funnel."""
    clauses: list = []
    if stage != "items":
        clauses.append(ItemRow.is_spam.is_(False))
    if stage == "ready":
        clauses.append(ItemRow.clean_text.is_not(None))
    if filters.sources:
        clauses.append(ItemRow.primary_source_name.in_(filters.sources))
    if filters.platforms:
        clauses.append(ItemRow.platform.in_(filters.platforms))
    if filters.languages:
        clauses.append(ItemRow.language.in_(filters.languages))
    if filters.date_start:
        clauses.append(func.date(ItemRow.date) >= filters.date_start.isoformat())
    if filters.date_end:
        clauses.append(func.date(ItemRow.date) <= filters.date_end.isoformat())
    narrowed = filters.rating_min > 1 or filters.rating_max < 5 or not filters.include_unrated
    if narrowed:
        rated = ItemRow.rating.between(filters.rating_min, filters.rating_max)
        clauses.append(or_(ItemRow.rating.is_(None), rated) if filters.include_unrated else rated)
    if stage in ("retrieval", "vague"):
        if stage == "vague":
            scope = Filters(**{**filters.__dict__, "retrieval_scope": "vague"})
        else:
            scope = filters
        clauses += _scope_clause(RelevanceRow.retrieval_type, ItemRow.item_id, scope, overrides)
        if stage == "retrieval" and filters.retrieval_scope == "all":
            clauses.append(RelevanceRow.retrieval_type.in_(_RETRIEVAL))
        clauses += _vague_floor(
            RelevanceRow.vague_memory_relevance, ItemRow.item_id, filters, overrides
        )
        clauses += _selected_clause(
            InsightRow.primary_category,
            ItemRow.item_id,
            filters.categories,
            "primary_category",
            overrides,
        )
        clauses += _selected_clause(
            InsightRow.content_type,
            ItemRow.item_id,
            filters.content_types,
            "content_type",
            overrides,
        )
        if filters.severity_min > 1 or filters.severity_max < 5:
            clauses.append(
                InsightRow.frustration_intensity.between(filters.severity_min, filters.severity_max)
            )
        if filters.high_stakes_only:
            clauses.append(InsightRow.high_stakes.is_(True))
        confidence = _confidence_clause(
            RelevanceRow.confidence, InsightRow.confidence, filters.min_confidence
        )
        if confidence is not None:
            clauses.append(confidence)
        hidden = _irrelevant_ids(overrides, show=filters.show_irrelevant)
        if hidden:
            clauses.append(ItemRow.item_id.notin_(tuple(hidden)))
    return clauses


def _count_corpus(session: Session, filters: Filters, overrides: dict, stage: str) -> int:
    stmt = select(func.count(distinct(ItemRow.item_id))).select_from(ItemRow)
    if stage in ("retrieval", "vague"):
        stmt = stmt.outerjoin(RelevanceRow, RelevanceRow.item_id == ItemRow.item_id).outerjoin(
            InsightRow, InsightRow.item_id == ItemRow.item_id
        )
    clauses = _corpus_clauses(filters, overrides, stage=stage)
    return int(session.scalar(stmt.where(*clauses)) or 0)


# --- areas, funnel, evidence --------------------------------------------------


def _decorate_area(row: dict, overrides: dict) -> dict:
    area_id = row["area_id"]
    name_ai = (_json(row.get("aggregates"), {}) or {}).get("ai_name") or row["name"]
    name = row["name"]
    name_over = overrides.get((area_id, "name"))
    if name_over and name_over["value"]:
        name = str(name_over["value"])
    status = row["status"]
    status_over = overrides.get((area_id, "status"))
    if status_over and status_over["value"]:
        status = str(status_over["value"])
    out = dict(row)
    out["name"] = name
    out["name_ai"] = name_ai
    out["name_overridden"] = bool(name_over and name_over["value"] and name != name_ai)
    out["status"] = status
    out["status_overridden"] = bool(status_over and status_over["value"])
    out["aggregates"] = _json(row.get("aggregates"), {})
    out["research_questions"] = _json(row.get("research_questions"), [])
    out["inputs"] = _json(row.get("inputs"), {})
    out["weights"] = _json(row.get("weights"), {})
    out["low_evidence_flag"] = _bool(row.get("low_evidence_flag"))
    out["is_emergent"] = _bool(row.get("is_emergent"))
    for key in DIMENSIONS:
        out[key] = float(row[key])
        prior = overrides.get((area_id, key))
        out[f"{key}_ai"] = float(row[key])
        out[f"{key}_overridden"] = False
        if prior and prior["value"] not in (None, ""):
            out[key] = float(prior["value"])
            ai_score = prior["ai_value"] if prior["ai_value"] is not None else row[key]
            out[f"{key}_ai"] = float(ai_score)
            out[f"{key}_overridden"] = True
    merge = overrides.get((area_id, "merge_into"))
    out["merge_into"] = str(merge["value"]) if merge and merge["value"] else None
    split = overrides.get((area_id, "split_clusters"))
    out["split_pending"] = list(split["value"]) if split and split["value"] else []
    note = overrides.get((area_id, "pm_note"))
    out["pm_note"] = str(note["value"]) if note and note["value"] else ""
    out["pm_note_overridden"] = bool(out["pm_note"])
    return out


def read_areas(
    session: Session,
    dashboard_views: DashboardViews,
    filters: Filters,
    area_overrides: dict,
    item_overrides: dict,
    *,
    active_only: bool = True,
) -> list[dict]:
    table = dashboard_views.areas
    rows = [dict(row._mapping) for row in session.execute(select(table)).all()]
    counts = _match_counts(session, dashboard_views, filters, item_overrides)
    areas = []
    for row in rows:
        area = _decorate_area(row, area_overrides)
        matched = counts.get(area["area_id"], {"n": 0, "vague": 0})
        area["match_count"] = matched["n"]
        area["vague_match_count"] = matched["vague"]
        if active_only and area["status"] != "active":
            continue
        if filters.active_count() and area["match_count"] == 0:
            continue
        areas.append(area)
    return areas


def _match_counts(session, dashboard_views, filters: Filters, overrides: dict) -> dict[str, dict]:
    table = dashboard_views.evidence
    vague = func.sum(case((table.c.retrieval_type == _VAGUE, 1), else_=0))
    stmt = (
        select(table.c.area_id, func.count(), vague)
        .where(*evidence_clauses(table, filters, overrides))
        .group_by(table.c.area_id)
    )
    return {
        area_id: {"n": int(n), "vague": int(vague_n or 0)}
        for area_id, n, vague_n in session.execute(stmt).all()
    }


def read_overview(session: Session, filters: Filters, overrides: dict, area_count: int) -> dict:
    raw_stmt = select(func.count()).select_from(RawItemRow)
    if filters.sources:
        raw_stmt = raw_stmt.where(RawItemRow.source_name.in_(filters.sources))
    raw_comparable = not any(
        (
            filters.platforms,
            filters.languages,
            filters.date_start,
            filters.date_end,
            filters.rating_min > 1 or filters.rating_max < 5,
            not filters.include_unrated,
        )
    )
    dialect = session.get_bind().dialect.name
    month = (
        func.strftime("%Y-%m", ItemRow.date)
        if dialect == "sqlite"
        else func.to_char(ItemRow.date, "YYYY-MM")
    )
    trend_where = _corpus_clauses(filters, overrides, stage="ready")
    trend = session.execute(
        select(month, ItemRow.primary_source_name, func.count())
        .where(*trend_where, ItemRow.date.is_not(None))
        .group_by(month, ItemRow.primary_source_name)
        .order_by(month)
    ).all()
    categories = _category_counts(session, filters, overrides)
    return {
        "raw": int(session.scalar(raw_stmt) or 0) if raw_comparable else None,
        "items": _count_corpus(session, filters, overrides, "items"),
        "ready": _count_corpus(session, filters, overrides, "ready"),
        "retrieval": _count_corpus(session, filters, overrides, "retrieval"),
        "vague": _count_corpus(session, filters, overrides, "vague"),
        "areas": area_count,
        "trend": [
            {"month": month_key or "undated", "source": source, "n": int(n)}
            for month_key, source, n in trend
        ],
        "categories": categories,
    }


def _category_counts(session: Session, filters: Filters, overrides: dict) -> list[dict]:
    rows = session.execute(
        select(ItemRow.item_id, InsightRow.primary_category)
        .join(InsightRow, InsightRow.item_id == ItemRow.item_id)
        .outerjoin(RelevanceRow, RelevanceRow.item_id == ItemRow.item_id)
        .where(*_corpus_clauses(filters, overrides, stage="retrieval"))
    ).all()
    counts: Counter[str] = Counter()
    for item_id, category in rows:
        override = overrides.get((item_id, "primary_category"))
        label = str(override["value"]) if override else (category or "unknown")
        if filters.categories and label not in filters.categories:
            continue
        counts[label] += 1
    return [{"category": key, "n": n} for key, n in counts.most_common()]


def _decorate_item(row: dict, overrides: dict) -> dict:
    item_id = row["item_id"]
    out = dict(row)
    out["date"] = _iso(row.get("date"))
    grounded = row.get("quote_grounded")
    out["quote_grounded"] = None if grounded is None else _bool(grounded)
    out["high_stakes"] = None if row.get("high_stakes") is None else _bool(row.get("high_stakes"))
    out["is_representative"] = _bool(row.get("is_representative"))
    corrected = False
    for field in ITEM_FIELDS:
        payload = overrides.get((item_id, field))
        if field == "marked_irrelevant":
            out["marked_irrelevant"] = bool(payload and payload["value"] is True)
            if out["marked_irrelevant"]:
                corrected = True
            continue
        if payload:
            out[f"{field}_ai"] = payload["ai_value"]
            out[field] = payload["value"]
            corrected = True
    out["corrected"] = corrected
    return out


def read_evidence(
    session: Session,
    dashboard_views: DashboardViews,
    filters: Filters,
    overrides: dict,
    *,
    search: str = "",
    page: int = 1,
    area_id: str | None = None,
) -> dict[str, Any]:
    table = dashboard_views.evidence
    clauses = evidence_clauses(table, filters, overrides)
    if area_id:
        clauses.append(table.c.area_id == area_id)
    term = search.strip().lower()
    if term:
        clauses.append(
            or_(
                func.lower(func.coalesce(table.c.clean_text, "")).contains(term),
                func.lower(func.coalesce(table.c.problem_statement, "")).contains(term),
                func.lower(func.coalesce(table.c.evidence_quote, "")).contains(term),
            )
        )
    total = int(session.scalar(select(func.count()).select_from(table).where(*clauses)) or 0)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(1, page), pages)
    rows = session.execute(
        select(table)
        .where(*clauses)
        .order_by(table.c.date.desc().nulls_last(), table.c.item_id)
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    ).all()
    return {
        "rows": [_decorate_item(dict(row._mapping), overrides) for row in rows],
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": PAGE_SIZE,
    }


def read_dossier(
    session: Session,
    dashboard_views: DashboardViews,
    area_id: str,
    filters: Filters,
    overrides: dict,
) -> dict[str, Any]:
    """Charts and quotes for one area. Members of one area, not the whole corpus."""
    page = read_evidence(
        session,
        dashboard_views,
        filters,
        overrides,
        area_id=area_id,
        page=1,
    )
    # Representative quotes and charts need every matching member, still one area.
    table = dashboard_views.evidence
    clauses = evidence_clauses(table, filters, overrides)
    clauses.append(table.c.area_id == area_id)
    members = [
        _decorate_item(dict(row._mapping), overrides)
        for row in session.execute(select(table).where(*clauses)).all()
    ]
    item_ids = [row["item_id"] for row in members]
    insights = {}
    if item_ids:
        found = session.scalars(select(InsightRow).where(InsightRow.item_id.in_(item_ids)))
        for insight in found.all():
            insights[insight.item_id] = insight
    cues: Counter[str] = Counter()
    forgotten: Counter[str] = Counter()
    attempts: Counter[str] = Counter()
    for row in members:
        insight = insights.get(row["item_id"])
        if insight is None:
            continue
        cue_types = {cue.get("cue_type") for cue in (insight.remembered_cues or [])}
        cues.update(cue_type for cue_type in cue_types if cue_type)
        forgotten.update(set(insight.forgotten_details or []))
        kinds = {
            attempt.get("attempt_type")
            for attempt in (insight.search_attempts or [])
            if attempt.get("attempt_type") not in (None, "not_stated")
        }
        attempts.update(kinds)
    quotes = [
        row
        for row in members
        if row.get("is_representative") and (row.get("evidence_quote") or "").strip()
    ]
    quotes.sort(key=lambda row: (row.get("quote_rank") is None, row.get("quote_rank") or 0))
    if not quotes:
        quotes = [row for row in members if (row.get("evidence_quote") or "").strip()][:8]
    return {
        "match_count": len(members),
        "sources": dict(Counter(row["source"] for row in members)),
        "platforms": dict(Counter(row["platform"] for row in members)),
        "content_types": dict(Counter(row.get("content_type") or "unknown" for row in members)),
        "cue_types": dict(cues.most_common()),
        "forgotten": dict(forgotten.most_common()),
        "attempts": dict(attempts.most_common()),
        "breakdown": _ordered_breakdown(
            Counter(row.get("breakdown_point") or "not_stated" for row in members)
        ),
        "quotes": quotes,
        "page": page,
    }


def _ordered_breakdown(counts: Counter[str]) -> dict[str, int]:
    order = list(breakdown_order())
    return {key: counts[key] for key in order if counts.get(key)} | {
        key: value for key, value in counts.items() if key not in order
    }


def read_languages(session: Session) -> list[str]:
    rows = session.scalars(
        select(ItemRow.language)
        .where(ItemRow.language.is_not(None))
        .distinct()
        .order_by(ItemRow.language)
    ).all()
    return [row for row in rows if row]


def read_quality(session: Session) -> dict[str, Any]:
    published = read_published(session)
    runs = session.scalars(
        select(PipelineRunRow).order_by(PipelineRunRow.started_at.desc()).limit(40)
    ).all()
    tokens, cost = session.execute(
        select(
            func.coalesce(func.sum(PipelineRunRow.llm_tokens), 0),
            func.coalesce(func.sum(PipelineRunRow.llm_cost_usd), 0.0),
        )
    ).one()
    grounded = session.execute(
        select(
            func.count(),
            func.sum(case((InsightRow.quote_grounded.is_(False), 1), else_=0)),
        ).where(InsightRow.quote_grounded.is_not(None))
    ).one()
    low = session.execute(
        select(
            InsightRow.item_id,
            InsightRow.confidence,
            InsightRow.primary_category,
            InsightRow.problem_statement,
            ItemRow.primary_source_name,
            ItemRow.platform,
            ItemRow.source_url,
        )
        .join(ItemRow, ItemRow.item_id == InsightRow.item_id)
        .where(InsightRow.confidence < LOW_CONFIDENCE)
        .order_by(InsightRow.confidence)
        .limit(30)
    ).all()
    dedup = {}
    ingest: dict[str, dict] = {}
    for row in runs:
        if row.stage == "prep" and not dedup:
            dedup = dict(row.counts or {})
        if row.stage.startswith("ingest:") and row.stage not in ingest:
            ingest[row.stage] = {
                "run_id": row.run_id,
                "status": row.status,
                "counts": row.counts or {},
                "errors": list(row.errors or [])[:8],
            }
    return {
        "published": published,
        "llm_tokens": int(tokens or 0),
        "llm_cost_usd": float(cost or 0.0),
        "grounding_total": int(grounded[0] or 0),
        "grounding_failed": int(grounded[1] or 0),
        "runs": [
            {
                "run_id": row.run_id,
                "stage": row.stage,
                "status": row.status,
                "started_at": _aware(row.started_at).isoformat() if row.started_at else None,
                "finished_at": _aware(row.finished_at).isoformat() if row.finished_at else None,
                "llm_tokens": row.llm_tokens,
                "llm_cost_usd": row.llm_cost_usd,
                "errors": len(row.errors or []),
            }
            for row in runs
        ],
        "dedup": dedup,
        "ingest": ingest,
        "publish_notes": [
            {
                "run_id": row.run_id,
                "status": row.status,
                "messages": [
                    (error.get("message") if isinstance(error, dict) else str(error))
                    for error in (row.errors or [])
                ][:8],
            }
            for row in runs
            if row.stage == "run_all" and row.status in ("failed", "partial") and row.errors
        ],
        "low_confidence": [
            {
                "item_id": item_id,
                "confidence": confidence,
                "category": category,
                "problem": problem,
                "source": source,
                "platform": platform,
                "source_url": url,
            }
            for item_id, confidence, category, problem, source, platform, url in low
        ],
        "override_count": int(session.scalar(select(func.count()).select_from(PMOverrideRow)) or 0),
        "item_corrections": int(
            session.scalar(
                select(func.count())
                .select_from(PMOverrideRow)
                .where(PMOverrideRow.target_type == "item")
            )
            or 0
        ),
    }


def gold_excerpts() -> list[dict[str, str]]:
    """Last committed eval reports, when this environment has the eval folder."""
    folder = PROJECT_ROOT / "eval"
    names = (
        "relevance_v5_claude-sonnet-5-5_report.md",
        "extraction_v2_claude-sonnet-5-5_report.md",
    )
    found = []
    for name in names:
        path = folder / name
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        excerpt = "\n".join(lines[:28])
        found.append({"name": name, "excerpt": excerpt})
    return found


# --- cached entry points used by the pages -----------------------------------


def _filters_key(filters: Filters) -> tuple:
    return (
        filters.sources,
        filters.platforms,
        filters.categories,
        filters.severity_min,
        filters.severity_max,
        filters.high_stakes_only,
        filters.retrieval_scope,
        round(filters.vague_min, 4),
        filters.content_types,
        round(filters.min_confidence, 4),
        filters.date_start.isoformat() if filters.date_start else None,
        filters.date_end.isoformat() if filters.date_end else None,
        filters.rating_min,
        filters.rating_max,
        filters.include_unrated,
        filters.languages,
        filters.show_irrelevant,
    )


@st.cache_data(show_spinner=False)
def cached_languages(token: int) -> list[str]:
    del token
    with _session() as session:
        return read_languages(session)


@st.cache_data(show_spinner=False)
def cached_areas(run_id: str, token: int, filt: tuple, *, active_only: bool) -> list[dict]:
    del run_id
    filters = _filters_from_key(filt)
    with _session() as session:
        return read_areas(
            session,
            views(),
            filters,
            latest_overrides(session, "area"),
            latest_overrides(session, "item"),
            active_only=active_only,
        )


@st.cache_data(show_spinner=False)
def cached_overview(run_id: str, token: int, filt: tuple, area_count: int) -> dict:
    del run_id, token
    with _session() as session:
        return read_overview(
            session, _filters_from_key(filt), latest_overrides(session, "item"), area_count
        )


@st.cache_data(show_spinner=False)
def cached_evidence(
    run_id: str, token: int, filt: tuple, search: str, page: int, area_id: str | None
) -> dict:
    del run_id, token
    with _session() as session:
        return read_evidence(
            session,
            views(),
            _filters_from_key(filt),
            latest_overrides(session, "item"),
            search=search,
            page=page,
            area_id=area_id,
        )


@st.cache_data(show_spinner=False)
def cached_dossier(run_id: str, token: int, area_id: str, filt: tuple) -> dict:
    del run_id, token
    with _session() as session:
        return read_dossier(
            session, views(), area_id, _filters_from_key(filt), latest_overrides(session, "item")
        )


@st.cache_data(show_spinner=False)
def cached_quality(run_id: str, token: int) -> dict:
    del run_id, token
    with _session() as session:
        snapshot = read_quality(session)
    if snapshot["published"] and snapshot["published"]["published_at"]:
        snapshot["published"] = {
            **snapshot["published"],
            "published_at": snapshot["published"]["published_at"].isoformat(),
        }
    snapshot["gold"] = gold_excerpts()
    return snapshot


def _filters_from_key(filt: tuple) -> Filters:
    return Filters(
        sources=filt[0],
        platforms=filt[1],
        categories=filt[2],
        severity_min=filt[3],
        severity_max=filt[4],
        high_stakes_only=filt[5],
        retrieval_scope=filt[6],
        vague_min=filt[7],
        content_types=filt[8],
        min_confidence=filt[9],
        date_start=datetime.fromisoformat(filt[10]).date() if filt[10] else None,
        date_end=datetime.fromisoformat(filt[11]).date() if filt[11] else None,
        rating_min=filt[12],
        rating_max=filt[13],
        include_unrated=filt[14],
        languages=filt[15],
        show_irrelevant=filt[16],
    )


def published_context() -> tuple[dict[str, Any] | None, dict[str, str] | None, int]:
    with _session() as session:
        published = read_published(session)
        banner = read_banner(session, published)
        token = override_token(session)
    return published, banner, token


def areas_for(
    published: dict | None, token: int, filters: Filters, *, active_only: bool = True
) -> list[dict]:
    if not published:
        return []
    return cached_areas(published["run_id"], token, _filters_key(filters), active_only=active_only)


def overview_for(published: dict | None, token: int, filters: Filters, area_count: int) -> dict:
    if not published:
        return {
            "raw": 0,
            "items": 0,
            "ready": 0,
            "retrieval": 0,
            "vague": 0,
            "areas": 0,
            "trend": [],
            "categories": [],
        }
    return cached_overview(published["run_id"], token, _filters_key(filters), area_count)


def evidence_for(published, token, filters, search, page, area_id=None) -> dict:
    if not published:
        return {"rows": [], "total": 0, "page": 1, "pages": 1, "page_size": PAGE_SIZE}
    return cached_evidence(published["run_id"], token, _filters_key(filters), search, page, area_id)


def dossier_for(published, token, area_id, filters) -> dict:
    if not published:
        return {}
    return cached_dossier(published["run_id"], token, area_id, _filters_key(filters))


def quality_for(published, token) -> dict:
    run_id = published["run_id"] if published else ""
    return cached_quality(run_id, token)
