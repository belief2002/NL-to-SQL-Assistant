"""
eval.py – Evaluation script with 15 question/expected-answer pairs.

Runs each question through the pipeline with a real LLM, executes the generated
SQL, compares results with expected answers, and reports an accuracy percentage.

Usage:
    python eval.py                     # Uses .env for API key
    GEMINI_API_KEY=AIzaSy... python eval.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

from nl2sql import GeminiLLM, NL2SQLPipeline
from seed_db import seed_database

load_dotenv()

DB_PATH = os.environ.get("DB_PATH", "sales.db")


# ── Evaluation Cases ─────────────────────────────────────────────────────────

@dataclass
class EvalCase:
    """A single evaluation question with its expected SQL and validation."""
    question: str
    expected_sql: str  # reference SQL to compute expected answer
    description: str   # human-readable description of what we're checking


EVAL_CASES: list[EvalCase] = [
    EvalCase(
        question="How many regions are there?",
        expected_sql="SELECT COUNT(*) FROM regions",
        description="Count of regions",
    ),
    EvalCase(
        question="What are the total sales this year?",
        expected_sql="""
            SELECT ROUND(SUM(oi.sales_amount), 2)
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.status NOT IN ('Cancelled', 'Returned')
            AND o.order_date >= '2026-01-01'
        """,
        description="Total sales for current year",
    ),
    EvalCase(
        question="Which region has the highest revenue?",
        expected_sql="""
            SELECT r.region_name
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            JOIN customers c ON o.customer_id = c.customer_id
            JOIN regions r ON c.region_id = r.region_id
            WHERE o.status NOT IN ('Cancelled', 'Returned')
            GROUP BY r.region_name
            ORDER BY SUM(oi.sales_amount) DESC
            LIMIT 1
        """,
        description="Top region by revenue",
    ),
    EvalCase(
        question="Top 5 products by sales amount",
        expected_sql="""
            SELECT p.product_name, ROUND(SUM(oi.sales_amount), 2) AS total
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            JOIN products p ON oi.product_id = p.product_id
            WHERE o.status NOT IN ('Cancelled', 'Returned')
            GROUP BY p.product_name
            ORDER BY total DESC
            LIMIT 5
        """,
        description="Top 5 products",
    ),
    EvalCase(
        question="How many orders were delivered?",
        expected_sql="SELECT COUNT(*) FROM orders WHERE status = 'Delivered'",
        description="Count of delivered orders",
    ),
    EvalCase(
        question="What is the average order value?",
        expected_sql="""
            SELECT ROUND(
                SUM(oi.sales_amount) / COUNT(DISTINCT o.order_id), 2
            )
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.status NOT IN ('Cancelled', 'Returned')
        """,
        description="Average order value",
    ),
    EvalCase(
        question="How many customers are in the Consumer segment?",
        expected_sql="SELECT COUNT(*) FROM customers WHERE segment = 'Consumer'",
        description="Consumer customer count",
    ),
    EvalCase(
        question="Total sales in the South region",
        expected_sql="""
            SELECT ROUND(SUM(oi.sales_amount), 2)
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            JOIN customers c ON o.customer_id = c.customer_id
            JOIN regions r ON c.region_id = r.region_id
            WHERE o.status NOT IN ('Cancelled', 'Returned')
            AND r.region_name = 'South'
        """,
        description="South region total sales",
    ),
    EvalCase(
        question="Which product category has the most orders?",
        expected_sql="""
            SELECT p.category
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            JOIN products p ON oi.product_id = p.product_id
            WHERE o.status NOT IN ('Cancelled', 'Returned')
            GROUP BY p.category
            ORDER BY COUNT(DISTINCT o.order_id) DESC
            LIMIT 1
        """,
        description="Category with most orders",
    ),
    EvalCase(
        question="How many orders were cancelled?",
        expected_sql="SELECT COUNT(*) FROM orders WHERE status = 'Cancelled'",
        description="Cancelled order count",
    ),
    EvalCase(
        question="What is the total number of customers?",
        expected_sql="SELECT COUNT(*) FROM customers",
        description="Total customer count",
    ),
    EvalCase(
        question="Top 3 cities by number of customers",
        expected_sql="""
            SELECT city, COUNT(*) AS cnt
            FROM customers
            GROUP BY city
            ORDER BY cnt DESC
            LIMIT 3
        """,
        description="Top 3 cities by customer count",
    ),
    EvalCase(
        question="What is the cheapest product?",
        expected_sql="""
            SELECT product_name
            FROM products
            ORDER BY unit_price ASC
            LIMIT 1
        """,
        description="Cheapest product",
    ),
    EvalCase(
        question="Total revenue from Electronics category",
        expected_sql="""
            SELECT ROUND(SUM(oi.sales_amount), 2)
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            JOIN products p ON oi.product_id = p.product_id
            WHERE o.status NOT IN ('Cancelled', 'Returned')
            AND p.category = 'Electronics'
        """,
        description="Electronics revenue",
    ),
    EvalCase(
        question="How many products are there in total?",
        expected_sql="SELECT COUNT(*) FROM products",
        description="Total product count",
    ),
]


# ── Evaluation Runner ────────────────────────────────────────────────────────

def _get_expected_result(db_path: str, sql: str) -> list[tuple]:
    """Execute the expected SQL and return the result rows."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(sql).fetchall()
    conn.close()
    return rows


def _results_match(expected: list[tuple], actual: list[tuple], tolerance: float = 0.01) -> bool:
    """
    Compare expected and actual results.
    For single-value results, compares the first value.
    For multi-row, checks that the first column values match in order.
    """
    if not expected and not actual:
        return True
    if not expected or not actual:
        return False

    # Single scalar result
    if len(expected) == 1 and len(expected[0]) == 1:
        exp_val = expected[0][0]
        if not actual or not actual[0]:
            return False
        act_val = actual[0][0]

        if isinstance(exp_val, (int, float)) and isinstance(act_val, (int, float)):
            return abs(exp_val - act_val) / max(abs(exp_val), 1) < tolerance
        return str(exp_val).lower() == str(act_val).lower()

    # Multi-row: compare first column values
    exp_first = [str(r[0]).lower() for r in expected]
    act_first = [str(r[0]).lower() for r in actual]

    # For "top N" or ordered queries, check sequence
    if len(exp_first) == len(act_first):
        return exp_first == act_first

    # At minimum, check the first value matches
    return exp_first[0] == act_first[0] if act_first else False


def run_evaluation(db_path: str = DB_PATH) -> float:
    """
    Run all evaluation cases and print results.
    Returns the accuracy as a fraction (0.0 to 1.0).
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

    if not api_key:
        print("❌ GEMINI_API_KEY not set. Cannot run evaluation.")
        sys.exit(1)

    # Ensure database exists
    if not os.path.exists(db_path):
        print(f"Creating database: {db_path}")
        seed_database(db_path)

    llm = GeminiLLM(api_key=api_key, model=model)
    pipeline = NL2SQLPipeline(db_path, llm, enable_summary=False)

    passed = 0
    total = len(EVAL_CASES)

    print(f"\n{'='*70}")
    print(f"  NL-to-SQL Evaluation — {total} questions")
    print(f"{'='*70}\n")

    for i, case in enumerate(EVAL_CASES, 1):
        expected = _get_expected_result(db_path, case.expected_sql)
        result = pipeline.ask(case.question)

        if result.error or result.refusal:
            status = "FAIL"
            reason = result.error or result.refusal
        elif _results_match(expected, result.rows):
            status = "PASS"
            reason = ""
            passed += 1
        else:
            status = "FAIL"
            reason = f"Expected {expected[:3]}, got {result.rows[:3]}"

        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} [{i:2d}/{total}] {case.description}")
        if status == "FAIL":
            print(f"       → {reason}")
        if result.sql:
            print(f"       SQL: {result.sql[:100]}{'...' if len(result.sql or '') > 100 else ''}")
        print()

    accuracy = passed / total if total > 0 else 0
    pct = accuracy * 100

    print(f"{'='*70}")
    print(f"  Result: {passed}/{total} correct — {pct:.1f}% accuracy")
    print(f"{'='*70}\n")

    return accuracy


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_evaluation()
