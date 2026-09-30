-- analytics star schema (synthetic Indian retail). Run once as the admin role.
-- Money columns are INR, NUMERIC(12,2). IDs are assigned by database/seed.py.

CREATE SCHEMA IF NOT EXISTS analytics;

-- ---------------------------------------------------------------- dimensions

CREATE TABLE analytics.dim_date (
    date_key        INTEGER     PRIMARY KEY,               -- YYYYMMDD
    date            DATE        NOT NULL UNIQUE,
    day             SMALLINT    NOT NULL CHECK (day BETWEEN 1 AND 31),
    month           SMALLINT    NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name      TEXT        NOT NULL,
    quarter         SMALLINT    NOT NULL CHECK (quarter BETWEEN 1 AND 4),   -- calendar quarter
    year            SMALLINT    NOT NULL,
    fiscal_quarter  SMALLINT    NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4), -- Indian FY: Q1 = Apr-Jun
    fiscal_year     TEXT        NOT NULL CHECK (fiscal_year ~ '^FY[0-9]{4}-[0-9]{2}$'), -- e.g. FY2026-27
    CHECK (date_key = (EXTRACT(YEAR FROM date) * 10000 + EXTRACT(MONTH FROM date) * 100
                       + EXTRACT(DAY FROM date))::INTEGER)
);

CREATE TABLE analytics.dim_region (
    region_id       SMALLINT    PRIMARY KEY,
    region_name     TEXT        NOT NULL UNIQUE CHECK (region_name IN
                        ('North India', 'South India', 'East India', 'West India', 'Central India')),
    zone            TEXT        NOT NULL
);

CREATE TABLE analytics.dim_store (
    store_id        INTEGER     PRIMARY KEY,
    store_name      TEXT        NOT NULL UNIQUE,
    city            TEXT        NOT NULL,
    state           TEXT        NOT NULL,
    region_id       SMALLINT    NOT NULL REFERENCES analytics.dim_region (region_id)
);

CREATE TABLE analytics.dim_customer (
    customer_id     INTEGER     PRIMARY KEY,
    name            TEXT        NOT NULL,
    segment         TEXT        NOT NULL CHECK (segment IN ('Consumer', 'Corporate', 'SMB')),
    city            TEXT        NOT NULL,
    state           TEXT        NOT NULL,
    region_id       SMALLINT    NOT NULL REFERENCES analytics.dim_region (region_id),
    signup_date     DATE        NOT NULL
);

CREATE TABLE analytics.dim_product (
    product_id      INTEGER       PRIMARY KEY,
    product_name    TEXT          NOT NULL UNIQUE,
    category        TEXT          NOT NULL,
    sub_category    TEXT          NOT NULL,
    brand           TEXT          NOT NULL,
    unit_cost       NUMERIC(12,2) NOT NULL CHECK (unit_cost > 0),
    list_price      NUMERIC(12,2) NOT NULL CHECK (list_price > 0),
    CHECK (unit_cost <= list_price)
);

-- --------------------------------------------------------------------- facts

CREATE TABLE analytics.fact_orders (
    order_id        INTEGER     PRIMARY KEY,
    order_date      DATE        NOT NULL REFERENCES analytics.dim_date (date),
    customer_id     INTEGER     NOT NULL REFERENCES analytics.dim_customer (customer_id),
    store_id        INTEGER     NOT NULL REFERENCES analytics.dim_store (store_id),
    channel         TEXT        NOT NULL CHECK (channel IN ('Online', 'Store')),
    status          TEXT        NOT NULL CHECK (status IN ('Completed', 'Returned', 'Cancelled'))
);

CREATE TABLE analytics.fact_order_items (
    order_item_id   INTEGER       PRIMARY KEY,
    order_id        INTEGER       NOT NULL REFERENCES analytics.fact_orders (order_id),
    product_id      INTEGER       NOT NULL REFERENCES analytics.dim_product (product_id),
    quantity        INTEGER       NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
    discount_pct    NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct < 100),
    line_revenue    NUMERIC(14,2) NOT NULL CHECK (line_revenue >= 0),  -- quantity * unit_price * (1 - discount_pct/100)
    line_cost       NUMERIC(14,2) NOT NULL CHECK (line_cost >= 0)      -- quantity * dim_product.unit_cost
);

-- ------------------------------------------------------------------- indexes

CREATE INDEX ix_fact_orders_order_date     ON analytics.fact_orders (order_date);
CREATE INDEX ix_fact_orders_store_id       ON analytics.fact_orders (store_id);
CREATE INDEX ix_fact_orders_customer_id    ON analytics.fact_orders (customer_id);
CREATE INDEX ix_fact_order_items_order_id  ON analytics.fact_order_items (order_id);
CREATE INDEX ix_fact_order_items_product_id ON analytics.fact_order_items (product_id);
CREATE INDEX ix_dim_store_region_id        ON analytics.dim_store (region_id);
CREATE INDEX ix_dim_customer_region_id     ON analytics.dim_customer (region_id);
