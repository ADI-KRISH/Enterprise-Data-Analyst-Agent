# Data dictionary (schema `analytics`, star schema, synthetic Indian retail, INR)

## fact_orders — one row per order
order_id (PK), order_date (DATE, FK dim_date.date), customer_id (FK dim_customer), store_id (FK dim_store; for Online orders this is the fulfilling store), channel ('Online' | 'Store'), status ('Completed' | 'Returned' | 'Cancelled').

## fact_order_items — one row per order line
order_item_id (PK), order_id (FK fact_orders), product_id (FK dim_product), quantity, unit_price (INR), discount_pct (percent 0–100, e.g. 10 means 10%), line_revenue (= quantity × unit_price × (1 − discount_pct/100), INR), line_cost (= quantity × unit_cost, INR).

## dim_date
date_key (yyyymmdd), date, day, month, month_name ('January'…), quarter (calendar 1–4), year (calendar), fiscal_quarter (Indian FY Apr–Mar; Apr–Jun = 1), fiscal_year (year the FY ends: Apr 2025–Mar 2026 = 2026).

## dim_region
region_id, region_name ('North India', 'South India', 'East India', 'West India', 'Central India'), zone.

## dim_store
store_id, store_name, city, state, region_id. 16 stores. Example: 'Chennai T Nagar' (store 5) in South India.

## dim_customer
customer_id, name, segment ('Consumer' | 'Corporate' | 'SMB'), city, state, region_id, signup_date.

## dim_product
product_id, product_name, category (Electronics, Apparel, Home & Kitchen, Grocery, Beauty, Sports, Furniture), sub_category, brand, unit_cost, list_price.

## Joins
- fact_order_items.order_id = fact_orders.order_id
- fact_order_items.product_id = dim_product.product_id
- fact_orders.customer_id = dim_customer.customer_id
- fact_orders.store_id = dim_store.store_id
- fact_orders.order_date = dim_date.date
- dim_customer.region_id = dim_region.region_id; dim_store.region_id = dim_region.region_id

## Region of a sale
"Region" means the **customer's** region (`dim_customer.region_id`) unless the user asks about stores, in which case use `dim_store.region_id`. Store-level questions (store, city of store) use `dim_store`.

## Join cautions
Join fact_order_items to fact_orders before aggregating orders; never SUM order-level attributes after joining to items (row fan-out). Count orders with COUNT(DISTINCT order_id).
