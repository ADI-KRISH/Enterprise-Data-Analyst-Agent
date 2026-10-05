-- analytics star schema (synthetic Indian retail). Idempotent.
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.dim_date (
    date_key       INTEGER PRIMARY KEY,          -- yyyymmdd
    date           DATE NOT NULL UNIQUE,
    day            SMALLINT NOT NULL,
    month          SMALLINT NOT NULL,
    month_name     TEXT NOT NULL,
    quarter        SMALLINT NOT NULL,            -- calendar quarter 1-4
    year           SMALLINT NOT NULL,            -- calendar year
    fiscal_quarter SMALLINT NOT NULL,            -- Indian FY (Apr-Mar): Apr-Jun = 1
    fiscal_year    SMALLINT NOT NULL             -- year in which the FY ends (Apr 2025-Mar 2026 = 2026)
);

CREATE TABLE IF NOT EXISTS analytics.dim_region (
    region_id   INTEGER PRIMARY KEY,
    region_name TEXT NOT NULL UNIQUE,
    zone        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.dim_store (
    store_id   INTEGER PRIMARY KEY,
    store_name TEXT NOT NULL,
    city       TEXT NOT NULL,
    state      TEXT NOT NULL,
    region_id  INTEGER NOT NULL REFERENCES analytics.dim_region (region_id)
);

CREATE TABLE IF NOT EXISTS analytics.dim_customer (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    segment     TEXT NOT NULL CHECK (segment IN ('Consumer', 'Corporate', 'SMB')),
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    region_id   INTEGER NOT NULL REFERENCES analytics.dim_region (region_id),
    signup_date DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.dim_product (
    product_id   INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    category     TEXT NOT NULL,
    sub_category TEXT NOT NULL,
    brand        TEXT NOT NULL,
    unit_cost    NUMERIC(12, 2) NOT NULL,
    list_price   NUMERIC(12, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.fact_orders (
    order_id    INTEGER PRIMARY KEY,
    order_date  DATE NOT NULL REFERENCES analytics.dim_date (date),
    customer_id INTEGER NOT NULL REFERENCES analytics.dim_customer (customer_id),
    store_id    INTEGER NOT NULL REFERENCES analytics.dim_store (store_id),
    channel     TEXT NOT NULL CHECK (channel IN ('Online', 'Store')),
    status      TEXT NOT NULL CHECK (status IN ('Completed', 'Returned', 'Cancelled'))
);

CREATE TABLE IF NOT EXISTS analytics.fact_order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES analytics.fact_orders (order_id),
    product_id    INTEGER NOT NULL REFERENCES analytics.dim_product (product_id),
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(12, 2) NOT NULL,
    discount_pct  NUMERIC(5, 2) NOT NULL DEFAULT 0,   -- percent, 0-100
    line_revenue  NUMERIC(14, 2) NOT NULL,            -- quantity * unit_price * (1 - discount_pct/100)
    line_cost     NUMERIC(14, 2) NOT NULL             -- quantity * unit_cost
);

CREATE INDEX IF NOT EXISTS ix_orders_date ON analytics.fact_orders (order_date);
CREATE INDEX IF NOT EXISTS ix_orders_customer ON analytics.fact_orders (customer_id);
CREATE INDEX IF NOT EXISTS ix_orders_store ON analytics.fact_orders (store_id);
CREATE INDEX IF NOT EXISTS ix_items_order ON analytics.fact_order_items (order_id);
CREATE INDEX IF NOT EXISTS ix_items_product ON analytics.fact_order_items (product_id);
