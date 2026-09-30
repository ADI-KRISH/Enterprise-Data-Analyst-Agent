"""Check that every number in an answer comes from the data (CLAUDE.md rule 1).

Numbers are extracted from the answer (₹, commas, lakh/crore/K/M, %) and each one must
match a value from the executed results, the deterministic analysis, the question, or a
row count, within the rounding its displayed precision implies. Dates, years, quarter
and fiscal-year labels are not treated as claims.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

MAX_COUNTED_ROWS = 20
UNITS: dict[str, float] = {
    "k": 1e3, "thousand": 1e3,
    "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "cr": 1e7, "crore": 1e7, "crores": 1e7,
    "m": 1e6, "mn": 1e6, "million": 1e6,
    "bn": 1e9, "billion": 1e9,
}
_UNIT_RE = "|".join(sorted(UNITS, key=len, reverse=True))
NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    rf"(?:\s*(?P<pct>%|percent\b|per cent\b|pp\b|percentage points?\b)|\s?(?P<unit>{_UNIT_RE})\b\.?)?",
    re.IGNORECASE,
)
_MONTHS = (r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|"
           r"sep(t(ember)?)?|oct(ober)?|nov(ember)?|dec(ember)?")
# Spans that are labels, not numeric claims.
NON_CLAIMS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO dates
    re.compile(r"\bFY\s?'?\d{2,4}(\s?[-–/]\s?\d{2,4})?\b", re.I),  # FY2026-27, FY26
    re.compile(r"\b[QH][1-4]\b", re.I),  # Q3, H1
    re.compile(rf"\b(\d{{1,2}}(st|nd|rd|th)?\s+)?({_MONTHS})\.?(\s+\d{{1,2}}(st|nd|rd|th)?\b,?)?"
               rf"(\s*[–-]\s*({_MONTHS})\.?)?(\s+'?\d{{2,4}})?\b", re.I),  # 14 Aug 2026, Jul–Sep 2026
    re.compile(r"\b(19|20)\d{2}\b(?!\s*(%|lakh|crore|cr\b|l\b|orders|units|customers))", re.I),  # bare years
    re.compile(r"\b(top|bottom|last|first|past|next)\s+\d+\b", re.I),  # "top 5", "last 30"
    re.compile(r"\b\d+(st|nd|rd|th)\b", re.I),  # ordinals
]


@dataclass
class GroundingResult:
    grounded: bool
    checked: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"grounded": self.grounded, "checked": self.checked, "unsupported": self.unsupported}


def _numbers_in(value: Any) -> Iterable[float]:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        if math.isfinite(value):
            yield float(value)
    elif isinstance(value, str):
        for m in re.finditer(r"\d+(?:\.\d+)?", value.replace(",", "")):
            yield float(m.group(0))
    elif isinstance(value, dict):
        for v in value.values():
            yield from _numbers_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _numbers_in(v)


def allowed_values(results: list[dict[str, Any]], analysis: Any, question: str) -> set[float]:
    values: set[float] = set()
    for res in results:
        for rec in res.get("records", []):
            values.update(_numbers_in(rec))
        n = res.get("row_count", len(res.get("records", [])))
        # counts of rows ("3 categories"), small counts only, plus the exact row count
        values.update(float(i) for i in range(0, min(n, MAX_COUNTED_ROWS) + 1))
        values.add(float(n))
    values.update(_numbers_in(analysis))
    values.update(_numbers_in(question))
    return {abs(v) for v in values}


def _strip_non_claims(text: str) -> str:
    for pattern in NON_CLAIMS:
        text = pattern.sub(" ", text)
    return text


def _matches(shown: float, decimals: int, scale: float, is_pct: bool, allowed: set[float]) -> bool:
    candidate = abs(shown) * scale
    tol = 0.5 * 10 ** (-decimals) * scale + 1e-9
    for a in allowed:
        if abs(a - candidate) <= tol:
            return True
        if is_pct and abs(a * 100 - candidate) <= tol:  # ratio stored as a fraction
            return True
        # a money value shown in plain rupees but rounded to a unit like ₹96.7 L
        if not is_pct and scale == 1 and decimals == 0 and abs(a - candidate) <= 0.5:
            return True
    return False


def check(answer: str, results: list[dict[str, Any]], analysis: Any, question: str) -> GroundingResult:
    allowed = allowed_values(results, analysis, question)
    checked, unsupported = [], []
    for m in NUMBER_RE.finditer(_strip_non_claims(answer or "")):
        raw = m.group(0).strip().rstrip(".")
        num = m.group("num").replace(",", "")
        decimals = len(num.split(".")[1]) if "." in num else 0
        unit = (m.group("unit") or "").lower().rstrip(".")
        scale = UNITS.get(unit, 1.0)
        is_pct = bool(m.group("pct"))
        checked.append(raw)
        if not _matches(float(num), decimals, scale, is_pct, allowed):
            unsupported.append(raw)
    return GroundingResult(grounded=not unsupported, checked=checked, unsupported=unsupported)
