"""
tests/test_pipeline.py – Pipeline tests with a scripted FakeLLM.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nl2sql import NL2SQLPipeline, PipelineResult, MAX_QUESTION_LENGTH
from seed_db import seed_database


# ── FakeLLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    """
    A scripted fake LLM for testing. Accepts a list of responses that are
    returned in order on successive .chat() calls.
    """

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self._call_index = 0

    def chat(self, system: str, user: str) -> str:
        if self._call_index >= len(self._responses):
            raise RuntimeError("FakeLLM: no more scripted responses")
        resp = self._responses[self._call_index]
        self._call_index += 1
        if isinstance(resp, Exception):
            raise resp
        return resp


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def test_db(tmp_path_factory) -> str:
    db_path = str(tmp_path_factory.mktemp("pipeline") / "test_sales.db")
    seed_database(db_path)
    return db_path


# ── Test Cases ───────────────────────────────────────────────────────────────

class TestPipeline:
    """Pipeline integration tests using FakeLLM."""

    def test_happy_path(self, test_db):
        """Normal question → SQL → result → summary."""
        llm = FakeLLM([
            "```sql\nSELECT COUNT(*) AS total FROM regions\n```",
            "There are 5 regions in total.",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=True)
        result = pipe.ask("How many regions?")

        assert result.error is None
        assert result.refusal is None
        assert result.sql is not None
        assert result.rows[0][0] == 5
        assert result.summary is not None
        assert result.attempts == 1

    def test_happy_path_no_summary(self, test_db):
        """Summary disabled: should not make a second LLM call."""
        llm = FakeLLM([
            "```sql\nSELECT COUNT(*) AS total FROM regions\n```",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("How many regions?")

        assert result.error is None
        assert result.summary is None
        assert result.rows[0][0] == 5

    def test_self_correction_bad_column(self, test_db):
        """First SQL has a bad column, retry succeeds."""
        llm = FakeLLM([
            # Attempt 1: bad column
            "```sql\nSELECT nonexistent_column FROM regions\n```",
            # Attempt 2: corrected
            "```sql\nSELECT COUNT(*) AS total FROM regions\n```",
            # Summary
            "There are 5 regions.",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=True)
        result = pipe.ask("How many regions?")

        assert result.error is None
        assert result.attempts == 2
        assert result.rows[0][0] == 5

    def test_max_attempts_exhausted(self, test_db):
        """Both attempts fail → error returned."""
        llm = FakeLLM([
            "```sql\nSELECT bad_col FROM regions\n```",
            "```sql\nSELECT still_bad FROM regions\n```",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("Count regions")

        assert result.error is not None
        assert result.attempts == 2

    def test_destructive_query_no_retry(self, test_db):
        """A blocked query (DROP) must NOT be retried."""
        llm = FakeLLM([
            "```sql\nDROP TABLE regions\n```",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("Delete all regions")

        assert result.error is not None
        assert "blocked" in result.error.lower() or "Blocked" in result.error
        assert result.attempts == 1

    def test_off_topic_refusal(self, test_db):
        """LLM returns CANNOT_ANSWER."""
        llm = FakeLLM([
            "CANNOT_ANSWER: This question is not related to the sales database.",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("What is the meaning of life?")

        assert result.refusal is not None
        assert "not related" in result.refusal.lower()
        assert result.error is None
        assert result.sql is None

    def test_api_failure(self, test_db):
        """LLM raises an exception → error in result."""
        llm = FakeLLM([
            RuntimeError("API connection timeout"),
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("Total sales")

        assert result.error is not None
        assert "API" in result.error or "error" in result.error.lower()

    def test_summary_failure_still_shows_table(self, test_db):
        """Summary LLM call fails, but we still get the table."""
        llm = FakeLLM([
            "```sql\nSELECT COUNT(*) AS total FROM regions\n```",
            RuntimeError("Summary API failed"),
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=True)
        result = pipe.ask("Count regions")

        assert result.error is None
        assert result.summary is None  # summary failed
        assert result.rows[0][0] == 5  # table still there

    def test_empty_question(self, test_db):
        llm = FakeLLM([])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("")

        assert result.error is not None
        assert "enter" in result.error.lower() or "empty" in result.error.lower() or "question" in result.error.lower()

    def test_whitespace_question(self, test_db):
        llm = FakeLLM([])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("   \n\t  ")

        assert result.error is not None

    def test_overlong_question(self, test_db):
        llm = FakeLLM([])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("x" * (MAX_QUESTION_LENGTH + 1))

        assert result.error is not None
        assert "too long" in result.error.lower()

    def test_empty_result_set(self, test_db):
        """Query returns no rows — should still succeed."""
        llm = FakeLLM([
            "```sql\nSELECT * FROM regions WHERE region_id = 999\n```",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=True)
        result = pipe.ask("Region 999?")

        assert result.error is None
        assert result.rows == []
        assert result.columns == ["region_id", "region_name"]
        # No summary for empty result
        assert result.summary is None

    def test_no_sql_block_in_response(self, test_db):
        """LLM returns text without a SQL block → error."""
        llm = FakeLLM([
            "I think you should run SELECT * FROM regions",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("Show regions")

        assert result.error is not None
        assert "valid SQL" in result.error or "did not return" in result.error

    def test_stacked_statements_blocked(self, test_db):
        """Stacked statements via LLM → blocked, no retry."""
        llm = FakeLLM([
            "```sql\nSELECT 1; DROP TABLE regions\n```",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=False)
        result = pipe.ask("Do something evil")

        assert result.error is not None
        assert result.attempts == 1

    def test_pipeline_stop_execution(self, test_db):
        """Pipeline respects cancel_check and aborts execution immediately."""
        llm = FakeLLM([
            "```sql\nSELECT COUNT(*) FROM regions\n```",
        ])
        pipe = NL2SQLPipeline(test_db, llm, enable_summary=True)
        result = pipe.ask("Count regions", cancel_check=lambda: True)

        assert result.error is not None
        assert "stopped" in result.error.lower()
        assert result.sql is None
