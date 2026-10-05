from agents.grounding import allowed_values, check_grounding, extract_mentions

RESULT = {"records": [
    {"region_name": "South India", "current_revenue": 14455679.5, "previous_revenue": 23275622.0},
    {"region_name": "West India", "current_revenue": 45230128.0, "previous_revenue": 36458064.0},
]}
ANALYSIS = {"drivers": [{"pct_change": -37.89}, {"pct_change": 24.06}], "row_count": 2}
TIME = {"description": "last quarter = Q2 FY2027 (Jul–Sep 2026)", "start": "2026-07-01", "end": "2026-09-30"}


def check(answer, question="why did revenue change last quarter?"):
    return check_grounding(answer, allowed_values(question, RESULT, ANALYSIS, TIME))


def test_grounded_answer_with_lakh_crore_and_percent():
    res = check("South India revenue fell 37.9% to ₹1.45 crore (from ₹2.33 crore) in Q2 FY2027, 2026-07-01 to 2026-09-30.")
    assert res.grounded, res.ungrounded


def test_invented_numbers_flagged():
    res = check("South India revenue was ₹9.99 crore, down 55.5%.")
    assert not res.grounded
    assert {"₹9.99 crore", "55.5%"} <= set(res.ungrounded)


def test_rounding_tolerance_respects_displayed_precision():
    assert check("West India: ₹4.52 crore").grounded          # 45230128 -> 4.52 crore
    assert not check("West India: ₹4.6 crore").grounded       # off by more than half a unit of the last digit
    assert check("₹45,230,128").grounded                      # exact with commas
    assert check("₹4,52,30,128").grounded                     # Indian grouping


def test_percent_of_ratio_and_signs():
    assert check("a drop of 37.89%").grounded                 # sign dropped, value matches
    ratio = allowed_values("q", {"records": [{"share": 0.123}]}, {})
    assert check_grounding("12.3% share", ratio).grounded


def test_labels_and_counts_not_flagged():
    res = check("Across the 2 regions, Q2 FY2027 (FY2026-27) 1st and 2nd were South and West. 1. South")
    assert res.grounded, res.ungrounded


def test_numbers_from_question_allowed():
    assert check_grounding("Here are the top 5 results", allowed_values("show top 5 categories", {"records": []}, {})).grounded


def test_extract_ignores_dates_and_fiscal_labels():
    assert extract_mentions("FY2027 Q3 2026-09-30 15 March 2026") == []


def test_units_k_and_million():
    allowed = {12000.0, 3_400_000.0}
    assert check_grounding("12k and 3.4 million", allowed).grounded
