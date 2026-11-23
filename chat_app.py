"""
chat_app.py – GPT-like chat interface for the Ask Your Data NL-to-SQL assistant.

Run with:
    streamlit run chat_app.py
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from data_loader import (
    IngestResult,
    build_byod_system_prompt,
    load_uploaded_files,
)
from nl2sql import GeminiLLM, NL2SQLPipeline, PipelineResult
from seed_db import seed_database

# ── Configuration ─────────────────────────────────────────────────────────────

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

# ── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Ask Your Data – Chat",
    page_icon="💬",
    layout="wide",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    .stAppDeployButton,
    [data-testid="stAppDeployButton"] { display: none !important; }
    footer { visibility: hidden; }
    .main .block-container { padding-bottom: 1rem; }
    [data-testid="stChatMessageContent"] p { margin: 0; }
    div[data-testid="stHorizontalBlock"] button[kind="secondary"] {
        border-radius: 20px; font-size: 0.8rem; padding: 4px 12px;
    }
    details summary { font-size: 0.85rem; color: #888; }
    [data-testid="stDataFrame"] { margin-top: 0.25rem; margin-bottom: 0.25rem; }
    [data-testid="stChatMessageContent"] .stAlert { padding: 0.5rem 0.75rem; margin-bottom: 0.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _looks_like_date(val: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}(-\d{2})?$", val.strip()))


def _detect_chart_type(df: pd.DataFrame) -> Optional[str]:
    """Return 'bar', 'line', or None based on DataFrame shape."""
    if len(df) < 2 or len(df) > 30:
        return None
    text_cols = df.select_dtypes(include=["object"]).columns.tolist()
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    if len(text_cols) == 1 and len(num_cols) >= 1:
        sample_vals = df[text_cols[0]].astype(str).tolist()
        is_time = all(_looks_like_date(v) for v in sample_vals if v and v != "None")
        return "line" if is_time else "bar"
    return None


# ── Session State Initialisation ──────────────────────────────────────────────

def _init_session_state() -> None:
    """Initialise all required session state keys once."""
    if "chat_messages" not in st.session_state:
        # Each entry: {"role": "user"|"assistant", "content": str, "result": PipelineResult|None}
        st.session_state["chat_messages"] = []
    if "pending_question" not in st.session_state:
        st.session_state["pending_question"] = None
    if "byod_db_path" not in st.session_state:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_file.close()
        st.session_state["byod_db_path"] = tmp_file.name
    if "uploaded_files_cache_key" not in st.session_state:
        st.session_state["uploaded_files_cache_key"] = ""
    if "ingest_result" not in st.session_state:
        st.session_state["ingest_result"] = None


_init_session_state()


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Settings")

    api_key = os.environ.get("GEMINI_API_KEY", "")

    model_name = st.text_input(
        "Model Name",
        value=DEFAULT_MODEL,
        help="Gemini model to use (e.g. gemini-3.8-flash, gemini-3.8-pro)",
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
        st.subheader("📤 Upload Files")
        raw_files = st.file_uploader(
            "Upload CSV or Excel files (max 50 MB each)",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            help="Files will be stored in a secure temporary SQLite database for this session.",
        )

        if raw_files:
            oversized = [f.name for f in raw_files if f.size > MAX_FILE_SIZE_MB * 1024 * 1024]
            if oversized:
                st.error(f"❌ File exceeds {MAX_FILE_SIZE_MB}MB limit: {', '.join(oversized)}")
            else:
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

            user_notes = st.text_area(
                "Notes about my data (optional)",
                placeholder="e.g. Sales means SUM(revenue); exclude returned items",
                help="Add custom business rules, definitions, or instructions for the AI assistant.",
            )

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
                    help="Used to compute date ranges using the max date as 'today'.",
                )
                if chosen_opt and chosen_opt != "None (Omit date reference)":
                    tbl_name, col_name = chosen_opt.split(".", 1)
                    selected_date_col_info = (tbl_name, col_name)

    st.divider()
    if st.button("🗑️ Clear Chat", use_container_width=True, type="secondary"):
        st.session_state["chat_messages"] = []
        st.session_state["pending_question"] = None
        st.rerun()


# ── Determine Active DB & Readiness ───────────────────────────────────────────

active_db_path = DB_PATH
custom_system_prompt: Optional[str] = None
is_ready_to_ask = True

if data_mode == "Sample sales data":
    ensure_sample_database()
    active_db_path = DB_PATH
else:
    ingest_res = st.session_state.get("ingest_result")
    if not ingest_res or not ingest_res.tables:
        is_ready_to_ask = False
    else:
        active_db_path = st.session_state["byod_db_path"]
        custom_system_prompt = build_byod_system_prompt(
            active_db_path,
            user_notes=user_notes,
            date_col_info=selected_date_col_info,
        )


# ── Render a single assistant result inside a chat bubble ─────────────────────

def _render_result(result: PipelineResult) -> None:
    """Render SQL, table, chart, download, summary inside any st.chat_message block."""

    if result.refusal:
        st.warning(f"🤷 {result.refusal}")
        return

    if result.error:
        st.error(f"❌ {result.error}")
        if result.sql:
            with st.expander("Generated SQL"):
                st.code(result.sql, language="sql")
        return

    if result.summary:
        st.success(result.summary)

    if result.sql:
        with st.expander("📝 View SQL", expanded=False):
            st.code(result.sql, language="sql")

    if result.columns and result.rows:
        df = pd.DataFrame(result.rows, columns=result.columns)
        st.dataframe(df, use_container_width=True)

        row_count_text = f"{len(df):,} row{'s' if len(df) != 1 else ''}"
        if result.truncated:
            row_count_text += " (truncated to 500)"
        st.caption(row_count_text)

        chart_type = _detect_chart_type(df)
        if chart_type:
            text_col = df.select_dtypes(include=["object"]).columns[0]
            num_col = df.select_dtypes(include=["number"]).columns[0]
            chart_df = df.set_index(text_col)[[num_col]]
            if chart_type == "line":
                st.line_chart(chart_df)
            else:
                st.bar_chart(chart_df)

        csv = df.to_csv(index=False)
        st.download_button(
            "📥 Download CSV",
            data=csv,
            file_name="query_result.csv",
            mime="text/csv",
            key=f"dl_{id(result)}",
        )

    elif result.columns and not result.rows:
        st.info("The query returned no rows.")


# ── Helper: run the pipeline and return a result ──────────────────────────────

def _run_pipeline(question: str) -> PipelineResult:
    """Instantiate the pipeline and ask a question. Returns PipelineResult."""
    try:
        llm = GeminiLLM(api_key=api_key, model=model_name)
        pipeline = NL2SQLPipeline(
            db_path=active_db_path,
            llm=llm,
            enable_summary=enable_summary,
            system_prompt=custom_system_prompt,
        )
        return pipeline.ask(question)
    except Exception as e:
        return PipelineResult(error=f"Unexpected error: {e}")


# ── Main Chat UI ───────────────────────────────────────────────────────────────

st.title("💬 Ask Your Data")
st.caption("Chat with your data in plain English — powered by Gemini + SQL.")

# BYOD Profile Expander
if data_mode == "Upload my data":
    ingest_res = st.session_state.get("ingest_result")
    if not ingest_res or not ingest_res.tables:
        st.info("👈 Please upload your CSV or Excel files in the sidebar to get started.")
    else:
        with st.expander("📋 Uploaded Data Profile & Preview", expanded=False):
            for t in ingest_res.tables:
                st.markdown(f"#### Table: `{t.name}` ({t.row_count:,} rows)")
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
                if t.sample_rows:
                    st.caption("First 5 rows:")
                    st.dataframe(pd.DataFrame(t.sample_rows), use_container_width=True)
                st.divider()

# Example question chips (only when chat is empty + sample mode)
if data_mode == "Sample sales data" and not st.session_state["chat_messages"]:
    st.markdown("**✨ Try an example question:**")
    cols = st.columns(3)
    for i, q in enumerate(SAMPLE_QUESTIONS):
        if cols[i % 3].button(q, key=f"chip_{i}", use_container_width=True):
            st.session_state["pending_question"] = q
            st.rerun()

# ── Replay existing chat history ───────────────────────────────────────────────
for msg in st.session_state["chat_messages"]:
    with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "🤖"):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            if msg.get("result") is not None:
                _render_result(msg["result"])
            else:
                st.markdown(msg["content"])

# ── Process a question that came from a chip click ────────────────────────────
if st.session_state.get("pending_question"):
    question = st.session_state.pop("pending_question")

    with st.chat_message("user", avatar="🧑"):
        st.markdown(question)
    st.session_state["chat_messages"].append(
        {"role": "user", "content": question, "result": None}
    )

    if not api_key:
        err_result = PipelineResult(error="🔑 GEMINI_API_KEY is not set. Please add your API key to the `.env` file.")
        with st.chat_message("assistant", avatar="🤖"):
            _render_result(err_result)
        st.session_state["chat_messages"].append(
            {"role": "assistant", "content": err_result.error, "result": err_result}
        )
    elif is_ready_to_ask:
        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Thinking…"):
                result = _run_pipeline(question)
            _render_result(result)
        st.session_state["chat_messages"].append(
            {"role": "assistant", "content": result.summary or result.error or "Done.", "result": result}
        )

    st.rerun()

# ── Chat Input at the bottom ──────────────────────────────────────────────────
if is_ready_to_ask:
    placeholder = (
        "Ask a question about your sales data…"
        if data_mode == "Sample sales data"
        else "Ask a question about your uploaded data…"
    )
    user_input = st.chat_input(placeholder, max_chars=500)

    if user_input:
        question = user_input.strip()

        with st.chat_message("user", avatar="🧑"):
            st.markdown(question)
        st.session_state["chat_messages"].append(
            {"role": "user", "content": question, "result": None}
        )

        if not api_key:
            err_result = PipelineResult(error="🔑 GEMINI_API_KEY is not set. Please add your API key to the `.env` file.")
            with st.chat_message("assistant", avatar="🤖"):
                _render_result(err_result)
            st.session_state["chat_messages"].append(
                {"role": "assistant", "content": err_result.error, "result": err_result}
            )
        else:
            with st.chat_message("assistant", avatar="🤖"):
                with st.spinner("Thinking…"):
                    result = _run_pipeline(question)
                _render_result(result)
            st.session_state["chat_messages"].append(
                {"role": "assistant", "content": result.summary or result.error or "Done.", "result": result}
            )

        st.rerun()
