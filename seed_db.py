"""
seed_db.py – Generate a synthetic Indian e-commerce sales database.

Deterministic (seed-based), covering Jan 2025 to mid Nov 2026, in INR.
Creates: regions, customers, products, orders, order_items.
"""

from __future__ import annotations

import os
import random
import sqlite3
from datetime import date, timedelta
from typing import Optional


# ── Constants ────────────────────────────────────────────────────────────────

SEED = 42
DB_PATH = "sales.db"

REGIONS = ["North", "South", "West", "East", "Central"]

# Weights control order volume per region (West/South get more)
REGION_WEIGHTS = {"North": 0.18, "South": 0.25, "West": 0.28, "East": 0.14, "Central": 0.15}

SEGMENTS = ["Consumer", "Corporate", "Small Business"]
SEGMENT_WEIGHTS = [0.55, 0.25, 0.20]

CATEGORIES_AND_PRODUCTS: dict[str, list[tuple[str, float, float]]] = {
    "Electronics": [
        ("Wireless Earbuds",        1499,  820),
        ("Bluetooth Speaker",       2499, 1350),
        ("USB-C Hub",               1299,  650),
        ("Smart Watch",             3999, 2200),
    ],
    "Office Supplies": [
        ("Notebook Set (5-pack)",    399,  150),
        ("Gel Pen Pack (10)",        249,   90),
        ("Desk Organizer",           899,  420),
        ("Sticky Notes Bundle",      199,   70),
    ],
    "Home & Kitchen": [
        ("Stainless Steel Bottle",   599,  260),
        ("Bamboo Cutting Board",     749,  340),
        ("LED Desk Lamp",           1199,  580),
        ("Spice Rack Organizer",     649,  280),
    ],
    "Clothing": [
        ("Cotton T-Shirt",           499,  180),
        ("Denim Jeans",             1299,  550),
        ("Casual Sneakers",         1999,  950),
        ("Linen Shirt",              899,  370),
    ],
    "Books": [
        ("Data Science Handbook",    599,  220),
        ("Python Programming",       449,  170),
        ("Business Analytics",       699,  280),
        ("Machine Learning Guide",   549,  210),
    ],
}

INDIAN_CITIES: dict[str, list[str]] = {
    "North":   ["Delhi", "Jaipur", "Lucknow", "Chandigarh", "Agra", "Varanasi", "Dehradun"],
    "South":   ["Bangalore", "Chennai", "Hyderabad", "Kochi", "Coimbatore", "Mysore", "Visakhapatnam"],
    "West":    ["Mumbai", "Pune", "Ahmedabad", "Surat", "Goa", "Nagpur", "Vadodara"],
    "East":    ["Kolkata", "Bhubaneswar", "Patna", "Guwahati", "Ranchi", "Siliguri"],
    "Central": ["Bhopal", "Indore", "Raipur", "Jabalpur", "Gwalior"],
}

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan",
    "Krishna", "Ishaan", "Ananya", "Diya", "Myra", "Sara", "Aadhya", "Isha",
    "Riya", "Priya", "Neha", "Kavya", "Rahul", "Amit", "Rohit", "Vikram",
    "Sanjay", "Pooja", "Meera", "Nisha", "Tanya", "Deepak", "Suresh", "Rajesh",
    "Ankita", "Sneha", "Payal", "Mohit", "Gaurav", "Harsh", "Manish", "Kiran",
    "Swati", "Divya", "Kritika", "Aryan", "Kabir", "Dev", "Yash", "Zara",
    "Naina", "Simran",
]

LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Singh", "Kumar", "Patel", "Reddy", "Nair",
    "Iyer", "Joshi", "Mehta", "Desai", "Shah", "Rao", "Pillai", "Das",
    "Bose", "Chakraborty", "Mukherjee", "Banerjee", "Mishra", "Pandey",
    "Tiwari", "Saxena", "Agarwal", "Jain", "Kapoor", "Malhotra", "Khanna",
    "Bhatia", "Chowdhury", "Sinha", "Thakur", "Kulkarni", "Patil", "Deshpande",
]

ORDER_STATUSES = ["Delivered", "Shipped", "Cancelled", "Returned"]
# Base probabilities (adjusted for festive months)
STATUS_WEIGHTS_NORMAL  = [0.72, 0.12, 0.10, 0.06]
STATUS_WEIGHTS_FESTIVE = [0.68, 0.15, 0.11, 0.06]

START_DATE = date(2025, 1, 1)
END_DATE   = date(2026, 11, 15)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_festive(d: date) -> bool:
    """Oct–Dec of any year."""
    return d.month in (10, 11, 12)


def _month_weight(d: date) -> float:
    """Relative order volume multiplier by month."""
    base = {
        1: 0.80, 2: 0.75, 3: 0.85, 4: 0.80, 5: 0.78,
        6: 0.82, 7: 0.90, 8: 0.95, 9: 1.00,
        10: 1.35, 11: 1.50, 12: 1.30,
    }
    return base[d.month]


# ── Main seeder ──────────────────────────────────────────────────────────────

def seed_database(db_path: str = DB_PATH, seed: int = SEED) -> None:
    """Create and populate the sales database (idempotent: drops existing tables)."""
    rng = random.Random(seed)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()

    # ── Schema ───────────────────────────────────────────────────────────
    cur.executescript("""
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS customers;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS regions;

        CREATE TABLE regions (
            region_id   INTEGER PRIMARY KEY,
            region_name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE customers (
            customer_id   INTEGER PRIMARY KEY,
            customer_name TEXT NOT NULL,
            city          TEXT NOT NULL,
            region_id     INTEGER NOT NULL REFERENCES regions(region_id),
            segment       TEXT NOT NULL,
            signup_date   TEXT NOT NULL  -- ISO YYYY-MM-DD
        );

        CREATE TABLE products (
            product_id   INTEGER PRIMARY KEY,
            product_name TEXT NOT NULL UNIQUE,
            category     TEXT NOT NULL,
            unit_price   REAL NOT NULL,
            unit_cost    REAL NOT NULL
        );

        CREATE TABLE orders (
            order_id   INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            order_date  TEXT NOT NULL,  -- ISO YYYY-MM-DD
            status      TEXT NOT NULL
        );

        CREATE TABLE order_items (
            item_id      INTEGER PRIMARY KEY,
            order_id     INTEGER NOT NULL REFERENCES orders(order_id),
            product_id   INTEGER NOT NULL REFERENCES products(product_id),
            quantity     INTEGER NOT NULL,
            discount_pct REAL NOT NULL,
            sales_amount REAL NOT NULL
        );
    """)

    # ── Regions ──────────────────────────────────────────────────────────
    for idx, name in enumerate(REGIONS, start=1):
        cur.execute("INSERT INTO regions VALUES (?, ?)", (idx, name))

    region_id_map = {name: idx for idx, name in enumerate(REGIONS, start=1)}

    # ── Customers (~600) ─────────────────────────────────────────────────
    customers: list[tuple[int, str, str, int, str, str]] = []
    cust_id = 0
    target_customers = 600

    for _ in range(target_customers):
        cust_id += 1
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        name = f"{first} {last}"

        region_name = rng.choices(REGIONS, weights=[REGION_WEIGHTS[r] for r in REGIONS])[0]
        rid = region_id_map[region_name]
        city = rng.choice(INDIAN_CITIES[region_name])
        segment = rng.choices(SEGMENTS, weights=SEGMENT_WEIGHTS)[0]

        # Signup date: spread across the range, slightly earlier bias
        days_range = (END_DATE - START_DATE).days
        signup_offset = int(rng.betavariate(2, 5) * days_range)
        signup = START_DATE + timedelta(days=signup_offset)
        if signup > END_DATE:
            signup = END_DATE

        customers.append((cust_id, name, city, rid, segment, signup.isoformat()))

    cur.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", customers
    )

    # ── Products ─────────────────────────────────────────────────────────
    prod_id = 0
    product_rows: list[tuple[int, str, str, float, float]] = []
    product_ids: list[int] = []
    product_prices: dict[int, float] = {}

    for category, items in CATEGORIES_AND_PRODUCTS.items():
        for pname, price, cost in items:
            prod_id += 1
            product_rows.append((prod_id, pname, category, price, cost))
            product_ids.append(prod_id)
            product_prices[prod_id] = price

    cur.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", product_rows)

    # ── Orders & Order Items (~12,000 orders) ────────────────────────────
    # Build a customer lookup with signup dates for fast access
    customer_signup = {c[0]: date.fromisoformat(c[5]) for c in customers}

    # Create a "long tail" distribution: a few very active customers
    # Use a Zipf-like weighting
    cust_ids_sorted = sorted(customer_signup.keys())
    rng.shuffle(cust_ids_sorted)
    cust_weights = [1.0 / (i + 1) ** 0.6 for i in range(len(cust_ids_sorted))]

    target_orders = 12000
    order_id = 0
    item_id = 0
    orders_batch: list[tuple[int, int, str, str]] = []
    items_batch: list[tuple[int, int, int, int, float, float]] = []

    # Distribute orders across days proportionally
    all_days: list[date] = []
    d = START_DATE
    while d <= END_DATE:
        all_days.append(d)
        d += timedelta(days=1)

    day_weights = [_month_weight(d) for d in all_days]

    # Pre-pick dates for all orders
    order_dates = rng.choices(all_days, weights=day_weights, k=target_orders)
    order_dates.sort()

    for od in order_dates:
        # Pick a customer whose signup_date <= order_date
        attempts = 0
        while attempts < 20:
            cid = rng.choices(cust_ids_sorted, weights=cust_weights)[0]
            if customer_signup[cid] <= od:
                break
            attempts += 1
        else:
            # Fallback: pick any customer signed up before this date
            eligible = [c for c in cust_ids_sorted if customer_signup[c] <= od]
            if not eligible:
                continue
            cid = rng.choice(eligible)

        order_id += 1
        festive = _is_festive(od)
        sw = STATUS_WEIGHTS_FESTIVE if festive else STATUS_WEIGHTS_NORMAL
        status = rng.choices(ORDER_STATUSES, weights=sw)[0]

        orders_batch.append((order_id, cid, od.isoformat(), status))

        # 1–4 items per order (most orders have 1–2)
        n_items = rng.choices([1, 2, 3, 4], weights=[0.45, 0.30, 0.15, 0.10])[0]
        chosen_products = rng.sample(product_ids, min(n_items, len(product_ids)))

        for pid in chosen_products:
            item_id += 1
            qty = rng.choices([1, 2, 3, 4, 5], weights=[0.50, 0.25, 0.12, 0.08, 0.05])[0]

            # Higher discounts during festive months
            if festive:
                disc = rng.choices(
                    [0, 5, 10, 15, 20, 25],
                    weights=[0.20, 0.15, 0.25, 0.20, 0.12, 0.08],
                )[0]
            else:
                disc = rng.choices(
                    [0, 5, 10, 15, 20],
                    weights=[0.40, 0.25, 0.20, 0.10, 0.05],
                )[0]

            price = product_prices[pid]
            sales = round(qty * price * (1 - disc / 100), 2)

            items_batch.append((item_id, order_id, pid, qty, disc, sales))

    cur.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", orders_batch)
    cur.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", items_batch)

    # ── Indexes ──────────────────────────────────────────────────────────
    cur.executescript("""
        CREATE INDEX IF NOT EXISTS idx_customers_region   ON customers(region_id);
        CREATE INDEX IF NOT EXISTS idx_customers_segment  ON customers(segment);
        CREATE INDEX IF NOT EXISTS idx_orders_customer    ON orders(customer_id);
        CREATE INDEX IF NOT EXISTS idx_orders_date        ON orders(order_date);
        CREATE INDEX IF NOT EXISTS idx_orders_status      ON orders(status);
        CREATE INDEX IF NOT EXISTS idx_order_items_order   ON order_items(order_id);
        CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_id);
    """)

    conn.commit()
    conn.close()

    # Print summary
    conn2 = sqlite3.connect(db_path)
    for table in ["regions", "customers", "products", "orders", "order_items"]:
        count = conn2.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count:,} rows")
    conn2.close()


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    path = os.environ.get("DB_PATH", DB_PATH)
    print(f"Seeding database → {path}")
    seed_database(path)
    print("Done ✓")
