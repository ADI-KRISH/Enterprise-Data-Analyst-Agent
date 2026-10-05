"""Deterministic synthetic data generator for the `analytics` star schema.

Run (admin credentials, never the read-only agent role):
    python -m database.seed

Everything is derived from a fixed RNG seed, so two runs produce identical data.
Injected, explainable patterns are listed in database/SEED_NOTES.md; if you change a
pattern here, update that file in the same commit.
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from faker import Faker

from app.config import PROJECT_ROOT

SEED = 42
START = dt.date(2024, 10, 1)
END = dt.date(2026, 9, 30)  # 24 full months; ANCHOR_DATE resolves to this (last order may be earlier)
TARGET_ORDERS = 50_000
N_CUSTOMERS = 4_000

# Injected patterns (see SEED_NOTES.md)
SOUTH_DIP_START = dt.date(2026, 7, 1)  # latest fiscal quarter (Q2 FY2027 = Jul-Sep 2026)
SOUTH_ELECTRONICS_WEIGHT = 0.30
CLOSED_STORE = "Chennai Central"
CLOSURE_DATE = dt.date(2026, 7, 20)
CLOSURE_SHIFT_ONLINE = 0.40  # share of would-be store orders that move online; the rest are lost
DIWALI = {dt.date(2024, 11, 1), dt.date(2025, 10, 20)}
FESTIVE_SALE = (dt.date(2025, 10, 10), dt.date(2025, 11, 5))  # deeper discounts
WEST_GROWTH = 0.5  # West customer activity grows linearly by +50% over the window

REGIONS = [
    (1, "North India", "North"),
    (2, "South India", "South"),
    (3, "East India", "East"),
    (4, "West India", "West"),
    (5, "Central India", "Central"),
]
# region_id -> [(city, state, weight)]
CITIES = {
    1: [("Delhi", "Delhi", 5), ("Chandigarh", "Chandigarh", 2), ("Jaipur", "Rajasthan", 3), ("Lucknow", "Uttar Pradesh", 3)],
    2: [("Chennai", "Tamil Nadu", 4), ("Bengaluru", "Karnataka", 5), ("Hyderabad", "Telangana", 4), ("Kochi", "Kerala", 2)],
    3: [("Kolkata", "West Bengal", 4), ("Bhubaneswar", "Odisha", 2), ("Patna", "Bihar", 2), ("Guwahati", "Assam", 2)],
    4: [("Mumbai", "Maharashtra", 6), ("Pune", "Maharashtra", 4), ("Ahmedabad", "Gujarat", 3), ("Surat", "Gujarat", 2)],
    5: [("Bhopal", "Madhya Pradesh", 2), ("Indore", "Madhya Pradesh", 2), ("Nagpur", "Maharashtra", 2), ("Raipur", "Chhattisgarh", 1)],
}
REGION_WEIGHT = {1: 1.0, 2: 1.0, 3: 0.7, 4: 1.1, 5: 0.5}

# category -> (selection weight, return propensity, {sub_category: (min price, max price, margin)})
CATALOG = {
    "Electronics": (0.13, 0.04, {
        "Smartphones": (12000, 70000, 0.12), "Laptops": (35000, 120000, 0.10),
        "Audio": (800, 15000, 0.30), "Accessories": (300, 5000, 0.45)}),
    "Apparel": (0.20, 0.12, {
        "Men's Wear": (500, 4000, 0.45), "Women's Wear": (600, 6000, 0.48), "Kids Wear": (300, 2500, 0.42)}),
    "Home & Kitchen": (0.14, 0.05, {
        "Appliances": (1500, 25000, 0.22), "Cookware": (400, 6000, 0.35), "Decor": (300, 8000, 0.45)}),
    "Furniture": (0.05, 0.10, {
        "Living Room": (5000, 60000, 0.30), "Bedroom": (6000, 80000, 0.30), "Office": (3000, 30000, 0.28)}),
    "Grocery": (0.30, 0.01, {
        "Staples": (100, 1500, 0.12), "Snacks": (50, 600, 0.20), "Beverages": (50, 900, 0.22)}),
    "Beauty & Personal Care": (0.18, 0.03, {
        "Skincare": (200, 3500, 0.50), "Haircare": (150, 2500, 0.45), "Fragrance": (400, 6000, 0.55)}),
}
BRANDS = ["Voltix", "Nexora", "Zenith", "Aurelia", "Kaveri", "Mantra", "Bharat Basics", "Orbis", "Lumina", "Veda"]
SUFFIXES = ["Lite", "Plus", "Pro", "Max", "Classic"]

SEASON = {1: 0.95, 2: 0.90, 3: 1.00, 4: 0.95, 5: 0.95, 6: 0.90, 7: 0.90, 8: 1.00, 9: 1.05, 10: 1.25, 11: 1.20, 12: 1.15}
DOW = {0: 0.92, 1: 0.90, 2: 0.92, 3: 0.95, 4: 1.05, 5: 1.25, 6: 1.20}


@dataclass
class Dataset:
    dim_date: pd.DataFrame
    dim_region: pd.DataFrame
    dim_store: pd.DataFrame
    dim_customer: pd.DataFrame
    dim_product: pd.DataFrame
    fact_orders: pd.DataFrame
    fact_order_items: pd.DataFrame


def build_dim_date() -> pd.DataFrame:
    days = pd.date_range(START, END, freq="D")
    fiscal_year = np.where(days.month >= 4, days.year + 1, days.year)
    fiscal_quarter = ((days.month - 4) % 12) // 3 + 1
    return pd.DataFrame({
        "date_key": days.year * 10000 + days.month * 100 + days.day,
        "date": days.date,
        "day": days.day,
        "month": days.month,
        "month_name": days.strftime("%B"),
        "quarter": days.quarter,
        "year": days.year,
        "fiscal_quarter": fiscal_quarter,
        "fiscal_year": fiscal_year,
    })


def build_stores() -> pd.DataFrame:
    rows, sid = [], 1
    for region_id, cities in CITIES.items():
        for city, state, _ in cities:
            rows.append((sid, f"{city} Central", city, state, region_id))
            sid += 1
    return pd.DataFrame(rows, columns=["store_id", "store_name", "city", "state", "region_id"])


def build_products(rng: np.random.Generator) -> pd.DataFrame:
    rows, pid = [], 1
    for category, (_, _, subs) in CATALOG.items():
        for sub, (lo, hi, margin) in subs.items():
            for suffix in SUFFIXES[: int(rng.integers(3, 6))]:
                brand = BRANDS[int(rng.integers(len(BRANDS)))]
                price = float(np.round(np.exp(rng.uniform(np.log(lo), np.log(hi))), -1) or lo)
                cost = round(price * (1 - margin * rng.uniform(0.85, 1.15)), 2)
                rows.append((pid, f"{brand} {sub} {suffix}", category, sub, brand, cost, price))
                pid += 1
    return pd.DataFrame(rows, columns=["product_id", "product_name", "category", "sub_category", "brand", "unit_cost", "list_price"])


def build_customers(rng: np.random.Generator, fake: Faker) -> pd.DataFrame:
    city_rows = [(r, c, s, w * REGION_WEIGHT[r]) for r, cs in CITIES.items() for c, s, w in cs]
    p = np.array([w for *_, w in city_rows], dtype=float)
    idx = rng.choice(len(city_rows), size=N_CUSTOMERS, p=p / p.sum())
    segments = rng.choice(["Consumer", "Corporate", "SMB"], size=N_CUSTOMERS, p=[0.70, 0.12, 0.18])
    early = rng.random(N_CUSTOMERS) < 0.8
    span = (END - START).days
    signup = [
        (dt.date(2023, 1, 1) + dt.timedelta(days=int(rng.integers(0, (START - dt.date(2023, 1, 1)).days))))
        if e else START + dt.timedelta(days=int(rng.integers(0, span - 30)))
        for e in early
    ]
    return pd.DataFrame({
        "customer_id": np.arange(1, N_CUSTOMERS + 1),
        "name": [fake.name() for _ in range(N_CUSTOMERS)],
        "segment": segments,
        "city": [city_rows[i][1] for i in idx],
        "state": [city_rows[i][2] for i in idx],
        "region_id": [city_rows[i][0] for i in idx],
        "signup_date": signup,
    })


def generate(seed: int = SEED) -> Dataset:
    rng = np.random.default_rng(seed)
    fake = Faker("en_IN")
    Faker.seed(seed)

    dim_date = build_dim_date()
    dim_region = pd.DataFrame(REGIONS, columns=["region_id", "region_name", "zone"])
    dim_store = build_stores()
    dim_product = build_products(rng)
    dim_customer = build_customers(rng, fake)

    # ---- lookups
    store_by_city = dict(zip(dim_store["city"], dim_store["store_id"]))
    stores_by_region = dim_store.groupby("region_id")["store_id"].apply(list).to_dict()
    closed_store_id = int(dim_store.loc[dim_store["store_name"] == CLOSED_STORE, "store_id"].iloc[0])
    closed_city = CLOSED_STORE.replace(" Central", "")

    cust = dim_customer.sort_values("signup_date").reset_index(drop=True)
    c_ids = cust["customer_id"].to_numpy()
    c_region = cust["region_id"].to_numpy()
    c_city = cust["city"].to_numpy()
    c_segment = cust["segment"].to_numpy()
    c_signup = cust["signup_date"].to_numpy()
    activity = rng.lognormal(0.0, 0.7, N_CUSTOMERS)  # some customers buy far more often

    prod = dim_product
    p_ids = prod["product_id"].to_numpy()
    p_cat = prod["category"].to_numpy()
    p_price = prod["list_price"].to_numpy(float)
    p_cost = prod["unit_cost"].to_numpy(float)
    cat_w = {c: v[0] for c, v in CATALOG.items()}
    cat_ret = {c: v[1] for c, v in CATALOG.items()}
    base_item_w = np.array([cat_w[c] / (p_cat == c).sum() for c in p_cat])
    electronics_mask = p_cat == "Electronics"

    # ---- daily order volume
    days = [START + dt.timedelta(days=i) for i in range((END - START).days + 1)]
    n_days = len(days)
    lam = np.zeros(n_days)
    for i, d in enumerate(days):
        season = SEASON[d.month]
        if any(abs((d - dd).days) <= 10 for dd in DIWALI):
            season *= 1.45
        lam[i] = season * DOW[d.weekday()] * (1 + 0.25 * i / n_days)  # +25% organic growth
    lam *= TARGET_ORDERS / lam.sum()

    orders, items = [], []
    order_id = item_id = 0
    for i, d in enumerate(days):
        k = int(np.searchsorted(c_signup, d, side="right"))  # customers who have signed up by d
        if k == 0:
            continue
        west_mult = 1 + WEST_GROWTH * i / n_days
        w = activity[:k] * np.where(c_region[:k] == 4, west_mult, 1.0)
        n = int(rng.poisson(lam[i]))
        chosen = rng.choice(k, size=n, p=w / w.sum()) if n else []
        online_p = 0.30 + 0.15 * i / n_days  # online share grows 30% -> 45%
        in_sale = FESTIVE_SALE[0] <= d <= FESTIVE_SALE[1]
        south_dip = d >= SOUTH_DIP_START

        for ci in chosen:
            region, city, segment = int(c_region[ci]), c_city[ci], c_segment[ci]
            channel = "Online" if rng.random() < online_p else "Store"
            if channel == "Store" and city == closed_city and d >= CLOSURE_DATE:
                if rng.random() < CLOSURE_SHIFT_ONLINE:
                    channel = "Online"
                else:
                    continue  # lost sale
            if channel == "Store":
                store_id = store_by_city[city]
            else:  # fulfilled by a store in the customer's region (never a closed one)
                options = [s for s in stores_by_region[region] if not (s == closed_store_id and d >= CLOSURE_DATE)]
                store_id = int(options[int(rng.integers(len(options)))])

            n_items = int(rng.choice([1, 2, 3, 4, 5], p=[0.45, 0.28, 0.16, 0.07, 0.04]))
            weights = base_item_w.copy()
            if south_dip and region == 2:
                weights[electronics_mask] *= SOUTH_ELECTRONICS_WEIGHT
            picks = rng.choice(len(p_ids), size=n_items, replace=False, p=weights / weights.sum())

            ret_p = 0.04
            order_id += 1
            for pi in picks:
                cat = p_cat[pi]
                qty_cap = 10 if segment == "Corporate" else (4 if cat == "Grocery" else 2)
                qty = int(rng.integers(1, qty_cap + 1))
                disc = float(rng.choice([0, 5, 10, 15, 20], p=[0.40, 0.25, 0.20, 0.10, 0.05]))
                if in_sale:
                    disc = min(40.0, disc + 10 + (5 if cat == "Electronics" else 0))
                price = round(float(p_price[pi]), 2)
                item_id += 1
                items.append((item_id, order_id, int(p_ids[pi]), qty, price, disc,
                              round(qty * price * (1 - disc / 100), 2), round(qty * float(p_cost[pi]), 2)))
                ret_p = max(ret_p, cat_ret[cat])
            u = rng.random()
            status = "Cancelled" if u < 0.035 else ("Returned" if u < 0.035 + ret_p else "Completed")
            orders.append((order_id, d, int(c_ids[ci]), store_id, channel, status))

    return Dataset(
        dim_date, dim_region, dim_store, dim_customer, dim_product,
        pd.DataFrame(orders, columns=["order_id", "order_date", "customer_id", "store_id", "channel", "status"]),
        pd.DataFrame(items, columns=["order_item_id", "order_id", "product_id", "quantity", "unit_price",
                                     "discount_pct", "line_revenue", "line_cost"]),
    )


TABLE_ORDER = ["dim_date", "dim_region", "dim_store", "dim_customer", "dim_product", "fact_orders", "fact_order_items"]


def load(ds: Dataset, admin_url: str) -> None:
    url = admin_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        exists = conn.execute("SELECT to_regclass('analytics.fact_orders')").fetchone()[0]
        if exists is None:
            conn.execute((PROJECT_ROOT / "database" / "schema.sql").read_text())
        conn.execute("TRUNCATE " + ", ".join(f"analytics.{t}" for t in reversed(TABLE_ORDER)))
        for table in TABLE_ORDER:
            df = getattr(ds, table)
            cols = list(df.columns)
            with conn.cursor() as cur, cur.copy(f"COPY analytics.{table} ({', '.join(cols)}) FROM STDIN") as cp:
                for row in df.itertuples(index=False, name=None):
                    cp.write_row([None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in row])
        conn.commit()


def main() -> int:
    from app.config import get_settings

    admin = get_settings().database_url_admin
    if not admin:
        print("DATABASE_URL_ADMIN is not set", file=sys.stderr)
        return 1
    ds = generate()
    load(ds, admin)
    print(f"seeded: {len(ds.fact_orders):,} orders, {len(ds.fact_order_items):,} items, "
          f"{len(ds.dim_customer):,} customers, {len(ds.dim_product)} products, "
          f"orders {ds.fact_orders['order_date'].min()} -> {ds.fact_orders['order_date'].max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
