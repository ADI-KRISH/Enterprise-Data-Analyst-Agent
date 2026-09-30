"""Deterministic Pandas analysis of executed results.

Phase 4 placeholder (Phase 5 adds growth / share / top_n / period_compare /
driver_breakdown properly). Every value here is computed from the SQL result, so the
responder may quote it and grounding accepts it.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

_PREV_CURR = re.compile(r"^(?P<base>.+?)_(?P<which>prev|curr)$")


def _round(x: Any) -> Any:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    return round(float(x), 2)


def to_frame(result: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(result.get("records", []), columns=result.get("columns", []))


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]


def label_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in numeric_columns(df)]


def period_changes(df: pd.DataFrame) -> list[dict[str, Any]]:
    """For each <m>_prev / <m>_curr column pair: per-row change, % change and share of total change."""
    pairs: dict[str, dict[str, str]] = {}
    for c in df.columns:
        if m := _PREV_CURR.match(str(c)):
            pairs.setdefault(m["base"], {})[m["which"]] = c
    labels = label_columns(df)
    out = []
    for base, cols in pairs.items():
        if {"prev", "curr"} - cols.keys():
            continue
        prev, curr = df[cols["prev"]].astype(float), df[cols["curr"]].astype(float)
        change = curr - prev
        total_prev, total_curr = prev.sum(), curr.sum()
        total_change = total_curr - total_prev
        rows = []
        for i in range(len(df)):
            rows.append({
                **{lc: df[lc].iloc[i] for lc in labels},
                "prev": _round(prev.iloc[i]), "curr": _round(curr.iloc[i]),
                "change": _round(change.iloc[i]),
                "pct_change": _round(100 * change.iloc[i] / prev.iloc[i]) if prev.iloc[i] else None,
                "share_of_total_change_pct": _round(100 * change.iloc[i] / total_change)
                if total_change and len(df) > 1 else None,
            })
        rows.sort(key=lambda r: r["change"] if r["change"] is not None else 0)
        out.append({
            "metric": base,
            "total_prev": _round(total_prev), "total_curr": _round(total_curr),
            "total_change": _round(total_change),
            "total_pct_change": _round(100 * total_change / total_prev) if total_prev else None,
            "rows": rows,
        })
    return out


def shares(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Share of total for the first numeric column when rows are one-per-label."""
    nums, labels = numeric_columns(df), label_columns(df)
    if len(df) < 2 or not nums or not labels or any(_PREV_CURR.match(str(c)) for c in nums):
        return []
    metric = nums[0]
    total = df[metric].astype(float).sum()
    if not total or df[metric].isna().any() or (df[metric] < 0).any():
        return []
    return [{**{lc: df[lc].iloc[i] for lc in labels},
             "value": _round(df[metric].iloc[i]),
             "share_pct": _round(100 * df[metric].iloc[i] / total)} for i in range(len(df))]


def analyze_result(result: dict[str, Any]) -> dict[str, Any]:
    df = to_frame(result)
    summary = {}
    if len(df) > 1:
        for c in numeric_columns(df):
            s = df[c].astype(float)
            summary[c] = {"total": _round(s.sum()), "mean": _round(s.mean()),
                          "min": _round(s.min()), "max": _round(s.max())}
    out: dict[str, Any] = {"row_count": len(df), "summary": summary}
    if changes := period_changes(df):
        out["period_changes"] = changes
    elif share := shares(df):
        out["shares"] = {"metric": numeric_columns(df)[0], "rows": share}
    return out


def analyze(queries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"queries": [
        {"id": q["id"], "purpose": q["purpose"], **analyze_result(q["result"] or {})}
        for q in queries if q.get("status") == "executed"
    ]}
