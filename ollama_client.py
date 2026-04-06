"""
Ollama API client for sending RCA requests to the Gemma model.
"""

import json
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


class OllamaClient:
    """Client for interacting with the Ollama /api/chat endpoint."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "gemma3:27b-it-qat",
        timeout: int = 180,
        max_retries: int = 3,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def health_check(self) -> bool:
        """Check if Ollama server is reachable and the model is loaded."""
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=10)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            logger.info("Available models: %s", model_names)

            # Check if our target model is available (match by prefix)
            model_base = self.model.split(":")[0]
            available = any(model_base in name for name in model_names)
            if not available:
                logger.warning(
                    "Model '%s' not found in available models: %s",
                    self.model,
                    model_names,
                )
            return available
        except requests.RequestException as e:
            logger.error("Ollama health check failed: %s", e)
            return False

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        num_ctx: int = 32768,
    ) -> dict[str, Any]:
        """
        Send a chat request to Ollama and return the parsed response.

        Returns the full response dict from Ollama, including:
        - message.content: the model's text response
        - total_duration: inference time in nanoseconds
        - eval_count: number of tokens generated
        """
        payload = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }

        url = f"{self.host}/api/chat"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "Sending RCA request to Ollama (attempt %d/%d, model=%s)",
                    attempt,
                    self.max_retries,
                    self.model,
                )
                resp = requests.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                result = resp.json()

                content = result.get("message", {}).get("content", "")
                duration_ns = result.get("total_duration", 0)
                duration_s = duration_ns / 1_000_000_000

                logger.info(
                    "RCA response received: %d chars in %.1f seconds",
                    len(content),
                    duration_s,
                )
                return result

            except requests.Timeout:
                logger.warning(
                    "Ollama request timed out (attempt %d/%d, timeout=%ds)",
                    attempt,
                    self.max_retries,
                    self.timeout,
                )
                last_error = TimeoutError(f"Ollama timed out after {self.timeout}s")

            except requests.RequestException as e:
                logger.warning(
                    "Ollama request failed (attempt %d/%d): %s",
                    attempt,
                    self.max_retries,
                    e,
                )
                last_error = e

            if attempt < self.max_retries:
                backoff = 2 ** attempt
                logger.info("Retrying in %d seconds...", backoff)
                time.sleep(backoff)

        raise RuntimeError(
            f"Ollama request failed after {self.max_retries} attempts: {last_error}"
        )

    def parse_rca_response(self, raw_content: str) -> dict[str, Any]:
        """
        Parse the model's JSON response into a structured RCA dict.
        Handles cases where the model wraps JSON in markdown code blocks.
        """
        content = raw_content.strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last lines (``` markers)
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse RCA JSON: %s", e)
            logger.debug("Raw content: %s", content[:500])
            # Return a fallback structure
            return {
                "root_cause": "Unable to parse model response — manual review needed",
                "category": "unknown",
                "failed_components": [],
                "build_time_analysis": {
                    "total_duration_minutes": None,
                    "exceeded_threshold": False,
                    "slowest_step": "unknown",
                    "slowest_step_duration_minutes": None,
                },
                "error_messages": [content[:500]],
                "recommendation": "Review build logs manually",
                "confidence": "low",
                "additional_notes": f"Model response was not valid JSON: {e}",
                "_raw_response": content[:2000],
            }
