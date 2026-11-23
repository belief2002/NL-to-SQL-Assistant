# Technology Stack

## Core Technologies

- **Language:** Python 3.11+ (running on Python 3.15.2 on Windows x64)
- **Frontend / UI:** Streamlit (`>=1.30.0`)
- **Database Engine:** SQLite 3 (built-in Python `sqlite3` module with URI support for read-only mode `?mode=ro`, authorizer hooks, and VM progress handlers)
- **Data Manipulation:** pandas (`>=2.1.0`), openpyxl (`>=3.1.0`)
- **AI / LLM Provider:** Google Gemini API via official `google-genai` SDK (`>=0.1.0`, installed version 3.5.0)
- **Primary Model:** `gemini-3.8-flash`
- **Fallback Models:** `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`
- **Configuration & Secrets:** `python-dotenv` (`>=1.0.0`) loading `.env`
- **Testing Framework:** pytest (`>=8.0.0`, pytest-9.2.0 installed)

## Runtime & Tooling

- **Package Manager:** `pip`
- **Server Execution:** Headless Streamlit configured via `.streamlit/config.toml` (`toolbarMode = "minimal"`, `headless = true`)
- **Execution Script:** `python -m streamlit run app.py`

## Dependencies (`requirements.txt`)

```text
streamlit>=1.30.0
pandas>=2.1.0
google-genai>=0.1.0
python-dotenv>=1.0.0
pytest>=8.0.0
openpyxl>=3.1.0
```

## Environment Variables

- `GEMINI_API_KEY`: API key for Google Gemini / Google AI Studio (required)
- `GEMINI_MODEL`: Gemini model name, defaults to `gemini-3.8-flash` (optional)
- `DB_PATH`: Path to SQLite database file, defaults to `sales.db` (optional)
