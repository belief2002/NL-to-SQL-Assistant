# 🔍 Ask Your Data — Natural Language to SQL Assistant

> Turn plain-English business questions into SQL queries, executed safely on a live database, with instant tables, charts, and natural-language summaries.

A production-grade portfolio application built for data analytics and agentic AI, demonstrating advanced prompt engineering, two-layer SQL safety, full-stack Python development, Google Gemini integration, and multi-turn chat capabilities.

---

## ✨ Features

- **💬 GPT-Like Chat Interface (chat_app.py)**: Persistent multi-turn conversation memory, chat bubbles, pinned bottom input (st.chat_input), and inline rendering of SQL, data tables, charts, and summaries.
- **🖥️ Classic Form UI (app.py)**: Single-turn prompt form with clickable example question chips, instant execution, and cancellation support.
- **📁 Bring-Your-Own-Data (BYOD) Mode**: Upload multiple CSV or Excel files (up to 50 MB each) to query your own datasets on the fly with auto-generated schemas.
- **📊 Automated Data Profiling**: Table row counts, inferred data types (INTEGER, REAL, TEXT, DATE), null counts, and 5-row data previews.
- **🧹 Smart Data Sanitization**: snake_case column deduplication, mixed date format detection to ISO YYYY-MM-DD, and currency/symbol cleaning.
- **🛡️ Two-Layer SQL Safety (sql_guard.py)**: 
  - **Layer 1 (Static):** Blocks destructive statements (DROP, INSERT, UPDATE, DELETE, ALTER, CREATE, PRAGMA, etc.), stacked queries, and load_extension.
  - **Layer 2 (Database):** Opens SQLite in strict read-only mode (?mode=ro), installs an authorizer callback to block unauthorized table reads, and sets a progress handler timeout.
- **🤖 Intelligent Pipeline (
l2sql.py)**: Dynamic prompt generation from live database schemas, categorical values, calendar date reference math, auto-correction on SQL errors (max 2 attempts), and plain-English summaries using the ₹ symbol.
- **📈 Automatic Visualizations & Export**: Detects bar charts (categorical grouping) and line charts (time trends) automatically, plus one-click CSV export.

---

## 🚀 How to Run

### 1. Prerequisites
- **Python 3.11+**
- A [Google Gemini API key](https://aistudio.google.com/app/apikey)

### 2. Installation

# Clone the repository
git clone https://github.com/YOUR_USERNAME/NL_to_SQL_Assistant.git
cd NL_to_SQL_Assistant

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate    # Linux / macOS
venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt


### 3. Environment Setup
Create a .env file in the root directory (or copy from .env.example):
`env
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_MODEL=gemini-3.8-flash
DB_PATH=sales.db
`

### 4. Running the Application
The sample E-commerce database (sales.db) is generated automatically on first run.


# Option A: Run the GPT-Like Chat Interface (Recommended)
streamlit run chat_app.py

# Option B: Run the Classic Form UI
streamlit run app.py
`

### 5. Running Tests & Evaluation

# Run pytest test suite (74 tests covering guards, pipeline, BYOD, and dates)
pytest tests/ -v

# Run the evaluation suite (15 Q&A test cases)
python eval.py
`

---

## 📁 File Structure

`
NL_to_SQL_Assistant/ \n
├── chat_app.py         # Streamlit GPT-like multi-turn chat interface
├── app.py              # Streamlit classic single-turn form interface
├── nl2sql.py           # Core NL-to-SQL pipeline (prompt builder, Gemini LLM, execution, retry)
├── data_loader.py      # BYOD ingestion, sanitization, profiling & dynamic prompt generator
├── sql_guard.py        # Two-layer SQL safety (static parser + read-only SQLite authorizer)
├── seed_db.py          # Synthetic Indian e-commerce database generator (sales.db)
├── eval.py             # Evaluation script (15 benchmark Q&A test pairs)
├── requirements.txt    # Python package dependencies
├── .env.example        # Environment variable template
├── .gitignore          # Git exclusion rules
├── README.md           # Project documentation
├── CHANGELOG.md        # Detailed project change log
├── resc_misc/          # Reference specifications and project prompts
└── tests/              # Comprehensive test suite
    ├── test_byod.py        # BYOD ingestion, profiling, dates & pipeline tests
    ├── test_sql_guard.py   # Static and database-level SQL safety tests
    ├── test_pipeline.py    # Pipeline execution, retry, and cancellation tests
    └── test_dates.py       # Calendar date reference calculation tests
`

---

## 🛠️ Tech Stack

| Layer | Technology / Library | Purpose |
|---|---|---|
| **Frontend** | Streamlit | Web UI (chat_app.py for chat, app.py for form) |
| **Backend & Pipeline** | Python 3.11+ | Core logic, regex, data transformation, prompt assembly |
| **Database** | SQLite & pandas | Relational data store, SQL execution, tabular manipulation |
| **LLM Backend** | Google Gemini SDK (google-genai) | Natural language understanding, SQL generation, plain-English summarization |
| **Safety & Security** | Custom AST/Regex parser + SQLite Authorizer | Multi-layer SQL injection prevention and write protection |
| **Testing** | pytest | Automated unit and integration testing (74 tests) |

---

## 📄 License

MIT License – feel free to use and adapt this project for your portfolio or analytics workflows.
