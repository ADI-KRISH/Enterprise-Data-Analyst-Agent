"""Understand the question: intent, metrics, entities, filters and time range.

The fast LLM extracts structure (JSON mode + Pydantic). Time ranges are then resolved by
deterministic rules against ANCHOR_DATE (agents/time_range.py); the LLM's own dates are
used only when no rule matches, and are clamped to the data window.
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents import time_range
from agents.prompting import DATA_NOTICE, data_block
from app import llm
from app.config import PROJECT_ROOT
from database.allowlist import load_allowlist

NODE = "understand"
KPI_DOC = PROJECT_ROOT / "retrieval" / "docs" / "kpi_definitions.md"
# Low-cardinality dimension values the model may map user phrasing onto.
VALUE_COLUMNS = (
    ("dim_region", "region_name"), ("dim_region", "zone"), ("dim_product", "category"),
    ("dim_customer", "segment"), ("fact_orders", "channel"), ("fact_orders", "status"),
)

Intent = Literal["aggregate", "trend", "compare", "rank", "diagnose", "definition"]


class Entity(BaseModel):
    type: str = Field(description="region | zone | category | sub_category | brand | product | store | city | state | segment | channel | status | other")
    value: str = Field(description="Exact value as stored in the data when known (e.g. 'South India').")


class Filter(BaseModel):
    column: str = Field(description="table.column, e.g. dim_region.region_name")
    op: str = "="
    value: str


class Understanding(BaseModel):
    intent: Intent = Field(description=(
        "aggregate: a single total/average; trend: over time; compare: between periods or "
        "groups; rank: top/bottom N; diagnose: why something changed; definition: what a "
        "metric means"))
    metrics: list[str] = Field(default_factory=list, description=(
        "kpi_name values from the KPI catalog when the question maps to one; otherwise a "
        "short snake_case description of a measure computable directly from the columns"))
    undefined_metrics: list[str] = Field(default_factory=list, description=(
        "Requested measures that are neither in the KPI catalog nor computable from the "
        "listed columns (e.g. footfall, customer satisfaction, marketing spend, targets)"))
    entities: list[Entity] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list, description="table.column to break results down by")
    top_n: int | None = None
    time_phrase: str | None = Field(None, description="The time expression exactly as written, or null")
    time_start: date | None = Field(None, description="Only if you are sure; code resolves common phrases itself")
    time_end: date | None = None
    needs_clarification: bool = Field(False, description=(
        "true only if the question cannot be answered without guessing: undefined metric, "
        "entity that does not exist in the data, or a genuinely ambiguous period"))
    clarification_reason: str | None = None


@lru_cache
def kpi_catalog() -> list[dict[str, str]]:
    """[{kpi_name, title, aliases}] parsed from kpi_definitions.md."""
    text = KPI_DOC.read_text(encoding="utf-8")
    out = []
    for section in re.split(r"^## ", text, flags=re.M)[1:]:
        title = section.splitlines()[0].strip()
        name = re.search(r"\*\*kpi_name:\*\*\s*(\S+)", section)
        aliases = re.search(r"\*\*Also known as:\*\*\s*(.+)", section)
        if name:
            out.append({"kpi_name": name.group(1), "title": title,
                        "aliases": aliases.group(1).strip() if aliases else ""})
    return out


@lru_cache
def schema_summary() -> str:
    from retrieval.data_dictionary import load_dictionary

    allowlist = load_allowlist()
    lines = [f"- {t}({', '.join(c for c in s.columns if c not in s.restricted)})"
             for t, s in allowlist.tables.items()]
    try:
        tables = load_dictionary()["tables"]
        for table, column in VALUE_COLUMNS:
            col = next(c for c in tables[table]["columns"] if c["name"] == column)
            lines.append(f"- {table}.{column} values: {', '.join(col.get('values', []))}")
    except Exception:  # noqa: BLE001 - dictionary missing: columns alone still work
        pass
    return "\n".join(lines)


def system_prompt(anchor: date, data_start: date) -> str:
    kpis = "\n".join(f"- {k['kpi_name']} ({k['title']}): {k['aliases']}" for k in kpi_catalog())
    return f"""You analyse business questions for a read-only analytics assistant over synthetic \
Indian retail sales data (INR). Extract the structure of the question; do not answer it.

KPI catalog (kpi_name (title): synonyms):
{kpis}

Tables and columns (customer names are personal data and never an output):
{schema_summary()}

Rules:
- Data covers {data_start.isoformat()} to {anchor.isoformat()}. ANCHOR_DATE = {anchor.isoformat()}; \
relative dates are resolved against it, never today's date. Copy the time expression into \
time_phrase; leave time_start/time_end null unless the period is explicit and unusual.
- "sales" / "revenue" = revenue. Ranking without a metric = revenue. "Top N" without N = 5.
- Map places to stored values: "South" / "southern region" -> dim_region.region_name = 'South India' \
(likewise North/East/West/Central). Cities/states are dim_store.city / dim_store.state (sales \
geography) unless the question is about where customers live.
- A "why did X change" question is intent diagnose.
- Only set needs_clarification when the measure is truly undefined here (put it in \
undefined_metrics) or an entity does not exist in the data. Measures computable from the \
columns (discount %, units, list price, order counts by status, signups) are defined.

{DATA_NOTICE}"""


@lru_cache
def date_window() -> tuple[date, date]:
    from agents.executor import get_executor

    return get_executor().date_window()


def resolve_time(u: Understanding, question: str, data_start: date, anchor: date) -> time_range.TimeRange:
    compare = u.intent in ("compare", "diagnose")
    for text in (question, u.time_phrase or ""):
        tr = time_range.resolve(text, anchor, compare=compare) if text else None
        if tr is not None:
            return tr
    if u.time_start and u.time_end and u.time_start <= u.time_end:
        start, end = max(u.time_start, data_start), min(u.time_end, anchor)
        if start <= end:
            tr = time_range.TimeRange(label=time_range.format_range(start, end), start=start, end=end,
                                      source="llm")
            return time_range._comparison(tr, question) if compare else tr
    return time_range.default_range(data_start, anchor)


def run(state: dict, trace: list[dict]) -> dict:
    data_start, anchor = date_window()
    question = state["question"]
    messages = [SystemMessage(system_prompt(anchor, data_start)),
                HumanMessage(data_block("user_question", question))]
    u = llm.invoke_structured(llm.get_llm_for_node(NODE, json_mode=True), messages, Understanding,
                              node=NODE, trace=trace)
    tr = resolve_time(u, question, data_start, anchor)
    return {
        "intent": u.intent,
        "metrics": u.metrics,
        "undefined_metrics": u.undefined_metrics,
        "entities": [e.model_dump() for e in u.entities],
        "filters": [f.model_dump() for f in u.filters],
        "group_by": u.group_by,
        "top_n": u.top_n,
        "time_range": tr.model_dump(mode="json"),
        "needs_clarification": bool(u.needs_clarification or u.undefined_metrics),
        "clarification_reason": u.clarification_reason,
        "_detail": {"intent": u.intent, "time_source": tr.source},
    }
