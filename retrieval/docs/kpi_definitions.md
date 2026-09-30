# KPI definitions

Canonical business metrics for the `analytics` schema. Every KPI section lists its
`kpi_name`, synonyms, tables, the exact SQL expression, and a reference query. Unless the
user explicitly asks otherwise, **all money KPIs count Completed orders only**
(`fact_orders.status = 'Completed'`): Returned and Cancelled orders are excluded. Amounts
are INR. Queries are written unqualified (the read-only role's `search_path` is
`analytics`) and use date literals resolved from `ANCHOR_DATE` (see reporting_policies.md),
never `now()` / `current_date`.

Standard join for line-level metrics:
`fact_order_items oi JOIN fact_orders o ON o.order_id = oi.order_id`, then
`JOIN dim_product p ON p.product_id = oi.product_id` for product/category/brand,
`JOIN dim_store s ON s.store_id = o.store_id JOIN dim_region r ON r.region_id = s.region_id`
for region/store/city of sale, `JOIN dim_customer c ON c.customer_id = o.customer_id` for
customer segment.

## Revenue (net sales)
**kpi_name:** revenue
**Also known as:** sales, net sales, net revenue, turnover, GMV (net), total sales, income from sales.
**Tables:** fact_order_items, fact_orders
**Definition:** Sum of line revenue after discount, for Completed orders only. Returned
and Cancelled orders are excluded. `line_revenue` already equals
`quantity * unit_price * (1 - discount_pct / 100)`, so do not re-apply the discount.
**SQL expression:** `SUM(oi.line_revenue)` with `WHERE o.status = 'Completed'`
**Reference query:**
```sql
SELECT SUM(oi.line_revenue) AS revenue
FROM fact_order_items oi
JOIN fact_orders o ON o.order_id = oi.order_id
WHERE o.status = 'Completed'
  AND o.order_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-30';
```
**Notes:** "Gross sales" (before discount) is `SUM(oi.quantity * oi.unit_price)`; use it only
when the user explicitly asks for revenue before discount.

## Gross margin
**kpi_name:** gross_margin
**Also known as:** margin, gross margin %, GM, profit margin, gross profit, profitability.
**Tables:** fact_order_items, fact_orders
**Definition:** Gross profit = net revenue − cost of goods, Completed orders only.
Gross margin % = gross profit ÷ net revenue × 100. `line_cost` = `quantity * dim_product.unit_cost`.
**SQL expression:**
- gross profit (INR): `SUM(oi.line_revenue) - SUM(oi.line_cost)`
- gross margin %: `ROUND(100.0 * (SUM(oi.line_revenue) - SUM(oi.line_cost)) / NULLIF(SUM(oi.line_revenue), 0), 2)`
with `WHERE o.status = 'Completed'`
**Reference query:**
```sql
SELECT p.category,
       SUM(oi.line_revenue) - SUM(oi.line_cost) AS gross_profit,
       ROUND(100.0 * (SUM(oi.line_revenue) - SUM(oi.line_cost))
             / NULLIF(SUM(oi.line_revenue), 0), 2) AS gross_margin_pct
FROM fact_order_items oi
JOIN fact_orders o ON o.order_id = oi.order_id
JOIN dim_product p ON p.product_id = oi.product_id
WHERE o.status = 'Completed'
GROUP BY p.category
ORDER BY gross_margin_pct DESC;
```
**Notes:** Never average per-line margin percentages; always compute the ratio of sums.

## Average order value (AOV)
**kpi_name:** aov
**Also known as:** AOV, average order value, average basket value, average bill value, average ticket size, revenue per order, average spend per order.
**Tables:** fact_order_items, fact_orders
**Definition:** Net revenue of Completed orders ÷ number of distinct Completed orders.
**SQL expression:** `SUM(oi.line_revenue) / NULLIF(COUNT(DISTINCT o.order_id), 0)` with `WHERE o.status = 'Completed'`
**Reference query:**
```sql
SELECT ROUND(SUM(oi.line_revenue) / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) AS aov
FROM fact_order_items oi
JOIN fact_orders o ON o.order_id = oi.order_id
WHERE o.status = 'Completed';
```
**AOV by category / product dimension:** join `dim_product p ON p.product_id = oi.product_id`
and `GROUP BY p.category`. The result is category revenue ÷ number of Completed orders
containing that category (average spend on the category per order that includes it).
```sql
SELECT p.category,
       ROUND(SUM(oi.line_revenue) / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) AS aov
FROM fact_order_items oi
JOIN fact_orders o ON o.order_id = oi.order_id
JOIN dim_product p ON p.product_id = oi.product_id
WHERE o.status = 'Completed'
GROUP BY p.category
ORDER BY aov DESC;
```
**Notes:** Must use `COUNT(DISTINCT o.order_id)`: an order has several lines, so `COUNT(*)`
over items would understate AOV. For order-level dimensions (region, store, channel,
segment, month) group by that dimension instead of the product.

## Order count
**kpi_name:** order_count
**Also known as:** orders, number of orders, order volume, transactions, bills.
**Tables:** fact_orders
**Definition:** Number of distinct Completed orders. When the user asks about orders
"placed" or "received", or about a specific status (cancelled, returned), count all
orders or filter on that status instead, and say which basis was used.
**SQL expression:** `COUNT(DISTINCT o.order_id)` with `WHERE o.status = 'Completed'`
**Reference query:**
```sql
SELECT o.channel, COUNT(DISTINCT o.order_id) AS orders
FROM fact_orders o
WHERE o.status = 'Completed'
GROUP BY o.channel;
```
**Notes:** Orders placed by status: `SELECT o.status, COUNT(*) FROM fact_orders o GROUP BY o.status`.
When joining `fact_order_items`, keep `COUNT(DISTINCT o.order_id)`.

## Units sold
**kpi_name:** units_sold
**Also known as:** units, quantity sold, volume, items sold, pieces sold.
**Tables:** fact_order_items, fact_orders
**Definition:** Sum of line quantities of Completed orders.
**SQL expression:** `SUM(oi.quantity)` with `WHERE o.status = 'Completed'`
**Reference query:**
```sql
SELECT p.product_name, SUM(oi.quantity) AS units_sold
FROM fact_order_items oi
JOIN fact_orders o ON o.order_id = oi.order_id
JOIN dim_product p ON p.product_id = oi.product_id
WHERE o.status = 'Completed'
GROUP BY p.product_name
ORDER BY units_sold DESC
LIMIT 10;
```

## Revenue growth (MoM, QoQ, YoY)
**kpi_name:** revenue_growth
**Also known as:** growth, sales growth, month-over-month (MoM), quarter-over-quarter (QoQ), year-over-year (YoY), change vs previous period, increase, decline, drop, trend.
**Tables:** fact_order_items, fact_orders
**Definition:** Growth % = (revenue in current period − revenue in comparison period) ÷
revenue in comparison period × 100, with revenue as defined above (Completed orders only).
- **MoM:** calendar month vs the previous calendar month (`date_trunc('month', …)`).
- **QoQ:** calendar quarter vs the previous calendar quarter (`date_trunc('quarter', …)`).
- **YoY:** a period vs the same period one year earlier (month vs same month last year,
  quarter vs same quarter last year).
Fiscal quarters are used only when the user says FY / fiscal (see reporting_policies.md).
**SQL expression:** `ROUND(100.0 * (curr - prev) / NULLIF(prev, 0), 2)`, where `prev` is
`LAG(revenue) OVER (ORDER BY period)` (MoM/QoQ) or `LAG(revenue, 12)` for monthly YoY /
`LAG(revenue, 4)` for quarterly YoY, over a gap-free period series.
**Reference query (QoQ series):**
```sql
WITH q AS (
    SELECT date_trunc('quarter', o.order_date) AS quarter_start,
           SUM(oi.line_revenue) AS revenue
    FROM fact_order_items oi
    JOIN fact_orders o ON o.order_id = oi.order_id
    WHERE o.status = 'Completed'
    GROUP BY 1
)
SELECT quarter_start,
       revenue,
       LAG(revenue) OVER (ORDER BY quarter_start) AS prev_revenue,
       ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY quarter_start))
             / NULLIF(LAG(revenue) OVER (ORDER BY quarter_start), 0), 2) AS qoq_growth_pct
FROM q
ORDER BY quarter_start;
```
**Reference query (two explicit periods, e.g. latest quarter vs previous quarter by region):**
```sql
SELECT r.region_name,
       SUM(CASE WHEN o.order_date BETWEEN DATE '2026-04-01' AND DATE '2026-06-30'
                THEN oi.line_revenue ELSE 0 END) AS revenue_prev,
       SUM(CASE WHEN o.order_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-30'
                THEN oi.line_revenue ELSE 0 END) AS revenue_curr
FROM fact_order_items oi
JOIN fact_orders o ON o.order_id = oi.order_id
JOIN dim_store s ON s.store_id = o.store_id
JOIN dim_region r ON r.region_id = s.region_id
WHERE o.status = 'Completed'
  AND o.order_date BETWEEN DATE '2026-04-01' AND DATE '2026-09-30'
GROUP BY r.region_name;
```
**Notes:** Data starts 2024-10-01, so the first period has no comparison (growth is NULL,
not 0). "Why did revenue drop?" questions: compare the two periods and break the change
down by category, store and channel (see category contribution).

## Return rate
**kpi_name:** return_rate
**Also known as:** returns, return %, returned orders rate, product returns.
**Tables:** fact_orders (plus fact_order_items and dim_product when split by product/category)
**Definition:** Returned orders ÷ delivered orders × 100, where delivered = Completed +
Returned. Cancelled orders are excluded from numerator and denominator (they were never
delivered). This KPI is the one exception to "Completed only".
**SQL expression:** `ROUND(100.0 * COUNT(DISTINCT CASE WHEN o.status = 'Returned' THEN o.order_id END) / NULLIF(COUNT(DISTINCT CASE WHEN o.status IN ('Completed', 'Returned') THEN o.order_id END), 0), 2)`
**Reference query (by category, orders containing the category):**
```sql
SELECT p.category,
       ROUND(100.0 * COUNT(DISTINCT CASE WHEN o.status = 'Returned' THEN o.order_id END)
             / NULLIF(COUNT(DISTINCT CASE WHEN o.status IN ('Completed', 'Returned')
                                          THEN o.order_id END), 0), 2) AS return_rate_pct
FROM fact_order_items oi
JOIN fact_orders o ON o.order_id = oi.order_id
JOIN dim_product p ON p.product_id = oi.product_id
GROUP BY p.category
ORDER BY return_rate_pct DESC;
```
**Notes:** Status is recorded per order, not per line: a Returned order counts as returned
for every category it contains.

## Active customers
**kpi_name:** active_customers
**Also known as:** active customers, buying customers, unique customers, customer count, purchasing customers.
**Tables:** fact_orders (plus dim_customer for segment / customer geography)
**Definition:** Number of distinct customers with at least one Completed order in the period.
**SQL expression:** `COUNT(DISTINCT o.customer_id)` with `WHERE o.status = 'Completed'`
**Reference query:**
```sql
SELECT c.segment, COUNT(DISTINCT o.customer_id) AS active_customers
FROM fact_orders o
JOIN dim_customer c ON c.customer_id = o.customer_id
WHERE o.status = 'Completed'
  AND o.order_date BETWEEN DATE '2025-10-01' AND DATE '2026-09-30'
GROUP BY c.segment;
```
**Notes:** Registered customers (all rows in `dim_customer`, by `signup_date`) is a
different metric; use it only when the user asks about sign-ups or the customer base.
Customer names are restricted and must never be selected as output columns.

## Category contribution
**kpi_name:** category_contribution
**Also known as:** category share, revenue share, revenue mix, contribution %, share of sales, category mix, contribution to growth / decline.
**Tables:** fact_order_items, fact_orders, dim_product
**Definition:** A category's net revenue (Completed orders) ÷ total net revenue in the
same period and filter × 100. Contribution to a change = category revenue change ÷ total
revenue change × 100 (used for "what drove the growth / drop").
**SQL expression:** `ROUND(100.0 * SUM(oi.line_revenue) / SUM(SUM(oi.line_revenue)) OVER (), 2)` grouped by `p.category`
**Reference query:**
```sql
SELECT p.category,
       SUM(oi.line_revenue) AS revenue,
       ROUND(100.0 * SUM(oi.line_revenue) / SUM(SUM(oi.line_revenue)) OVER (), 2) AS contribution_pct
FROM fact_order_items oi
JOIN fact_orders o ON o.order_id = oi.order_id
JOIN dim_product p ON p.product_id = oi.product_id
WHERE o.status = 'Completed'
GROUP BY p.category
ORDER BY revenue DESC;
```
**Notes:** The same pattern gives the share of any dimension (region, channel, brand,
store): replace `p.category` with that column. Shares within a filter (e.g. South India
only) are shares of that filtered total.
