# SEED_NOTES — synthetic data and injected patterns

**All data in schema `analytics` is synthetic**, produced by `database/seed.py`
(`SEED = 42`). No real customers, stores, or sales figures. Re-running the seed truncates
and reloads identical rows.

## Window and volume
- Orders from **2024-10-01 to 2026-09-30** (730 days, two Diwali seasons). The window is
  fixed in code, not taken from the system clock, so `ANCHOR_DATE = max(order_date) = 2026-09-30`.
  "Latest quarter" = **Jul–Sep 2026** (calendar Q3 2026 = FY2026-27 fiscal Q2).
- ~50k orders, ~90k order lines, 5 regions, 40 stores, 5,000 customers, 200 products in
  8 categories (25 each). Amounts in INR.
- `dim_date` covers exactly the order window. Fiscal year is Indian (Apr–Mar),
  `fiscal_year` label like `FY2026-27`, `fiscal_quarter` 1 = Apr–Jun.

## Generation rules (background behaviour, not "patterns")
- Demand is simulated per store per day: `store weight × seasonality(day) × region multiplier`,
  scaled so the total is ≈ 50k, then Poisson-sampled.
- **Seasonality:** Diwali build-up (≈ 25 days before Diwali, peak ~5 days before; Diwali
  2024-11-01 and 2025-10-21) up to ~2× demand; +15% Oct–Nov; +15% end-of-season sales in
  the first half of Jan and Jul; −8% in Feb and Jun; +15% weekends. October is the peak
  month in both years.
- **Channel:** each order's `store_id` is the selling / fulfilling store, also for Online
  orders. Online share rises linearly from ~30% to ~55% over the window.
- **Baskets:** 1–4 lines; each order has a primary category (regional mix differs slightly:
  South over-indexes Electronics ×1.5, West Fashion ×1.15, East Grocery ×1.2); extra lines
  are 80% from the primary category. Within a category, cheaper products sell more
  (P ∝ price^−0.5). Corporate/SMB customers sometimes buy double quantities.
- **Pricing:** `unit_price = list_price` (retail prices ending in 9).
  `discount_pct` is a percentage (0–40): base 0/5/10/15, +5–20 during the Diwali window,
  +5 on ~30% of Online orders.
  `line_revenue = quantity × unit_price × (1 − discount_pct/100)`, `line_cost = quantity × unit_cost`.
- **Status:** 4% Cancelled; Returned 3.5% (Fashion 14%, see P4); rest Completed (~90%).
  Revenue for Returned/Cancelled orders is still recorded on the lines — KPI definitions
  decide whether to exclude them.
- **Customers:** live in a store city of their region; orders go only to customers of the
  store's region who had signed up on or before the order date.

## Injected patterns (the answers to "why" questions)

### P1 — South India revenue drop in the latest quarter (Jul–Sep 2026)
Two causes, both in `seed.py`:
1. **Electronics demand collapse in South India.** From 2026-07-01, 65% of South India
   orders whose primary category is Electronics are not generated
   (`SOUTH_ELECTRONICS_KEEP = 0.35`). Other categories and regions are unaffected.
2. **Store closure.** `Chennai - Velachery` (store in Chennai, Tamil Nadu) has its last
   trading day on **2026-08-14**, mid-quarter; it has no orders afterwards and its demand
   is lost, not transferred.

Observed in the seeded data (completed orders): South India revenue ≈ ₹137.7 L in Apr–Jun 2026
→ ≈ ₹96.7 L in Jul–Sep 2026 (≈ −30%), and ≈ ₹149.3 L in Jul–Sep 2025 (≈ −35% YoY).
South Electronics fell ≈ ₹60.4 L → ≈ ₹26.6 L, most of the drop; Velachery fell ≈ ₹13.1 L → ≈ ₹7.0 L.
Other regions are flat or up in the same quarter.

### P2 — East India is the fast-growing region
East India demand multiplier ramps linearly from 1.0 (Oct 2024) to 2.4 (Sep 2026)
(`EAST_GROWTH_END_MULT`); other regions get only +10% over two years. East customer
sign-ups are also skewed toward recent dates, so growth coincides with new-customer
acquisition. Observed: completed revenue Oct 2024–Sep 2025 ≈ ₹3.69 Cr → Oct 2025–Sep 2026
≈ ₹5.68 Cr (≈ +54%), while other regions move between roughly −6% and +7%.

### P3 — Furniture has the highest AOV
Furniture list prices are ₹14k–₹60k (the most expensive category) and it is a
low-volume category (5% of orders). Orders containing Furniture have an average order
value ≈ ₹66k vs ≈ ₹24k for Electronics (next highest) and ≈ ₹3–4k for Grocery/Books.

### P4 — Fashion has a high return rate
Orders whose primary category is Fashion are Returned with probability 14% vs 3.5% for
other categories (`RETURN_RATE`). Observed: ≈ 13% of orders containing Fashion are
Returned vs ≈ 3–4% for every other category.

### Also explainable (by construction)
- Festive (Diwali) peaks every October, with higher discounts in that window.
- Rising Online channel share (~30% → ~55%).

"Observed" figures above were produced by ad-hoc SQL against the seeded database and are
approximate; if the generator changes, re-check them.
