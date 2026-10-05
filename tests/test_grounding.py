from agents.grounding import check_grounding, extract_numbers

RESULT = {"columns": ["region", "revenue"],
          "records": [{"region": "South India", "revenue": 12345678.9}, {"region": "West India", "revenue": 20000000}]}
ANALYSIS = {"share": [{"label": "South India", "share_pct": 38.15}], "period_compare": {"change_pct": -45.2}}


def ok(text):
    return check_grounding(text, RESULT, ANALYSIS).grounded


def test_exact_and_commas():
    assert ok("South India revenue was ₹1,23,45,678.90.")
    assert ok("Revenue: 12,345,678.9")


def test_lakh_crore_rounding():
    assert ok("About ₹123.46 lakh")
    assert ok("₹1.23 crore")
    assert ok("₹2 crore for West India")


def test_percent():
    assert ok("a share of 38.2%")
    assert ok("down 45.2%")


def test_invented_number_flagged():
    r = check_grounding("South India revenue was ₹150 lakh, up 12%", RESULT, ANALYSIS)
    assert not r.grounded and len(r.ungrounded) >= 2


def test_labels_and_ordinals_ignored():
    assert extract_numbers("1. South India leads in FY2025-26 Q4 (2026-03-31).") == []
    assert extract_numbers("Q4 FY26 ranking:\n1. South\n2) West") == []


def test_question_numbers_allowed():
    assert check_grounding("Top 5 regions", RESULT, "show top 5 regions").grounded
