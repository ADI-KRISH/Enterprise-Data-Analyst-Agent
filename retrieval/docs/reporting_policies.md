# Reporting policies

Rules for interpreting business questions against the `analytics` schema. They apply to
SQL generation, clarification and answer wording.

## Relative dates (last quarter, this month, last year, YTD) and ANCHOR_DATE
`ANCHOR_DATE` = `max(fact_orders.order_date)` = **2026-09-30**. The data is a fixed
synthetic window (2024-10-01 to 2026-09-30), so relative dates resolve against
ANCHOR_DATE, **never the system clock**. SQL must use explicit date literals
(`DATE '2026-07-01'`); clock functions such as `now()`, `current_date` and
`current_timestamp` are rejected by the SQL validator.

A period is **complete** when its last day is on or before ANCHOR_DATE.
- "this month / this quarter / this year": the calendar period containing ANCHOR_DATE,
  up to ANCHOR_DATE (this year = 2026-01-01..2026-09-30, year-to-date).
- "last month / last quarter / last year" used alone: the latest **complete** period.
  With ANCHOR_DATE 2026-09-30: last month = Sep 2026, last quarter = Jul–Sep 2026
  (calendar Q3 2026), last year = calendar 2025.
- In a comparison ("this quarter vs last quarter", "compared to the previous month"),
  the current period is the latest one and the comparison period is the one before it:
  Jul–Sep 2026 vs Apr–Jun 2026; Sep 2026 vs Aug 2026.
- "last N days": ANCHOR_DATE − (N − 1) .. ANCHOR_DATE (last 30 days = 2026-09-01..2026-09-30).
- "last N months / quarters": the N complete calendar periods ending at ANCHOR_DATE
  (last 12 months = 2025-10-01..2026-09-30; last 6 months = 2026-04-01..2026-09-30).
- "YTD": 2026-01-01..ANCHOR_DATE; "FYTD": 2026-04-01..ANCHOR_DATE.
- "latest / recent / current" without a period: the latest complete quarter.
- The answer states the resolved date range explicitly (e.g. "Jul–Sep 2026").

## Data coverage
- Orders exist only from **2024-10-01 to 2026-09-30**. Questions about dates outside this
  window have no data: say so instead of returning zeros or guessing.
- Partial periods: calendar 2024 has only Oct–Dec; FY2024-25 has only Oct 2024–Mar 2025.
  Flag partial periods when comparing them with full ones.
- The first period in the data has no prior period, so its growth is undefined (not 0%).

## Calendar vs Indian fiscal year
- **Default: calendar periods.** "Q1" means Jan–Mar, "2025" means calendar year 2025.
- Use the **Indian fiscal year (April–March)** only when the user says FY, fiscal, or
  financial year. FY2026-27 = 2026-04-01..2027-03-31. Fiscal quarters: Q1 = Apr–Jun,
  Q2 = Jul–Sep, Q3 = Oct–Dec, Q4 = Jan–Mar. "FY26" / "FY2025-26" = 2025-04-01..2026-03-31
  (fiscal years are named by the start year and end year; "FY26" means the year ending
  March 2026).
- `dim_date` has both: `quarter` / `year` (calendar) and `fiscal_quarter` / `fiscal_year`
  (label format `FY2026-27`). Join `dim_date d ON d.date = o.order_date` to use them,
  or filter `o.order_date` with explicit date ranges.
- If a question uses a quarter without a year, assume the most recent complete quarter of
  that name within the data and state the assumption.

## Currency and number formatting
- All amounts are **Indian Rupees (INR, ₹)**. There is no currency conversion; if the user
  asks for another currency, explain that only INR is available.
- Answers may use Indian units: 1 lakh (L) = 1,00,000; 1 crore (Cr) = 1,00,00,000 =
  100 lakh. Example: ₹96,70,000 = ₹96.7 L; ₹5,68,00,000 = ₹5.68 Cr.
- Round money to 2 decimals in SQL; percentages to 2 decimals in SQL and 1 decimal in prose.
- Every number in an answer must come from the query result or a deterministic
  computation on it.

## Region, store and customer geography
- Regions are exactly: 'North India', 'South India', 'East India', 'West India',
  'Central India' (`dim_region.region_name`). Map user phrasing to these values:
  "South", "South India", "southern region", "the south" → `region_name = 'South India'`
  (likewise for North, East, West, Central). "North-Central" is a **zone**
  (`dim_region.zone`) that contains North India and Central India.
- **Sales geography = store geography.** Revenue/orders "in a region / state / city" use
  the selling store: `fact_orders.store_id → dim_store → dim_region`. Online orders also
  carry the fulfilling store.
- Use customer geography (`dim_customer.region_id / city / state`) only when the question is
  about customers themselves ("customers based in Chennai", "where do our customers live").
- City names are stored in their current official form: Bengaluru (not Bangalore),
  Mumbai (not Bombay), Kolkata (not Calcutta), Chennai (not Madras), Gurugram (not
  Gurgaon), New Delhi (city) / Delhi (state).
- Store names are "City - Locality", e.g. 'Chennai - Velachery'.

## Default metric choices and ambiguity
- "Sales" / "revenue" = net revenue of Completed orders (kpi_definitions.md). State the
  basis in the answer ("completed orders, net of discount").
- "Orders" = Completed orders unless the question is about placed, cancelled or returned
  orders.
- "Top N" without N: top 5. Ranking without a metric: by revenue.
- "Best / worst performing" without a metric: by revenue; mention the metric used.
- Ask for clarification instead of guessing when a metric is not defined in
  kpi_definitions.md or the data (e.g. "profit after marketing spend", "customer
  satisfaction", "footfall", "inventory", "targets / budget"), when an entity does not
  exist in the data, or when the time period is genuinely ambiguous.

## Privacy
- `dim_customer.name` is personal data and restricted: it may be used in filters, joins,
  grouping and `COUNT`, but it must never be returned as an output column. Report customer
  insights at aggregate level (segment, city, region, customer_id counts).
