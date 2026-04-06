"""Tests for RCA prompt builder."""

from src.rca_prompt import build_rca_prompt, SYSTEM_PROMPT


class TestBuildRcaPrompt:
    def test_returns_two_messages(self):
        messages = build_rca_prompt(
            workflow_name="release_build",
            repo="myorg/myrepo",
            branch="main",
            sha="abc123def",
            actor="subhash",
            run_url="https://github.com/myorg/myrepo/actions/runs/999",
            started_at="2025-06-01T10:00:00Z",
            updated_at="2025-06-01T10:35:00Z",
            jobs_summary="- build [FAILED] — 35.0 min",
            logs_content="ERROR: NullPointerException at Main.java:42",
            threshold_minutes=20,
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_system_prompt_contains_json_format(self):
        assert "root_cause" in SYSTEM_PROMPT
        assert "category" in SYSTEM_PROMPT
        assert "confidence" in SYSTEM_PROMPT

    def test_user_prompt_includes_metadata(self):
        messages = build_rca_prompt(
            workflow_name="snapshot_build",
            repo="fabric2/core-service",
            branch="feature/auth-fix",
            sha="deadbeef",
            actor="developer1",
            run_url="https://github.com/fabric2/core-service/actions/runs/555",
            started_at="2025-06-01T08:00:00Z",
            updated_at="2025-06-01T08:22:00Z",
            jobs_summary="- unit-tests [FAILED]",
            logs_content="FAILED test_auth_token_validation",
            threshold_minutes=20,
        )
        user_content = messages[1]["content"]
        assert "snapshot_build" in user_content
        assert "fabric2/core-service" in user_content
        assert "feature/auth-fix" in user_content
        assert "deadbeef" in user_content
        assert "test_auth_token_validation" in user_content
        assert "20 minutes" in user_content

    def test_logs_content_included(self):
        long_log = "ERROR: " + "x" * 5000
        messages = build_rca_prompt(
            workflow_name="build",
            repo="org/repo",
            branch="main",
            sha="aaa",
            actor="user",
            run_url="",
            started_at="",
            updated_at="",
            jobs_summary="",
            logs_content=long_log,
        )
        assert long_log in messages[1]["content"]
