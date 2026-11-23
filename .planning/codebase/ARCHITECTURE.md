# System Architecture

## Architecture Overview

Ask Your Data implements a defense-in-depth, pipeline-oriented architecture for natural language to SQL generation and execution.

```
┌──────────────────┐
│  Streamlit UI    │
│  (app.py)        │
└────────┬─────────┘
         │ User Question (≤500 chars)
         ▼
┌────────────────────────────────────────────────────────┐
│  NL2SQLPipeline (nl2sql.py)                            │
│  1. Prompt Builder (Dynamic live DB schema + dates)   │
│  2. LLM Call 1: Generate SQL query                     │
│  3. Regex Extraction & Refusal Detection               │
└────────┬───────────────────────────────────────────────┘
         │ SQL Statement
         ▼
┌────────────────────────────────────────────────────────┐
│  SQL Safety Guard (sql_guard.py)                       │
│  Layer 1: Static AST / Lexer-style validation          │
│    - Strips comments & string literals                 │
│    - Single statement enforcement (no semicolons)      │
│    - Must start with SELECT or WITH                    │
│    - Blocked keyword detection (DROP, DELETE, etc.)    │
└────────┬───────────────────────────────────────────────┘
         │ Safe Query
         ▼
┌────────────────────────────────────────────────────────┐
│  SafeDB (sql_guard.py)                                 │
│  Layer 2: Database-Level Isolation                     │
│    - SQLite URI: mode=ro (Read-Only)                   │
│    - Custom SQLite Authorizer (Whitelisted tables only)│
│    - Progress Handler Timeout (~200M instructions)     │
│    - Row Truncation Cap (max 500 rows)                 │
└────────┬───────────────────────────────────────────────┘
         │ Results (columns, rows, truncated flag)
         ▼
┌────────────────────────────────────────────────────────┐
│  Self-Correction Loop (in nl2sql.py)                   │
│  - If SQLite error occurs, feed error to LLM & retry   │
│  - Blocked queries (Layer 1 violation) are never       │
│    retried                                             │
└────────┬───────────────────────────────────────────────┘
         │ Final Execution Result
         ▼
┌────────────────────────────────────────────────────────┐
│  Summary Generation (Optional, in nl2sql.py)           │
│  - LLM Call 2: 1–3 plain English sentences with ₹      │
└────────┬───────────────────────────────────────────────┘
         │ PipelineResult
         ▼
┌──────────────────┐
│  Streamlit UI    │
│  - Metric/Summary│
│  - Code expander │
│  - DataFrame     │
│  - Bar/Line Chart│
│  - CSV Download  │
└──────────────────┘
```

## Core Design Principles

1. **Defense in Depth:** Security is never delegated exclusively to prompt instructions or LLM compliance. Static code analysis (Layer 1) and kernel/driver-level read-only authorizers (Layer 2) ensure total database safety.
2. **Zero Calendar Math by LLM:** Date offsets ("this month", "last quarter", "last 30 days") are computed deterministically against `MAX(order_date)` and injected into the prompt.
3. **Dependency Inversion / Testability:** The pipeline consumes an `LLMInterface` protocol. Testing requires no live network or API keys (`FakeLLM` scripted runs).
4. **Resilient Failure Handling:** Expected failures return structured `PipelineResult` objects instead of unhandled exceptions; UI renders informative status messages for refusals, syntax errors, and timeouts.
