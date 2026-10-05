"""Deterministic Pandas analysis of SQL results.

Every number in the returned dict is computed here from the result frame, never by an LLM. The responder
may only quote values from the result rows or from this dict (enforced by agents/grounding.py).
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

_TIME_NAMES = {"date", "day", "week", "month", "month_name", "quarter", "year", "period", "fiscal_quarter",
               "fiscal_year", "date_key", "month_start", "quarter_start", "order_date", "month_year"}
_PERIOD_NAMES = {"period", "time_period", "quarter", "fiscal_quarter", "fiscal_period", "period_label", "year", "fiscal_year"}
_NON_ADDITIVE = re.compile(r"(pct|percent|rate|margin|share|avg|average|aov|ratio|growth|change|per_|mean|median|price)", re.I)
_ID = re.compile(r"(^|_)id$|^date_key$")
_CURRENT = re.compile(r"(current|curr|this|latest|recent|after|new)", re.I)
_PREVIOUS = re.compile(r"(previous|prev|prior|last|base|before|old)", re.I)
_TIME_VALUE = re.compile(r"^(\d{4}(-\d{2}(-\d{2})?)?|Q[1-4]\b.*|FY\s?\d{2,4}.*|[A-Z][a-z]+ \d{4})", re.I)


def _r(x: Any, nd: int = 2) -> Any:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    return round(float(x), nd)


def pct_change(current: float, previous: float) -> float | None:
    """(current - previous) / |previous| * 100, or None when previous is 0/missing."""
    if previous in (0, None) or pd.isna(previous) or pd.isna(current):
        return None
    return _r((current - previous) / abs(previous) * 100)


# ----------------------------------------------------------------------------- building blocks

def growth(df: pd.DataFrame, time_col: str, value_col: str) -> dict[str, Any]:
    """Period-over-period change of value_col ordered by time_col (summed within a period)."""
    s = df.groupby(time_col, sort=True)[value_col].sum()
    periods = [
        {"period": str(p), "value": _r(v), "change": _r(v - s.iloc[i - 1]) if i else None,
         "pct_change": pct_change(v, s.iloc[i - 1]) if i else None}
        for i, (p, v) in enumerate(s.items())
    ]
    out: dict[str, Any] = {"value_col": value_col, "periods": periods}
    if len(s) >= 2:
        out["first_to_last_pct_change"] = pct_change(s.iloc[-1], s.iloc[0])
        out["peak"] = {"period": str(s.idxmax()), "value": _r(s.max())}
        out["trough"] = {"period": str(s.idxmin()), "value": _r(s.min())}
    return out


def share(df: pd.DataFrame, cat_col: str, value_col: str) -> list[dict[str, Any]]:
    s = df.groupby(cat_col)[value_col].sum().sort_values(ascending=False)
    total = s.sum()
    return [{"category": str(k), "value": _r(v), "share_pct": _r(v / total * 100) if total else None} for k, v in s.items()]


def top_n(df: pd.DataFrame, cat_col: str, value_col: str, n: int = 5) -> dict[str, Any]:
    s = df.groupby(cat_col)[value_col].sum().sort_values(ascending=False)
    return {
        "value_col": value_col,
        "top": [{"category": str(k), "value": _r(v)} for k, v in s.head(n).items()],
        "bottom": [{"category": str(k), "value": _r(v)} for k, v in s.tail(min(n, len(s))).items()][::-1],
    }


def period_compare(current: float, previous: float) -> dict[str, Any]:
    return {"current": _r(current), "previous": _r(previous), "change": _r(current - previous),
            "pct_change": pct_change(current, previous)}


def driver_breakdown(df: pd.DataFrame, dim_col: str, current_col: str, previous_col: str) -> dict[str, Any]:
    """Which members of `dim_col` explain the change between two period columns.

    share_of_movement_pct = member change / sum of absolute member changes (negative = drag).
    """
    g = df.groupby(dim_col)[[current_col, previous_col]].sum().fillna(0)
    g["change"] = g[current_col] - g[previous_col]
    movement = g["change"].abs().sum()  # gross movement: stays meaningful when gains and losses offset
    g = g.reindex(g["change"].abs().sort_values(ascending=False).index)
    rows = []
    for k, r in g.iterrows():
        rows.append({
            "member": str(k), "current": _r(r[current_col]), "previous": _r(r[previous_col]),
            "change": _r(r["change"]), "pct_change": pct_change(r[current_col], r[previous_col]),
            "share_of_movement_pct": _r(r["change"] / movement * 100) if movement else None,
        })
    return {"dimension": dim_col, "total": period_compare(g[current_col].sum(), g[previous_col].sum()), "drivers": rows}


# ----------------------------------------------------------------------------- column roles

def _is_time_col(df: pd.DataFrame, col: str) -> bool:
    s = df[col]
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if col.lower() in _TIME_NAMES:
        return True
    return s.dtype == object and len(s) > 0 and s.dropna().astype(str).map(lambda v: bool(_TIME_VALUE.match(v))).all()


def classify_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    time_cols, numeric, category = [], [], []
    for c in df.columns:
        if _ID.search(c.lower()):
            category.append(c)
        elif _is_time_col(df, c):
            time_cols.append(c)
        elif pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]):
            numeric.append(c)
        else:
            category.append(c)
    return {"time": time_cols, "numeric": numeric, "category": category}


def _wide_pair(numeric: list[str]) -> tuple[str, str] | None:
    cur = next((c for c in numeric if _CURRENT.search(c) and not _PREVIOUS.search(c)), None)
    prev = next((c for c in numeric if _PREVIOUS.search(c)), None)
    return (cur, prev) if cur and prev and cur != prev else None


# ----------------------------------------------------------------------------- entry point

def analyze(df: pd.DataFrame, intent: str | None = None) -> dict[str, Any]:
    """Pick the right deterministic analyses for the shape of `df`."""
    out: dict[str, Any] = {"row_count": int(len(df)), "intent": intent}
    if df.empty:
        return out
    roles = classify_columns(df)
    time_cols, numeric, category = roles["time"], roles["numeric"], roles["category"]
    out["columns"] = roles

    out["stats"] = {
        c: {"min": _r(df[c].min()), "max": _r(df[c].max()), "mean": _r(df[c].mean())}
        | ({"total": _r(df[c].sum())} if not _NON_ADDITIVE.search(c) and len(df) > 1 else {})
        for c in numeric
    }
    if not numeric:
        return out

    value = numeric[0]
    pair = _wide_pair(numeric)
    dims = [c for c in category if not _ID.search(c.lower())]

    if pair and dims:  # wide current-vs-previous layout
        out["driver_breakdown"] = driver_breakdown(df, dims[0], *pair)
        return out

    # long layout: exactly two periods x a dimension
    period_cols = [c for c in time_cols if c.lower() in _PERIOD_NAMES and df[c].nunique() == 2]
    if period_cols and dims:
        pc, dim = period_cols[0], dims[0]
        prev_label, cur_label = sorted(df[pc].dropna().unique(), key=str)
        wide = df.pivot_table(index=dim, columns=pc, values=value, aggfunc="sum").fillna(0)
        wide = wide.rename(columns={cur_label: "_cur", prev_label: "_prev"}).reset_index()
        out["period_labels"] = {"current": str(cur_label), "previous": str(prev_label)}
        out["driver_breakdown"] = driver_breakdown(wide, dim, "_cur", "_prev")
        return out

    if time_cols and df[time_cols[0]].nunique() >= 2:
        tc = time_cols[0]
        out["growth"] = growth(df, tc, value)
        if dims:
            ordered = sorted(df[tc].unique(), key=str)
            first, last = ordered[0], ordered[-1]
            piv = df.pivot_table(index=dims[0], columns=tc, values=value, aggfunc="sum").fillna(0)
            out["series_summary"] = [
                {"member": str(k), "first": _r(r[first]), "last": _r(r[last]), "pct_change": pct_change(r[last], r[first])}
                for k, r in piv.iterrows()
            ]
        return out

    if dims and len(df) > 1:
        dim = dims[0]
        out["top_n"] = top_n(df, dim, value)
        if not _NON_ADDITIVE.search(value) and (df[value] >= 0).all():
            out["share"] = share(df, dim, value)
    return out
