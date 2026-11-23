# Integrations & External Interfaces

## 1. Google Gemini API (`google-genai` SDK)

- **Protocol:** REST/gRPC via official `google-genai` client.
- **Client Class:** `GeminiLLM` (in `nl2sql.py`), conforming to the `LLMInterface` protocol (`chat(system: str, user: str) -> str`).
- **Generation Settings:** `temperature=0.0` for deterministic SQL output, with `system_instruction` provided via `GenerateContentConfig`.
- **Fault-Tolerance & Failover:** Automatic fallback pipeline to absorb temporary 503 high-demand spikes, 404 retired model errors, or 429 rate limits:
  1. Primary: configured `GEMINI_MODEL` (default: `gemini-3.8-flash`)
  2. Fallback 1: `gemini-3.5-flash-lite`
  3. Fallback 2: `gemini-3.1-flash-lite`
- **Call Points:**
  - Call 1: SQL Generation (`nl2sql.py:NL2SQLPipeline.ask()`)
  - Call 2: Plain-English Result Summary (1–3 sentences with ₹ formatting)

## 2. SQLite Database Engine

- **Storage:** Local single-file database (`sales.db`), generated deterministically by `seed_db.py`.
- **Read-Only URI:** Opened with `file:sales.db?mode=ro` and `PRAGMA query_only=ON`.
- **Authorizer Callback:** Hooks into `sqlite3.Connection.set_authorizer()`.
  - Grants: `SQLITE_SELECT` statement, `SQLITE_READ` on authorized tables only, safe built-in aggregate/scalar functions (`SUM`, `COUNT`, `ROUND`, `strftime`, etc.).
  - Denies: table writes (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`), administrative operations (`PRAGMA`, `ATTACH`, `DETACH`), functions (`load_extension`), and schema reads (`sqlite_master`, `sqlite_temp_master`).
- **Progress Handler:** Installs VM instruction counter via `set_progress_handler` every 1,000,000 opcodes. Aborts queries exceeding ~200 million VM opcodes to stop runaway or infinite recursive queries.

## 3. Streamlit Client Interface

- **Port / Host:** Default local port 8501.
- **Session State:**
  - `user_question`: Two-way binding for the user question text area.
  - `auto_submit`: Boolean trigger indicating an example question was clicked and should immediately execute without requiring a manual click on "🚀 Ask".
