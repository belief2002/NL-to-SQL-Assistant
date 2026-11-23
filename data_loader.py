"""
data_loader.py – Data ingestion, sanitization, and profiling for Bring-Your-Own-Data mode.

Features:
- Ingests multiple CSV and XLSX/XLS files into a per-session SQLite database.
- Sanitizes table and column names to snake_case and deduplicates names.
- Auto-detects date columns across mixed formats and stores them as ISO YYYY-MM-DD text.
- Converts numeric-looking text columns (e.g. '$1,200.50' or '1,500') to numbers.
- Profiles data: row counts, column types, null counts, 5 sample rows.
- Builds dynamic, general system prompts with no hard-coded business rules or table names.
"""

from __future__ import annotations

import io
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, BinaryIO, Optional

import pandas as pd


# ── Name Sanitization & Deduplication ────────────────────────────────────────

_SQLITE_RESERVED = {
    "add", "all", "alter", "and", "as", "asc", "between", "by", "case", "check",
    "collate", "column", "commit", "conflict", "constraint", "create", "cross",
    "current_date", "current_time", "current_timestamp", "database", "default",
    "deferrable", "deferred", "delete", "desc", "distinct", "drop", "each",
    "else", "end", "escape", "except", "exclusive", "exists", "foreign", "from",
    "full", "group", "having", "if", "in", "index", "initially", "inner", "insert",
    "instead", "intersect", "into", "is", "isnull", "join", "key", "left", "like",
    "limit", "match", "natural", "not", "notnull", "null", "of", "offset", "on",
    "or", "order", "outer", "plan", "pragma", "primary", "query", "raise",
    "recursive", "references", "regexp", "reindex", "release", "rename", "replace",
    "restrict", "right", "rollback", "row", "savepoint", "select", "set", "table",
    "temp", "temporary", "then", "to", "transaction", "trigger", "union", "unique",
    "update", "using", "vacuum", "values", "view", "virtual", "when", "where", "with",
}


def sanitize_name(name: str, prefix: str = "col") -> str:
    """
    Convert an arbitrary string (header or filename) into a valid snake_case identifier.
    
    Examples:
      'Order Date (IST)' -> 'order_date_ist'
      'Total $ Amount'   -> 'total_amount'
      '123_test'         -> 'col_123_test'
      'select'           -> 'col_select'
    """
    if not name or not str(name).strip():
        return f"{prefix}_unnamed"

    s = str(name).strip().lower()
    # Replace non-alphanumeric with underscore
    s = re.sub(r"[^a-z0-9]+", "_", s)
    # Strip leading/trailing underscores
    s = s.strip("_")

    if not s:
        return f"{prefix}_unnamed"

    # If starts with digit, prepend prefix
    if s[0].isdigit():
        s = f"{prefix}_{s}"

    # If reserved keyword, prepend prefix
    if s in _SQLITE_RESERVED:
        s = f"{prefix}_{s}"

    return s


def deduplicate_names(names: list[str]) -> list[str]:
    """
    Ensure all names in the list are unique by appending _1, _2, etc.
    Preserves order.
    """
    seen: dict[str, int] = {}
    result: list[str] = []

    for name in names:
        if name not in seen:
            seen[name] = 0
            result.append(name)
        else:
            seen[name] += 1
            result.append(f"{name}_{seen[name]}")

    return result


# ── Data Type Inference & Cleaning ───────────────────────────────────────────

def _try_convert_numeric(series: pd.Series) -> pd.Series:
    """
    Attempt to convert a text series to numeric if at least 80% of non-null
    values look like numbers (with currency symbols, commas, or spaces removed).
    """
    if pd.api.types.is_numeric_dtype(series):
        return series

    non_null = series.dropna()
    if len(non_null) == 0:
        return series

    # Strip currency symbols, commas, and whitespace
    cleaned = (
        non_null.astype(str)
        .str.replace(r"[₹$,€£\s]", "", regex=True)
        .str.replace(",", "", regex=False)
    )

    numeric_series = pd.to_numeric(cleaned, errors="coerce")
    valid_count = numeric_series.notna().sum()

    # If >80% of non-null values converted successfully, apply conversion
    if valid_count / len(non_null) >= 0.8:
        full_cleaned = (
            series.astype(str)
            .str.replace(r"[₹$,€£\s]", "", regex=True)
            .str.replace(",", "", regex=False)
        )
        converted = pd.to_numeric(full_cleaned, errors="coerce")
        # Try downcasting to int if integer-like
        if converted.dropna().apply(lambda x: float(x).is_integer()).all():
            return converted.astype("Int64")  # nullable integer
        return converted

    return series


def _try_convert_dates(series: pd.Series) -> tuple[pd.Series, bool]:
    """
    Attempt to convert a series to ISO YYYY-MM-DD text if at least 80%
    of non-null values can be parsed as dates.
    
    Returns:
        (converted_series, is_date_boolean)
    """
    # If already datetime
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.strftime("%Y-%m-%d"), True

    # If numeric, do not treat as date (e.g. integer years or timestamps can be ambiguous)
    if pd.api.types.is_numeric_dtype(series):
        return series, False

    non_null = series.dropna().astype(str).str.strip()
    if len(non_null) == 0:
        return series, False

    # Check for basic date pattern clues (/ or -)
    has_date_delimiters = non_null.str.contains(r"[\/\-]").mean() >= 0.7
    if not has_date_delimiters:
        return series, False

    try:
        parsed = pd.to_datetime(non_null, errors="coerce", format="mixed")
        valid_count = parsed.notna().sum()

        if valid_count / len(non_null) >= 0.8:
            # Check reasonable year range (1900–2100)
            valid_years = parsed.dt.year.dropna()
            if ((valid_years >= 1900) & (valid_years <= 2100)).mean() >= 0.9:
                full_parsed = pd.to_datetime(series, errors="coerce", format="mixed")
                return full_parsed.dt.strftime("%Y-%m-%d"), True
    except Exception:
        pass

    return series, False


# ── Profiling Data Structures ────────────────────────────────────────────────

@dataclass
class ColumnProfile:
    name: str
    dtype: str
    null_count: int
    is_date: bool = False
    sample_values: list[Any] = field(default_factory=list)


@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: list[ColumnProfile]
    sample_rows: list[dict[str, Any]]
    date_columns: list[str] = field(default_factory=list)


@dataclass
class IngestResult:
    db_path: str
    tables: list[TableProfile]
    total_files: int
    error: Optional[str] = None


# ── File Ingestion Engine ────────────────────────────────────────────────────

def load_dataframe_to_sqlite(
    df: pd.DataFrame,
    table_name: str,
    conn: sqlite3.Connection,
) -> TableProfile:
    """
    Clean, sanitize, and save a pandas DataFrame into SQLite as table_name.
    Returns the TableProfile for the created table.
    """
    # Sanitize and deduplicate column names
    raw_cols = [str(c) for c in df.columns]
    sanitized_cols = [sanitize_name(c, prefix="col") for c in raw_cols]
    final_cols = deduplicate_names(sanitized_cols)
    df.columns = final_cols

    date_columns: list[str] = []
    col_profiles: list[ColumnProfile] = []

    # Clean columns: dates & numerics
    for col in final_cols:
        series = df[col]
        null_count = int(series.isna().sum())

        # Try date conversion first
        converted_series, is_date = _try_convert_dates(series)
        if is_date:
            df[col] = converted_series
            date_columns.append(col)
            dtype_label = "DATE (TEXT)"
        else:
            # Try numeric conversion
            converted_series = _try_convert_numeric(series)
            df[col] = converted_series
            if pd.api.types.is_integer_dtype(converted_series):
                dtype_label = "INTEGER"
            elif pd.api.types.is_float_dtype(converted_series):
                dtype_label = "REAL"
            else:
                dtype_label = "TEXT"

        # Collect distinct non-null sample values (up to 3)
        samples = df[col].dropna().unique()[:3].tolist()
        col_profiles.append(
            ColumnProfile(
                name=col,
                dtype=dtype_label,
                null_count=null_count,
                is_date=is_date,
                sample_values=samples,
            )
        )

    # Write to SQLite
    df.to_sql(table_name, conn, if_exists="replace", index=False)

    # Extract 5 sample rows for profile display
    sample_records = df.head(5).to_dict(orient="records")

    return TableProfile(
        name=table_name,
        row_count=len(df),
        columns=col_profiles,
        sample_rows=sample_records,
        date_columns=date_columns,
    )


def load_uploaded_files(
    uploaded_files: list[tuple[str, bytes | BinaryIO]],
    db_path: str,
) -> IngestResult:
    """
    Ingest multiple CSV or Excel files into a SQLite database.

    Args:
        uploaded_files: list of (filename, bytes_or_stream)
        db_path: path to the target SQLite database file

    Returns:
        IngestResult containing table profiles and database path.
    """
    if not uploaded_files:
        return IngestResult(db_path=db_path, tables=[], total_files=0, error="No files provided.")

    conn = sqlite3.connect(db_path)
    table_profiles: list[TableProfile] = []
    seen_table_names: list[str] = []

    for filename, file_data in uploaded_files:
        try:
            # Table name from filename
            base_name = filename.rsplit(".", 1)[0]
            raw_table_name = sanitize_name(base_name, prefix="table")
            table_name = deduplicate_names(seen_table_names + [raw_table_name])[-1]
            seen_table_names.append(table_name)

            # Read file data
            if isinstance(file_data, bytes):
                stream = io.BytesIO(file_data)
            else:
                stream = file_data

            if filename.lower().endswith(".csv"):
                # Handle possible encodings
                try:
                    df = pd.read_csv(stream, encoding="utf-8")
                except UnicodeDecodeError:
                    stream.seek(0)
                    df = pd.read_csv(stream, encoding="latin1")
            elif filename.lower().endswith((".xlsx", ".xls")):
                df = pd.read_excel(stream)
            else:
                continue

            profile = load_dataframe_to_sqlite(df, table_name, conn)
            table_profiles.append(profile)

        except Exception as e:
            conn.close()
            return IngestResult(
                db_path=db_path,
                tables=table_profiles,
                total_files=len(uploaded_files),
                error=f"Error reading '{filename}': {e}",
            )

    conn.commit()
    conn.close()

    return IngestResult(
        db_path=db_path,
        tables=table_profiles,
        total_files=len(table_profiles),
    )


# ── Dynamic Prompt Generation (No Hard-Coded Business Rules) ─────────────────

def compute_dynamic_date_reference(
    db_path: str,
    table_name: str,
    date_col: str,
) -> Optional[str]:
    """
    Compute date reference ranges against the max date of the selected date column.
    """
    try:
        conn = sqlite3.connect(db_path)
        q = f"SELECT MAX({date_col}) FROM {table_name} WHERE {date_col} IS NOT NULL AND {date_col} != ''"
        row = conn.execute(q).fetchone()
        conn.close()

        if not row or not row[0]:
            return None

        today = date.fromisoformat(str(row[0])[:10])
    except Exception:
        return None

    # This month
    this_month_start = today.replace(day=1)
    this_month_end = today

    # Last month
    last_month_end = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # This quarter
    q_num = (today.month - 1) // 3
    this_q_start_month = q_num * 3 + 1
    this_q_start = today.replace(month=this_q_start_month, day=1)
    this_q_end = today

    # Last quarter
    if q_num == 0:
        last_q_start = date(today.year - 1, 10, 1)
        last_q_end = date(today.year - 1, 12, 31)
    else:
        lq = q_num - 1
        last_q_start = today.replace(month=lq * 3 + 1, day=1)
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
        f'\n### Date references (the latest date in column "{date_col}" is {today.isoformat()}, treat it as "today"):',
        f'  "this month":    {this_month_start.isoformat()} to {this_month_end.isoformat()}',
        f'  "last month":    {last_month_start.isoformat()} to {last_month_end.isoformat()}',
        f'  "this quarter":  {this_q_start.isoformat()} to {this_q_end.isoformat()}',
        f'  "last quarter":  {last_q_start.isoformat()} to {last_q_end.isoformat()}',
        f'  "this year":     {this_year_start.isoformat()} to {this_year_end.isoformat()}',
        f'  "last year":     {last_year_start.isoformat()} to {last_year_end.isoformat()}',
        f'  "last 30 days":  {last_30_start.isoformat()} to {last_30_end.isoformat()}',
    ]
    return "\n".join(lines)


def build_byod_system_prompt(
    db_path: str,
    user_notes: str = "",
    date_col_info: Optional[tuple[str, str]] = None,
) -> str:
    """
    Generate the system prompt dynamically from the uploaded database:
    - Table and column names and data types
    - Detected primary keys or unique identifiers
    - Sample values for all columns
    - Categorical distinct values for text columns with <= 30 unique values
    - Date reference range (if a date column is selected)
    - User notes inserted as business rules
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    schema_lines: list[str] = ["## Uploaded Database Schema"]

    for (tbl,) in tables:
        schema_lines.append(f"\n### Table: {tbl}")

        # PRAGMA table_info
        cols = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
        for c in cols:
            col_name = c["name"]
            col_type = c["type"]
            pk_str = " [PRIMARY KEY]" if c["pk"] else ""

            # Fetch up to 3 non-null sample values
            try:
                samples = conn.execute(
                    f"SELECT DISTINCT {col_name} FROM {tbl} WHERE {col_name} IS NOT NULL LIMIT 3"
                ).fetchall()
                sample_str = ", ".join(repr(s[0]) for s in samples)
                sample_note = f" (sample values: {sample_str})" if sample_str else ""
            except Exception:
                sample_note = ""

            schema_lines.append(f"  - {col_name} {col_type}{pk_str}{sample_note}")

        # Check for categorical columns (<= 30 distinct values)
        categorical_info: list[str] = []
        for c in cols:
            col_name = c["name"]
            try:
                cnt = conn.execute(
                    f"SELECT COUNT(DISTINCT {col_name}) FROM {tbl} WHERE {col_name} IS NOT NULL"
                ).fetchone()[0]
                if 1 < cnt <= 30:
                    vals = conn.execute(
                        f"SELECT DISTINCT {col_name} FROM {tbl} WHERE {col_name} IS NOT NULL ORDER BY {col_name}"
                    ).fetchall()
                    val_list = ", ".join(repr(v[0]) for v in vals)
                    categorical_info.append(f"  - {col_name} allowed values: {val_list}")
            except Exception:
                pass

        if categorical_info:
            schema_lines.append("  Categorical values:")
            schema_lines.extend(categorical_info)

    conn.close()

    schema_text = "\n".join(schema_lines)

    # Date references (if any)
    date_text = ""
    if date_col_info:
        tbl_name, d_col = date_col_info
        ref = compute_dynamic_date_reference(db_path, tbl_name, d_col)
        if ref:
            date_text = ref

    # User notes (business rules)
    notes_section = ""
    if user_notes and user_notes.strip():
        notes_section = f"\n## User Business Rules & Data Notes\n{user_notes.strip()}\n"

    return f"""You are a SQL expert for an uploaded SQLite database.
Your job: convert a natural-language business question into a single SQLite SELECT query.

{schema_text}
{date_text}
{notes_section}
## SQL Generation Rules
- Return ONLY valid SQLite SELECT queries.
- Use explicit JOINs when querying multiple tables.
- Round numeric/monetary calculations to 2 decimal places using ROUND(..., 2).
- Return only what was asked: use LIMIT 1 for "which / top 1" questions, LIMIT N for "top N".
- For time-based grouping by month or year, use SQLite's strftime('%Y-%m', date_column).

## Output Format
Return exactly one SQL query inside a ```sql code block.
If the question is off-topic, unanswerable, or cannot be answered using this schema, return:
CANNOT_ANSWER: <brief reason>

Do NOT include any explanation outside the code block. Do NOT return multiple queries.
"""
