"""
tests/test_byod.py – Test suite for Bring-Your-Own-Data (BYOD) mode.

Tests:
- Small CSV upload & FakeLLM end-to-end question answering.
- Name sanitization (spaces, symbols, leading numbers, SQLite reserved keywords).
- Duplicate column deduplication.
- Mixed date format detection and conversion to ISO YYYY-MM-DD text.
- Currency / comma-separated numeric string conversion.
- Multi-file ingestion into SQLite tables.
- Dynamic schema prompt building with categorical value discovery (<= 30 distinct values).
- Safety guard and authorizer enforcement on user-uploaded database.
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
import tempfile

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loader import (
    build_byod_system_prompt,
    compute_dynamic_date_reference,
    deduplicate_names,
    load_uploaded_files,
    sanitize_name,
)
from nl2sql import NL2SQLPipeline
from sql_guard import SafeDB, get_allowed_tables, validate_sql


# ── FakeLLM for Pipeline Testing ─────────────────────────────────────────────

class FakeLLM:
    """Scripted fake LLM returning predetermined responses."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def chat(self, system: str, user: str) -> str:
        if self._idx >= len(self._responses):
            raise RuntimeError("FakeLLM: no more scripted responses")
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


# ── Sanitization & Deduplication Tests ────────────────────────────────────────

class TestSanitization:
    """Test header/table name cleaning and deduplication."""

    def test_messy_column_names(self):
        assert sanitize_name("Order Date (IST)") == "order_date_ist"
        assert sanitize_name("Total $ Amount / USD") == "total_amount_usd"
        assert sanitize_name(" Customer Name ") == "customer_name"
        assert sanitize_name("special@#%&*characters!") == "special_characters"

    def test_leading_digits_and_empty(self):
        assert sanitize_name("123_units", prefix="col") == "col_123_units"
        assert sanitize_name("2026 Sales", prefix="col") == "col_2026_sales"
        assert sanitize_name("", prefix="col") == "col_unnamed"
        assert sanitize_name("   ", prefix="col") == "col_unnamed"

    def test_reserved_sqlite_keywords(self):
        assert sanitize_name("select", prefix="col") == "col_select"
        assert sanitize_name("order", prefix="col") == "col_order"
        assert sanitize_name("table", prefix="col") == "col_table"
        assert sanitize_name("from", prefix="col") == "col_from"

    def test_duplicate_columns(self):
        raw = ["sales", "sales", "date", "sales", "date"]
        deduped = deduplicate_names(raw)
        assert deduped == ["sales", "sales_1", "date", "sales_2", "date_1"]

    def test_already_unique_names(self):
        raw = ["id", "name", "price"]
        assert deduplicate_names(raw) == raw


# ── Data Conversion & Ingestion Tests ────────────────────────────────────────

class TestIngestionAndTypeInference:
    """Test date parsing, numeric cleaning, and file loading into SQLite."""

    def test_mixed_date_formats(self, tmp_path):
        """Mixed formats (YYYY-MM-DD, DD/MM/YYYY, MM-DD-YYYY) should parse to ISO."""
        csv_data = (
            "id,event_date,event_name\n"
            "1,2026-01-15,New Year Event\n"
            "2,2026/02/20,Spring Fest\n"
            "3,15-03-2026,Summer Kickoff\n"
            "4,2026-04-10,Q2 Launch\n"
        ).encode("utf-8")

        db_file = str(tmp_path / "test_dates.db")
        res = load_uploaded_files([("events.csv", csv_data)], db_file)

        assert res.error is None
        assert len(res.tables) == 1
        table = res.tables[0]
        assert "event_date" in table.date_columns

        # Verify values in SQLite are standard ISO strings
        conn = sqlite3.connect(db_file)
        rows = conn.execute("SELECT event_date FROM events ORDER BY id").fetchall()
        conn.close()

        dates = [r[0] for r in rows]
        assert dates[0] == "2026-01-15"
        assert dates[1] == "2026-02-20"
        assert dates[3] == "2026-04-10"

    def test_numeric_currency_conversion(self, tmp_path):
        """Text columns with currency symbols and commas should convert to numbers."""
        csv_data = (
            "item,revenue,cost\n"
            "Item A,\"$1,200.50\",500\n"
            "Item B,\"$2,450.00\",1000\n"
            "Item C,\"$350.25\",120\n"
        ).encode("utf-8")

        db_file = str(tmp_path / "test_currency.db")
        res = load_uploaded_files([("financials.csv", csv_data)], db_file)

        assert res.error is None
        conn = sqlite3.connect(db_file)
        # Should allow numeric aggregation directly
        total = conn.execute("SELECT SUM(revenue) FROM financials").fetchone()[0]
        conn.close()

        assert round(total, 2) == 4000.75

    def test_multiple_files_uploaded(self, tmp_path):
        """Multiple CSVs should be loaded into separate SQLite tables."""
        users_csv = "user_id,username\n1,Alice\n2,Bob\n".encode("utf-8")
        logs_csv = "log_id,user_id,action\n101,1,login\n102,2,purchase\n".encode("utf-8")

        db_file = str(tmp_path / "test_multi.db")
        res = load_uploaded_files(
            [("users.csv", users_csv), ("logs.csv", logs_csv)],
            db_file,
        )

        assert res.error is None
        assert len(res.tables) == 2
        table_names = {t.name for t in res.tables}
        assert table_names == {"users", "logs"}

        conn = sqlite3.connect(db_file)
        # Test joining the two uploaded tables
        joined = conn.execute(
            "SELECT u.username, l.action FROM users u JOIN logs l ON u.user_id = l.user_id WHERE u.user_id = 1"
        ).fetchall()
        conn.close()

        assert joined == [("Alice", "login")]


# ── Dynamic Prompt Generation Tests ──────────────────────────────────────────

class TestDynamicPrompt:
    """Test BYOD prompt builder without hard-coded rules."""

    def test_byod_prompt_structure(self, tmp_path):
        csv_data = (
            "dept_id,department,budget,status\n"
            "1,Engineering,500000,Active\n"
            "2,Sales,300000,Active\n"
            "3,Marketing,200000,Pending\n"
        ).encode("utf-8")

        db_file = str(tmp_path / "test_prompt.db")
        load_uploaded_files([("departments.csv", csv_data)], db_file)

        prompt = build_byod_system_prompt(
            db_file,
            user_notes="Exclude Pending departments from budget totals.",
        )

        # Check schema inclusion
        assert "departments" in prompt
        assert "dept_id" in prompt
        assert "department" in prompt
        assert "budget" in prompt

        # Check categorical discovery (status has Active, Pending <= 30 values)
        assert "status allowed values:" in prompt
        assert "'Active'" in prompt
        assert "'Pending'" in prompt

        # Check user notes included
        assert "Exclude Pending departments" in prompt

        # Ensure NO hard-coded Indian e-commerce rules leaked into generic BYOD prompt
        assert "regions" not in prompt
        assert "Festive" not in prompt
        assert "order_items" not in prompt

    def test_dynamic_date_reference(self, tmp_path):
        csv_data = (
            "order_id,txn_date,amount\n"
            "1,2026-11-10,100\n"
            "2,2026-11-15,250\n"
        ).encode("utf-8")

        db_file = str(tmp_path / "test_byod_date.db")
        load_uploaded_files([("orders.csv", csv_data)], db_file)

        date_ref = compute_dynamic_date_reference(db_file, "orders", "txn_date")
        assert date_ref is not None
        assert "2026-11-15" in date_ref  # Max date
        assert '"this month":    2026-11-01 to 2026-11-15' in date_ref


# ── Pipeline & Security Integration ──────────────────────────────────────────

class TestBYODPipelineAndSecurity:
    """Test full NL2SQLPipeline on custom uploaded DB with SafeDB security."""

    def test_byod_pipeline_happy_path(self, tmp_path):
        """Upload CSV, ask question with FakeLLM, verify answer."""
        csv_data = (
            "employee,department,salary\n"
            "John Doe,Engineering,95000\n"
            "Jane Smith,Engineering,105000\n"
            "Bob Taylor,Sales,70000\n"
        ).encode("utf-8")

        db_file = str(tmp_path / "test_pipe.db")
        res = load_uploaded_files([("employees.csv", csv_data)], db_file)
        assert res.error is None

        byod_prompt = build_byod_system_prompt(db_file)

        fake_llm = FakeLLM([
            "```sql\nSELECT department, AVG(salary) AS avg_sal FROM employees GROUP BY department ORDER BY avg_sal DESC\n```",
            "The Engineering department has the highest average salary at 100000.",
        ])

        pipeline = NL2SQLPipeline(
            db_path=db_file,
            llm=fake_llm,
            enable_summary=True,
            system_prompt=byod_prompt,
        )

        result = pipeline.ask("What is the average salary by department?")
        assert result.error is None
        assert result.refusal is None
        assert result.columns == ["department", "avg_sal"]
        assert len(result.rows) == 2
        assert result.rows[0][0] == "Engineering"
        assert result.rows[0][1] == 100000.0

    def test_byod_security_guard_enforced(self, tmp_path):
        """Verify that malicious queries are blocked by SafeDB even on custom DB."""
        csv_data = "id,val\n1,test\n".encode("utf-8")
        db_file = str(tmp_path / "test_sec.db")
        load_uploaded_files([("data.csv", csv_data)], db_file)

        allowed_tables = get_allowed_tables(db_file)
        assert "data" in allowed_tables

        with SafeDB(db_file, allowed_tables) as safe_db:
            # 1. Normal SELECT works
            r1 = safe_db.execute("SELECT * FROM data")
            assert r1.error is None

            # 2. DROP is denied by authorizer & read-only mode
            r2 = safe_db.execute("DROP TABLE data")
            assert r2.error is not None

            # 3. Reading sqlite_master is denied
            r3 = safe_db.execute("SELECT * FROM sqlite_master")
            assert r3.error is not None
