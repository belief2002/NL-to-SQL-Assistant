# 🔍 Ask Your Data — Natural Language to SQL Assistant

> Turn plain-English business questions into SQL queries, executed safely on a live database, with instant tables and charts.

A portfolio project built for a **Data Analyst resume**, demonstrating prompt engineering, SQL safety, full-stack Python, and LLM integration.

---

## 🏗️ Architecture

```
┌──────────────┐    ┌────────────────┐    ┌──────────┐    ┌──────────────┐    ┌─────────┐    ┌────────────┐
│   Question   │───▶│ Prompt Builder │───▶│   LLM    │───▶│  SQL Guard   │───▶│ SQLite  │───▶│  Summary   │
│  (plain EN)  │    │ (schema+rules) │    │ (Google  │    │ (validate +  │    │ (read-  │    │ (LLM call) │
│              │    │                │    │  Gemini) │    │  authorize)  │    │  only)  │    │            │
└──────────────┘    └────────────────┘    └──────────┘    └──────────────┘    └─────────┘    └────────────┘
                                                                                  │
                                                                                  ▼
                                                                          ┌──────────────┐
                                                                          │  Streamlit   │
                                                                          │  UI: table,  │
                                                                          │  chart, CSV  │
                                                                          └──────────────┘
```

## ✨ Features

- **Natural language → SQL**: Ask questions in plain English, get accurate SQL
- **Bring-Your-Own-Data (BYOD) mode**: Upload multiple CSV/XLSX files (up to 50 MB each), query your own datasets on the fly
- **Automated Data Profiling**: Table row counts, inferred types, null counts, sample previews
- **Smart Data Sanitization**: `snake_case` column deduplication, mixed date format detection to ISO `YYYY-MM-DD`, and currency string parsing
- **Two-layer safety**: Static SQL validation + database-level authorizer & read-only mode
- **Dynamic Prompts**: Schema, detected keys, categorical values (<= 30 distinct values), and dynamic date references injected on the fly
- **User Notes for Custom Rules**: Provide custom domain context and business rules directly in the UI
- **Auto-correction**: If the first SQL fails, the error is fed back to the LLM for a retry
- **Plain-English summaries**: Results explained in 1–3 sentences with ₹ values
- **Automatic charts**: Bar charts for categorical data, line charts for time trends
- **CSV download**: Export any result with one click

---

## 🚀 Setup

### Prerequisites
- Python 3.11+
- A [Google Gemini API key](https://aistudio.google.com/app/apikey)

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/NL_to_SQL_Assistant.git
cd NL_to_SQL_Assistant

# Create a virtual environment
python -m venv venv
source venv/bin/activate    # Linux/Mac
venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### Running the App

```bash
# The database is created automatically on first run
streamlit run app.py
```

### Running Tests

```bash
pytest tests/ -v
```

### Running the Evaluation

```bash
python eval.py
```

---

## 💬 Example Questions & Generated SQL

### 1. "What are the total sales this year?"
```sql
SELECT ROUND(SUM(oi.sales_amount), 2) AS total_sales
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
WHERE o.status NOT IN ('Cancelled', 'Returned')
  AND o.order_date >= '2026-01-01'
```

### 2. "Which region has the highest revenue?"
```sql
SELECT r.region_name, ROUND(SUM(oi.sales_amount), 2) AS revenue
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
JOIN customers c ON o.customer_id = c.customer_id
JOIN regions r ON c.region_id = r.region_id
WHERE o.status NOT IN ('Cancelled', 'Returned')
GROUP BY r.region_name
ORDER BY revenue DESC
LIMIT 1
```

### 3. "Top 5 products by sales amount"
```sql
SELECT p.product_name, ROUND(SUM(oi.sales_amount), 2) AS total_sales
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
JOIN products p ON oi.product_id = p.product_id
WHERE o.status NOT IN ('Cancelled', 'Returned')
GROUP BY p.product_name
ORDER BY total_sales DESC
LIMIT 5
```

### 4. "Monthly sales trend for 2026"
```sql
SELECT strftime('%Y-%m', o.order_date) AS month, ROUND(SUM(oi.sales_amount), 2) AS sales
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
WHERE o.status NOT IN ('Cancelled', 'Returned')
  AND o.order_date >= '2026-01-01'
GROUP BY month
ORDER BY month
```

### 5. "What is the average order value by segment?"
```sql
SELECT c.segment,
       ROUND(SUM(oi.sales_amount) / COUNT(DISTINCT o.order_id), 2) AS aov
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
JOIN customers c ON o.customer_id = c.customer_id
WHERE o.status NOT IN ('Cancelled', 'Returned')
GROUP BY c.segment
ORDER BY aov DESC
```

### 6. "How many orders were cancelled last quarter?"
```sql
SELECT COUNT(*) AS cancelled_orders
FROM orders
WHERE status = 'Cancelled'
  AND order_date BETWEEN '2026-07-01' AND '2026-09-30'
```

### 7. "Total revenue from Electronics category"
```sql
SELECT ROUND(SUM(oi.sales_amount), 2) AS electronics_revenue
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
JOIN products p ON oi.product_id = p.product_id
WHERE o.status NOT IN ('Cancelled', 'Returned')
  AND p.category = 'Electronics'
```

### 8. "Top 3 cities by number of customers"
```sql
SELECT c.city, COUNT(*) AS customer_count
FROM customers c
GROUP BY c.city
ORDER BY customer_count DESC
LIMIT 3
```

---

## 🛡️ Safety Design

### Layer 1: Static SQL Validation (`sql_guard.py`)
- Allows only single `SELECT` or `WITH ... SELECT` statements
- Strips string literals and comments before scanning for blocked keywords
- Blocks: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `DETACH`, `PRAGMA`, `VACUUM`, `REINDEX`, transactions, `load_extension`
- Detects stacked statements (`;` followed by another statement)
- **Blocked queries are never retried**

### Layer 2: Database-Level Protection (`sql_guard.py`)
- SQLite connection opened in **read-only mode** (`?mode=ro`)
- **Authorizer callback** restricts reads to known tables only (blocks `sqlite_master`)
- **Progress handler** aborts runaway queries after ~200M VM instructions
- Results capped at **500 rows** with a truncation flag

### Defense in Depth
Even if Layer 1 is bypassed, Layer 2 prevents writes, schema access, and resource exhaustion at the database level.

---

## 📊 Screenshots

> *Add screenshots of the app here after running it*

| Main Page | Query Result | Chart |
|-----------|-------------|-------|
| ![Main](screenshots/main.png) | ![Result](screenshots/result.png) | ![Chart](screenshots/chart.png) |

---

## ⚠️ Limitations & Next Steps

### Current Limitations
- **No conversation memory**: Each question is independent (no "and what about last year?")
- **SQLite only**: The sample database uses SQLite; production data lives in Postgres/MySQL
- **Single-turn**: Cannot ask follow-up questions that reference previous answers
- **English only**: Questions must be in English

### Planned Improvements
- 🔄 **Conversation memory** – Let users ask follow-up questions
- 🐘 **Postgres / MySQL support** – Connect to production databases
- 📊 **Evaluation set expansion** – More edge-case questions for accuracy testing
- 🌐 **Multi-language support** – Accept questions in other languages
- 📈 **Query explanation** – Show step-by-step SQL breakdown

---

## 📁 Project Structure

```
NL_to_SQL_Assistant/
├── app.py              # Streamlit web application (Sample & BYOD modes)
├── data_loader.py      # BYOD ingestion, sanitization, profiling & dynamic prompt generator
├── nl2sql.py           # Core NL→SQL pipeline (prompt builder, LLM, execution)
├── sql_guard.py        # Two-layer SQL safety (validation + authorizer)
├── seed_db.py          # Synthetic database generator
├── eval.py             # Accuracy evaluation (15 Q&A pairs)
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
├── .gitignore          # Git ignore rules
├── README.md           # This file
├── CHANGELOG.md        # Project change log
├── resc_misc/          # Reference materials
│   └── NL_to_SQL_Project_Prompts.docx
└── tests/
    ├── test_byod.py        # 12 BYOD ingestion, profiling, dates & pipeline tests
    ├── test_sql_guard.py   # 40 SQL guard tests
    ├── test_pipeline.py    # 14 Pipeline tests with FakeLLM
    └── test_dates.py       # 7 Date reference computation tests
```

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|-----------|
| Frontend  | Streamlit |
| Backend   | Python 3.11+ |
| Database  | SQLite |
| LLM       | Google Gemini (`gemini-3.8-flash` / configurable) |
| SDK       | `google-genai` |
| Data      | pandas |
| Testing   | pytest |

---

## 📄 License

MIT License – feel free to use this project in your portfolio.
