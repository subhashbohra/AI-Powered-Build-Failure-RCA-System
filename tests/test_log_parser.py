"""Tests for log parsing utilities."""

import pytest
from src.log_parser import (
    extract_error_context,
    trim_to_token_limit,
    format_jobs_summary,
    parse_job_info,
    JobInfo,
)


class TestExtractErrorContext:
    def test_finds_error_lines(self):
        log = """Step 1: Setup
Everything is fine here.
Step 2: Compile
ERROR: cannot find symbol
  symbol:   class FooBar
  location: class com.example.Main
Step 3: Cleanup
Done."""
        result = extract_error_context(log, context_lines=2)
        assert any("cannot find symbol" in line for line in result)

    def test_finds_test_failures(self):
        log = """Running tests...
Tests run: 42, Failures: 3, Errors: 0, Skipped: 1
FAILED test_user_login
FAILED test_payment_process
FAILED test_email_send"""
        result = extract_error_context(log, context_lines=1)
        assert any("Failures: 3" in line for line in result)
        assert any("test_user_login" in line for line in result)

    def test_empty_on_clean_logs(self):
        log = """Step 1: Setup
All good.
Step 2: Build
Build successful.
Step 3: Test
All 100 tests passed."""
        result = extract_error_context(log, context_lines=2)
        assert result == []

    def test_finds_oom(self):
        log = "Process killed: OutOfMemoryError"
        result = extract_error_context(log, context_lines=0)
        assert any("OutOfMemoryError" in line for line in result)

    def test_skips_debug_lines(self):
        log = """##[debug] some debug info
##[group] group start
ERROR: real error here
##[endgroup]"""
        result = extract_error_context(log, context_lines=0)
        matched = [l for l in result if l.strip() and "gap" not in l]
        assert len(matched) == 1
        assert "real error here" in matched[0]

    def test_finds_build_failure(self):
        log = """[INFO] Tests run: 3, Failures: 0, Errors: 0, Skipped: 0
[INFO] BUILD FAILURE
[INFO] Total time: 01:22 min
[ERROR] Failed to execute goal"""
        result = extract_error_context(log, context_lines=1)
        assert any("BUILD FAILURE" in line for line in result)

    def test_finds_npm_error(self):
        log = """npm warn deprecated package@1.0.0
npm ERR! code ENOTFOUND
npm ERR! errno ENOTFOUND
npm ERR! network request to https://registry.npmjs.org failed"""
        result = extract_error_context(log, context_lines=1)
        assert any("ERR!" in line for line in result)


class TestTrimToTokenLimit:
    def test_short_text_unchanged(self):
        text = "short text"
        result = trim_to_token_limit(text, max_tokens=1000)
        assert result == text

    def test_long_text_trimmed(self):
        text = "x" * 100000
        result = trim_to_token_limit(text, max_tokens=100)
        assert len(result) < len(text)
        assert "TRIMMED" in result

    def test_preserves_head_and_tail(self):
        text = "HEAD_MARKER " + "x" * 100000 + " TAIL_MARKER"
        result = trim_to_token_limit(text, max_tokens=500)
        assert result.startswith("HEAD_MARKER")
        assert result.endswith("TAIL_MARKER")

    def test_exact_limit_unchanged(self):
        text = "a" * 400  # 400 chars = 100 tokens
        result = trim_to_token_limit(text, max_tokens=100)
        assert result == text


class TestParseJobInfo:
    def test_basic_parsing(self):
        data = {
            "id": 123,
            "name": "build",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T00:25:00Z",
            "steps": [
                {"name": "Setup", "conclusion": "success"},
                {"name": "Build", "conclusion": "failure"},
                {"name": "Test", "conclusion": "skipped"},
            ],
        }
        job = parse_job_info(data)
        assert job.job_id == 123
        assert job.name == "build"
        assert job.conclusion == "failure"
        assert job.duration_minutes == 25.0
        assert len(job.failed_steps) == 1
        assert job.failed_steps[0]["name"] == "Build"

    def test_missing_timestamps(self):
        data = {
            "id": 456,
            "name": "test",
            "status": "completed",
            "conclusion": "success",
            "started_at": None,
            "completed_at": None,
            "steps": [],
        }
        job = parse_job_info(data)
        assert job.duration_minutes == 0.0

    def test_no_failed_steps(self):
        data = {
            "id": 789,
            "name": "lint",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T00:02:00Z",
            "steps": [
                {"name": "Run lint", "conclusion": "success"},
            ],
        }
        job = parse_job_info(data)
        assert job.failed_steps == []
        assert job.duration_minutes == 2.0


class TestFormatJobsSummary:
    def test_formats_correctly(self):
        jobs = [
            JobInfo(
                job_id=1, name="compile", status="completed",
                conclusion="failure", started_at="", completed_at="",
                duration_minutes=12.5,
                steps=[], failed_steps=[{"name": "mvn compile"}],
            ),
            JobInfo(
                job_id=2, name="test", status="completed",
                conclusion="success", started_at="", completed_at="",
                duration_minutes=8.0,
                steps=[], failed_steps=[],
            ),
        ]
        result = format_jobs_summary(jobs)
        assert "compile" in result
        assert "FAILED" in result
        assert "12.5 min" in result
        assert "mvn compile" in result

    def test_empty_jobs(self):
        result = format_jobs_summary([])
        assert result == ""
