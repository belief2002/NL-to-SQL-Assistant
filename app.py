"""
app.py – Streamlit UI for the Ask Your Data NL-to-SQL assistant.
Supports both Sample Sales Data mode and Bring-Your-Own-Data (BYOD) mode.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from data_loader import (
    IngestResult,
    TableProfile,
    build_byod_system_prompt,
    load_uploaded_files,
)
from nl2sql import GeminiLLM, NL2SQLPipeline, PipelineResult
from seed_db import seed_database

# ── Configuration ────────────────────────────────────────────────────────────

load_dotenv()

DB_PATH = os.environ.get("DB_PATH", "sales.db")
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
MAX_FILE_SIZE_MB = 50

SAMPLE_QUESTIONS = [
    "What are the total sales this year?",
    "Which region has the highest revenue?",
    "Top 5 products by sales amount",
    "Monthly sales trend for 2026",
    "What is the average order value by segment?",
    "How many orders were cancelled last quarter?",
]

SAMPLE_DATA_DESCRIPTION = """
**Indian E-Commerce Sales Database**

A synthetic dataset covering **Jan 2025 – Nov 2026** with:
- **5 regions**: North, South, West, East, Central
- **~600 customers** across Consumer, Corporate, and Small Business segments
- **20 products** in 5 categories
- **~12,000 orders** with realistic seasonal patterns

All amounts are in **₹ (INR)**.
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def ensure_sample_database() -> None:
    """Create the sample database if it doesn't exist."""
    if not os.path.exists(DB_PATH):
        with st.spinner("Creating sample database…"):
            seed_database(DB_PATH)


def get_table_preview(db_path: str, table_name: str, limit: int = 5) -> pd.DataFrame:
    """Get a preview of a database table."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(f"SELECT * FROM {table_name} LIMIT {limit}", conn)
    conn.close()
    return df


def _detect_chart_type(df: pd.DataFrame) -> Optional[str]:
    """
    Detect which chart to show:
      - 'bar': 1 text col + 1 numeric col, 2–30 rows
      - 'line': text col looks like month/date, 2–30 rows
      - None: no chart
    """
    if len(df) < 2 or len(df) > 30:
        return None

    text_cols = df.select_dtypes(include=["object"]).columns.tolist()
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()

    if len(text_cols) == 1 and len(num_cols) >= 1:
        sample_vals = df[text_cols[0]].astype(str).tolist()
        is_time = all(_looks_like_date(v) for v in sample_vals if v and v != "None")
        return "line" if is_time else "bar"

    return None


def _looks_like_date(val: str) -> bool:
    """Check if a string looks like a date or month (YYYY-MM, YYYY-MM-DD)."""
    import re
    return bool(re.match(r"^\d{4}-\d{2}(-\d{2})?$", val.strip()))


# ── Page Config & Custom CSS ─────────────────────────────────────────────────

st.set_page_config(
    page_title="Ask Your Data – NL to SQL",
    page_icon="🔍",
    layout="wide",
)

# Hide Streamlit Deploy button and footer
st.markdown(
    """
    <style>
    .stAppDeployButton,
    [data-testid="stAppDeployButton"] {
        display: none !important;
    }
    footer {
        visibility: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Session State Setup ──────────────────────────────────────────────────────

if "user_question" not in st.session_state:
    st.session_state["user_question"] = ""
if "auto_submit" not in st.session_state:
    st.session_state["auto_submit"] = False
if "byod_db_path" not in st.session_state:
    tmp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_file.close()
    st.session_state["byod_db_path"] = tmp_file.name
if "uploaded_files_cache_key" not in st.session_state:
    st.session_state["uploaded_files_cache_key"] = ""
if "ingest_result" not in st.session_state:
    st.session_state["ingest_result"] = None


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Settings")

    api_key = os.environ.get("GEMINI_API_KEY", "")

    model_name = st.text_input(
        "Model Name",
        value=DEFAULT_MODEL,
        help="Gemini model to use for SQL generation (e.g. gemini-3.8-flash, gemini-3.8-pro)",
    )

    enable_summary = st.toggle(
        "Plain-English explanation",
        value=True,
        help="Generate a natural-language summary of the results",
    )

    st.divider()

    st.subheader("📁 Data Mode")
    data_mode = st.radio(
        "Select Data Source",
        ["Sample sales data", "Upload my data"],
        index=0,
        help="Switch between sample Indian e-commerce data and your own uploaded CSV/Excel files.",
    )

    # Privacy notice
    st.info("🔒 **Privacy notice:** Column names, sample values and query results are sent to the AI provider.")

    st.divider()

    user_notes = ""
    selected_date_col_info: Optional[tuple[str, str]] = None

    if data_mode == "Sample sales data":
        st.subheader("📊 About the Data")
        st.markdown(SAMPLE_DATA_DESCRIPTION)

        st.divider()

        st.subheader("🗂️ Table Previews")
        ensure_sample_database()
        table_choice = st.selectbox(
            "Select a table",
            ["regions", "customers", "products", "orders", "order_items"],
        )
        if os.path.exists(DB_PATH):
            preview_df = get_table_preview(DB_PATH, table_choice)
            st.dataframe(preview_df, use_container_width=True)

    else:
        # Upload my data mode
        st.subheader("📤 Upload Files")
        raw_files = st.file_uploader(
            "Upload CSV or Excel files (max 50 MB each)",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            help="Files will be stored in a secure temporary SQLite database for this session.",
        )

        # Process uploads
        if raw_files:
            # Validate size
            oversized = [f.name for f in raw_files if f.size > MAX_FILE_SIZE_MB * 1024 * 1024]
            if oversized:
                st.error(f"❌ File exceeds {MAX_FILE_SIZE_MB}MB limit: {', '.join(oversized)}")
            else:
                # Key based on file names and sizes
                cache_key = "|".join(f"{f.name}:{f.size}" for f in raw_files)
                if cache_key != st.session_state["uploaded_files_cache_key"]:
                    with st.spinner("Ingesting and profiling data…"):
                        file_tuples = [(f.name, f.getvalue()) for f in raw_files]
                        result = load_uploaded_files(
                            file_tuples,
                            st.session_state["byod_db_path"],
                        )
                        st.session_state["ingest_result"] = result
                        st.session_state["uploaded_files_cache_key"] = cache_key

        ingest_res: Optional[IngestResult] = st.session_state.get("ingest_result")

        if ingest_res and ingest_res.tables:
            st.success(f"Loaded {len(ingest_res.tables)} table(s)")

            # Optional notes
            user_notes = st.text_area(
                "Notes about my data (optional)",
                placeholder="e.g. Sales means SUM(revenue); exclude returned items",
                help="Add custom business rules, definitions, or instructions for the AI assistant.",
            )

            # Date column selector
            all_date_cols: list[tuple[str, str]] = []
            for t in ingest_res.tables:
                for c in t.date_columns:
                    all_date_cols.append((t.name, c))

            if all_date_cols:
                date_options = ["None (Omit date reference)"] + [
                    f"{tbl}.{col}" for tbl, col in all_date_cols
                ]
                chosen_opt = st.selectbox(
                    "Reference Date Column",
                    options=date_options,
                    index=1,
                    help="Used to compute date ranges (this month, last quarter, etc.) using its maximum date as 'today'.",
                )
                if chosen_opt and chosen_opt != "None (Omit date reference)":
                    tbl_name, col_name = chosen_opt.split(".", 1)
                    selected_date_col_info = (tbl_name, col_name)


# ── Main Page ────────────────────────────────────────────────────────────────

st.title("🔍 Ask Your Data")
st.caption("Type a business question in plain English → get SQL, a table, and a chart.")


def _on_example_click(example_text: str) -> None:
    """Callback when an example question is clicked: set text and trigger auto-submit."""
    st.session_state["user_question"] = example_text
    st.session_state["auto_submit"] = True


# Determine active database and prompt
active_db_path = DB_PATH
custom_system_prompt: Optional[str] = None
is_ready_to_ask = True

if data_mode == "Sample sales data":
    ensure_sample_database()
    active_db_path = DB_PATH

    # Example questions
    st.markdown("**Try an example:**")
    cols = st.columns(3)
    for i, q in enumerate(SAMPLE_QUESTIONS):
        col = cols[i % 3]
        col.button(
            q,
            key=f"example_{i}",
            on_click=_on_example_click,
            args=(q,),
            use_container_width=True,
        )

else:
    # Bring-your-own-data mode
    ingest_res = st.session_state.get("ingest_result")

    if not ingest_res or not ingest_res.tables:
        st.info("👈 Please upload your CSV or Excel files in the sidebar to get started.")
        is_ready_to_ask = False
    else:
        active_db_path = st.session_state["byod_db_path"]
        custom_system_prompt = build_byod_system_prompt(
            active_db_path,
            user_notes=user_notes,
            date_col_info=selected_date_col_info,
        )

        # Show Data Profile Expander
        with st.expander("📋 Uploaded Data Profile & Preview", expanded=True):
            for t in ingest_res.tables:
                st.markdown(f"#### Table: `{t.name}` ({t.row_count:,} rows)")

                # Column summary table
                col_data = []
                for c in t.columns:
                    samples_str = ", ".join(str(s) for s in c.sample_values[:3])
                    col_data.append({
                        "Column": c.name,
                        "Type": c.dtype,
                        "Nulls": c.null_count,
                        "Sample Values": samples_str,
                    })
                st.dataframe(pd.DataFrame(col_data), use_container_width=True)

                # Sample 5 rows
                if t.sample_rows:
                    st.caption("First 5 rows:")
                    st.dataframe(pd.DataFrame(t.sample_rows), use_container_width=True)
                st.divider()


# ── Question Input ───────────────────────────────────────────────────────────

if is_ready_to_ask:
    placeholder_text = (
        "e.g., What are the top 5 products by revenue this year?"
        if data_mode == "Sample sales data"
        else "e.g., How many total records are there, grouped by category?"
    )

    question = st.text_area(
        "Your question",
        key="user_question",
        max_chars=500,
        height=80,
        placeholder=placeholder_text,
    )

    # Check if a stop was requested during execution
    was_stopped = False
    if st.session_state.get("stop_btn_running", False):
        st.session_state["auto_submit"] = False
        st.session_state["stop_btn_running"] = False
        was_stopped = True

    btn_placeholder = st.empty()

    # Always render the buttons: Ask is enabled, Stop is disabled by default
    with btn_placeholder.container():
        btn_col1, btn_col2 = st.columns([5, 1])
        with btn_col1:
            ask_clicked = st.button(
                "🚀 Ask",
                type="primary",
                use_container_width=True,
                disabled=False,
                key="ask_btn_idle",
            )
        with btn_col2:
            st.button(
                "⏹️ Stop",
                type="secondary",
                use_container_width=True,
                disabled=True,
                key="stop_btn_idle",
                help="Stop is only enabled while a query is running",
            )

    if was_stopped:
        st.warning("⏹️ Execution stopped by user.")
        should_run = False
    else:
        # Trigger execution if either user clicked 'Ask' OR clicked an example question
        should_run = ask_clicked or st.session_state.get("auto_submit", False)
    
    st.session_state["auto_submit"] = False

    # ── Process Question ─────────────────────────────────────────────────────

    if should_run and question:
        if not api_key:
            st.error("🔑 GEMINI_API_KEY is not set. Please add your API key to the `.env` file.")
            st.stop()

        # Switch buttons to running state: Ask disabled, Stop enabled
        with btn_placeholder.container():
            btn_col1, btn_col2 = st.columns([5, 1])
            with btn_col1:
                st.button(
                    "🚀 Ask",
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="ask_btn_running",
                )
            with btn_col2:
                st.button(
                    "⏹️ Stop",
                    type="secondary",
                    use_container_width=True,
                    disabled=False,
                    key="stop_btn_running",
                    help="Stop ongoing execution",
                )

        with st.spinner("Thinking…"):
            try:
                llm = GeminiLLM(api_key=api_key, model=model_name)
                pipeline = NL2SQLPipeline(
                    db_path=active_db_path,
                    llm=llm,
                    enable_summary=enable_summary,
                    system_prompt=custom_system_prompt,
                )
                result: PipelineResult = pipeline.ask(question)
                
            except Exception as e:
                st.error(f"❌ Unexpected error: {e}")
                st.stop()

        # Restore buttons back to idle state: Ask enabled, Stop disabled
        with btn_placeholder.container():
            btn_col1, btn_col2 = st.columns([5, 1])
            with btn_col1:
                st.button(
                    "🚀 Ask",
                    type="primary",
                    use_container_width=True,
                    disabled=False,
                    key="ask_btn_done",
                )
            with btn_col2:
                st.button(
                    "⏹️ Stop",
                    type="secondary",
                    use_container_width=True,
                    disabled=True,
                    key="stop_btn_done",
                    help="Stop is only enabled while a query is running",
                )

        # ── Display Result ───────────────────────────────────────────────────

        # Refusal
        if result.refusal:
            st.warning(f"🤷 {result.refusal}")
            st.stop()

        # Error
        if result.error:
            st.error(f"❌ {result.error}")
            if result.sql:
                with st.expander("Generated SQL"):
                    st.code(result.sql, language="sql")
            st.stop()

        # Success!
        if result.summary:
            st.success(result.summary)

        if result.sql:
            with st.expander("📝 Generated SQL", expanded=False):
                st.code(result.sql, language="sql")

        # Table
        if result.columns and result.rows:
            df = pd.DataFrame(result.rows, columns=result.columns)
            st.dataframe(df, use_container_width=True)

            row_count_text = f"{len(df):,} row{'s' if len(df) != 1 else ''}"
            if result.truncated:
                row_count_text += " (truncated to 500)"
            st.caption(row_count_text)

            # Chart
            chart_type = _detect_chart_type(df)
            if chart_type:
                text_col = df.select_dtypes(include=["object"]).columns[0]
                num_col = df.select_dtypes(include=["number"]).columns[0]

                chart_df = df.set_index(text_col)[[num_col]]
                if chart_type == "line":
                    st.line_chart(chart_df)
                else:
                    st.bar_chart(chart_df)

            # Download CSV
            csv = df.to_csv(index=False)
            st.download_button(
                "📥 Download CSV",
                data=csv,
                file_name="query_result.csv",
                mime="text/csv",
            )

        elif result.columns and not result.rows:
            st.info("The query returned no rows.")

    elif should_run and not question:
        st.warning("Please enter a question.")
