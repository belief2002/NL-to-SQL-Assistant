"""
nl2sql.py – Core NL-to-SQL pipeline.

Builds a dynamic prompt from the live database, calls the LLM to generate SQL,
validates and executes safely, and optionally summarizes the result.
"""

from __future__ import annotations

import os
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional, Protocol

from sql_guard import SafeDB, get_allowed_tables, validate_sql


# ── LLM Interface (injectable) ──────────────────────────────────────────────

class LLMInterface(Protocol):
    """Protocol for LLM backends – implement this for real or fake LLMs."""

    def chat(self, system: str, user: str) -> str:
        """Send a system + user message and return the assistant text."""
        ...


class GeminiLLM:
    """Production LLM backend using Google Gemini (via google-genai SDK)."""

    FALLBACK_MODELS = [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    ]
    _client_cache: dict[str, Any] = {}

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash") -> None:
        from google import genai
        from google.genai import types

        if api_key not in self._client_cache:
            self._client_cache[api_key] = genai.Client(api_key=api_key)
        self._client = self._client_cache[api_key]
        self._model = model
        self._types = types

    def _build_config(self, system: str, model_name: str, disable_thinking: bool = True):
        """Build GenerateContentConfig, disabling thinking on flash/lite models for speed."""
        kwargs: dict[str, Any] = {
            "system_instruction": system,
            "temperature": 0.0,
        }
        if disable_thinking and ("flash" in model_name.lower() or "lite" in model_name.lower()):
            thinking_cfg_cls = getattr(self._types, "ThinkingConfig", None)
            if thinking_cfg_cls is not None:
                kwargs["thinking_config"] = thinking_cfg_cls(thinking_budget=0)
        return self._types.GenerateContentConfig(**kwargs)

    def chat(self, system: str, user: str) -> str:
        # Build candidate model list starting with configured model
        models_to_try = [self._model] + [m for m in self.FALLBACK_MODELS if m != self._model]
        last_error = None

        for model_name in models_to_try:
            config = self._build_config(system, model_name, disable_thinking=True)
            try:
                response = self._client.models.generate_content(
                    model=model_name,
                    contents=user,
                    config=config,
                )
                return response.text or ""
            except Exception as e:
                err_str = str(e).lower()
                # If thinking_config is unsupported for this model, retry once without it
                if "thinking" in err_str:
                    try:
                        fallback_cfg = self._build_config(system, model_name, disable_thinking=False)
                        response = self._client.models.generate_content(
                            model=model_name,
                            contents=user,
                            config=fallback_cfg,
                        )
                        return response.text or ""
                    except Exception as inner_e:
                        e = inner_e
                        err_str = str(e).lower()
                # If unavailable (503), not found/retired (404), or rate-limited (429), try fallback
                if any(code in err_str for code in ["503", "404", "unavailable", "not_found", "rate"]):
                    last_error = e
                    continue
                raise e

        if last_error:
            raise last_error
        return ""


# Backwards compatibility alias
AnthropicLLM = GeminiLLM


# ── Result Object ────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """Everything the UI needs to display."""
    sql: Optional[str] = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    summary: Optional[str] = None
    refusal: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    truncated: bool = False


# ── Prompt Builder ───────────────────────────────────────────────────────────

_COLUMN_NOTES: dict[str, str] = {
    "region_name": "One of the 5 Indian regions",
    "segment": "Customer segment",
    "category": "Product category",
    "status": "Order status",
    "unit_price": "Price in INR",
    "unit_cost": "Cost in INR",
    "sales_amount": "Line-item revenue in INR (after discount)",
    "discount_pct": "Discount percentage (0–25)",
    "signup_date": "ISO date YYYY-MM-DD",
    "order_date": "ISO date YYYY-MM-DD",
}


def _get_schema_text(db_path: str) -> str:
    """Build a human-readable schema description from the live database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    lines: list[str] = []
    for (tbl,) in tables:
        lines.append(f"\n### Table: {tbl}")

        # Columns
        cols = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
        pk_cols = [c["name"] for c in cols if c["pk"]]

        for c in cols:
            note = _COLUMN_NOTES.get(c["name"], "")
            pk_flag = " [PK]" if c["pk"] else ""
            null_flag = " NOT NULL" if c["notnull"] else ""
            line = f"  - {c['name']} {c['type']}{pk_flag}{null_flag}"
            if note:
                line += f"  -- {note}"
            lines.append(line)

        # Foreign keys
        fks = conn.execute(f"PRAGMA foreign_key_list({tbl})").fetchall()
        for fk in fks:
            lines.append(f"  FK: {fk['from']} → {fk['table']}({fk['to']})")

    conn.close()
    return "\n".join(lines)


def _get_categorical_values(db_path: str) -> str:
    """Fetch allowed values for categorical columns."""
    queries = {
        "region_name": "SELECT DISTINCT region_name FROM regions ORDER BY region_name",
        "segment":     "SELECT DISTINCT segment FROM customers ORDER BY segment",
        "category":    "SELECT DISTINCT category FROM products ORDER BY category",
        "status":      "SELECT DISTINCT status FROM orders ORDER BY status",
    }
    conn = sqlite3.connect(db_path)
    lines: list[str] = ["\n### Allowed values for categorical columns:"]
    for col, q in queries.items():
        vals = [r[0] for r in conn.execute(q).fetchall()]
        lines.append(f"  {col}: {', '.join(vals)}")
    conn.close()
    return "\n".join(lines)


def _compute_date_references(db_path: str) -> str:
    """
    Compute date ranges for natural-language time references.
    Uses the latest order_date in the database as 'today'.
    """
    conn = sqlite3.connect(db_path)
    max_date_str = conn.execute("SELECT MAX(order_date) FROM orders").fetchone()[0]
    conn.close()

    today = date.fromisoformat(max_date_str)

    # This month
    this_month_start = today.replace(day=1)
    this_month_end = today

    # Last month
    last_month_end = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # This quarter
    q = (today.month - 1) // 3
    this_q_start_month = q * 3 + 1
    this_q_start = today.replace(month=this_q_start_month, day=1)
    this_q_end = today

    # Last quarter
    if q == 0:
        last_q_start = date(today.year - 1, 10, 1)
        last_q_end = date(today.year - 1, 12, 31)
    else:
        lq = q - 1
        lq_start_month = lq * 3 + 1
        last_q_start = today.replace(month=lq_start_month, day=1)
        # End of last quarter
        lq_end_month = lq * 3 + 3
        if lq_end_month == 12:
            last_q_end = date(today.year, 12, 31)
        else:
            last_q_end = date(today.year, lq_end_month + 1, 1) - timedelta(days=1)

    # This year
    this_year_start = today.replace(month=1, day=1)
    this_year_end = today

    # Last year
    last_year_start = date(today.year - 1, 1, 1)
    last_year_end = date(today.year - 1, 12, 31)

    # Last 30 days
    last_30_start = today - timedelta(days=30)
    last_30_end = today

    lines = [
        f'\n### Date references (the latest data date is {today.isoformat()}, treat it as "today"):',
        f'  "this month":    {this_month_start.isoformat()} to {this_month_end.isoformat()}',
        f'  "last month":    {last_month_start.isoformat()} to {last_month_end.isoformat()}',
        f'  "this quarter":  {this_q_start.isoformat()} to {this_q_end.isoformat()}',
        f'  "last quarter":  {last_q_start.isoformat()} to {last_q_end.isoformat()}',
        f'  "this year":     {this_year_start.isoformat()} to {this_year_end.isoformat()}',
        f'  "last year":     {last_year_start.isoformat()} to {last_year_end.isoformat()}',
        f'  "last 30 days":  {last_30_start.isoformat()} to {last_30_end.isoformat()}',
    ]
    return "\n".join(lines)


_SYSTEM_PROMPT_CACHE: dict[tuple[str, float], str] = {}
_ALLOWED_TABLES_CACHE: dict[tuple[str, float], set[str]] = {}


def _get_db_cache_key(db_path: str) -> Optional[tuple[str, float]]:
    """Return a (resolved_path, mtime) cache key if the DB file exists."""
    try:
        abs_path = os.path.abspath(db_path)
        return (abs_path, os.path.getmtime(abs_path))
    except OSError:
        return None


def build_system_prompt(db_path: str) -> str:
    """Assemble the full system prompt for the SQL-generation LLM call (cached by DB mtime)."""
    cache_key = _get_db_cache_key(db_path)
    if cache_key and cache_key in _SYSTEM_PROMPT_CACHE:
        return _SYSTEM_PROMPT_CACHE[cache_key]

    schema = _get_schema_text(db_path)
    categorical = _get_categorical_values(db_path)
    dates = _compute_date_references(db_path)

    prompt = f"""You are a SQL expert for a SQLite e-commerce analytics database.
Your job: convert a natural-language e-commerce business question into a single SQLite SELECT query.

## Database Schema
{schema}
{categorical}
{dates}

## Business Rules
- "sales" or "revenue" means SUM(sales_amount).
- Exclude orders with status 'Cancelled' or 'Returned' UNLESS the user explicitly asks about them.
- Average order value (AOV) = SUM(sales_amount) / COUNT(DISTINCT order_id).
- Round all monetary values to 2 decimal places using ROUND(..., 2).
- Use explicit JOINs with short table aliases (r, c, p, o, oi).
- Return only what was asked: use LIMIT 1 for "which …?" questions, LIMIT N for "top N".
- For time-based grouping by month, use strftime('%Y-%m', order_date).
- All monetary values are in INR (₹).

## Output Format
Return exactly one SQL query inside a ```sql code block.
If the question is NOT related to e-commerce data (sales, orders, products, customers, regions) or is unanswerable from this database, immediately return ONLY:
CANNOT_ANSWER: This question is not related to e-commerce data.

Do NOT include any explanation outside the code block. Do NOT return multiple queries.
"""
    if cache_key:
        _SYSTEM_PROMPT_CACHE[cache_key] = prompt
    return prompt


def build_summary_prompt(question: str, sql: str, columns: list[str], rows: list[tuple]) -> str:
    """Build the system prompt for the summary LLM call."""
    # Format rows as a small table
    if len(rows) > 10:
        display_rows = rows[:10]
        note = f"\n(showing first 10 of {len(rows)} rows)"
    else:
        display_rows = rows
        note = ""

    table_text = " | ".join(columns) + "\n"
    table_text += " | ".join(["---"] * len(columns)) + "\n"
    for row in display_rows:
        table_text += " | ".join(str(v) for v in row) + "\n"
    table_text += note

    return f"""You are a data analyst assistant. The user asked: "{question}"

The SQL query was:
```sql
{sql}
```

The result is:
{table_text}

Write 1 to 3 plain-English sentences that answer the user's question using ONLY the numbers
in the result above. Use the ₹ symbol for monetary values. Be concise and specific.
Do NOT suggest follow-up queries. Do NOT repeat the SQL."""


# ── Pipeline ─────────────────────────────────────────────────────────────────

_SQL_BLOCK_RE = re.compile(r"```sql\s*\n?(.*?)\n?\s*```", re.DOTALL | re.IGNORECASE)
_CANNOT_ANSWER_RE = re.compile(r"CANNOT_ANSWER:\s*(.+)", re.IGNORECASE)

_ECOMMERCE_KEYWORDS_RE = re.compile(
    r"\b("
    r"sales?|revenue|orders?|order_items?|products?|customers?|regions?|categor(?:y|ies)|"
    r"segments?|items?|buyers?|merchants?|sellers?|vendors?|shops?|stores?|ecommerce|e-commerce|"
    r"retail|cart|checkout|purchas(?:e|ed|es|ing)|buy|bought|sold|sell|selling|transactions?|"
    r"invoices?|billing|payments?|refunds?|shipping|shipment|delivery|delivered|"
    r"cancell?ed|returned|returns?|pending|shipped|processing|"
    r"amount|price|cost|discounts?|quantity|qty|profit|margin|aov|"
    r"unit_price|unit_cost|sales_amount|discount_pct|signup|signup_date|order_date|"
    r"inr|rupees?|north|south|east|west|central|consumer|corporate|small business|"
    r"electronics|office supplies|home & kitchen|clothing|books|"
    r"earbuds|speaker|smart watch|usb-c|notebook|gel pen|desk organizer|sticky notes|"
    r"bottle|cutting board|desk lamp|spice rack|t-shirt|jeans|sneakers|linen shirt|"
    r"cit(?:y|ies)|states?"
    r")\b",
    re.IGNORECASE,
)

_OFF_TOPIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(hi|hello|hey|hola|namaste|good\s+(morning|afternoon|evening|night)|"
        r"how\s+are\s+you|what'?s\s+up|thanks|thank\s+you|bye|goodbye|"
        r"who\s+are\s+you|what\s+is\s+your\s+name|what\s+can\s+you\s+do)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:what\s+is\s+|calculate\s+|solve\s+)?\d+(?:\.\d+)?\s*[\+\-\*\/\^]\s*\d+(?:\.\d+)?\s*\??\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b("
        r"meaning of life|weather|forecast|temperature|rain|sunny|climate|"
        r"cricket|football|soccer|nba|fifa|world cup|ipl|tennis|olympics|"
        r"movie|movies|film|films|actor|actress|song|songs|music|lyrics|"
        r"recipe|recipes|cook|cooking|bake|baking|calories|workout|exercise|"
        r"symptom|symptoms|medicine|doctor|disease|hospital|patient|"
        r"stock price|stocks|bitcoin|crypto|cryptocurrency|ethereum|"
        r"president|prime minister|election|parliament|politics|"
        r"quantum|relativity|photosynthesis|gravity|planet|solar system|universe|"
        r"horoscope|zodiac|astrology|religion|god|"
        r"tell me a (?:joke|story|fact|riddle)|"
        r"write (?:a|me|an) (?:poem|story|essay|song|code|script|program|email|letter)|"
        r"what is the capital of|who (?:invented|discovered|won)|"
        r"translate\b|python script|javascript|html|css|c\+\+|react"
        r")\b",
        re.IGNORECASE,
    ),
]

_ECOMMERCE_REFUSAL_MSG = (
    "This question is not related to e-commerce data. "
    "Please ask about sales, orders, products, customers, or regions."
)


def is_ecommerce_question(question: str) -> tuple[bool, Optional[str]]:
    """
    Fast pre-flight relevance check for e-commerce questions.

    Returns (True, None) if the question is e-commerce related or plausibly about
    the database, or (False, refusal_reason) if it is clearly off-topic.
    """
    if _ECOMMERCE_KEYWORDS_RE.search(question) or "₹" in question:
        return True, None

    for pattern in _OFF_TOPIC_PATTERNS:
        if pattern.search(question):
            return False, _ECOMMERCE_REFUSAL_MSG

    return True, None


MAX_QUESTION_LENGTH = 500
MAX_ATTEMPTS = 2  # initial + 1 retry


class NL2SQLPipeline:
    """
    End-to-end pipeline: question → SQL → execute → summarize.

    Args:
        db_path: Path to the SQLite database.
        llm: An object implementing LLMInterface.
        enable_summary: Whether to generate a plain-English summary.
        system_prompt: Optional custom system prompt (disables e-commerce-only pre-filter unless ecommerce_only=True).
        ecommerce_only: Whether to enforce the fast e-commerce relevance pre-filter.
    """

    def __init__(
        self,
        db_path: str,
        llm: LLMInterface,
        enable_summary: bool = True,
        system_prompt: Optional[str] = None,
        ecommerce_only: Optional[bool] = None,
    ) -> None:
        self._db_path = db_path
        self._llm = llm
        self._enable_summary = enable_summary
        self._system_prompt = system_prompt if system_prompt is not None else build_system_prompt(db_path)
        self._ecommerce_only = (system_prompt is None) if ecommerce_only is None else ecommerce_only

        cache_key = _get_db_cache_key(db_path)
        if cache_key and cache_key in _ALLOWED_TABLES_CACHE:
            self._allowed_tables = _ALLOWED_TABLES_CACHE[cache_key]
        else:
            self._allowed_tables = get_allowed_tables(db_path)
            if cache_key:
                _ALLOWED_TABLES_CACHE[cache_key] = self._allowed_tables

    def ask(
        self,
        question: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> PipelineResult:
        """Process a natural-language question and return a PipelineResult."""
        result = PipelineResult()

        if cancel_check and cancel_check():
            result.error = "Execution stopped by user."
            return result

        # ── Input validation ─────────────────────────────────────────────
        if not question or not question.strip():
            result.error = "Please enter a question."
            return result

        question = question.strip()
        if len(question) > MAX_QUESTION_LENGTH:
            result.error = f"Question is too long ({len(question)} chars). Maximum is {MAX_QUESTION_LENGTH}."
            return result

        # ── Fast pre-flight e-commerce relevance check ───────────────────
        if self._ecommerce_only:
            is_relevant, refusal_reason = is_ecommerce_question(question)
            if not is_relevant:
                result.refusal = refusal_reason
                return result

        # ── Generate SQL ─────────────────────────────────────────────────
        last_error: Optional[str] = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if cancel_check and cancel_check():
                result.error = "Execution stopped by user."
                return result

            result.attempts = attempt

            try:
                if last_error:
                    user_msg = (
                        f"{question}\n\n"
                        f"[The previous query failed with this error: {last_error}. "
                        f"Please fix the SQL and try again.]"
                    )
                else:
                    user_msg = question

                llm_response = self._llm.chat(self._system_prompt, user_msg)
            except Exception as e:
                result.error = f"LLM API error: {e}"
                return result

            # Check for refusal
            refusal_match = _CANNOT_ANSWER_RE.search(llm_response)
            if refusal_match:
                result.refusal = refusal_match.group(1).strip()
                return result

            # Extract SQL
            sql_match = _SQL_BLOCK_RE.search(llm_response)
            if not sql_match:
                result.error = "The AI did not return a valid SQL query."
                return result

            sql = sql_match.group(1).strip()
            result.sql = sql

            # ── Static validation ────────────────────────────────────────
            is_safe, reason = validate_sql(sql)
            if not is_safe:
                result.error = f"Query blocked: {reason}"
                # Blocked queries must NOT be retried
                return result

            # ── Execute ──────────────────────────────────────────────────
            if cancel_check and cancel_check():
                result.error = "Execution stopped by user."
                return result

            try:
                with SafeDB(self._db_path, self._allowed_tables) as db:
                    qr = db.execute(sql)
            except Exception as e:
                result.error = f"Database connection error: {e}"
                return result

            if qr.error:
                if attempt < MAX_ATTEMPTS:
                    last_error = qr.error
                    continue  # retry
                else:
                    result.error = qr.error
                    return result

            # ── Success ──────────────────────────────────────────────────
            result.columns = qr.columns
            result.rows = qr.rows
            result.truncated = qr.truncated
            result.error = None
            break

        # ── Summary ──────────────────────────────────────────────────────
        if cancel_check and cancel_check():
            result.summary = None
            return result

        if self._enable_summary and result.sql and not result.error and result.rows:
            try:
                summary_prompt = build_summary_prompt(
                    question, result.sql, result.columns, result.rows
                )
                result.summary = self._llm.chat(
                    "You are a helpful data analyst. Summarize query results concisely.",
                    summary_prompt,
                )
            except Exception:
                # Summary failure is non-fatal
                result.summary = None

        return result
