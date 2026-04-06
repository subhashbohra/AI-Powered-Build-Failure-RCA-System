"""Tests for Ollama client (mock-based, no running Ollama needed)."""

import json
import pytest
from unittest.mock import patch, MagicMock
from src.ollama_client import OllamaClient


class TestOllamaClient:
    def setup_method(self):
        self.client = OllamaClient(
            host="http://localhost:11434",
            model="gemma3:27b-it-qat",
            timeout=30,
            max_retries=2,
        )

    @patch("src.ollama_client.requests.get")
    def test_health_check_success(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"models": [{"name": "gemma3:27b-it-qat"}]},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        assert self.client.health_check() is True

    @patch("src.ollama_client.requests.get")
    def test_health_check_model_missing(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"models": [{"name": "llama3:8b"}]},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        assert self.client.health_check() is False

    @patch("src.ollama_client.requests.get")
    def test_health_check_connection_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.ConnectionError("refused")
        assert self.client.health_check() is False

    @patch("src.ollama_client.requests.post")
    def test_chat_success(self, mock_post):
        expected_response = {
            "message": {"content": '{"root_cause": "test failure"}'},
            "total_duration": 5_000_000_000,
            "eval_count": 100,
        }
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: expected_response,
        )
        mock_post.return_value.raise_for_status = MagicMock()

        messages = [{"role": "user", "content": "test"}]
        result = self.client.chat(messages)

        assert result["message"]["content"] == '{"root_cause": "test failure"}'
        mock_post.assert_called_once()

    @patch("src.ollama_client.requests.post")
    @patch("src.ollama_client.time.sleep")
    def test_chat_retries_on_timeout(self, mock_sleep, mock_post):
        import requests
        mock_post.side_effect = requests.Timeout("timed out")

        messages = [{"role": "user", "content": "test"}]
        with pytest.raises(RuntimeError, match="failed after 2 attempts"):
            self.client.chat(messages)

        assert mock_post.call_count == 2

    @patch("src.ollama_client.requests.post")
    @patch("src.ollama_client.time.sleep")
    def test_chat_retries_then_succeeds(self, mock_sleep, mock_post):
        import requests
        success_response = MagicMock(
            status_code=200,
            json=lambda: {
                "message": {"content": "ok"},
                "total_duration": 1_000_000_000,
            },
        )
        success_response.raise_for_status = MagicMock()

        mock_post.side_effect = [
            requests.ConnectionError("connection refused"),
            success_response,
        ]

        messages = [{"role": "user", "content": "test"}]
        result = self.client.chat(messages)
        assert result["message"]["content"] == "ok"
        assert mock_post.call_count == 2


class TestParseRcaResponse:
    def setup_method(self):
        self.client = OllamaClient()

    def test_valid_json(self):
        raw = json.dumps({
            "root_cause": "NullPointerException in UserService",
            "category": "compilation_error",
            "confidence": "high",
        })
        result = self.client.parse_rca_response(raw)
        assert result["root_cause"] == "NullPointerException in UserService"
        assert result["category"] == "compilation_error"

    def test_json_in_code_fence(self):
        raw = '```json\n{"root_cause": "test", "category": "unknown"}\n```'
        result = self.client.parse_rca_response(raw)
        assert result["root_cause"] == "test"

    def test_invalid_json_returns_fallback(self):
        raw = "This is not valid JSON at all"
        result = self.client.parse_rca_response(raw)
        assert result["category"] == "unknown"
        assert result["confidence"] == "low"
        assert "manual review" in result["root_cause"].lower()

    def test_empty_string(self):
        result = self.client.parse_rca_response("")
        assert result["category"] == "unknown"

    def test_partial_json(self):
        raw = '{"root_cause": "test"'  # Missing closing brace
        result = self.client.parse_rca_response(raw)
        assert result["category"] == "unknown"
