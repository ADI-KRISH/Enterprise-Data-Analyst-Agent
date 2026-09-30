"""Typed loader for database/allowlist.yaml (tables/columns the agent may query)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ALLOWLIST_PATH = Path(__file__).resolve().parent / "allowlist.yaml"


@dataclass(frozen=True)
class AllowedTable:
    name: str
    description: str
    columns: tuple[str, ...]
    restricted: frozenset[str]


@dataclass(frozen=True)
class Allowlist:
    schema: str
    tables: dict[str, AllowedTable]


@lru_cache
def load_allowlist(path: Path = ALLOWLIST_PATH) -> Allowlist:
    raw = yaml.safe_load(path.read_text())
    tables = {
        name: AllowedTable(
            name=name,
            description=spec.get("description", ""),
            columns=tuple(spec["columns"]),
            restricted=frozenset(spec.get("restricted", [])),
        )
        for name, spec in raw["tables"].items()
    }
    for t in tables.values():
        unknown = t.restricted - set(t.columns)
        if unknown:
            raise ValueError(f"{t.name}: restricted columns not in columns: {sorted(unknown)}")
    return Allowlist(schema=raw["schema"], tables=tables)
