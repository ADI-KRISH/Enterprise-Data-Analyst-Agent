"""Deterministic resolution of relative periods against ANCHOR_DATE (never the system clock).

Policy (retrieval/docs/reporting_policies.md): "quarter"/"year" are Indian fiscal (Apr-Mar); "last X" is the most
recent *complete* period as of the anchor (the anchor's own period counts as complete when the anchor is its last
day); "this X" is the period containing the anchor, to date.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

Period = tuple[dt.date, dt.date, str]  # (start, end, label)


def _month_end(y: int, m: int) -> dt.date:
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _add_months(d: dt.date, n: int) -> dt.date:
    idx = d.year * 12 + (d.month - 1) + n
    return dt.date(idx // 12, idx % 12 + 1, 1)


def fiscal_year_of(d: dt.date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def _quarter_start(d: dt.date, fiscal: bool) -> dt.date:
    if fiscal:
        offset = (d.month - 4) % 12
        return _add_months(dt.date(d.year, d.month, 1), -(offset % 3))
    return dt.date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _quarter_label(start: dt.date, fiscal: bool) -> str:
    if fiscal:
        q = ((start.month - 4) % 12) // 3 + 1
        return f"Q{q} FY{fiscal_year_of(start)} ({start:%b}–{_add_months(start, 2):%b %Y})"
    return f"CY Q{(start.month - 1) // 3 + 1} {start.year}"



def resolve_relative_period(text: str, anchor: dt.date) -> Period | None:
    """Map common relative phrases to (start, end, label); None when the phrase is not recognised."""
    t = text.lower()
    fiscal = "calendar" not in t

    if m := re.search(r"last\s+(\d+)\s+days?", t):
        n = int(m.group(1))
        return anchor - dt.timedelta(days=n - 1), anchor, f"last {n} days"
    if m := re.search(r"last\s+(\d+)\s+months?", t):
        n = int(m.group(1))
        start = _add_months(dt.date(anchor.year, anchor.month, 1), -(n - 1))
        return start, anchor, f"last {n} months"

    if re.search(r"year[- ]to[- ]date|\bytd\b", t):
        start = dt.date(anchor.year if anchor.month >= 4 else anchor.year - 1, 4, 1) if fiscal else dt.date(anchor.year, 1, 1)
        return start, anchor, "year to date"

    for unit in ("quarter", "month", "year"):
        for which in ("this", "last", "previous"):
            if re.search(rf"\b{which}\s+(?:calendar\s+|fiscal\s+)?{unit}\b", t):
                return _period(unit, "this" if which == "this" else "last", anchor, fiscal)
    return None


def _period(unit: str, which: str, anchor: dt.date, fiscal: bool) -> Period:
    if unit == "month":
        start = dt.date(anchor.year, anchor.month, 1)
        end_cur = _month_end(anchor.year, anchor.month)
        if which == "last" and anchor != end_cur:
            start = _add_months(start, -1)
        end = _month_end(start.year, start.month) if which == "last" else anchor
        return start, end, f"{'last' if which == 'last' else 'this'} month ({start:%B %Y})"
    if unit == "quarter":
        start = _quarter_start(anchor, fiscal)
        end_cur = _month_end(*divmod_ym(_add_months(start, 2)))
        if which == "last" and anchor != end_cur:
            start = _add_months(start, -3)
        end = _month_end(*divmod_ym(_add_months(start, 2))) if which == "last" else anchor
        return start, end, f"{which} quarter = {_quarter_label(start, fiscal)}"
    # year
    if fiscal:
        start = dt.date(anchor.year if anchor.month >= 4 else anchor.year - 1, 4, 1)
        end_cur = _month_end(start.year + 1, 3)
        if which == "last" and anchor != end_cur:
            start = dt.date(start.year - 1, 4, 1)
        end = _month_end(start.year + 1, 3) if which == "last" else anchor
        return start, end, f"{which} year = FY{fiscal_year_of(start)}"
    start = dt.date(anchor.year, 1, 1)
    if which == "last" and anchor != dt.date(anchor.year, 12, 31):
        start = dt.date(anchor.year - 1, 1, 1)
    end = dt.date(start.year, 12, 31) if which == "last" else anchor
    return start, end, f"{which} calendar year = {start.year}"


def divmod_ym(d: dt.date) -> tuple[int, int]:
    return d.year, d.month


def previous_period(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date]:
    """The period of equal length immediately before [start, end] (whole-month aware)."""
    if start.day == 1 and end == _month_end(end.year, end.month):
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        prev_start = _add_months(start, -months)
        return prev_start, start - dt.timedelta(days=1)
    length = (end - start).days + 1
    return start - dt.timedelta(days=length), start - dt.timedelta(days=1)
