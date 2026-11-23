"""
tests/test_sql_guard.py – 25+ test cases for the SQL guard (Layer 1 & Layer 2).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sql_guard import SafeDB, validate_sql
from seed_db import seed_database


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def test_db(tmp_path_factory) -> str:
    """Create a temporary seeded database for Layer 2 tests."""
    db_path = str(tmp_path_factory.mktemp("data") / "test_sales.db")
    seed_database(db_path)
    return db_path


@pytest.fixture(scope="module")
def allowed_tables() -> set[str]:
    return {"regions", "customers", "products", "orders", "order_items"}


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1: Static Validation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStaticValidation:
    """Test cases for validate_sql() – static SQL validation."""

    # ── Valid queries ────────────────────────────────────────────────────

    def test_simple_select(self):
        ok, reason = validate_sql("SELECT * FROM regions")
        assert ok is True, reason

    def test_select_with_where(self):
        ok, reason = validate_sql("SELECT region_name FROM regions WHERE region_id = 1")
        assert ok is True, reason

    def test_select_with_join(self):
        sql = """
        SELECT c.customer_name, r.region_name
        FROM customers c
        JOIN regions r ON c.region_id = r.region_id
        """
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_select_with_aggregation(self):
        sql = "SELECT category, SUM(sales_amount) FROM order_items oi JOIN products p ON oi.product_id = p.product_id GROUP BY category"
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_cte_select(self):
        sql = """
        WITH monthly AS (
            SELECT strftime('%Y-%m', order_date) AS month, COUNT(*) AS cnt
            FROM orders
            GROUP BY 1
        )
        SELECT * FROM monthly ORDER BY month
        """
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_subquery(self):
        sql = "SELECT * FROM customers WHERE customer_id IN (SELECT customer_id FROM orders WHERE status = 'Delivered')"
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_keyword_in_string_literal(self):
        """DROP inside a string literal should NOT trigger blocking."""
        sql = "SELECT * FROM products WHERE product_name = 'DROP TABLE test'"
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_keyword_in_string_with_apostrophe(self):
        """Escaped apostrophe in string literal."""
        sql = "SELECT * FROM customers WHERE customer_name = 'O''Brien DELETE'"
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_keyword_in_comment(self):
        """Keyword inside a comment should NOT trigger blocking."""
        sql = "SELECT * FROM regions -- DROP TABLE regions"
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_keyword_in_block_comment(self):
        """Keyword inside block comment should NOT trigger blocking."""
        sql = "SELECT * FROM regions /* INSERT INTO regions VALUES (99, 'Test') */"
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    def test_trailing_semicolon_ok(self):
        ok, reason = validate_sql("SELECT 1;")
        assert ok is True, reason

    def test_select_with_case(self):
        sql = """SELECT CASE WHEN status = 'Delivered' THEN 'Yes' ELSE 'No' END FROM orders"""
        ok, reason = validate_sql(sql)
        assert ok is True, reason

    # ── Blocked queries ──────────────────────────────────────────────────

    def test_drop_table(self):
        ok, reason = validate_sql("DROP TABLE regions")
        assert ok is False
        assert "SELECT" in reason or "DROP" in reason

    def test_insert(self):
        ok, reason = validate_sql("INSERT INTO regions VALUES (99, 'Test')")
        assert ok is False

    def test_update(self):
        ok, reason = validate_sql("UPDATE regions SET region_name = 'X' WHERE region_id = 1")
        assert ok is False

    def test_delete(self):
        ok, reason = validate_sql("DELETE FROM regions WHERE region_id = 1")
        assert ok is False

    def test_alter_table(self):
        ok, reason = validate_sql("ALTER TABLE regions ADD COLUMN test TEXT")
        assert ok is False

    def test_create_table(self):
        ok, reason = validate_sql("CREATE TABLE evil (id INTEGER)")
        assert ok is False

    def test_attach_database(self):
        ok, reason = validate_sql("ATTACH DATABASE 'other.db' AS other")
        assert ok is False

    def test_detach(self):
        ok, reason = validate_sql("DETACH DATABASE other")
        assert ok is False

    def test_pragma(self):
        ok, reason = validate_sql("PRAGMA table_info(regions)")
        assert ok is False

    def test_vacuum(self):
        ok, reason = validate_sql("VACUUM")
        assert ok is False

    def test_stacked_statements(self):
        ok, reason = validate_sql("SELECT 1; DROP TABLE regions")
        assert ok is False

    def test_stacked_with_comment_trick(self):
        """Try to hide a second statement with a comment."""
        ok, reason = validate_sql("SELECT 1; --\nDROP TABLE regions")
        assert ok is False

    def test_cte_wrapped_delete(self):
        """CTE that wraps a DELETE — should be blocked."""
        sql = "WITH x AS (SELECT 1) DELETE FROM regions"
        ok, reason = validate_sql(sql)
        assert ok is False

    def test_load_extension(self):
        ok, reason = validate_sql("SELECT load_extension('evil.so')")
        assert ok is False

    def test_begin_transaction(self):
        ok, reason = validate_sql("BEGIN TRANSACTION")
        assert ok is False

    def test_empty_query(self):
        ok, reason = validate_sql("")
        assert ok is False

    def test_whitespace_only(self):
        ok, reason = validate_sql("   \n\t  ")
        assert ok is False

    def test_reindex(self):
        ok, reason = validate_sql("REINDEX regions")
        assert ok is False

    def test_select_into_insert(self):
        """Stacked: SELECT then INSERT."""
        ok, reason = validate_sql("SELECT 1; INSERT INTO regions VALUES (10, 'X')")
        assert ok is False


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2: Database-Level Protection Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDatabaseProtection:
    """Test SafeDB: read-only connection, authorizer, timeout."""

    def test_valid_select(self, test_db, allowed_tables):
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute("SELECT COUNT(*) FROM regions")
            assert result.error is None
            assert result.rows[0][0] == 5

    def test_join_query(self, test_db, allowed_tables):
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute(
                "SELECT r.region_name, COUNT(c.customer_id) "
                "FROM customers c JOIN regions r ON c.region_id = r.region_id "
                "GROUP BY r.region_name"
            )
            assert result.error is None
            assert len(result.rows) == 5

    def test_write_blocked_by_readonly(self, test_db, allowed_tables):
        """Even if static validation is bypassed, the read-only connection blocks writes."""
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute("INSERT INTO regions VALUES (99, 'Evil')")
            assert result.error is not None

    def test_drop_blocked_by_authorizer(self, test_db, allowed_tables):
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute("DROP TABLE regions")
            assert result.error is not None

    def test_sqlite_master_blocked(self, test_db, allowed_tables):
        """Reading sqlite_master should be blocked by the authorizer."""
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute("SELECT * FROM sqlite_master")
            assert result.error is not None

    def test_runaway_query_timeout(self, test_db, allowed_tables):
        """A recursive CTE that runs forever should time out."""
        sql = """
        WITH RECURSIVE r(n) AS (
            SELECT 1
            UNION ALL
            SELECT n + 1 FROM r
        )
        SELECT SUM(n) FROM r
        """
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute(sql)
            assert result.error is not None
            assert "timed out" in result.error.lower() or "abort" in result.error.lower() or "interrupted" in result.error.lower() or "error" in result.error.lower()

    def test_max_rows_truncation(self, test_db, allowed_tables):
        """Results exceeding 500 rows should be truncated."""
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute("SELECT * FROM orders")
            assert result.error is None
            assert len(result.rows) <= 500
            assert result.truncated is True

    def test_pragma_blocked(self, test_db, allowed_tables):
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute("PRAGMA table_info(regions)")
            assert result.error is not None

    def test_attach_blocked(self, test_db, allowed_tables):
        with SafeDB(test_db, allowed_tables) as db:
            result = db.execute("ATTACH DATABASE ':memory:' AS mem")
            assert result.error is not None
