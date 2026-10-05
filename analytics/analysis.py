"""Deterministic Pandas analysis. Every number the responder may quote comes from here
or directly from the SQL result (CLAUDE.md rule 1)."""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

_TIME_NAME = re.compile(r"(^|_)(date|day|week|month|quarter|year|period|fy)($|_)", re.I)
_ISO = re.compile(r"^\d{4}(-\d{2}){0,2}$")
_DELTA_NAME = re.compile(r"(change|delta|diff|variance)", re.I)


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def pct_change(current: float, previous: float) -> float | None:
    if previous in (0, None) or current is None:
        return None
    return _r((current - previous) / abs(previous) * 100)


def growth(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """Period-over-period change for rows already sorted in time order."""
    out = df.copy()
    out["change"] = out[value_col].diff()
    out["growth_pct"] = out[value_col].pct_change(fill_method=None) * 100
    return out.replace([np.inf, -np.inf], np.nan)


def share(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    out = df.copy()
    total = out[value_col].sum()
    out["share_pct"] = out[value_col] / total * 100 if total else np.nan
    return out


def top_n(df: pd.DataFrame, value_col: str, n: int = 5, ascending: bool = False) -> pd.DataFrame:
    return df.sort_values(value_col, ascending=ascending).head(n)


def period_compare(df: pd.DataFrame, value_col: str) -> dict | None:
    """Last row vs the one before it (rows in time order)."""
    if len(df) < 2:
        return None
    cur, prev = _num(df[value_col].iloc[-1]), _num(df[value_col].iloc[-2])
    if cur is None or prev is None:
        return None
    return {"current": cur, "previous": prev, "change": _r(cur - prev),
            "change_pct": pct_change(cur, prev)}


def driver_breakdown(df: pd.DataFrame, label_col: str, delta_col: str) -> list[dict]:
    """Each label's contribution to the total change (sorted by absolute impact)."""
    total = df[delta_col].sum()
    rows = []
    for _, r in df.iterrows():
        d = _num(r[delta_col])
        if d is None:
            continue
        rows.append({"label": str(r[label_col]), "change": _r(d),
                     "contribution_pct": _r(d / total * 100) if total else None})
    return sorted(rows, key=lambda x: -abs(x["change"] or 0))


# ----------------------------------------------------------------------------- orchestration

def _split_columns(df: pd.DataFrame) -> tuple[list[str], list[str], str | None]:
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
               and not (_TIME_NAME.search(c) and c.lower() in ("year", "month", "quarter", "day"))]
    time_col = None
    for c in df.columns:
        if c in numeric and not _TIME_NAME.search(c):
            continue
        if _TIME_NAME.search(c) and (
            pd.api.types.is_datetime64_any_dtype(df[c])
            or df[c].astype(str).str.match(_ISO).all()
        ):
            time_col = c
            break
    labels = [c for c in df.columns if c not in numeric and c != time_col]
    return numeric, labels, time_col


def analyze(frame: dict | None, intent: str = "other") -> dict:
    """-> {"summary", "growth"?, "share"?, "top"?, "drivers"?, "notes"}; all values deterministic."""
    df = pd.DataFrame.from_records(frame["records"], columns=frame["columns"]) if frame else pd.DataFrame()
    out: dict[str, Any] = {"row_count": int(len(df)), "notes": []}
    if df.empty:
        out["notes"].append("Query returned no rows.")
        return out
    numeric, labels, time_col = _split_columns(df)
    out["numeric_columns"], out["label_columns"], out["time_column"] = numeric, labels, time_col
    if not numeric:
        return out

    out["summary"] = {
        c: {"total": _r(df[c].sum()), "mean": _r(df[c].mean()), "min": _r(df[c].min()),
            "max": _r(df[c].max())}
        for c in numeric
    }
    main = numeric[0]
    if time_col and len(df) >= 2:
        series_df = df.sort_values(time_col) if not labels else df
        if not labels:
            g = growth(series_df, main)
            out["growth"] = [
                {"period": str(r[time_col]), "value": _r(_num(r[main])), "change": _r(_num(r["change"])),
                 "growth_pct": _r(_num(r["growth_pct"]))} for _, r in g.iterrows()
            ]
            out["period_compare"] = period_compare(series_df, main)
            first, last = _num(series_df[main].iloc[0]), _num(series_df[main].iloc[-1])
            out["overall_change_pct"] = pct_change(last, first) if first and last is not None else None
            peak = series_df.loc[series_df[main].idxmax()]
            out["peak"] = {"period": str(peak[time_col]), "value": _r(_num(peak[main]))}
    elif labels and len(df) >= 2:
        lab = labels[0]
        nonneg = (df[main].dropna() >= 0).all()
        # prev/curr or delta columns -> driver analysis
        delta = next((c for c in numeric if _DELTA_NAME.search(c)), None)
        if delta is None:
            prev_c = next((c for c in numeric if re.search(r"prev|prior|before|last", c, re.I)), None)
            cur_c = next((c for c in numeric if re.search(r"curr|current|latest|this", c, re.I)), None)
            if prev_c and cur_c:
                d = df[[lab]].copy()
                d["change"] = df[cur_c] - df[prev_c]
                out["drivers"] = driver_breakdown(d, lab, "change")
                out["period_compare"] = {
                    "current": _r(df[cur_c].sum()), "previous": _r(df[prev_c].sum()),
                    "change": _r(df[cur_c].sum() - df[prev_c].sum()),
                    "change_pct": pct_change(df[cur_c].sum(), df[prev_c].sum())}
        else:
            out["drivers"] = driver_breakdown(df, lab, delta)
        if nonneg and "drivers" not in out:
            s = share(df, main)
            out["share"] = [{"label": str(r[lab]), "share_pct": _r(_num(r["share_pct"]))}
                            for _, r in s.sort_values(main, ascending=False).head(15).iterrows()]
        t = top_n(df, main, 5)
        out["top"] = [{"label": str(r[lab]), "value": _r(_num(r[main]))} for _, r in t.iterrows()]
        b = top_n(df, main, 1, ascending=True).iloc[0]
        out["bottom"] = {"label": str(b[lab]), "value": _r(_num(b[main]))}
    return out


def collect_numbers(obj: Any) -> list[float]:
    """Every numeric value inside nested dict/list structures (and numbers inside strings)."""
    found: list[float] = []

    def walk(x: Any) -> None:
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float, np.integer, np.floating)):
            v = _num(x)
            if v is not None:
                found.append(v)
        elif isinstance(x, str):
            for m in re.findall(r"\d+(?:\.\d+)?", x):
                found.append(float(m))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(obj)
    return found
