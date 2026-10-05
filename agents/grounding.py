"""Grounding check: every number in the answer must come from the SQL result or the deterministic analysis.

Numbers are extracted from the answer text and matched (with tolerance for the rounding the answer displays,
%, and lakh / crore / thousand / million formatting) against:
  * numeric cells of the result,
  * numbers in the analysis dict,
  * numbers that are part of the question, the resolved time range, or string cells (dates, labels, "top 5"),
  * row count, and rank positions 1..N for results of up to 10 rows.
Anything else is reported as ungrounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

_SCALES = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7,
           "k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}

_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.,])"
    r"(?:₹\s?|Rs\.?\s?|INR\s?)?"
    r"(\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(%|percent\b|lakhs?\b|lacs?\b|crores?\b|cr\b|k\b|thousand\b|mn\b|million\b|bn\b|billion\b|m\b))?"
    r"(?![A-Za-z0-9_]|\.\d)(?!(?:st|nd|rd|th)\b)",
    re.I,
)
# Patterns whose digits are labels, not claims.
_STRIP = [
    re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b"),                                     # 2026-07, 2026-07-01
    re.compile(r"\bFY\s?\d{2,4}(?:\s?[-–/]\s?\d{2,4})?", re.I),                    # FY2027, FY2026-27
    re.compile(r"\bQ[1-4]\b", re.I),
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?(?:\s+\d{4})?", re.I),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}?,?\s*\d{4}", re.I),
    re.compile(r"(?m)^\s*(?:[-*•]\s*)?\d+[.)]\s"),                                 # list numbering
]


@dataclass
class Mention:
    text: str
    value: float          # as displayed, before applying the unit scale
    decimals: int
    scale: float
    is_pct: bool

    @property
    def actual(self) -> float:
        return self.value * self.scale

    @property
    def tolerance(self) -> float:
        return 0.5 * 10 ** (-self.decimals) * self.scale * 1.0001 + 1e-9


@dataclass
class GroundingResult:
    grounded: bool
    ungrounded: list[str] = field(default_factory=list)
    checked: int = 0


def extract_mentions(text: str) -> list[Mention]:
    for pat in _STRIP:
        text = pat.sub(" ", text)
    out = []
    for m in _NUMBER.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        digits = raw.replace(",", "")
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        out.append(Mention(m.group(0).strip(), float(digits), decimals, _SCALES.get(unit, 1.0), unit in ("%", "percent")))
    return out


def _walk_numbers(obj: Any) -> Iterable[float]:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_numbers(v)


def _context_numbers(*texts: Any) -> set[float]:
    found: set[float] = set()
    for t in texts:
        if not isinstance(t, str):
            continue
        for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", t):
            found.add(float(m.group(0).replace(",", "")))
    return found


def allowed_values(
    question: str,
    result: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    time_range: dict[str, Any] | None = None,
    extra_text: Iterable[str] = (),
) -> set[float]:
    result = result or {}
    records = result.get("records", [])
    allowed: set[float] = set()
    string_cells: list[str] = []
    for row in records:
        for v in row.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                allowed.add(abs(float(v)))
            elif isinstance(v, str):
                string_cells.append(v)
    allowed |= {abs(x) for x in _walk_numbers(analysis)}
    allowed |= _context_numbers(question, *(time_range or {}).values(), *string_cells, *extra_text)
    n = len(records)
    allowed.add(float(n))
    if n <= 10:
        allowed |= {float(i) for i in range(1, n + 1)}
    return allowed


def check_grounding(answer: str, allowed: set[float]) -> GroundingResult:
    ungrounded, checked = [], 0
    for m in extract_mentions(answer):
        checked += 1
        actual = m.actual
        candidates = [actual]
        ok = any(abs(c - actual) <= m.tolerance for c in allowed)
        if not ok and m.is_pct:  # ratio stored as 0.123 shown as 12.3%
            ok = any(abs(c * 100 - actual) <= m.tolerance for c in allowed if c <= 1.5)
        if not ok:
            ungrounded.append(m.text)
    return GroundingResult(not ungrounded, ungrounded, checked)
