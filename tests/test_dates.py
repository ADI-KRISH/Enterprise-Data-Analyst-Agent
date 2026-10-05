import datetime as dt

from agents.dates import previous_period, resolve_relative_period as r

END_OF_Q = dt.date(2026, 9, 30)   # anchor = last day of Q2 FY2027
MID_Q = dt.date(2026, 8, 14)


def test_last_quarter_when_anchor_is_quarter_end_is_that_quarter():
    s, e, label = r("why did revenue drop last quarter", END_OF_Q)
    assert (s, e) == (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    assert "Q2 FY2027" in label


def test_last_quarter_mid_quarter_is_previous_complete_quarter():
    s, e, _ = r("last quarter", MID_Q)
    assert (s, e) == (dt.date(2026, 4, 1), dt.date(2026, 6, 30))


def test_this_quarter_and_month_are_to_date():
    assert r("this quarter", MID_Q)[:2] == (dt.date(2026, 7, 1), MID_Q)
    assert r("this month", MID_Q)[:2] == (dt.date(2026, 8, 1), MID_Q)


def test_last_month_and_year():
    assert r("last month", MID_Q)[:2] == (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    assert r("last year", END_OF_Q)[:2] == (dt.date(2025, 4, 1), dt.date(2026, 3, 31))   # FY2026
    assert r("this year", END_OF_Q)[:2] == (dt.date(2026, 4, 1), END_OF_Q)


def test_last_n_days_and_months_and_ytd():
    assert r("last 30 days", END_OF_Q)[:2] == (dt.date(2026, 9, 1), END_OF_Q)
    assert r("last 3 months", END_OF_Q)[:2] == (dt.date(2026, 7, 1), END_OF_Q)
    assert r("year to date", END_OF_Q)[:2] == (dt.date(2026, 4, 1), END_OF_Q)


def test_calendar_quarter():
    assert r("last calendar quarter", MID_Q)[:2] == (dt.date(2026, 4, 1), dt.date(2026, 6, 30))


def test_unrecognised_phrase():
    assert r("in Diwali season", END_OF_Q) is None


def test_system_clock_is_not_used():
    assert r("last quarter", dt.date(2025, 3, 31))[:2] == (dt.date(2025, 1, 1), dt.date(2025, 3, 31))


def test_previous_period_whole_months_and_days():
    assert previous_period(dt.date(2026, 7, 1), dt.date(2026, 9, 30)) == (dt.date(2026, 4, 1), dt.date(2026, 6, 30))
    assert previous_period(dt.date(2026, 9, 1), dt.date(2026, 9, 30)) == (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert previous_period(dt.date(2026, 9, 15), dt.date(2026, 9, 21)) == (dt.date(2026, 9, 8), dt.date(2026, 9, 14))
