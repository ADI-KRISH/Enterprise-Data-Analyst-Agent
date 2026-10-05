# Reporting policies

## ANCHOR_DATE — relative dates
The data is a static synthetic snapshot. Relative time expressions ("today", "this month", "last quarter", "last 30 days", "YTD") are resolved against **ANCHOR_DATE = MAX(order_date)** in `fact_orders`, never the system clock. The agent computes it at startup and injects it into prompts and the response. In the current data ANCHOR_DATE is 2026-03-31.

- "This month" = the calendar month containing ANCHOR_DATE; "last month" = the one before.
- "This quarter" / "latest quarter" = the **fiscal** quarter containing ANCHOR_DATE (Indian FY, Apr–Mar); "last quarter" = the preceding fiscal quarter. Say which one in the answer.
- "This year" / "YTD" = the **fiscal** year containing ANCHOR_DATE up to ANCHOR_DATE; "last year" = the previous fiscal year.
- "Last N days" = the N days ending on ANCHOR_DATE, inclusive.
- Growth compares equal-length complete periods. If the latest period is incomplete, state it.

## Fiscal calendar
Indian fiscal year runs 1 April – 31 March. Q1 = Apr–Jun, Q2 = Jul–Sep, Q3 = Oct–Dec, Q4 = Jan–Mar. FY2025-26 has `fiscal_year = 2026`.

## Status filter
Revenue, orders, AOV, profit and margin use Completed orders only. Return and cancellation rates use the definitions in kpi_definitions.md.

## Currency and formatting
All amounts are INR. Large values may be shown in lakh (1,00,000 = 100,000) or crore (1,00,00,000 = 10,000,000) with the unit stated; figures in answers must come from query results.

## Ambiguity
If a metric, entity or period is not defined here or in the data, ask a clarifying question rather than guessing. Ranking questions ("top") without N default to top 5 and the answer says so.
