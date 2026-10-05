# KPI definitions

All KPIs are computed from schema `analytics`. Unless stated otherwise, revenue-type KPIs count only orders with `status = 'Completed'`.

## Revenue (a.k.a. sales, net sales)
`SUM(fact_order_items.line_revenue)` joined to `fact_orders` with `fact_orders.status = 'Completed'`. Already net of line discount. Returned and Cancelled orders are excluded.

## Gross revenue (before discount)
`SUM(quantity * unit_price)` over Completed orders.

## Discount amount
`SUM(quantity * unit_price - line_revenue)` over Completed orders.

## Cost / COGS
`SUM(fact_order_items.line_cost)` over Completed orders.

## Gross profit
Revenue − COGS = `SUM(line_revenue - line_cost)` over Completed orders.

## Gross margin %
`100 * SUM(line_revenue - line_cost) / NULLIF(SUM(line_revenue), 0)` over Completed orders.

## Orders (order count)
`COUNT(DISTINCT fact_orders.order_id)` with `status = 'Completed'`. "Total orders placed" (all statuses) only if the user says so explicitly.

## Average order value (AOV)
Revenue / Orders (Completed). Compute as `SUM(line_revenue) / NULLIF(COUNT(DISTINCT order_id), 0)`.

## Units sold
`SUM(quantity)` over Completed orders.

## Return rate
Returned orders / (Completed + Returned orders), as a percentage. Cancelled orders are excluded from the denominator.
`100.0 * COUNT(*) FILTER (WHERE status='Returned') / NULLIF(COUNT(*) FILTER (WHERE status IN ('Completed','Returned')), 0)`.

## Cancellation rate
Cancelled orders / all orders, as a percentage.

## Customers (active customers)
`COUNT(DISTINCT customer_id)` with at least one Completed order in the period.

## New customers
Customers whose `signup_date` falls in the period.

## Revenue per customer
Revenue / active customers in the period.

## Growth (period-over-period)
`(current − previous) / previous * 100`. QoQ compares to the immediately preceding quarter; YoY compares to the same period one year earlier. Use fiscal quarters unless the user says "calendar".

## Share / contribution
Segment value / total value * 100 for the same period and filters.

## Online share
Revenue (or orders) with `channel = 'Online'` divided by total.

## Not defined in the data (ask for clarification)
Customer lifetime value, churn, retention, NPS, inventory, stock-outs, marketing spend, ROAS, CAC, footfall, conversion rate, employee metrics, tax/GST amounts, profit after overheads, forecasts/targets/budgets. There is no data for these; do not guess.
