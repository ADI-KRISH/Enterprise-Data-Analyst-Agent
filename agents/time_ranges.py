"""Deterministic resolution of relative time expressions against ANCHOR_DATE.

The LLM only classifies the expression into a `TimeRange`; the actual dates are computed
here (CLAUDE.md rule 6: anchor = MAX(order_date), never the system clock).
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Literal

from pydantic import BaseModel

Kind = Literal[
    "unspecified", "all_time", "this_month", "last_month", "this_quarter", "last_quarter",
    "this_fy", "last_fy", "ytd", "last_n_days", "last_n_months", "fiscal_quarter",
    "fiscal_year", "calendar_year", "month", "custom",
]


class TimeRange(BaseModel):
    kind: Kind = "unspecified"
    n: int | None = None  # last_n_days / last_n_months
    fiscal_year: int | None = None  # year the FY ends (Apr 2025-Mar 2026 = 2026)
    quarter: int | None = None  # fiscal quarter 1-4
    year: int | None = None  # calendar year (calendar_year / month)
    month: int | None = None  # 1-12
    start: str | None = None  # custom, ISO date
    end: str | None = None
    compare_to: Literal["none", "previous_period", "same_period_last_year"] = "none"


def _month_end(y: int, m: int) -> dt.date:
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _add_months(d: dt.date, k: int) -> dt.date:
    idx = d.year * 12 + (d.month - 1) + k
    return dt.date(idx // 12, idx % 12 + 1, 1)


def fiscal_year_of(d: dt.date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def fiscal_quarter_of(d: dt.date) -> int:
    return ((d.month - 4) % 12) // 3 + 1


def fq_bounds(fy: int, q: int) -> tuple[dt.date, dt.date]:
    start = _add_months(dt.date(fy - 1, 4, 1), 3 * (q - 1))
    return start, _month_end(*(lambda e: (e.year, e.month))(_add_months(start, 2)))


def fy_bounds(fy: int) -> tuple[dt.date, dt.date]:
    return dt.date(fy - 1, 4, 1), dt.date(fy, 3, 31)


def _label_fq(fy: int, q: int) -> str:
    return f"FY{fy - 1}-{str(fy)[2:]} Q{q}"


def _prev_fq(fy: int, q: int) -> tuple[int, int]:
    return (fy, q - 1) if q > 1 else (fy - 1, 4)


def _shift_year(d: dt.date, k: int) -> dt.date:
    try:
        return d.replace(year=d.year + k)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + k, day=28)


def resolve_time_range(tr: TimeRange, anchor: dt.date) -> dict | None:
    """-> {"start","end","label","comparison": {"start","end","label"}|None} or None (no filter)."""
    k = tr.kind
    prev: tuple[dt.date, dt.date, str] | None = None
    if k in ("unspecified", "all_time"):
        return None
    if k == "this_month":
        s, e, label = anchor.replace(day=1), anchor, f"{anchor:%B %Y} (to {anchor})"
        ps = _add_months(s, -1)
        prev = (ps, _month_end(ps.year, ps.month), f"{ps:%B %Y}")
    elif k == "last_month":
        s = _add_months(anchor.replace(day=1), -1)
        e, label = _month_end(s.year, s.month), f"{s:%B %Y}"
        ps = _add_months(s, -1)
        prev = (ps, _month_end(ps.year, ps.month), f"{ps:%B %Y}")
    elif k in ("this_quarter", "last_quarter"):
        fy, q = fiscal_year_of(anchor), fiscal_quarter_of(anchor)
        if k == "last_quarter":
            fy, q = _prev_fq(fy, q)
        s, e = fq_bounds(fy, q)
        if k == "this_quarter":
            e = min(e, anchor)
        label = _label_fq(fy, q)
        pfy, pq = _prev_fq(fy, q)
        ps, pe = fq_bounds(pfy, pq)
        prev = (ps, pe, _label_fq(pfy, pq))
    elif k in ("this_fy", "ytd", "last_fy", "fiscal_year"):
        fy = fiscal_year_of(anchor) if k in ("this_fy", "ytd") else (
            fiscal_year_of(anchor) - 1 if k == "last_fy" else tr.fiscal_year)
        if fy is None:
            return None
        s, e = fy_bounds(fy)
        if k in ("this_fy", "ytd"):
            e = min(e, anchor)
        label = f"FY{fy - 1}-{str(fy)[2:]}" + (" (to date)" if k in ("this_fy", "ytd") else "")
        ps, pe = fy_bounds(fy - 1)
        prev = (ps, _shift_year(e, -1) if k in ("this_fy", "ytd") else pe, f"FY{fy - 2}-{str(fy - 1)[2:]}")
    elif k == "last_n_days":
        n = max(tr.n or 30, 1)
        s, e, label = anchor - dt.timedelta(days=n - 1), anchor, f"last {n} days to {anchor}"
        prev = (s - dt.timedelta(days=n), s - dt.timedelta(days=1), f"previous {n} days")
    elif k == "last_n_months":
        n = max(tr.n or 3, 1)
        s = _add_months(anchor.replace(day=1), -(n - 1))
        e, label = anchor, f"last {n} months to {anchor}"
        ps = _add_months(s, -n)
        prev = (ps, s - dt.timedelta(days=1), f"previous {n} months")
    elif k == "fiscal_quarter":
        if tr.fiscal_year is None or tr.quarter is None:
            return None
        s, e = fq_bounds(tr.fiscal_year, tr.quarter)
        label = _label_fq(tr.fiscal_year, tr.quarter)
        pfy, pq = _prev_fq(tr.fiscal_year, tr.quarter)
        ps, pe = fq_bounds(pfy, pq)
        prev = (ps, pe, _label_fq(pfy, pq))
    elif k == "calendar_year":
        if tr.year is None:
            return None
        s, e, label = dt.date(tr.year, 1, 1), dt.date(tr.year, 12, 31), f"calendar {tr.year}"
        prev = (dt.date(tr.year - 1, 1, 1), dt.date(tr.year - 1, 12, 31), f"calendar {tr.year - 1}")
    elif k == "month":
        if tr.year is None or tr.month is None:
            return None
        s = dt.date(tr.year, tr.month, 1)
        e, label = _month_end(tr.year, tr.month), f"{s:%B %Y}"
        ps = _add_months(s, -1)
        prev = (ps, _month_end(ps.year, ps.month), f"{ps:%B %Y}")
    elif k == "custom":
        if not (tr.start and tr.end):
            return None
        s, e = dt.date.fromisoformat(tr.start), dt.date.fromisoformat(tr.end)
        label = f"{s} to {e}"
        span = (e - s).days + 1
        prev = (s - dt.timedelta(days=span), s - dt.timedelta(days=1), "previous period")
    else:  # pragma: no cover
        return None

    comparison = None
    if tr.compare_to == "previous_period" and prev:
        comparison = {"start": prev[0].isoformat(), "end": prev[1].isoformat(), "label": prev[2]}
    elif tr.compare_to == "same_period_last_year":
        comparison = {"start": _shift_year(s, -1).isoformat(), "end": _shift_year(e, -1).isoformat(),
                      "label": f"same period last year ({_shift_year(s, -1)} to {_shift_year(e, -1)})"}
    out = {"start": s.isoformat(), "end": e.isoformat(), "label": label, "comparison": comparison}
    if prev:  # always expose the preceding period: handy for "why did X drop" questions
        out["previous_period"] = {"start": prev[0].isoformat(), "end": prev[1].isoformat(),
                                  "label": prev[2]}
    return out
