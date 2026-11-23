"""
sql_guard.py – Two-layer SQL safety for the NL-to-SQL pipeline.

Layer 1: Static validation (parse & block dangerous statements).
Layer 2: Database-level protection (read-only connection, authorizer, timeout).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


# ── Layer 1: Static Validation ───────────────────────────────────────────────

# Keywords that must NEVER appear as statements or clauses
_BLOCKED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
}

# Pattern to match load_extension (case-insensitive)
_LOAD_EXTENSION_RE = re.compile(r"\bload_extension\s*\(", re.IGNORECASE)


def _strip_strings_and_comments(sql: str) -> str:
    """
    Replace string literals and comments with whitespace so keyword scanning
    doesn't trigger on values like 'DROP TABLE' inside a WHERE clause.

    Handles:
      - Single-quoted strings: 'hello', 'it''s'
      - Double-quoted identifiers: "column name"
      - Single-line comments: -- ...
      - Block comments: /* ... */
    """
    result: list[str] = []
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]

        # Single-line comment
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            # Skip to end of line
            while i < n and sql[i] != "\n":
                i += 1
            result.append(" ")
            continue

        # Block comment
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2  # skip */
            result.append(" ")
            continue

        # Single-quoted string (with '' escape)
        if ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2  # escaped quote
                elif sql[i] == "'":
                    i += 1
                    break
                else:
                    i += 1
            result.append(" ")
            continue

        # Double-quoted identifier
        if ch == '"':
            i += 1
            while i < n and sql[i] != '"':
                i += 1
            i += 1  # skip closing "
            result.append(" ")
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    Statically validate a SQL string.

    Returns:
        (True, "") if the SQL is safe to execute.
        (False, reason) if the SQL should be blocked.
    """
    if not sql or not sql.strip():
        return False, "Empty SQL query."

    stripped = sql.strip().rstrip(";").strip()

    # Remove strings and comments for keyword scanning
    cleaned = _strip_strings_and_comments(stripped)

    # Check for stacked statements: split on ; and check for multiple non-empty parts
    parts = [p.strip() for p in cleaned.split(";") if p.strip()]
    if len(parts) > 1:
        return False, "Multiple statements are not allowed."

    # Must start with SELECT or WITH
    first_word = cleaned.strip().split()[0].upper() if cleaned.strip() else ""
    if first_word not in ("SELECT", "WITH"):
        return False, f"Only SELECT queries are allowed (found '{first_word}')."

    # Scan for blocked keywords (word-boundary match in the cleaned SQL)
    cleaned_upper = cleaned.upper()
    for kw in _BLOCKED_KEYWORDS:
        # Use word boundary to avoid false positives like "CREATING" matching "CREATE"
        if re.search(r"\b" + kw + r"\b", cleaned_upper):
            return False, f"Blocked keyword: {kw}."

    # Check for load_extension
    if _LOAD_EXTENSION_RE.search(cleaned):
        return False, "load_extension is not allowed."

    return True, ""


# ── Layer 2: Database-Level Protection ───────────────────────────────────────

# SQLite authorizer action codes
_SQLITE_OK     = 0  # SQLITE_OK
_SQLITE_DENY   = 1  # SQLITE_DENY
_SQLITE_IGNORE = 2  # SQLITE_IGNORE

# Authorizer action type constants
_SQLITE_SELECT         = 21
_SQLITE_READ           = 20
_SQLITE_FUNCTION       = 31
_SQLITE_PRAGMA         = 19
_SQLITE_ATTACH         = 24
_SQLITE_DETACH         = 25
_SQLITE_CREATE_TABLE   = 1
_SQLITE_DROP_TABLE     = 15
_SQLITE_INSERT         = 18
_SQLITE_UPDATE         = 23
_SQLITE_DELETE         = 9

# Maximum rows returned
MAX_ROWS = 500

# Progress handler interval (every N VM instructions) and max steps
_PROGRESS_INTERVAL = 1_000_000
_MAX_STEPS = 200  # allow ~200M instructions before timeout


@dataclass
class QueryResult:
    """Result of executing a guarded SQL query."""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    error: Optional[str] = None


class SafeDB:
    """
    A read-only SQLite connection with authorizer and timeout.

    Usage:
        db = SafeDB("sales.db", allowed_tables={"regions", "customers", ...})
        result = db.execute("SELECT * FROM regions")
        db.close()
    """

    def __init__(self, db_path: str, allowed_tables: set[str]) -> None:
        self._allowed_tables = {t.lower() for t in allowed_tables}
        self._step_count = 0

        # Open in read-only mode via URI
        uri = f"file:{db_path}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True)
        self._conn.execute("PRAGMA query_only=ON")

        # Install authorizer
        self._conn.set_authorizer(self._authorizer)

        # Install progress handler for timeout
        self._conn.set_progress_handler(self._progress_handler, _PROGRESS_INTERVAL)

    def _authorizer(
        self,
        action_code: int,
        arg1: Optional[str],
        arg2: Optional[str],
        db_name: Optional[str],
        trigger_name: Optional[str],
    ) -> int:
        """SQLite authorizer callback: allow only SELECT/READ on known tables."""
        # Always allow SELECT (the statement type)
        if action_code == _SQLITE_SELECT:
            return _SQLITE_OK

        # Allow READ only on allowed tables
        if action_code == _SQLITE_READ:
            table = (arg1 or "").lower()
            if table in self._allowed_tables:
                return _SQLITE_OK
            # Block reads on sqlite_master, sqlite_temp_master, etc.
            return _SQLITE_DENY

        # Allow built-in functions (SUM, COUNT, etc.)
        if action_code == _SQLITE_FUNCTION:
            func_name = (arg2 or "").lower()
            # Block load_extension even as a function
            if func_name == "load_extension":
                return _SQLITE_DENY
            return _SQLITE_OK

        # Block everything else (PRAGMA, ATTACH, INSERT, etc.)
        return _SQLITE_DENY

    def _progress_handler(self) -> int:
        """Progress handler: abort query if it runs too long."""
        self._step_count += 1
        if self._step_count > _MAX_STEPS:
            return 1  # non-zero → abort
        return 0

    def execute(self, sql: str) -> QueryResult:
        """Execute a SQL query with safety guards. Returns a QueryResult."""
        self._step_count = 0
        try:
            cur = self._conn.execute(sql)
            columns = [desc[0] for desc in cur.description] if cur.description else []

            rows = cur.fetchmany(MAX_ROWS + 1)
            truncated = len(rows) > MAX_ROWS
            if truncated:
                rows = rows[:MAX_ROWS]

            return QueryResult(columns=columns, rows=[tuple(r) for r in rows], truncated=truncated)

        except sqlite3.OperationalError as e:
            msg = str(e)
            if "interrupted" in msg.lower() or "abort" in msg.lower():
                return QueryResult(error="Query timed out (too many operations).")
            if "not authorized" in msg.lower() or "authorization denied" in msg.lower():
                return QueryResult(error="Access denied by database authorizer.")
            return QueryResult(error=f"Database error: {msg}")
        except sqlite3.DatabaseError as e:
            return QueryResult(error=f"Database error: {e}")

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "SafeDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ── Convenience ──────────────────────────────────────────────────────────────

def get_allowed_tables(db_path: str) -> set[str]:
    """Read the list of user tables from the database."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}
