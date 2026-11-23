"""
tests/test_dates.py – Date reference computation tests, including January wrap-around.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nl2sql import _compute_date_references


# ── Helpers ──────────────────────────────────────────────────────────────────

def _create_db_with_max_date(tmp_path, max_date: str) -> str:
    """Create a minimal database with a single order at the given date."""
    db_path = str(tmp_path / f"test_{max_date}.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE regions (region_id INTEGER PRIMARY KEY, region_name TEXT);
        INSERT INTO regions VALUES (1, 'North');

        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY, customer_name TEXT, city TEXT,
            region_id INTEGER, segment TEXT, signup_date TEXT
        );
        INSERT INTO customers VALUES (1, 'Test', 'Delhi', 1, 'Consumer', '2025-01-01');

        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY, product_name TEXT, category TEXT,
            unit_price REAL, unit_cost REAL
        );
        INSERT INTO products VALUES (1, 'Widget', 'Electronics', 100, 50);

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY, customer_id INTEGER,
            order_date TEXT, status TEXT
        );

        CREATE TABLE order_items (
            item_id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER,
            quantity INTEGER, discount_pct REAL, sales_amount REAL
        );
    """)
    conn.execute(
        "INSERT INTO orders VALUES (1, 1, ?, 'Delivered')", (max_date,)
    )
    conn.commit()
    conn.close()
    return db_path


# ── Tests ────────────────────────────────────────────────────────────────────

class TestDateReferences:
    """Test _compute_date_references for various 'today' dates."""

    def test_mid_year(self, tmp_path):
        """Standard mid-year date: 2026-07-15."""
        db = _create_db_with_max_date(tmp_path, "2026-07-15")
        text = _compute_date_references(db)

        assert "2026-07-15" in text  # today
        assert '"this month":    2026-07-01 to 2026-07-15' in text
        assert '"last month":    2026-06-01 to 2026-06-30' in text
        assert '"this quarter":  2026-07-01 to 2026-07-15' in text
        assert '"last quarter":  2026-04-01 to 2026-06-30' in text
        assert '"this year":     2026-01-01 to 2026-07-15' in text
        assert '"last year":     2025-01-01 to 2025-12-31' in text

    def test_january_wrap_around(self, tmp_path):
        """January date: last month/quarter must wrap to previous year."""
        db = _create_db_with_max_date(tmp_path, "2026-01-20")
        text = _compute_date_references(db)

        assert '"this month":    2026-01-01 to 2026-01-20' in text
        assert '"last month":    2025-12-01 to 2025-12-31' in text
        # Q1 → last quarter is Q4 of previous year
        assert '"last quarter":  2025-10-01 to 2025-12-31' in text
        assert '"this year":     2026-01-01 to 2026-01-20' in text
        assert '"last year":     2025-01-01 to 2025-12-31' in text

    def test_end_of_quarter(self, tmp_path):
        """End of Q2: 2026-06-30."""
        db = _create_db_with_max_date(tmp_path, "2026-06-30")
        text = _compute_date_references(db)

        assert '"this quarter":  2026-04-01 to 2026-06-30' in text
        assert '"last quarter":  2026-01-01 to 2026-03-31' in text

    def test_start_of_year(self, tmp_path):
        """Very first day of the year: 2026-01-01."""
        db = _create_db_with_max_date(tmp_path, "2026-01-01")
        text = _compute_date_references(db)

        assert '"this month":    2026-01-01 to 2026-01-01' in text
        assert '"last month":    2025-12-01 to 2025-12-31' in text
        assert '"last quarter":  2025-10-01 to 2025-12-31' in text
        assert '"last year":     2025-01-01 to 2025-12-31' in text

    def test_november_date(self, tmp_path):
        """Nov 2026 — matches the actual database's expected latest date."""
        db = _create_db_with_max_date(tmp_path, "2026-11-15")
        text = _compute_date_references(db)

        assert '"this month":    2026-11-01 to 2026-11-15' in text
        assert '"last month":    2026-10-01 to 2026-10-31' in text
        # Q4
        assert '"this quarter":  2026-10-01 to 2026-11-15' in text
        # Q3
        assert '"last quarter":  2026-07-01 to 2026-09-30' in text

    def test_last_30_days(self, tmp_path):
        """Check the 'last 30 days' range."""
        db = _create_db_with_max_date(tmp_path, "2026-03-15")
        text = _compute_date_references(db)

        assert '"last 30 days":  2026-02-13 to 2026-03-15' in text

    def test_december_q4(self, tmp_path):
        """December: Q4 current, Q3 is last quarter."""
        db = _create_db_with_max_date(tmp_path, "2025-12-25")
        text = _compute_date_references(db)

        assert '"this quarter":  2025-10-01 to 2025-12-25' in text
        assert '"last quarter":  2025-07-01 to 2025-09-30' in text
