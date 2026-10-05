# Reporting policies

## ANCHOR_DATE (relative dates)
All relative dates ("last quarter", "this month", "last 30 days", "year to date", "recently") resolve against
ANCHOR_DATE, **not the system clock**. ANCHOR_DATE = MAX(fact_orders.order_date) in the database. The data is
historical and synthetic, so the system clock is irrelevant.

- "this month / quarter / year" = the period containing ANCHOR_DATE, up to ANCHOR_DATE (period to date).
- "last month / quarter / year" = the most recent complete period as of ANCHOR_DATE. If ANCHOR_DATE is the last day of
  a period, that period counts as complete (so "last quarter" is then the quarter that ends on ANCHOR_DATE).
- "last N days / months" = the N days / months ending on ANCHOR_DATE, inclusive.
- "previous period" / "vs before" = the period of equal length immediately before the compared period.
- "year to date" = fiscal year to date (from 1 April) unless the user says "calendar year".

## Fiscal calendar
"Quarter" and "year" mean the Indian fiscal year (April–March) unless the user says "calendar". Q1 = Apr–Jun,
Q2 = Jul–Sep, Q3 = Oct–Dec, Q4 = Jan–Mar. FY2027 = 1 April 2026 – 31 March 2027. Use dim_date.fiscal_quarter and
dim_date.fiscal_year. State the resolved date range in every answer that uses a relative period.

## Currency and formatting
All amounts are INR. Large values are shown in lakh (1,00,000) and crore (1,00,00,000), for example ₹4.52 crore.

## Data scope and honesty
Data covers 2024-10-01 to ANCHOR_DATE only and is synthetic. Answers must only use numbers returned by executed SQL or
computed deterministically from them. If the data cannot answer a question (missing metric, entity, or period), say so
or ask for clarification rather than estimating. "Why" questions are answered by decomposing the change in a metric
across dimensions (region, category, store, channel); the result shows where the change happened, not proof of cause.

## Result limits
Queries return at most 1000 rows. If a result hits the limit it may be truncated and the answer must say so.
