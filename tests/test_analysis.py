import pandas as pd
import pytest

from analytics.analysis import analyze, driver_breakdown, growth, pct_change, period_compare, share, top_n
from analytics.formatting import format_analysis, format_cell, format_inr
from visualization.charts import build_chart, choose_chart


def test_pct_change_and_zero_base():
    assert pct_change(110, 100) == 10.0
    assert pct_change(5, 0) is None


def test_growth_series():
    df = pd.DataFrame({"month": ["2026-01", "2026-02", "2026-03"], "revenue": [100.0, 120.0, 90.0]})
    g = growth(df, "month", "revenue")
    assert [p["pct_change"] for p in g["periods"]] == [None, 20.0, -25.0]
    assert g["first_to_last_pct_change"] == -10.0
    assert g["peak"]["period"] == "2026-02"


def test_share_and_top_n():
    df = pd.DataFrame({"category": ["A", "B", "C"], "revenue": [50.0, 30.0, 20.0]})
    assert [s["share_pct"] for s in share(df, "category", "revenue")] == [50.0, 30.0, 20.0]
    assert top_n(df, "category", "revenue", 2)["top"][0] == {"category": "A", "value": 50.0}


def test_driver_breakdown_ranks_by_absolute_change():
    df = pd.DataFrame({"region": ["N", "S", "W"], "cur": [100.0, 50.0, 140.0], "prev": [100.0, 100.0, 100.0]})
    d = driver_breakdown(df, "region", "cur", "prev")
    assert [r["member"] for r in d["drivers"]] == ["S", "W", "N"]
    assert d["total"] == period_compare(290.0, 300.0)
    assert d["drivers"][0]["share_of_movement_pct"] == pytest.approx(-55.56, abs=0.01)


def test_analyze_wide_comparison():
    df = pd.DataFrame({"region_name": ["N", "S"], "current_revenue": [10.0, 5.0], "previous_revenue": [10.0, 10.0]})
    out = analyze(df, "diagnostic")
    assert out["driver_breakdown"]["drivers"][0]["member"] == "S"


def test_analyze_long_two_period_layout():
    df = pd.DataFrame({"region": ["N", "S", "N", "S"], "fiscal_quarter": [1, 1, 2, 2], "revenue": [10.0, 10.0, 10.0, 5.0]})
    out = analyze(df)
    assert out["period_labels"] == {"current": "2", "previous": "1"}
    assert out["driver_breakdown"]["total"]["change"] == -5.0


def test_analyze_time_series_and_ranking_and_scalar():
    ts = pd.DataFrame({"month": ["2026-01", "2026-02"], "orders": [10, 15]})
    assert analyze(ts)["growth"]["first_to_last_pct_change"] == 50.0
    rk = pd.DataFrame({"category": ["A", "B"], "revenue": [3.0, 1.0]})
    assert "share" in analyze(rk) and "top_n" in analyze(rk)
    assert "growth" not in analyze(pd.DataFrame({"total_revenue": [5.0]}))
    assert analyze(pd.DataFrame({"x": []})) == {"row_count": 0, "intent": None}


def test_non_additive_columns_have_no_total_or_share():
    df = pd.DataFrame({"category": ["A", "B"], "gross_margin_pct": [30.0, 40.0]})
    out = analyze(df)
    assert "total" not in out["stats"]["gross_margin_pct"] and "share" not in out


def test_chart_choice():
    assert choose_chart(pd.DataFrame({"month": ["a", "b", "c"], "revenue": [1, 2, 3]})) == "line"
    assert choose_chart(pd.DataFrame({"category": list("ab"), "revenue": [1, 2]})) == "bar"
    assert choose_chart(pd.DataFrame({"category": list("abcdefgh"), "revenue": range(8)})) == "hbar"
    assert choose_chart(pd.DataFrame({"r": list("ab"), "current_revenue": [1, 2], "previous_revenue": [2, 2]})) == "grouped_bar"
    assert choose_chart(pd.DataFrame({"total": [5]})) is None
    assert choose_chart(pd.DataFrame({"category": []})) is None


def test_chart_spec_is_plotly_json():
    spec = build_chart(pd.DataFrame({"month": ["a", "b", "c"], "revenue": [1.0, 2.0, 3.0]}))
    assert spec["data"][0]["type"] == "scatter" and spec["layout"]["title"]["text"] == "Revenue by Month"


def test_formatting():
    assert format_inr(45230128) == "₹4.52 crore"
    assert format_inr(8_772_064) == "₹87.72 lakh"
    assert format_inr(1234.5) == "₹1,234.50"
    assert format_cell("gross_margin_pct", 31.234) == "31.2%"
    assert format_cell("orders", 12345) == "12,345"
    out = format_analysis({"total": {"current": 45230128.0, "pct_change": 24.06}}, money=True)
    assert out == {"total": {"current": "₹4.52 crore", "pct_change": "24.1%"}}
