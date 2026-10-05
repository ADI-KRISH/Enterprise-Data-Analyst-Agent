-- analytics star schema (synthetic Indian retail). Runs once on an empty Postgres volume.
CREATE SCHEMA IF NOT EXISTS analytics;
SET search_path = analytics;

CREATE TABLE dim_date (
    date_key        INTEGER PRIMARY KEY,           -- yyyymmdd
    date            DATE NOT NULL UNIQUE,
    day             SMALLINT NOT NULL,
    month           SMALLINT NOT NULL,
    month_name      TEXT NOT NULL,
    quarter         SMALLINT NOT NULL,             -- calendar quarter 1-4
    year            SMALLINT NOT NULL,             -- calendar year
    fiscal_quarter  SMALLINT NOT NULL,             -- Indian FY (Apr-Mar): Apr-Jun = 1
    fiscal_year     SMALLINT NOT NULL              -- FY ending year: Apr 2026-Mar 2027 = 2027
);

CREATE TABLE dim_region (
    region_id    SMALLINT PRIMARY KEY,
    region_name  TEXT NOT NULL UNIQUE,
    zone         TEXT NOT NULL
);

CREATE TABLE dim_store (
    store_id    INTEGER PRIMARY KEY,
    store_name  TEXT NOT NULL,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    region_id   SMALLINT NOT NULL REFERENCES dim_region(region_id)
);

CREATE TABLE dim_customer (
    customer_id  INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    segment      TEXT NOT NULL CHECK (segment IN ('Consumer', 'Corporate', 'SMB')),
    city         TEXT NOT NULL,
    state        TEXT NOT NULL,
    region_id    SMALLINT NOT NULL REFERENCES dim_region(region_id),
    signup_date  DATE NOT NULL
);

CREATE TABLE dim_product (
    product_id    INTEGER PRIMARY KEY,
    product_name  TEXT NOT NULL,
    category      TEXT NOT NULL,
    sub_category  TEXT NOT NULL,
    brand         TEXT NOT NULL,
    unit_cost     NUMERIC(12, 2) NOT NULL,
    list_price    NUMERIC(12, 2) NOT NULL
);

CREATE TABLE fact_orders (
    order_id     INTEGER PRIMARY KEY,
    order_date   DATE NOT NULL REFERENCES dim_date(date),
    customer_id  INTEGER NOT NULL REFERENCES dim_customer(customer_id),
    store_id     INTEGER NOT NULL REFERENCES dim_store(store_id),   -- selling / fulfilling store
    channel      TEXT NOT NULL CHECK (channel IN ('Online', 'Store')),
    status       TEXT NOT NULL CHECK (status IN ('Completed', 'Returned', 'Cancelled'))
);

CREATE TABLE fact_order_items (
    order_item_id  INTEGER PRIMARY KEY,
    order_id       INTEGER NOT NULL REFERENCES fact_orders(order_id),
    product_id     INTEGER NOT NULL REFERENCES dim_product(product_id),
    quantity       SMALLINT NOT NULL,
    unit_price     NUMERIC(12, 2) NOT NULL,
    discount_pct   NUMERIC(5, 2) NOT NULL,
    line_revenue   NUMERIC(14, 2) NOT NULL,   -- quantity * unit_price * (1 - discount_pct/100)
    line_cost      NUMERIC(14, 2) NOT NULL    -- quantity * unit_cost
);

CREATE INDEX ix_orders_date     ON fact_orders(order_date);
CREATE INDEX ix_orders_customer ON fact_orders(customer_id);
CREATE INDEX ix_orders_store    ON fact_orders(store_id);
CREATE INDEX ix_items_order     ON fact_order_items(order_id);
CREATE INDEX ix_items_product   ON fact_order_items(product_id);
