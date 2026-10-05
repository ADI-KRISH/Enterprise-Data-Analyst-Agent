"""Seed the analytics schema with synthetic Indian retail data (fixed RNG seed).

Run with the ADMIN connection only:  python -m database.seed
Patterns injected on purpose are documented in database/SEED_NOTES.md.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from app.config import PROJECT_ROOT

SEED = 42
START = dt.date(2024, 4, 1)
END = dt.date(2026, 3, 31)
N_ORDERS = 50_000
N_CUSTOMERS = 8_000
CLOSURE_DATE = dt.date(2026, 1, 10)  # last trading day of the Chennai T Nagar store
DIP_START = dt.date(2026, 1, 1)  # latest fiscal quarter (Jan-Mar 2026)

REGIONS = [
    (1, "North India", "North"),
    (2, "South India", "South"),
    (3, "East India", "East"),
    (4, "West India", "West"),
    (5, "Central India", "Central"),
]
# (store_id, store_name, city, state, region_id)
STORES = [
    (1, "Delhi Connaught Place", "Delhi", "Delhi", 1),
    (2, "Gurugram Cyber Hub", "Gurugram", "Haryana", 1),
    (3, "Chandigarh Sector 17", "Chandigarh", "Chandigarh", 1),
    (4, "Bengaluru Indiranagar", "Bengaluru", "Karnataka", 2),
    (5, "Chennai T Nagar", "Chennai", "Tamil Nadu", 2),
    (6, "Hyderabad Banjara Hills", "Hyderabad", "Telangana", 2),
    (7, "Kochi MG Road", "Kochi", "Kerala", 2),
    (8, "Kolkata Park Street", "Kolkata", "West Bengal", 3),
    (9, "Bhubaneswar Saheed Nagar", "Bhubaneswar", "Odisha", 3),
    (10, "Guwahati GS Road", "Guwahati", "Assam", 3),
    (11, "Mumbai Andheri", "Mumbai", "Maharashtra", 4),
    (12, "Pune Koregaon Park", "Pune", "Maharashtra", 4),
    (13, "Ahmedabad CG Road", "Ahmedabad", "Gujarat", 4),
    (14, "Bhopal MP Nagar", "Bhopal", "Madhya Pradesh", 5),
    (15, "Indore Vijay Nagar", "Indore", "Madhya Pradesh", 5),
    (16, "Raipur Civil Lines", "Raipur", "Chhattisgarh", 5),
]
CHENNAI_STORE = 5
SOUTH_FALLBACK_STORE = 4  # Bengaluru absorbs some of the Chennai demand
REGION_WEIGHTS = [0.20, 0.24, 0.16, 0.28, 0.12]
CITY_BY_REGION = {
    r: [(s[2], s[3]) for s in STORES if s[4] == r] for r, _, _ in REGIONS
}

# category -> (sub_category -> (brands, price range INR), popularity weight)
CATALOG = {
    "Electronics": ({
        "Mobiles": (["Nova", "Zenith", "Pixelon"], (8000, 70000)),
        "Laptops": (["Zenith", "Corebook"], (30000, 120000)),
        "Audio": (["Sonara", "Boomix", "Nova"], (700, 15000)),
        "Accessories": (["Nova", "ChargeX"], (300, 4000)),
    }, 1.0),
    "Apparel": ({
        "Menswear": (["UrbanLoom", "Raymore", "Kasa"], (500, 5000)),
        "Womenswear": (["Kasa", "Fabindia Co", "UrbanLoom"], (500, 7000)),
        "Footwear": (["StrideX", "Kasa"], (600, 6000)),
    }, 1.4),
    "Home & Kitchen": ({
        "Appliances": (["HomeKraft", "Prestige Co"], (1500, 25000)),
        "Cookware": (["Prestige Co", "HomeKraft"], (400, 4000)),
        "Decor": (["Dwell", "HomeKraft"], (300, 6000)),
    }, 1.0),
    "Grocery": ({
        "Staples": (["FarmFresh", "Annapurna"], (60, 900)),
        "Snacks": (["Crunchy", "Annapurna"], (20, 400)),
        "Beverages": (["Chaiwala", "Crunchy"], (30, 800)),
    }, 1.8),
    "Beauty": ({
        "Skincare": (["Glowra", "Ayurvana"], (200, 3500)),
        "Haircare": (["Ayurvana", "Glowra"], (150, 2500)),
        "Fragrance": (["Attar&Co", "Glowra"], (400, 6000)),
    }, 0.9),
    "Sports": ({
        "Fitness": (["FlexFit", "StrideX"], (400, 12000)),
        "Outdoor": (["TrailKing", "FlexFit"], (500, 9000)),
    }, 0.6),
    "Furniture": ({
        "Living Room": (["Dwell", "WoodCraft"], (4000, 45000)),
        "Bedroom": (["WoodCraft", "Dwell"], (6000, 60000)),
    }, 0.4),
}
PRODUCTS_PER_SUBCAT = 7


def _fiscal(d: dt.date) -> tuple[int, int]:
    fy_end = d.year + 1 if d.month >= 4 else d.year
    fq = ((d.month - 4) % 12) // 3 + 1
    return fq, fy_end


def build_dates() -> pd.DataFrame:
    days = pd.date_range(START, END, freq="D")
    rows = []
    for ts in days:
        d = ts.date()
        fq, fy = _fiscal(d)
        rows.append(
            (int(ts.strftime("%Y%m%d")), d, d.day, d.month, ts.strftime("%B"),
             (d.month - 1) // 3 + 1, d.year, fq, fy)
        )
    return pd.DataFrame(
        rows,
        columns=["date_key", "date", "day", "month", "month_name", "quarter",
                 "year", "fiscal_quarter", "fiscal_year"],
    )


def build_products(rng: np.random.Generator) -> pd.DataFrame:
    rows, pid = [], 1
    for category, (subs, _w) in CATALOG.items():
        for sub, (brands, (lo, hi)) in subs.items():
            for i in range(PRODUCTS_PER_SUBCAT):
                brand = brands[i % len(brands)]
                price = float(np.round(np.exp(rng.uniform(np.log(lo), np.log(hi))) / 10) * 10)
                price = max(price, lo)
                cost = round(price * rng.uniform(0.55, 0.78), 2)
                rows.append((pid, f"{brand} {sub} {i + 1}", category, sub, brand, cost, price))
                pid += 1
    return pd.DataFrame(
        rows,
        columns=["product_id", "product_name", "category", "sub_category", "brand",
                 "unit_cost", "list_price"],
    )


def build_customers(rng: np.random.Generator, faker) -> pd.DataFrame:
    region = rng.choice([r[0] for r in REGIONS], size=N_CUSTOMERS, p=REGION_WEIGHTS)
    segment = rng.choice(["Consumer", "Corporate", "SMB"], size=N_CUSTOMERS, p=[0.7, 0.12, 0.18])
    span = (END - dt.date(2022, 4, 1)).days
    signup = [dt.date(2022, 4, 1) + dt.timedelta(days=int(x))
              for x in np.minimum(rng.exponential(span * 0.45, N_CUSTOMERS), span - 1)]
    rows = []
    for i in range(N_CUSTOMERS):
        city, state = CITY_BY_REGION[int(region[i])][rng.integers(len(CITY_BY_REGION[int(region[i])]))]
        name = faker.name() if segment[i] == "Consumer" else faker.company()
        rows.append((i + 1, name, segment[i], city, state, int(region[i]), signup[i]))
    return pd.DataFrame(
        rows, columns=["customer_id", "name", "segment", "city", "state", "region_id", "signup_date"]
    )


def day_weights(dates: pd.DatetimeIndex) -> np.ndarray:
    """Relative order volume per day: growth trend x festive/seasonal x weekend."""
    n = len(dates)
    trend = np.linspace(1.0, 1.28, n)
    w = trend.copy()
    month, dow = dates.month.values, dates.dayofweek.values
    w *= np.where(dow >= 5, 1.25, 1.0)
    w *= np.where(month == 10, 1.45, 1.0)  # Navratri / Dussehra ramp-up
    w *= np.where(month == 11, 1.55, 1.0)  # Diwali
    w *= np.where(month == 12, 1.15, 1.0)  # year-end sale
    w *= np.where(month == 1, 1.10, 1.0)  # Republic Day sale
    w *= np.where(month == 4, 0.92, 1.0)
    w *= np.where(month == 7, 0.90, 1.0)
    # sharp Diwali-day peaks (31 Oct 2024, 20 Oct 2025)
    for diwali in (dt.date(2024, 10, 31), dt.date(2025, 10, 20)):
        for off, boost in zip(range(-5, 3), [1.2, 1.3, 1.5, 1.7, 1.9, 2.1, 1.6, 1.3]):
            idx = np.where(dates.date == diwali + dt.timedelta(days=off))[0]
            w[idx] *= boost
    return w / w.sum()


def build_orders_and_items(rng, customers: pd.DataFrame, products: pd.DataFrame):
    dates = pd.date_range(START, END, freq="D")
    order_day = rng.choice(len(dates), size=N_ORDERS, p=day_weights(dates))
    order_day.sort()
    order_date = dates[order_day]

    # customers only order after they signed up
    cust_ids = customers["customer_id"].to_numpy()
    cust_signup = pd.to_datetime(customers["signup_date"]).to_numpy()
    cust_region = customers["region_id"].to_numpy()
    segment = customers["segment"].to_numpy()
    seg_w = np.where(segment == "Corporate", 3.0, np.where(segment == "SMB", 1.6, 1.0))
    # vectorised: sample by weight, then fix up those who signed up after the order date
    p = seg_w / seg_w.sum()
    cust_idx = rng.choice(len(cust_ids), size=N_ORDERS, p=p)
    bad = cust_signup[cust_idx] > order_date.to_numpy()
    while bad.any():
        cust_idx[bad] = rng.choice(len(cust_ids), size=int(bad.sum()), p=p)
        bad = cust_signup[cust_idx] > order_date.to_numpy()

    region = cust_region[cust_idx]
    # online share grows from ~30% to ~48% over the period
    online_p = np.linspace(0.30, 0.48, len(dates))[order_day]
    channel = np.where(rng.random(N_ORDERS) < online_p, "Online", "Store")

    store_by_region = {r: np.array([s[0] for s in STORES if s[4] == r]) for r, _, _ in REGIONS}
    store = np.array([rng.choice(store_by_region[int(r)]) for r in region])

    # Pattern: Chennai store closes -> 70% of its later orders are lost, rest go to Bengaluru
    after_closure = order_date.to_numpy() > np.datetime64(CLOSURE_DATE)
    chennai_after = (store == CHENNAI_STORE) & after_closure
    lost = chennai_after & (rng.random(N_ORDERS) < 0.70)
    moved = chennai_after & ~lost
    store[moved] = SOUTH_FALLBACK_STORE
    keep = ~lost

    orders = pd.DataFrame(
        {
            "order_date": order_date.date,
            "customer_id": cust_ids[cust_idx],
            "store_id": store,
            "channel": channel,
        }
    )[keep].reset_index(drop=True)
    region = region[keep]
    order_day = order_day[keep]
    orders.insert(0, "order_id", np.arange(1, len(orders) + 1))
    n = len(orders)

    # ---- items
    n_items = np.clip(rng.geometric(0.42, size=n), 1, 6)
    item_order = np.repeat(np.arange(n), n_items)
    m = len(item_order)

    prod_cat = products["category"].to_numpy()
    cat_w = np.array([CATALOG[c][1] for c in prod_cat])
    pop = rng.pareto(1.6, size=len(products)) + 1.0
    pw = cat_w * pop
    pw /= pw.sum()
    prod_idx = rng.choice(len(products), size=m, p=pw)

    # Pattern: South India Electronics demand drops in the latest quarter
    item_date = orders["order_date"].to_numpy()[item_order]
    item_region = region[item_order]
    flag = (
        (item_region == 2)
        & (item_date >= np.datetime64(DIP_START))
        & (prod_cat[prod_idx] == "Electronics")
        & (rng.random(m) < 0.65)
    )
    non_elec = np.where(prod_cat != "Electronics")[0]
    pw_ne = pw[non_elec] / pw[non_elec].sum()
    prod_idx[flag] = rng.choice(non_elec, size=int(flag.sum()), p=pw_ne)

    list_price = products["list_price"].to_numpy()[prod_idx]
    unit_cost = products["unit_cost"].to_numpy()[prod_idx]
    cheap = list_price < 1000
    qty = np.where(cheap, rng.integers(1, 6, m), rng.integers(1, 3, m))
    month = pd.DatetimeIndex(item_date).month.to_numpy()
    festive = np.isin(month, [10, 11])
    base_disc = rng.choice([0, 5, 10, 15, 20], size=m, p=[0.45, 0.2, 0.2, 0.1, 0.05])
    fest_disc = rng.choice([0, 10, 15, 20, 25], size=m, p=[0.15, 0.25, 0.3, 0.2, 0.1])
    disc = np.where(festive, fest_disc, base_disc).astype(float)
    unit_price = list_price
    line_rev = np.round(qty * unit_price * (1 - disc / 100), 2)
    line_cost = np.round(qty * unit_cost, 2)

    items = pd.DataFrame(
        {
            "order_item_id": np.arange(1, m + 1),
            "order_id": item_order + 1,
            "product_id": products["product_id"].to_numpy()[prod_idx],
            "quantity": qty,
            "unit_price": unit_price,
            "discount_pct": disc,
            "line_revenue": line_rev,
            "line_cost": line_cost,
        }
    )

    # ---- status. Pattern: Apparel orders are returned far more often.
    first_cat = pd.Series(prod_cat[prod_idx]).groupby(item_order).first().reindex(range(n)).to_numpy()
    r = rng.random(n)
    ret_p = np.where(first_cat == "Apparel", 0.15, 0.04)
    status = np.where(r < ret_p, "Returned", np.where(r > 1 - 0.05, "Cancelled", "Completed"))
    orders["status"] = status
    return orders[["order_id", "order_date", "customer_id", "store_id", "channel", "status"]], items


def _copy(cur: psycopg.Cursor, table: str, df: pd.DataFrame) -> None:
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    cols = ", ".join(df.columns)
    with cur.copy(f"COPY analytics.{table} ({cols}) FROM STDIN WITH (FORMAT csv)") as cp:
        while chunk := buf.read(1 << 20):
            cp.write(chunk)


def main() -> None:
    from faker import Faker

    url = os.environ.get("DATABASE_URL_ADMIN")
    if not url:
        from app.config import get_settings

        url = get_settings().database_url_admin
    if not url:
        raise SystemExit("DATABASE_URL_ADMIN is not set")
    url = url.replace("postgresql+psycopg://", "postgresql://")

    rng = np.random.default_rng(SEED)
    faker = Faker("en_IN")
    Faker.seed(SEED)

    dim_date = build_dates()
    products = build_products(rng)
    customers = build_customers(rng, faker)
    orders, items = build_orders_and_items(rng, customers, products)
    regions = pd.DataFrame(REGIONS, columns=["region_id", "region_name", "zone"])
    stores = pd.DataFrame(STORES, columns=["store_id", "store_name", "city", "state", "region_id"])

    with psycopg.connect(url, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute((Path(__file__).parent / "schema.sql").read_text())
        cur.execute(
            "TRUNCATE analytics.fact_order_items, analytics.fact_orders, analytics.dim_customer, "
            "analytics.dim_product, analytics.dim_store, analytics.dim_region, analytics.dim_date"
        )
        for name, df in [
            ("dim_date", dim_date), ("dim_region", regions), ("dim_store", stores),
            ("dim_customer", customers), ("dim_product", products),
            ("fact_orders", orders), ("fact_order_items", items),
        ]:
            _copy(cur, name, df)
        cur.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='analyst_ro') THEN "
            "GRANT USAGE ON SCHEMA analytics TO analyst_ro; "
            "GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analyst_ro; END IF; END $$"
        )
        cur.execute("ANALYZE")
        conn.commit()
    print(f"seeded: {len(orders)} orders, {len(items)} items, {len(customers)} customers, "
          f"{len(products)} products, max(order_date)={orders['order_date'].max()}")


if __name__ == "__main__":
    main()
