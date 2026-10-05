"""Post-check: every number in the final answer must trace to an executed SQL result or a
deterministic Pandas computation on it (CLAUDE.md rule 1).

Handles rounding (tolerance = half a unit of the stated precision), percentages
(value or fraction), and Indian/international scale words (lakh, crore, k, million, billion).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from analytics.analysis import collect_numbers

_SCALES = {
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "l": 1e5,
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}
_NUM = re.compile(
    r"(?<![\w.])(?P<prefix>[A-Za-z]{0,2})(?P<cur>₹|Rs\.?|INR)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<pct>\s?%)?(?:\s?(?P<scale>lakhs?|lacs?|crores?|cr|thousand|million|billion|mn|bn|[kKmMbBlL])\b)?",
)
_REL_TOL = 1e-6


@dataclass
class GroundingReport:
    grounded: bool
    checked: int
    ungrounded: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"grounded": self.grounded, "checked": self.checked, "ungrounded": self.ungrounded}


def extract_numbers(text: str) -> list[dict]:
    """Numbers in `text` -> [{"raw", "value", "tol", "pct"}] (scaled, abs value, with tolerance)."""
    found = []
    for line in text.splitlines():
        # drop list ordinals ("1.", "2)") and markdown headers' numbering
        line = re.sub(r"^\s*(?:[-*]\s+)?\d{1,2}[.)]\s+", "", line)
        for m in _NUM.finditer(line):
            prefix = m.group("prefix") or ""
            if prefix.upper() in ("Q", "FY", "H", "WK", "V", "NO") or prefix.upper().endswith(("FY", "Q")):
                continue  # Q4, FY26, H1 labels
            raw = m.group("num")
            digits = raw.replace(",", "")
            # fiscal-year spans like 2025-26 / dates like 2026-03-31: skip tokens joined by hyphen
            start, end = m.start("num"), m.end("num")
            if (start > 0 and line[start - 1] == "-" and start > 1 and line[start - 2].isdigit()) or (
                end < len(line) - 1 and line[end] == "-" and line[end + 1].isdigit()
            ):
                continue
            value = float(digits)
            decimals = len(digits.split(".")[1]) if "." in digits else 0
            scale_word = (m.group("scale") or "")
            scale = _SCALES.get(scale_word.lower(), 1.0) if scale_word else 1.0
            if scale_word and scale_word.lower() in ("l", "m", "b", "k") and not (
                m.group("cur") or "." in digits or value >= 1
            ):
                scale = 1.0
            tol = 0.5 * 10 ** (-decimals) * scale
            found.append({"raw": m.group(0).strip(), "value": abs(value * scale), "tol": tol,
                          "pct": bool(m.group("pct"))})
    return found


def _matches(item: dict, allowed: list[float]) -> bool:
    v, tol = item["value"], item["tol"]
    cands = [v]
    if item["pct"]:
        cands.append(v / 100)  # 0.123 stored, "12.3%" stated
    for a in allowed:
        a = abs(a)
        for c in cands:
            if abs(c - a) <= tol + _REL_TOL * max(a, 1.0):
                return True
        if item["pct"] and abs(v - a * 100) <= tol + _REL_TOL * max(a * 100, 1.0):
            return True
    return False


def check_grounding(answer: str, *sources, extra_allowed: list[float] | None = None) -> GroundingReport:
    """`sources`: any nested structures (result frame, analysis, question, time window...)."""
    allowed = collect_numbers(list(sources)) + list(extra_allowed or [])
    items = extract_numbers(answer)
    bad = [i["raw"] for i in items if not _matches(i, allowed)]
    return GroundingReport(grounded=not bad, checked=len(items), ungrounded=bad)
