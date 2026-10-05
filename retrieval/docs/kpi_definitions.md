# KPI definitions

Use these exact definitions. If a requested metric is not defined here or derivable from the data dictionary, ask for
clarification instead of inventing a definition.

## Revenue
Revenue = SUM(fact_order_items.line_revenue) over orders with fact_orders.status = 'Completed'. Returned and Cancelled
orders are excluded. Also called sales or net sales. Amounts are INR.

## Gross revenue (including returns)
SUM(line_revenue) over all orders regardless of status. Only use when the user explicitly asks to include returned or
cancelled orders.

## Cost of goods sold (COGS)
SUM(fact_order_items.line_cost) over Completed orders.

## Gross profit
Revenue − COGS = SUM(line_revenue − line_cost) over Completed orders.

## Gross margin %
Gross profit / Revenue × 100 over Completed orders. Compute as a ratio of sums, never an average of row margins.

## Orders
COUNT(DISTINCT fact_orders.order_id) with status = 'Completed' unless stated otherwise.

## Units sold
SUM(fact_order_items.quantity) over Completed orders.

## Average order value (AOV)
Revenue / Orders (Completed orders). Compute as SUM(line_revenue) / COUNT(DISTINCT order_id).

## Active customers
COUNT(DISTINCT customer_id) with at least one Completed order in the period.

## Return rate
Returned orders / (Completed + Returned orders) × 100, by COUNT of orders. Cancelled orders are excluded from both
numerator and denominator.

## Cancellation rate
Cancelled orders / all orders × 100, by COUNT of orders.

## Average discount %
Revenue-weighted: 1 − SUM(line_revenue) / SUM(quantity × unit_price), expressed as a percent, over Completed orders.

## Online share %
Revenue from channel = 'Online' / total Revenue × 100.

## Growth (period over period)
(Current − Previous) / Previous × 100, where Previous is the immediately preceding period of the same length.
Year-over-year compares the same period one year earlier (only possible where 12 months of earlier data exist, i.e.
from October 2025 onwards).

## Regional attribution
Revenue by region is attributed by the customer's region (dim_customer.region_id), not the store's region, unless the
user asks for store region / store performance. Store-level questions use fact_orders.store_id.

## Not defined (ask for clarification)
Customer lifetime value, churn, retention, conversion rate, profit after marketing, NPS, forecasts, and "best" or
"worst" without a metric are not defined. Ask the user which metric or definition they want.
