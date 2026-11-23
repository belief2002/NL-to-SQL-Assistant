# Coding Standards & Conventions

## Python Code Standards

- **Python Version:** 3.11+ syntax (`from __future__ import annotations`, modern union types `X | Y` or `Optional[X]`).
- **Typing:** Strict type annotations across all function signatures, dataclasses, and protocols.
- **Docstrings:** Concise module-level and function-level docstrings detailing contracts, return types, and exceptions.
- **Dataclasses & Immutability:** Use `@dataclass` for transfer objects (`QueryResult`, `PipelineResult`, `EvalCase`).
- **Dependency Inversion:** Interfaces modeled via `typing.Protocol` (e.g. `LLMInterface`) to decouple high-level orchestration from external APIs.

## SQL Conventions & Dialect

- **Target Engine:** SQLite 3.
- **Aliases:** Explicit, short table aliases (`r` for regions, `c` for customers, `p` for products, `o` for orders, `oi` for order_items).
- **Date Formatting:** ISO-8601 strings (`YYYY-MM-DD`). Time grouping using SQLite's built-in `strftime('%Y-%m', o.order_date)`.
- **Currency & Numerics:** All currency figures are in Indian Rupees (₹), explicitly rounded using `ROUND(..., 2)`.
- **Business Exclusions:** Orders with status `'Cancelled'` or `'Returned'` are systematically excluded unless explicitly requested.

## Safety & Security Conventions

- **No Secrets in Code:** Zero hardcoded API keys. Values are loaded strictly through `os.environ` via `python-dotenv`.
- **Defensive Error Encapsulation:** Functions never propagate raw unhandled database or network exceptions to the UI. Instead, structured errors are returned in `PipelineResult.error`.
- **Immediate Termination on Malicious Intent:** Queries flagged by Layer 1 static analysis (such as `DROP`, stacked statements, transaction keywords) are aborted immediately and **never** fed into the retry/self-correction loop.
