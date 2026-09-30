"""Resolve relative time phrases against ANCHOR_DATE (CLAUDE.md rule 6, reporting_policies.md).

Deterministic: the LLM only extracts the phrase; dates come from these rules, so "last
quarter" can never be resolved against the system clock or mis-computed by a model.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from pydantic import BaseModel

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS |= {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS["sept"] = 9
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18,
              "twenty-four": 24, "a": 1}
_N = r"(\d+|" + "|".join(_NUM_WORDS) + r")"

COMPARE_WORDS = re.compile(
    r"\b(vs\.?|versus|compared?|comparison|against|growth|grew|change[ds]?|increase[ds]?|decrease[ds]?|"
    r"decline[ds]?|drop(ped|s)?|fell|fall(en|ing)?|rise|rose|down|up|why|mom|qoq|yoy)\b", re.I)
YOY_WORDS = re.compile(r"\b(yoy|year[- ]over[- ]year|year[- ]on[- ]year|same (quarter|month|period) (last|previous|prior) year)\b", re.I)


class TimeRange(BaseModel):
    label: str
    start: date
    end: date
    comparison_label: str | None = None
    comparison_start: date | None = None
    comparison_end: date | None = None
    source: str = "rule"  # rule | llm | default
    months: int | None = None  # length in whole calendar months, if month-aligned


# ------------------------------------------------------------------------ date helpers

def month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def add_months(d: date, n: int) -> date:
    """First day of the month n months after d's month."""
    idx = d.year * 12 + (d.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def fy_start(d: date) -> date:
    """Start of the Indian fiscal year (April) containing d."""
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def last_complete_month_end(anchor: date) -> date:
    return anchor if anchor == month_end(anchor.year, anchor.month) else add_months(anchor, 0) - timedelta(days=1)


def format_range(start: date, end: date) -> str:
    month_aligned = start.day == 1 and end == month_end(end.year, end.month)
    if not month_aligned:
        return f"{start.isoformat()} to {end.isoformat()}"
    s, e = start.strftime("%b"), end.strftime("%b")
    if (start.year, start.month) == (end.year, end.month):
        return f"{s} {start.year}"
    if start.year == end.year:
        return f"{s}–{e} {start.year}"
    return f"{s} {start.year}–{e} {end.year}"


def _months_between(start: date, end: date) -> int | None:
    if start.day != 1 or end != month_end(end.year, end.month):
        return None
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _period(start: date, end: date, label: str | None = None) -> TimeRange:
    rng = format_range(start, end)
    return TimeRange(label=f"{label} ({rng})" if label else rng, start=start, end=end,
                     months=_months_between(start, end))


def _num(token: str) -> int:
    return int(token) if token.isdigit() else _NUM_WORDS[token.lower()]


def _fy_year(token: str) -> int:
    """'26' / '2026' / '2025-26' → start year of that fiscal year (FY26 = FY2025-26)."""
    token = token.strip()
    if "-" in token:
        first = token.split("-")[0]
        return int(first) if len(first) == 4 else 2000 + int(first)
    y = int(token)
    y = y if y > 100 else 2000 + y
    return y - 1  # FY named by its end year: FY26 ends March 2026


# ------------------------------------------------------------------------------ rules

def _resolve_primary(q: str, anchor: date) -> TimeRange | None:
    fiscal = bool(re.search(r"\b(fiscal|financial year|fy)\b", q, re.I))
    lcm_end = last_complete_month_end(anchor)

    if m := re.search(rf"\b(last|past|previous|trailing)\s+{_N}\s+days?\b", q, re.I):
        n = _num(m.group(2))
        return _period(anchor - timedelta(days=n - 1), anchor, f"last {n} days")
    if m := re.search(rf"\b(last|past|previous|trailing)\s+{_N}\s+(months?|quarters?|years?)\b", q, re.I):
        n = _num(m.group(2)) * {"m": 1, "q": 3, "y": 12}[m.group(3)[0].lower()]
        start = add_months(date(lcm_end.year, lcm_end.month, 1), -(n - 1))
        return _period(start, lcm_end, f"last {n} months")
    if re.search(r"\b(fytd|fiscal year to date|financial year to date)\b", q, re.I):
        return _period(fy_start(anchor), anchor, "FYTD")
    if re.search(r"\b(ytd|year to date)\b", q, re.I):
        return _period(date(anchor.year, 1, 1), anchor, "YTD")

    # FY26 / FY2025-26 / FY 2026-27, optionally with a fiscal quarter
    if m := re.search(r"\bfy\s?'?(\d{4}\s?-\s?\d{2,4}|\d{2}\s?-\s?\d{2}|\d{4}|\d{2})\b", q, re.I):
        y = _fy_year(m.group(1).replace(" ", ""))
        if qm := re.search(r"\bq([1-4])\b", q, re.I):
            qs = add_months(date(y, 4, 1), 3 * (int(qm.group(1)) - 1))
            return _period(qs, month_end(add_months(qs, 2).year, add_months(qs, 2).month),
                           f"FY{y}-{str(y + 1)[2:]} Q{qm.group(1)}")
        return _period(date(y, 4, 1), date(y + 1, 3, 31), f"FY{y}-{str(y + 1)[2:]}")
    # Q3 2026 / Q3 of 2026 / 2026 Q3 (calendar quarters unless the question says fiscal)
    if m := re.search(r"\bq([1-4])\s*(?:of\s+|,\s*)?(20\d\d)\b|\b(20\d\d)\s*q([1-4])\b", q, re.I):
        qn, y = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(4)), int(m.group(3)))
        qs = date(y, 3 * (qn - 1) + 1, 1)
        return _period(qs, month_end(y, qs.month + 2), f"Q{qn} {y}")
    # "September 2026", "Sep 2026"
    if m := re.search(rf"\b({_MONTH_RE})\.?\s+(20\d\d)\b", q, re.I):
        mo, y = MONTHS[m.group(1).lower()], int(m.group(2))
        return _period(date(y, mo, 1), month_end(y, mo))

    if re.search(r"\b(last|previous|past|prior)\s+(fiscal|financial)\s+year\b", q, re.I):
        start = add_months(fy_start(anchor), -12)
        if anchor == date(fy_start(anchor).year + 1, 3, 31):
            start = fy_start(anchor)
        return _period(start, date(start.year + 1, 3, 31), "last fiscal year")
    if re.search(r"\b(this|current)\s+(fiscal|financial)\s+year\b", q, re.I):
        return _period(fy_start(anchor), anchor, "this fiscal year to date")
    latest_words = re.search(r"\b(latest|recent|current)\b(?!.*\b(month|year|week|days?|quarter)\b)", q, re.I)
    if re.search(r"\b(last|previous|past|prior|latest|recent|most recent)\s+(complete\s+)?quarter\b", q, re.I) \
            or latest_words:
        # "latest / recent / current" without a period = latest complete quarter (policy)
        qs = quarter_start(lcm_end)
        qend = month_end(add_months(qs, 2).year, add_months(qs, 2).month)
        if qend > anchor:  # latest quarter not complete yet: take the one before
            qs = add_months(qs, -3)
            qend = month_end(add_months(qs, 2).year, add_months(qs, 2).month)
        label = "last quarter" if re.search(r"\b(last|previous|past|prior)\b", q, re.I) else "latest quarter"
        return _period(qs, qend, f"{label}, Q{(qs.month - 1) // 3 + 1} {qs.year}")
    if re.search(r"\b(this|current)\s+quarter\b", q, re.I):
        qs = quarter_start(anchor)
        return _period(qs, anchor, "this quarter")
    if re.search(r"\b(last|previous|past|prior)\s+month\b", q, re.I):
        return _period(date(lcm_end.year, lcm_end.month, 1), lcm_end, "last month")
    if re.search(r"\b(this|current)\s+month\b", q, re.I):
        return _period(date(anchor.year, anchor.month, 1), anchor, "this month")
    if re.search(r"\b(last|previous|past|prior)\s+year\b", q, re.I) and not YOY_WORDS.search(q):
        y = anchor.year if anchor == date(anchor.year, 12, 31) else anchor.year - 1
        return _period(date(y, 1, 1), date(y, 12, 31), "last year")
    if re.search(r"\b(this|current)\s+year\b", q, re.I):
        return _period(date(anchor.year, 1, 1), anchor, "this year to date")
    if not fiscal and (m := re.search(r"\b(?:in|for|during|of)\s+(20\d\d)\b", q, re.I)):
        y = int(m.group(1))
        return _period(date(y, 1, 1), date(y, 12, 31), str(y))
    return None


def _year_earlier(d: date) -> date:
    return d.replace(year=d.year - 1, day=min(d.day, calendar.monthrange(d.year - 1, d.month)[1]))


def _comparison(tr: TimeRange, question: str) -> TimeRange:
    """Attach the comparison period: same period a year earlier (YoY) or the preceding one."""
    if YOY_WORDS.search(question):
        cs, ce = _year_earlier(tr.start), _year_earlier(tr.end)
        if tr.end == month_end(tr.end.year, tr.end.month):
            ce = month_end(ce.year, ce.month)
        label = "same period last year"
    elif tr.months:
        cs = add_months(tr.start, -tr.months)
        ce = tr.start - timedelta(days=1)
        label = "previous period"
    else:
        days = (tr.end - tr.start).days + 1
        cs, ce = tr.start - timedelta(days=days), tr.start - timedelta(days=1)
        label = "previous period"
    return tr.model_copy(update={
        "comparison_start": cs, "comparison_end": ce,
        "comparison_label": f"{label} ({format_range(cs, ce)})",
    })


def resolve(question: str, anchor: date, *, compare: bool = False) -> TimeRange | None:
    """Resolve the question's time phrase; add a comparison period for compare/diagnose."""
    tr = _resolve_primary(question, anchor)
    if tr is None:
        return None
    if compare or COMPARE_WORDS.search(question):
        tr = _comparison(tr, question)
    return tr


def default_range(data_start: date, anchor: date) -> TimeRange:
    rng = format_range(data_start, anchor)
    return TimeRange(label=f"all available data ({rng})", start=data_start, end=anchor,
                     source="default", months=_months_between(data_start, anchor))
