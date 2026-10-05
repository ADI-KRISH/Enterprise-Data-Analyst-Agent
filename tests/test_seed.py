from database.seed import generate


def test_seed_is_deterministic_and_has_injected_patterns():
    a, b = generate(), generate()
    assert a.fact_orders.equals(b.fact_orders) and a.fact_order_items.equals(b.fact_order_items)
    assert 45_000 <= len(a.fact_orders) <= 55_000

    orders = a.fact_orders.merge(a.dim_store[["store_id", "store_name"]], on="store_id")
    chennai_store = orders[(orders.store_name == "Chennai Central") & (orders.channel == "Store")]
    assert chennai_store.order_date.max().isoformat() < "2026-07-21"        # closure on 2026-07-20
    assert set(a.fact_orders.status) == {"Completed", "Returned", "Cancelled"}
    assert a.dim_date.date.min().isoformat() == "2024-10-01"
