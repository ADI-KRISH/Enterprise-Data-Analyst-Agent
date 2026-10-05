# Data dictionary (schema `analytics`)

Synthetic Indian retail star schema. All amounts are INR. Always qualify tables with `analytics.` or rely on the
role's search_path. Only the tables and columns listed here exist.

## dim_date
One row per calendar day (2024-10-01 to 2026-09-30). Columns: date_key (yyyymmdd int, PK), date (DATE, unique; joins to
fact_orders.order_date), day, month (1-12), month_name, quarter (calendar quarter 1-4), year (calendar year),
fiscal_quarter, fiscal_year. Indian fiscal year runs April–March: fiscal_quarter 1 = Apr–Jun, 2 = Jul–Sep, 3 = Oct–Dec,
4 = Jan–Mar. fiscal_year is the year in which the FY ends (April 2026–March 2027 is fiscal_year 2027, shown "FY2027").

## dim_region
region_id (PK), region_name ('North India', 'South India', 'East India', 'West India', 'Central India'), zone.

## dim_store
store_id (PK), store_name (e.g. 'Chennai Central'), city, state, region_id → dim_region. One store per city.

## dim_customer
customer_id (PK), name, segment ('Consumer', 'Corporate', 'SMB'), city, state, region_id → dim_region, signup_date.

## dim_product
product_id (PK), product_name, category ('Electronics', 'Apparel', 'Home & Kitchen', 'Furniture', 'Grocery',
'Beauty & Personal Care'), sub_category, brand, unit_cost, list_price.

## fact_orders
One row per order. order_id (PK), order_date → dim_date.date, customer_id → dim_customer, store_id → dim_store (the
selling store, or the fulfilling store for online orders), channel ('Online' or 'Store'), status ('Completed',
'Returned', 'Cancelled').

## fact_order_items
One row per order line. order_item_id (PK), order_id → fact_orders, product_id → dim_product, quantity, unit_price,
discount_pct (0-100, a percentage), line_revenue (= quantity × unit_price × (1 − discount_pct/100)), line_cost
(= quantity × unit_cost). There is no tax, shipping or payment data and no customer satisfaction / NPS data.

## Join paths
- Items to orders: fact_order_items.order_id = fact_orders.order_id
- Orders to date: fact_orders.order_date = dim_date.date
- Orders to customer and region: fact_orders.customer_id = dim_customer.customer_id, dim_customer.region_id = dim_region.region_id
- Orders to store: fact_orders.store_id = dim_store.store_id (store region via dim_store.region_id)
- Items to product: fact_order_items.product_id = dim_product.product_id

## Not available
Marketing spend, inventory levels, employee data, web traffic, customer satisfaction, forecasts, and any data before
2024-10-01 or after the latest order date do not exist in the database.
