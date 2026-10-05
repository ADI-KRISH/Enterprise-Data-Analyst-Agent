"""INR / percent / count formatting shared by the responder and the grounding check."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

LAKH, CRORE = 1e5, 1e7
_MONEY_COL = re.compile(r"(revenue|sales|cost|cogs|profit|aov|amount|price|value|spend|turnover|income)", re.I)
_PCT_COL = re.compile(r"(pct|percent|rate|share|margin|growth)", re.I)
_PCT_KEYS = {"pct_change", "share_pct", "share_of_movement_pct", "first_to_last_pct_change"}
_AMOUNT_KEYS = {"value", "change", "current", "previous", "total", "min", "max", "mean", "first", "last"}
_COUNT_KEYS = {"row_count"}


def format_inr(x: float, decimals: int = 2) -> str:
    sign, v = ("-" if x < 0 else ""), abs(float(x))
    if v >= CRORE:
        return f"{sign}₹{v / CRORE:.{decimals}f} crore"
    if v >= LAKH:
        return f"{sign}₹{v / LAKH:.{decimals}f} lakh"
    return f"{sign}₹{v:,.2f}" if v % 1 else f"{sign}₹{v:,.0f}"


def format_pct(x: float, decimals: int = 1) -> str:
    return f"{x:.{decimals}f}%"


def format_number(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}"


def is_money_col(name: str) -> bool:
    return bool(_MONEY_COL.search(name)) and not _PCT_COL.search(name)


def is_pct_col(name: str) -> bool:
    return bool(_PCT_COL.search(name))


def format_cell(col: str, value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if is_pct_col(col):
        return format_pct(value)
    if is_money_col(col):
        return format_inr(value)
    return format_number(value)


def format_analysis(obj: Any, money: bool, key: str = "") -> Any:
    """Copy of an analysis dict with numbers replaced by display strings (₹ lakh/crore, %, counts)."""
    if isinstance(obj, dict):
        return {k: format_analysis(v, money, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [format_analysis(v, money, key) for v in obj]
    if obj is None or isinstance(obj, (str, bool)) or not isinstance(obj, (int, float)):
        return obj
    if key in _PCT_KEYS:
        return format_pct(obj)
    if key in _AMOUNT_KEYS:
        return format_inr(obj) if money else format_number(obj)
    return format_number(obj) if key in _COUNT_KEYS or isinstance(obj, int) else obj
