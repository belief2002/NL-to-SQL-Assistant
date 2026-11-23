# Repository Structure

```
NL_to_SQL_Assistant/
├── .streamlit/
│   └── config.toml           # Streamlit configuration (minimal toolbar, headless mode)
├── .env                      # Local environment secrets (ignored by git)
├── .env.example              # Environment variables template
├── .gitignore                # Git exclusions (.env, sales.db, caches, venvs)
├── CHANGELOG.md              # Project modification and session changelog
├── README.md                 # Project documentation and architecture guide
├── requirements.txt          # Production and test Python dependencies
├── app.py                    # Streamlit web application & UI presentation logic
├── nl2sql.py                 # Core NL2SQL pipeline, dynamic prompt builder & LLM adapter
├── sql_guard.py              # Two-layer SQL safety system (static filter + SafeDB authorizer)
├── seed_db.py                # Synthetic Indian e-commerce SQLite database generator
├── eval.py                   # 15-case accuracy evaluation script
├── resc_misc/
│   └── NL_to_SQL_Project_Prompts.docx   # Original specification and multi-phase roadmap
├── tests/
│   ├── __init__.py           # Package marker
│   ├── test_dates.py         # Date reference range calculation tests (7 tests)
│   ├── test_pipeline.py      # Pipeline & FakeLLM end-to-end integration tests (14 tests)
│   └── test_sql_guard.py     # Static validation & SQLite authorizer security tests (40 tests)
└── .planning/
    ├── codebase/             # Evidence-backed codebase map
    │   ├── STACK.md
    │   ├── INTEGRATIONS.md
    │   ├── ARCHITECTURE.md
    │   ├── STRUCTURE.md
    │   ├── CONVENTIONS.md
    │   ├── TESTING.md
    │   └── CONCERNS.md
    └── onboarding/
        └── SUMMARY.md        # Onboarding index and roadmap handoff
```

## Module Responsibilities

| File | Primary Responsibility |
|---|---|
| `app.py` | UI layout, sidebar controls, example questions, execution routing, chart selection, CSV download |
| `nl2sql.py` | Prompt construction, date boundary calculations, LLM invocation with failover, query orchestration, plain-English summary |
| `sql_guard.py` | Static query sanitization, lexical parsing, `SafeDB` context manager, SQLite authorizer, timeout progress handler |
| `seed_db.py` | Synthetic generation of 5 tables (~12,000 orders, seasonal distribution, foreign keys, and indexes) |
| `eval.py` | Automated accuracy benchmark running 15 realistic business questions against expected SQL results |
| `tests/` | Comprehensive test suite containing 61 tests validating security boundaries, self-correction, and calendar transitions |
