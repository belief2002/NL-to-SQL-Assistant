# NL-to-SQL Assistant — Project Changelog

> **Purpose:** This file tracks every change made during the development of this project — files created, modified, deleted, code changes, assets added, and architectural decisions.

---

## Project Overview

**Project Name:** Ask Your Data — Natural Language to SQL Assistant  
**Start Date:** 2026-11-18  
**Tech Stack:** Python 3.11+, Streamlit, SQLite, pandas, Anthropic API, python-dotenv, pytest  
**Source Document:** [`NL_to_SQL_Project_Prompts.docx`](file:///d:/data%20analytics/project/NL_to_SQL_Assistant/resc_misc/NL_to_SQL_Project_Prompts.docx)

---

## Pre-existing Files (Before Development)

| File | Description |
|------|-------------|
| `resc_misc/NL_to_SQL_Project_Prompts.docx` | Project specification and build prompts document |

---

## Planned Deliverables

The following files are planned as part of this project (from the spec):

| File/Folder | Purpose |
|---|---|
| `app.py` | Streamlit web app — UI, sidebar, result display, charts |
| `nl2sql.py` | Core pipeline — prompt builder, LLM calls, query execution, summary |
| `sql_guard.py` | SQL safety — static validation + database-level read-only enforcement |
| `seed_db.py` | Database seeder — generates synthetic Indian e-commerce data |
| `tests/` | pytest test suite (guard, pipeline, date references) |
| `requirements.txt` | Python dependencies |
| `.env.example` | Example environment variables file |
| `.gitignore` | Git ignore rules (`.env`, `sales.db`) |
| `README.md` | Project documentation with architecture, examples, safety design |
| `eval.py` | Evaluation script — 15 Q&A pairs, accuracy percentage |
| `CHANGELOG.md` | This file — tracks all project changes |

---

## Change Log

### [2026-11-18] — Session 1: Project Setup & Initialization

#### Added
- **`CHANGELOG.md`** — Created this project changelog to track all changes, files, code modifications, and assets throughout development.

#### Reviewed
- **`resc_misc/NL_to_SQL_Project_Prompts.docx`** — Read and analyzed the full project specification document containing:
  - Prompt 1: Full project build (database, pipeline, safety, UI, tests, evaluation)
  - Prompt 2: Bring-your-own-data mode (CSV/XLSX upload, dynamic schema)

---

### [2026-11-18] — Session 2: Full Project Build (Prompt 1)

#### Added — Config Files
- **`requirements.txt`** — Python dependencies: streamlit, pandas, anthropic, python-dotenv, pytest, openpyxl
- **`.env.example`** — Template with `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` variables
- **`.gitignore`** — Ignores `.env`, `sales.db`, `__pycache__`, venvs, IDE files, OS files, pytest cache

#### Added — Core Modules
- **`seed_db.py`** — Synthetic Indian e-commerce database generator
  - 5 regions, ~600 customers, 20 products (5 categories), ~12,000 orders, ~22,700 order items
  - Deterministic (seed=42), Jan 2025 → mid Nov 2026, all amounts in INR
  - Realistic patterns: festive season boost (Oct–Dec), Zipf customer distribution, region-weighted volume, higher festive discounts, orders only after signup
  - Foreign keys and 7 indexes on key columns

- **`sql_guard.py`** — Two-layer SQL safety system
  - **Layer 1 (static):** strips strings/comments before scanning; allows only single `SELECT`/`WITH`; blocks 15+ dangerous keywords, `load_extension`, stacked statements
  - **Layer 2 (database):** `mode=ro` URI, SQLite authorizer (allow only SELECT/READ on known tables, block `sqlite_master`), progress handler timeout (~200M instructions), 500-row cap with truncation flag
  - `QueryResult` dataclass, `SafeDB` context manager, `validate_sql()` function, `get_allowed_tables()` helper

- **`nl2sql.py`** — Core NL-to-SQL pipeline
  - `LLMInterface` protocol for dependency injection (testable with fake LLM)
  - `AnthropicLLM` production backend
  - `PipelineResult` dataclass: sql, columns, rows, summary, refusal, error, attempts, truncated
  - Dynamic prompt builder: schema from live DB, categorical values, pre-computed date ranges (this/last month/quarter/year, last 30 days), business rules
  - SQL extraction via regex, CANNOT_ANSWER refusal detection
  - Error-retry loop (max 2 attempts), blocked queries never retried
  - Optional second LLM call for plain-English summary with ₹ symbol
  - Input validation: empty check, 500-char limit

- **`app.py`** — Streamlit web application
  - Sidebar: API key (password), model selector, summary toggle, data description, table previews
  - Main page: title, 6 clickable example questions, 500-char text area, Ask button
  - Result view: success box summary, SQL expander with syntax highlighting, data table, row count, auto bar/line charts, CSV download
  - Friendly messages for missing API key, refusals, blocked queries, errors
  - Auto-creates database on first run

- **`eval.py`** — Evaluation script with 15 question/expected-answer pairs
  - Runs questions through the full pipeline with a real LLM
  - Compares results against reference SQL output
  - Reports pass/fail per question and overall accuracy percentage

#### Added — Test Suite
- **`tests/__init__.py`** — Package marker
- **`tests/test_sql_guard.py`** — 31 tests covering:
  - 12 valid query tests (joins, CTEs, subqueries, keywords in strings/comments, CASE, trailing semicolons)
  - 15 blocked query tests (DROP, INSERT, UPDATE, DELETE, ALTER, CREATE, ATTACH, DETACH, PRAGMA, VACUUM, REINDEX, stacked statements, comment tricks, CTE-wrapped DELETE, load_extension, BEGIN)
  - 2 edge cases (empty query, whitespace only)
  - 9 database-level tests (read-only write block, authorizer, sqlite_master block, runaway timeout, row truncation, pragma/attach block)
- **`tests/test_pipeline.py`** — 14 pipeline tests with scripted FakeLLM:
  - Happy path (with & without summary), self-correction, max attempts, destructive query no-retry, refusal, API failure, summary failure, empty/whitespace/overlong questions, empty result, no SQL block, stacked statements
- **`tests/test_dates.py`** — 7 date reference tests:
  - Mid-year, January wrap-around, end-of-quarter, start-of-year, November, last-30-days, December Q4

#### Added — Documentation
- **`README.md`** — Full project documentation including:
  - Architecture diagram (text-based flow)
  - Feature list, setup instructions, running tests & eval
  - 8 example questions with SQL
  - Safety design section (Layer 1 & 2)
  - Limitations & next steps
  - Project structure tree, tech stack table

#### Generated — Database
- **`sales.db`** — Seeded synthetic database (in .gitignore):
  - regions: 5 rows
  - customers: 600 rows
  - products: 20 rows
  - orders: 11,957 rows
  - order_items: 22,676 rows

#### Test Results
- **61/61 tests passed** in 2.13 seconds ✅
  - 7 date reference tests ✅
  - 14 pipeline tests ✅
  - 31+9 = 40 SQL guard tests (static + database level) ✅

---

### [2026-11-18] — Session 3: Migration from Claude (Anthropic) to Google Gemini

#### Changed — LLM Provider & Dependencies
- **`requirements.txt`**:
  - Replaced `anthropic>=0.40.0` with `google-genai>=0.1.0` (official Google GenAI SDK).
- **`.env.example`**:
  - Replaced `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` with `GEMINI_API_KEY` and `GEMINI_MODEL=gemini-2.5-flash`.
- **`nl2sql.py`**:
  - Added `GeminiLLM` implementing `LLMInterface` using `from google import genai` and `types.GenerateContentConfig`.
  - Added backwards-compatibility alias `AnthropicLLM = GeminiLLM`.
- **`app.py`**:
  - Updated imports to import `GeminiLLM`.
  - Updated sidebar to prompt for `GEMINI_API_KEY` and `GEMINI_MODEL`.
  - Updated error messages and instantiation to use `GeminiLLM`.
- **`eval.py`**:
  - Updated evaluation runner to use `GEMINI_API_KEY`, `GEMINI_MODEL` (`gemini-2.5-flash`), and `GeminiLLM`.
- **`README.md`**:
  - Updated architecture diagram from Claude to Google Gemini.
  - Updated prerequisites, setup instructions, and tech stack table.

#### Test Results
- **61/61 tests passed** in 1.82 seconds ✅

---

### [2026-11-18] — Session 4: Configuration File Setup

#### Added
- **`.env`** — Created local environment file for the user with `GEMINI_API_KEY` placeholder and default `GEMINI_MODEL=gemini-2.5-flash` (ignored by `.gitignore` to protect credentials).

---

### [2026-11-18] — Session 5: Model Update to Gemini 3.8 & High-Demand Resilience

#### Changed — Model Upgrade
- **`.env`** & **`.env.example`**:
  - Updated default `GEMINI_MODEL` to `gemini-3.8-flash` (resolving Google's retirement notice for `gemini-2.5-flash` to new users).
- **`nl2sql.py`**:
  - Updated default model in `GeminiLLM` to `gemini-3.8-flash`.
  - Added multi-model fallback resiliency (`FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]`).
  - Automatically catches 503 (high demand), 404 (retired), and 429 errors and falls back to an available, active model.
- **`app.py`**:
  - Updated default model to `gemini-3.8-flash` and sidebar help tooltip.
- **`eval.py`**:
  - Updated default model to `gemini-3.8-flash`.
- **`README.md`**:
  - Updated model documentation to `gemini-3.8-flash`.

#### Test Results
- **Live Gemini API Verification**: Verified end-to-end question answering with SQL generation and natural-language summary.
- **61/61 tests passed** in 1.63 seconds ✅

---

### [2026-11-18] — Session 6: UI Privacy Enhancement — Hide API Key in Settings

#### Changed — App UI
- **`app.py`**:
  - Removed the API key input field from the sidebar settings to ensure user API keys are never displayed or exposed on screen.
  - The application now securely reads `GEMINI_API_KEY` directly from the `.env` file (or system environment).
  - Updated error messaging when `GEMINI_API_KEY` is missing to guide users to `.env`.

#### Test Results
- **61/61 tests passed** in 1.76 seconds ✅

---

### [2026-11-18] — Session 7: UI Clean-up — Remove Deploy Button

#### Added
- **`.streamlit/config.toml`** — Set `toolbarMode = "minimal"` and `headless = true` to remove toolbar clutter.

#### Changed — App UI
- **`app.py`** — Added custom CSS targeting `.stAppDeployButton` and `[data-testid="stAppDeployButton"]` (`display: none !important`), completely hiding the Streamlit Deploy button and footer.

---

### [2026-11-18] — Session 8: Interactive Example Auto-Execution

#### Changed — App UI
- **`app.py`**:
  - Bound each example button under "Try an example" to an `on_click` callback (`_on_example_click`).
  - Clicking an example button instantly populates the text area (`user_question`) and sets `auto_submit = True`.
  - The query pipeline now runs automatically upon clicking an example button (no manual second click on "Ask" required).
  - Maintained full support for manual typing and clicking the "🚀 Ask" button.

---

### [2026-11-19] — Session 9: Bring-Your-Own-Data (BYOD) Mode (Prompt 2 Deliverable)

#### Added — Modules & Engine
- **`data_loader.py`**:
  - **Sanitization & Deduplication**: Converts headers and filenames to `snake_case`, strips non-alphanumeric characters, handles numeric prefixes and SQLite reserved keywords, and deduplicates column collisions (`col`, `col_1`, `col_2`).
  - **Date Parsing & ISO Normalization**: Automatically detects date columns across mixed date formats (slashes, hyphens, standard ISO) and normalizes to ISO `YYYY-MM-DD` text.
  - **Numeric Parsing**: Converts stringified numbers with currency symbols (`$`, `₹`, `€`) and commas (`$1,200.50`) into numeric columns.
  - **Data Profiling**: Generates `TableProfile` objects with row count, inferred dtypes, null counts, sample values, and 5-row preview.
  - **Dynamic General Prompt Builder**: Inspects the uploaded SQLite database schema without any hard-coded rules; extracts columns, types, sample values, and all distinct values for categorical columns (≤ 30 unique values).
  - **Dynamic Date Reference**: Calculates "this month", "last month", "this quarter", "last quarter", "this year", "last year", and "last 30 days" against the maximum date in the selected reference date column.
  - **User Notes**: Injects user-provided domain rules into the prompt under `## User Business Rules & Data Notes`.

#### Added — Test Suite
- **`tests/test_byod.py`**:
  - 12 new comprehensive tests covering:
    - Messy column names (special characters, whitespace, brackets, currency symbols)
    - Leading numbers and SQLite reserved words
    - Duplicate column deduplication
    - Mixed date formats parsing to standard ISO strings
    - Currency and comma-separated string conversions
    - Multi-file ingestion and cross-table joins
    - Dynamic prompt schema extraction and categorical value discovery
    - Dynamic date reference calculation
    - End-to-end `NL2SQLPipeline` execution on custom uploaded DB using `FakeLLM`
    - Security guard (`SafeDB`, read-only connection, authorizer) enforcement on custom uploaded DB

#### Changed — Core Pipeline & App UI
- **`nl2sql.py`**:
  - Updated `NL2SQLPipeline.__init__` to accept an optional `system_prompt` argument, allowing custom/BYOD prompts while defaulting to the sample database prompt.
- **`app.py`**:
  - Added sidebar radio switch: `"Sample sales data"` or `"Upload my data"`.
  - Added prominent privacy notice: *"Column names, sample values and query results are sent to the AI provider."*
  - Added multiple file uploader supporting `.csv`, `.xlsx`, and `.xls` (enforcing max 50 MB per file).
  - Ingests uploaded files into a per-session temporary SQLite database.
  - Added interactive Data Profile Expander showing table row counts, column types, null counts, sample values, and 5 preview rows.
  - Added optional "Notes about my data" text area for custom business rules.
  - Added reference date column selector to drive calendar date ranges dynamically.
  - Main area adapts seamlessly: shows instructions if no files uploaded, or renders query input, summary, SQL expander, table, automatic charts, and CSV download once files are loaded.
- **`README.md`**:
  - Updated feature list, project structure, and roadmap to document BYOD mode.

#### Test Results
- **73/73 tests passed** in 5.20 seconds ✅
  - 12 BYOD ingestion, profiling, dates & pipeline tests ✅
  - 7 date reference tests ✅
  - 14 pipeline tests ✅
  - 40 SQL guard tests (static + database level) ✅

---

### [2026-11-19] — Session 10: Execution Cancellation & Stop Button

#### Changed — Core Pipeline & App UI
- **`nl2sql.py`**:
  - Added `cancel_check: Optional[Callable[[], bool]] = None` callback to `NL2SQLPipeline.ask()`.
  - Injected cancellation checkpoints before initial LLM chat, before retry LLM chat, before SQLite database execution, and before plain-English summary generation.
- **`app.py`**:
  - Added dynamic state to the **"⏹️ Stop"** button:
    - **Idle State:** When no query is actively executing, the Stop button is visually and functionally **disabled** (`disabled=True`), and the "🚀 Ask" button is enabled.
    - **Running State:** As soon as a query starts executing, the button bar instantly updates: "🚀 Ask" becomes disabled and "⏹️ Stop" becomes **enabled** (`disabled=False`).
    - Clicking the active Stop button interrupts the backend pipeline, resets state, and reports: *"⏹️ Execution stopped by user."*
- **`tests/test_pipeline.py`**:
  - Added `test_pipeline_stop_execution` verifying that `cancel_check` interrupts and halts query execution immediately.

#### Test Results
- **74/74 tests passed** in 3.13 seconds ✅

---

### [2026-11-19] — Session 11: UI Refinement — Button Persistence During Stop

#### Changed — App UI
- **`app.py`**:
  - Removed the premature `st.stop()` call that previously halted Python execution before the button widgets could render.
  - Buttons (`Ask` and `Stop`) are now guaranteed to remain rendered on screen in their idle state (Ask enabled, Stop disabled) even when an execution cancellation occurs.
  - After stopping, users can immediately edit their question or click "Ask" again without any disappearance or flashing of the button bar.
  - Dynamically restores the idle button state immediately after successful query completion.

#### Test Results
- **74/74 tests passed** in 3.47 seconds ✅

---

---

### [2026-11-23] — Session 12: GPT-Like Chat Interface & Branch Merge

#### Added
- **`chat_app.py`** — GPT-like chat interface for the NL-to-SQL Assistant:
  - Persistent conversation memory via `st.session_state["chat_messages"]` holding user messages and assistant responses.
  - Pinned bottom chat input (`st.chat_input`) for natural multi-turn chat interaction.
  - Inline rendering of SQL expanders, data tables, automatic charts (bar/line), summaries, and CSV download buttons inside chat bubbles.
  - \"🗑️ Clear Chat\" button in sidebar to reset conversation history.
  - Full compatibility with both Sample Sales Data and Bring-Your-Own-Data (BYOD) modes.

#### Changed — Git & Workflow
- **`.gitignore`** — Added `*.db-shm` and `*.db-wal` to ignore SQLite WAL files.
- **Git Merge** — Merged `chatui` branch into `main` (`git merge chatui --no-ff`).

#### Test Results
- **Syntax & AST Validation** — Verified `chat_app.py` passes Python compilation and AST parsing (2,285 nodes) ✅
- **74/74 tests passed** ✅

### [2026-11-23] — Session 13: README Documentation Update

#### Changed — Documentation
- **`README.md`** — Updated project documentation to include the new **GPT-like Chat Interface (`chat_app.py`)**, adding instructions to run both the classic form UI (`app.py`) and chat UI (`chat_app.py`), and updating the project structure tree.

#### Test Results
- **74/74 tests passed** ✅

### [2026-11-23] — Session 14: Complete README Rewrite

#### Changed — Documentation
- **`README.md`** — Completely rewritten from scratch to provide a pristine, professional overview including Title, Description, Features, How to Run, File Structure, and Tech Stack.

#### Test Results
- **74/74 tests passed** ✅

*This log will be updated continuously as development progresses.*
