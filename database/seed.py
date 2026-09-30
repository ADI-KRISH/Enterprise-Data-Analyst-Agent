"""Deterministic synthetic data generator for the `analytics` star schema.

Usage:  python -m database.seed          (uses DATABASE_URL_ADMIN from .env)

Everything is driven by a fixed RNG seed and a fixed date window, so repeated runs
produce identical rows. The injected, explainable patterns are documented in
database/SEED_NOTES.md — keep the two in sync.
"""

from __future__ import annotations

import calendar
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import psycopg
from faker import Faker

from app.config import get_settings

SEED = 42
START_DATE = date(2024, 10, 1)
END_DATE = date(2026, 9, 30)  # fixed, so ANCHOR_DATE (= max(order_date)) is stable
TARGET_ORDERS = 50_000
N_CUSTOMERS = 5_000

# --- Injected patterns (see SEED_NOTES.md) -----------------------------------
LATEST_Q_START = date(2026, 7, 1)  # latest quarter: Jul-Sep 2026 (FY2026-27 Q2)
SOUTH_ELECTRONICS_KEEP = 0.35  # P1: South Electronics orders keep 35% of demand in latest quarter
CLOSED_STORE = "Chennai - Velachery"  # P1: closes mid-quarter
CLOSED_STORE_LAST_DAY = date(2026, 8, 14)
EAST_GROWTH_END_MULT = 2.4  # P2: East India demand ramps linearly 1.0 -> 2.4 over the window
HIGH_AOV_CATEGORY = "Furniture"  # P3
RETURN_RATE = {"Fashion": 0.14}  # P4: default below
DEFAULT_RETURN_RATE = 0.035
CANCEL_RATE = 0.04
DIWALI = [date(2024, 11, 1), date(2025, 10, 21)]

# --- Reference data ------------------------------------------------------------
REGIONS = [  # region_id, name, zone, base demand weight per store
    (1, "North India", "North-Central", 1.00),
    (2, "South India", "South", 1.10),
    (3, "East India", "East", 0.75),
    (4, "West India", "West", 1.05),
    (5, "Central India", "North-Central", 0.80),
]

STORES = {  # region_id -> [(store_name, city, state)]
    1: [("Delhi - Connaught Place", "New Delhi", "Delhi"),
        ("Delhi - Rajouri Garden", "New Delhi", "Delhi"),
        ("Gurugram - Cyber Hub", "Gurugram", "Haryana"),
        ("Noida - Sector 18", "Noida", "Uttar Pradesh"),
        ("Lucknow - Hazratganj", "Lucknow", "Uttar Pradesh"),
        ("Jaipur - MI Road", "Jaipur", "Rajasthan"),
        ("Chandigarh - Sector 17", "Chandigarh", "Chandigarh"),
        ("Amritsar - Lawrence Road", "Amritsar", "Punjab"),
        ("Dehradun - Rajpur Road", "Dehradun", "Uttarakhand")],
    2: [("Bengaluru - Indiranagar", "Bengaluru", "Karnataka"),
        ("Bengaluru - Koramangala", "Bengaluru", "Karnataka"),
        ("Chennai - T Nagar", "Chennai", "Tamil Nadu"),
        ("Chennai - Velachery", "Chennai", "Tamil Nadu"),
        ("Hyderabad - Banjara Hills", "Hyderabad", "Telangana"),
        ("Kochi - MG Road", "Kochi", "Kerala"),
        ("Coimbatore - RS Puram", "Coimbatore", "Tamil Nadu"),
        ("Visakhapatnam - Dwaraka Nagar", "Visakhapatnam", "Andhra Pradesh"),
        ("Mysuru - Sayyaji Rao Road", "Mysuru", "Karnataka")],
    3: [("Kolkata - Park Street", "Kolkata", "West Bengal"),
        ("Kolkata - Salt Lake", "Kolkata", "West Bengal"),
        ("Bhubaneswar - Saheed Nagar", "Bhubaneswar", "Odisha"),
        ("Patna - Fraser Road", "Patna", "Bihar"),
        ("Guwahati - GS Road", "Guwahati", "Assam"),
        ("Ranchi - Main Road", "Ranchi", "Jharkhand"),
        ("Siliguri - Sevoke Road", "Siliguri", "West Bengal")],
    4: [("Mumbai - Bandra West", "Mumbai", "Maharashtra"),
        ("Mumbai - Andheri East", "Mumbai", "Maharashtra"),
        ("Pune - FC Road", "Pune", "Maharashtra"),
        ("Pune - Viman Nagar", "Pune", "Maharashtra"),
        ("Ahmedabad - SG Highway", "Ahmedabad", "Gujarat"),
        ("Surat - Adajan", "Surat", "Gujarat"),
        ("Vadodara - Alkapuri", "Vadodara", "Gujarat"),
        ("Panaji - 18th June Road", "Panaji", "Goa"),
        ("Nashik - College Road", "Nashik", "Maharashtra")],
    5: [("Bhopal - MP Nagar", "Bhopal", "Madhya Pradesh"),
        ("Indore - Vijay Nagar", "Indore", "Madhya Pradesh"),
        ("Jabalpur - Napier Town", "Jabalpur", "Madhya Pradesh"),
        ("Gwalior - City Centre", "Gwalior", "Madhya Pradesh"),
        ("Raipur - Pandri", "Raipur", "Chhattisgarh"),
        ("Nagpur - Sitabuldi", "Nagpur", "Maharashtra")],
}


@dataclass(frozen=True)
class Category:
    name: str
    order_weight: float  # share of orders whose primary category this is
    price_range: tuple[float, float]  # INR list price, sampled log-uniformly
    margin_range: tuple[float, float]  # unit_cost / list_price
    max_qty: int
    sub_categories: tuple[str, ...]
    brands: tuple[str, ...]


CATEGORIES = [
    Category("Electronics", 0.17, (799, 70_000), (0.72, 0.86), 2,
             ("Smartphones", "Laptops", "Headphones", "Televisions", "Accessories"),
             ("Samsung", "Xiaomi", "OnePlus", "boAt", "Lenovo", "Sony")),
    Category("Home Appliances", 0.10, (1_999, 48_000), (0.68, 0.80), 1,
             ("Refrigerators", "Washing Machines", "Air Conditioners", "Microwaves", "Mixer Grinders"),
             ("LG", "Whirlpool", "Voltas", "Bajaj", "Prestige", "IFB")),
    Category("Furniture", 0.05, (14_000, 60_000), (0.55, 0.68), 1,
             ("Sofas", "Beds", "Dining Tables", "Office Chairs", "Wardrobes"),
             ("Godrej Interio", "Urban Ladder", "Nilkamal", "Durian", "Wakefit")),
    Category("Fashion", 0.20, (399, 6_500), (0.40, 0.58), 3,
             ("Men's Clothing", "Women's Clothing", "Footwear", "Ethnic Wear", "Accessories"),
             ("Fabindia", "Allen Solly", "Biba", "Bata", "Peter England", "W")),
    Category("Grocery & Staples", 0.22, (60, 1_600), (0.78, 0.90), 6,
             ("Rice & Atta", "Edible Oils", "Pulses", "Spices", "Beverages"),
             ("Aashirvaad", "Fortune", "Tata", "India Gate", "MDH", "Tata Tea")),
    Category("Beauty & Personal Care", 0.11, (149, 3_200), (0.45, 0.62), 3,
             ("Skincare", "Haircare", "Fragrances", "Makeup", "Grooming"),
             ("Himalaya", "Lakme", "Mamaearth", "Nivea", "Biotique", "Forest Essentials")),
    Category("Sports & Fitness", 0.07, (399, 24_000), (0.55, 0.70), 2,
             ("Cricket", "Cycling", "Gym Equipment", "Yoga", "Sportswear"),
             ("SG", "Hero", "Decathlon", "Nivia", "Cosco", "Puma")),
    Category("Books & Stationery", 0.08, (99, 1_900), (0.60, 0.75), 4,
             ("Fiction", "Academic", "Children's Books", "Notebooks", "Art Supplies"),
             ("Penguin", "HarperCollins", "Classmate", "Camlin", "Rupa", "S. Chand")),
]
PRODUCTS_PER_CATEGORY = 25
POPULARITY_EXPONENT = 0.5  # within a category, P(product) ∝ list_price^-0.5: cheaper items sell more
# Regional tilt on category mix (multiplier on order_weight).
REGION_CATEGORY_TILT = {2: {"Electronics": 1.5}, 4: {"Fashion": 1.15}, 3: {"Grocery & Staples": 1.2}}


# --- Helpers (pure, unit-tested) ----------------------------------------------

def fiscal_year_label(d: date) -> str:
    """Indian FY (Apr-Mar): 2026-05-10 -> 'FY2026-27', 2026-02-10 -> 'FY2025-26'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"FY{start}-{(start + 1) % 100:02d}"


def fiscal_quarter(d: date) -> int:
    """Apr-Jun = 1, Jul-Sep = 2, Oct-Dec = 3, Jan-Mar = 4."""
    return (d.month - 4) % 12 // 3 + 1


def date_row(d: date) -> tuple:
    return (d.year * 10000 + d.month * 100 + d.day, d, d.day, d.month, calendar.month_name[d.month],
            (d.month - 1) // 3 + 1, d.year, fiscal_quarter(d), fiscal_year_label(d))


def daterange(start: date, end: date):
    for i in range((end - start).days + 1):
        yield start + timedelta(days=i)


def seasonality(d: date) -> float:
    """Demand multiplier: Diwali festive peak, end-of-season sales, weekends."""
    m = 1.0
    for diwali in DIWALI:
        gap = (d - diwali).days
        if -25 <= gap <= 5:  # build-up to Diwali (Dhanteras, festive sales), sharp drop after
            m += 1.1 * math.exp(-((gap + 5) ** 2) / (2 * 9.0**2))
    if d.month in (10, 11):
        m += 0.15
    if d.month in (1, 7) and d.day <= 15:  # end-of-season sales
        m += 0.15
    if d.month in (2, 6):  # post-festive / monsoon lull
        m -= 0.08
    if d.weekday() >= 5:
        m += 0.15
    return m


def region_multiplier(region_id: int, d: date) -> float:
    if region_id == 3:  # P2: East India steady ramp
        t = (d - START_DATE).days / (END_DATE - START_DATE).days
        return 1.0 + (EAST_GROWTH_END_MULT - 1.0) * t
    return 1.0 + 0.10 * (d - START_DATE).days / 730  # modest organic growth elsewhere


def round_price(x: float) -> float:
    """Retail-style INR price ending in 9 (e.g. 1,249 / 18,999)."""
    if x < 1000:
        return float(max(49, int(round(x / 10.0)) * 10 - 1))
    return float(int(round(x / 100.0)) * 100 - 1)


# --- Generators -----------------------------------------------------------------

def build_products(rng: np.random.Generator) -> list[tuple]:
    rows, pid = [], 1
    for cat in CATEGORIES:
        lo, hi = np.log(cat.price_range[0]), np.log(cat.price_range[1])
        for i in range(PRODUCTS_PER_CATEGORY):
            sub = cat.sub_categories[i % len(cat.sub_categories)]
            brand = cat.brands[int(rng.integers(len(cat.brands)))]
            price = round_price(float(np.exp(rng.uniform(lo, hi))))
            cost = round(price * float(rng.uniform(*cat.margin_range)), 2)
            name = f"{brand} {sub} {chr(65 + i // 5)}{100 + (i * 37) % 900}"
            rows.append((pid, name, cat.name, sub, brand, cost, price))
            pid += 1
    return rows


def build_stores() -> list[tuple]:
    rows, sid = [], 1
    for region_id, _name, _zone, _w in REGIONS:
        for store_name, city, state in STORES[region_id]:
            rows.append((sid, store_name, city, state, region_id))
            sid += 1
    return rows


def build_customers(rng: np.random.Generator, fake: Faker, stores: list[tuple]) -> list[tuple]:
    """Customers live in store cities. East India signups skew late (drives P2)."""
    region_share = {1: 0.22, 2: 0.24, 3: 0.16, 4: 0.23, 5: 0.15}
    region_ids = list(region_share)
    probs = np.array([region_share[r] for r in region_ids])
    cities = {r: [(s[2], s[3]) for s in stores if s[4] == r] for r in region_ids}
    earliest = START_DATE - timedelta(days=540)
    span = (END_DATE - timedelta(days=30) - earliest).days
    rows = []
    for cid in range(1, N_CUSTOMERS + 1):
        r = int(rng.choice(region_ids, p=probs))
        city, state = cities[r][int(rng.integers(len(cities[r])))]
        seg = str(rng.choice(["Consumer", "Corporate", "SMB"], p=[0.72, 0.13, 0.15]))
        u = rng.beta(2.2, 1.0) if r == 3 else rng.beta(1.4, 1.2)  # East skews to recent signups
        signup = earliest + timedelta(days=int(u * span))
        rows.append((cid, fake.name(), seg, city, state, r, signup))
    return rows


def build_orders(rng: np.random.Generator, stores: list[tuple], customers: list[tuple],
                 products: list[tuple]):
    region_weight = {r[0]: r[3] for r in REGIONS}
    store_weight = {s[0]: region_weight[s[4]] * float(rng.uniform(0.7, 1.3)) for s in stores}
    closed_id = next(s[0] for s in stores if s[1] == CLOSED_STORE)
    stores_by_region = {r[0]: [s for s in stores if s[4] == r[0]] for r in REGIONS}

    # Customers per region sorted by signup, so "signed up by date d" is a prefix.
    cust_by_region = {}
    for r in stores_by_region:
        cs = sorted((c for c in customers if c[5] == r), key=lambda c: c[6])
        cust_by_region[r] = (np.array([c[0] for c in cs]), np.array([c[6].toordinal() for c in cs]))
    segment = {c[0]: c[2] for c in customers}

    cat_names = [c.name for c in CATEGORIES]
    cat_by_name = {c.name: c for c in CATEGORIES}
    prods_by_cat = {n: [p for p in products if p[2] == n] for n in cat_names}
    prod_probs = {}
    for n, pool in prods_by_cat.items():
        w = np.array([p[6] for p in pool]) ** -POPULARITY_EXPONENT
        prod_probs[n] = w / w.sum()
    cat_probs = {}
    for r in stores_by_region:
        w = np.array([c.order_weight * REGION_CATEGORY_TILT.get(r, {}).get(c.name, 1.0) for c in CATEGORIES])
        cat_probs[r] = w / w.sum()

    days = list(daterange(START_DATE, END_DATE))
    # Expected orders per (day, store) before normalisation to TARGET_ORDERS.
    raw = {}
    for d in days:
        s_mult = seasonality(d)
        for s in stores:
            if s[0] == closed_id and d > CLOSED_STORE_LAST_DAY:
                continue
            raw[(d, s[0])] = store_weight[s[0]] * s_mult * region_multiplier(s[4], d)
    # South Electronics rejection removes ~a few % of demand; inflate slightly so total ≈ target.
    scale = TARGET_ORDERS * 1.005 / sum(raw.values())
    store_region = {s[0]: s[4] for s in stores}

    orders, items = [], []
    oid, iid = 1, 1
    for d in days:
        festive = any(-25 <= (d - dw).days <= 5 for dw in DIWALI)
        online_p = 0.30 + 0.25 * (d - START_DATE).days / 730  # online share 30% -> 55%
        for s in stores:
            key = (d, s[0])
            if key not in raw:
                continue
            region = store_region[s[0]]
            ids, signups = cust_by_region[region]
            n_eligible = int(np.searchsorted(signups, d.toordinal(), side="right"))
            if n_eligible == 0:
                continue
            for _ in range(rng.poisson(raw[key] * scale)):
                primary = cat_names[int(rng.choice(len(cat_names), p=cat_probs[region]))]
                # P1: South India Electronics demand collapses in the latest quarter.
                if (region == 2 and primary == "Electronics" and d >= LATEST_Q_START
                        and rng.random() > SOUTH_ELECTRONICS_KEEP):
                    continue
                cust = int(ids[int(rng.integers(n_eligible))])
                channel = "Online" if rng.random() < online_p else "Store"
                u = rng.random()
                ret = RETURN_RATE.get(primary, DEFAULT_RETURN_RATE)
                status = "Cancelled" if u < CANCEL_RATE else "Returned" if u < CANCEL_RATE + ret else "Completed"
                orders.append((oid, d, cust, s[0], channel, status))

                n_lines = int(rng.choice([1, 2, 3, 4], p=[0.50, 0.28, 0.15, 0.07]))
                qty_boost = 2 if segment[cust] in ("Corporate", "SMB") and rng.random() < 0.3 else 1
                for li in range(n_lines):
                    cat = primary if li == 0 or rng.random() < 0.8 else cat_names[
                        int(rng.choice(len(cat_names), p=cat_probs[region]))]
                    # Keep extra lines of high-ticket categories rare so baskets stay realistic.
                    if li > 0 and cat in ("Furniture", "Home Appliances") and cat != primary:
                        cat = "Grocery & Staples"
                    pool = prods_by_cat[cat]
                    p = pool[int(rng.choice(len(pool), p=prod_probs[cat]))]
                    c = cat_by_name[cat]
                    qty = int(rng.integers(1, c.max_qty + 1)) * qty_boost
                    base_disc = [0, 0, 5, 5, 10, 15]
                    disc = float(base_disc[int(rng.integers(len(base_disc)))])
                    if festive:
                        disc += float(rng.choice([5, 10, 15, 20]))
                    if channel == "Online":
                        disc += 5.0 if rng.random() < 0.3 else 0.0
                    unit_price = p[6]
                    revenue = round(qty * unit_price * (1 - disc / 100), 2)
                    cost = round(qty * p[5], 2)
                    items.append((iid, oid, p[0], qty, unit_price, disc, revenue, cost))
                    iid += 1
                oid += 1
    return orders, items


# --- Load ---------------------------------------------------------------------------

TABLES = [  # load order (parents first)
    ("dim_date", "date_key, date, day, month, month_name, quarter, year, fiscal_quarter, fiscal_year"),
    ("dim_region", "region_id, region_name, zone"),
    ("dim_store", "store_id, store_name, city, state, region_id"),
    ("dim_customer", "customer_id, name, segment, city, state, region_id, signup_date"),
    ("dim_product", "product_id, product_name, category, sub_category, brand, unit_cost, list_price"),
    ("fact_orders", "order_id, order_date, customer_id, store_id, channel, status"),
    ("fact_order_items",
     "order_item_id, order_id, product_id, quantity, unit_price, discount_pct, line_revenue, line_cost"),
]


def generate() -> dict[str, list[tuple]]:
    rng = np.random.default_rng(SEED)
    fake = Faker("en_IN")
    Faker.seed(SEED)
    stores = build_stores()
    products = build_products(rng)
    customers = build_customers(rng, fake, stores)
    orders, items = build_orders(rng, stores, customers, products)
    return {
        "dim_date": [date_row(d) for d in daterange(START_DATE, END_DATE)],
        "dim_region": [(r[0], r[1], r[2]) for r in REGIONS],
        "dim_store": stores,
        "dim_customer": customers,
        "dim_product": products,
        "fact_orders": orders,
        "fact_order_items": items,
    }


def to_conninfo(url: str) -> str:
    """SQLAlchemy URL (postgresql+psycopg://...) -> libpq URL."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def load(data: dict[str, list[tuple]], url: str) -> None:
    with psycopg.connect(to_conninfo(url)) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(f"analytics.{t}" for t, _ in reversed(TABLES)))
            for table, cols in TABLES:
                with cur.copy(f"COPY analytics.{table} ({cols}) FROM STDIN") as copy:
                    for row in data[table]:
                        copy.write_row(row)
        conn.commit()
        conn.autocommit = True
        conn.execute("ANALYZE")


def main() -> int:
    url = get_settings().database_url_admin
    if not url:
        print("DATABASE_URL_ADMIN is not set (see .env.example)", file=sys.stderr)
        return 1
    t0 = time.perf_counter()
    data = generate()
    t1 = time.perf_counter()
    load(data, url.get_secret_value())
    t2 = time.perf_counter()
    for table, _ in TABLES:
        print(f"{table:<18} {len(data[table]):>8,}")
    print(f"generated in {t1 - t0:.1f}s, loaded in {t2 - t1:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
