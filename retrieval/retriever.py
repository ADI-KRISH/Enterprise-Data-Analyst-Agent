"""Retrieve KPI definitions, reporting policies and table schema cards for a question.

`retrieve(question, k)` returns:
- `kpis`: the top-k KPI definition chunks (vector search, doc = kpi_definitions)
- `policies`: the most relevant reporting-policy chunks
- `tables`: schema cards for the tables the question needs. Tables come from the
  best-matching KPI chunk, from keyword / known-value matches in the question, and are
  then closed over the foreign-key graph so every card needed for the joins is present.
  Cards are built from database/allowlist.yaml + the generated data dictionary.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastembed import TextEmbedding

from database.allowlist import Allowlist, load_allowlist
from retrieval.build_index import COLLECTION, _slug, get_embedder, open_client
from retrieval.data_dictionary import load_dictionary, table_markdown

N_POLICIES = 3

# Deterministic question pattern -> reporting-policy section; matched sections are always
# returned (ahead of vector hits), because dense search misses short temporal phrases.
POLICY_TERMS: dict[str, str] = {
    r"last|this|previous|latest|recent|current|ytd|fytd|to date|past|\bago\b|mom|qoq|yoy|growth|trend":
        "Relative dates (last quarter, this month, last year, YTD) and ANCHOR_DATE",
    r"fiscal|financial year|\bfy|\bq[1-4]\b|quarter": "Calendar vs Indian fiscal year",
    r"regions?|zones?|north|south|east|west|central|cit(y|ies)|states?|stores?": "Region, store and customer geography",
    r"customer names?|names? of|who (are|is|were)|phone|email|address": "Privacy",
}

# Deterministic keyword -> table rules (question vocabulary that implies a table).
TERM_TABLES: dict[str, str] = {
    r"categor(y|ies)|sub[- ]?categor(y|ies)|brands?|products?|sku": "dim_product",
    r"regions?|regional|zones?|(north|south|east|west|central)(ern)?\b": "dim_region",
    r"stores?|outlets?|branch(es)?|cit(y|ies)|states?|locations?": "dim_store",
    r"customers?|segments?|consumer|corporate|smb|sign[- ]?ups?|registered": "dim_customer",
    r"orders?|channels?|online|offline|status|returns?|returned|cancel(led|lations?)?": "fact_orders",
    r"revenue|sales|margin|profit|units|quantity|discounts?|price|cost|aov|basket|gmv": "fact_order_items",
    r"fiscal|financial year|fy ?\d{0,4}": "dim_date",
}

# Columns whose known values are not matched against the question: dates are filtered on
# fact_orders.order_date directly, and place names imply the store (sales geography),
# not the customer, unless the question mentions customers (handled by TERM_TABLES).
VALUE_MATCH_SKIP = {"dim_date", "dim_customer.city", "dim_customer.state"}

# Table order for output and tie-breaking in join paths (sales geography via the store).
TABLE_PRIORITY = (
    "fact_orders", "fact_order_items", "dim_product", "dim_store",
    "dim_region", "dim_customer", "dim_date",
)


@dataclass(frozen=True)
class DocChunk:
    id: str
    doc: str
    section: str
    kpi_name: str
    tables: tuple[str, ...]
    text: str
    score: float  # cosine similarity, higher is better


@dataclass(frozen=True)
class SchemaCard:
    table: str
    columns: tuple[str, ...]
    restricted: tuple[str, ...]
    text: str


@dataclass
class RetrievalResult:
    question: str
    kpis: list[DocChunk] = field(default_factory=list)
    policies: list[DocChunk] = field(default_factory=list)
    tables: list[SchemaCard] = field(default_factory=list)

    @property
    def table_names(self) -> list[str]:
        return [c.table for c in self.tables]

    def as_text(self) -> str:
        """Plain-text context for prompts (callers wrap it in a <retrieved_docs> block)."""
        parts = ["# KPI definitions"] + [c.text for c in self.kpis]
        parts += ["# Reporting policies"] + [c.text for c in self.policies]
        parts += ["# Tables"] + [c.text for c in self.tables]
        return "\n\n".join(parts)


def _fk_graph(dictionary: dict[str, Any]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {t: set() for t in dictionary["tables"]}
    for t, spec in dictionary["tables"].items():
        for fk in spec["foreign_keys"]:
            graph[t].add(fk["ref_table"])
            graph.setdefault(fk["ref_table"], set()).add(t)
    return graph


def _rank(table: str) -> int:
    return TABLE_PRIORITY.index(table) if table in TABLE_PRIORITY else len(TABLE_PRIORITY)


def connect_tables(tables: set[str], graph: dict[str, set[str]]) -> set[str]:
    """Add the tables on shortest FK paths needed to join all `tables` together."""
    if len(tables) <= 1:
        return set(tables)
    ordered = sorted(tables, key=_rank)
    connected = {ordered[0]}
    for target in ordered[1:]:
        if target in connected:
            continue
        # BFS from the connected set to target; neighbours visited in priority order.
        prev: dict[str, str | None] = {t: None for t in connected}
        queue = deque(sorted(connected, key=_rank))
        while queue:
            node = queue.popleft()
            if node == target:
                break
            for nb in sorted(graph.get(node, ()), key=_rank):
                if nb not in prev:
                    prev[nb] = node
                    queue.append(nb)
        if target not in prev:
            raise ValueError(f"table {target} is not reachable through foreign keys")
        node: str | None = target
        while node is not None and node not in connected:
            connected.add(node)
            node = prev[node]
    return connected


class Retriever:
    def __init__(
        self,
        chroma_dir: Path | None = None,
        embedder: TextEmbedding | None = None,
        allowlist: Allowlist | None = None,
        dictionary: dict[str, Any] | None = None,
    ) -> None:
        from app.config import get_settings

        self._embedder = embedder or get_embedder()
        client = open_client(chroma_dir or get_settings().chroma_dir)
        self._collection = client.get_collection(COLLECTION, embedding_function=None)
        self._allowlist = allowlist or load_allowlist()
        self._dictionary = dictionary or load_dictionary()
        self._graph = _fk_graph(self._dictionary)
        self._cards = {t: self._build_card(t) for t in self._allowlist.tables}
        self._value_patterns = self._build_value_patterns()

    # ------------------------------------------------------------------ cards
    def _build_card(self, table: str) -> SchemaCard:
        allowed = self._allowlist.tables[table]
        spec = self._dictionary["tables"].get(table)
        if spec is None:  # dictionary out of date: fall back to the allowlist alone
            text = f"## {table}\n\n{allowed.description}\n\nColumns: {', '.join(allowed.columns)}"
        else:
            cols = [
                {**c, "restricted": c["name"] in allowed.restricted}
                for c in spec["columns"]
                if c["name"] in allowed.columns
            ]
            text = table_markdown(table, {**spec, "description": allowed.description, "columns": cols})
        return SchemaCard(
            table=table,
            columns=allowed.columns,
            restricted=tuple(sorted(allowed.restricted)),
            text=text,
        )

    def _build_value_patterns(self) -> list[tuple[re.Pattern[str], str]]:
        patterns = []
        for table, spec in self._dictionary["tables"].items():
            if table in VALUE_MATCH_SKIP or table not in self._allowlist.tables:
                continue
            for col in spec["columns"]:
                if col.get("restricted") or f"{table}.{col['name']}" in VALUE_MATCH_SKIP:
                    continue
                for value in col.get("values", []):
                    patterns.append((re.compile(rf"\b{re.escape(value)}\b", re.IGNORECASE), table))
        return patterns

    # ---------------------------------------------------------------- search
    @staticmethod
    def _chunk(id_: str, text: str, meta: dict[str, Any], score: float) -> DocChunk:
        return DocChunk(
            id=id_,
            doc=meta["doc"],
            section=meta["section"],
            kpi_name=meta["kpi_name"],
            tables=tuple(t for t in meta["tables"].split(",") if t),
            text=text,
            score=score,
        )

    def _query(self, vector: list[float], doc: str, n: int) -> list[DocChunk]:
        res = self._collection.query(
            query_embeddings=[vector], n_results=n, where={"doc": doc},
            include=["documents", "metadatas", "distances"],
        )
        return [
            self._chunk(i, t, m, 1.0 - d)
            for i, t, m, d in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]

    def _policies(self, question: str, vector: list[float]) -> list[DocChunk]:
        ids = [f"reporting_policies:{_slug(section)}"
               for pat, section in POLICY_TERMS.items() if re.search(pat, question, re.I)]
        forced: list[DocChunk] = []
        if ids:
            res = self._collection.get(ids=ids, include=["documents", "metadatas"])
            by_id = {i: self._chunk(i, t, m, 1.0) for i, t, m in zip(res["ids"], res["documents"], res["metadatas"])}
            forced = [by_id[i] for i in ids if i in by_id]
        seen = {c.id for c in forced}
        ranked = [c for c in self._query(vector, "reporting_policies", N_POLICIES + 1)
                  if c.section != "Overview" and c.id not in seen]
        return forced + ranked[: max(0, N_POLICIES - len(forced))]

    def lexical_tables(self, question: str) -> set[str]:
        tables = {t for pat, t in TERM_TABLES.items() if re.search(rf"\b(?:{pat})", question, re.I)}
        tables |= {t for pat, t in self._value_patterns if pat.search(question)}
        return tables & set(self._allowlist.tables)

    def retrieve(self, question: str, k: int = 4) -> RetrievalResult:
        vector = next(iter(self._embedder.query_embed([question]))).tolist()
        kpis = [c for c in self._query(vector, "kpi_definitions", k + 1) if c.kpi_name][:k]
        policies = self._policies(question, vector)

        # Only the best KPI contributes tables: runner-up scores are often close together
        # and would pull in unrelated tables. Other needs are caught lexically.
        tables = set(kpis[0].tables) if kpis else set()
        tables |= self.lexical_tables(question)
        if not tables:  # nothing recognised: fall back to the closest table descriptions
            tables = {t for c in self._query(vector, "data_dictionary", 2) for t in c.tables}
        tables = connect_tables(tables & set(self._allowlist.tables), self._graph)

        return RetrievalResult(
            question=question,
            kpis=kpis,
            policies=policies,
            tables=[self._cards[t] for t in sorted(tables, key=_rank)],
        )


@lru_cache
def get_retriever() -> Retriever:
    return Retriever()


def retrieve(question: str, k: int = 4) -> RetrievalResult:
    return get_retriever().retrieve(question, k)
