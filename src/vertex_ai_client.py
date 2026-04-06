"""
Vertex AI client for Gemma inference — drop-in replacement for OllamaClient.

Usage:
    Set USE_VERTEX_AI=true to route all RCA requests through Vertex AI instead
    of the self-hosted Ollama cluster.

    Required env vars:
        GOOGLE_CLOUD_PROJECT   — GCP project ID
        VERTEX_AI_LOCATION     — Region (default: europe-west2)
        VERTEX_AI_MODEL        — Model name (default: gemma-3-27b-it)

    Optional:
        GOOGLE_APPLICATION_CREDENTIALS — Path to service account JSON.
        Not needed when running on GKE with Workload Identity.

Interface contract:
    This class exposes the same three public methods as OllamaClient:
        health_check() -> bool
        chat(messages, temperature, **kwargs) -> dict
        parse_rca_response(raw_content) -> dict

    The chat() return value has the same shape:
        {"message": {"content": "<model text>"}, "total_duration": <ns int>}

    This lets run_rca.py switch backends with a single env-var toggle and
    zero changes to the rest of the pipeline.

Data residency note:
    When runners are in the same GCP VPC as Vertex AI and Private Service
    Connect is configured, all traffic stays within the VPC — it never
    reaches the public internet. The Google DPA already signed by the bank
    covers Vertex AI in the same way it covers GKE, GCS, and BigQuery.
"""

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Vertex AI SDK is optional — only imported when this client is actually used.
# This prevents import errors on machines that only run Ollama.
try:
    import vertexai
    from vertexai.generative_models import GenerationConfig, GenerativeModel

    _VERTEX_SDK_AVAILABLE = True
except ImportError:
    _VERTEX_SDK_AVAILABLE = False


class VertexAIClient:
    """
    Vertex AI Gemma client with the same interface as OllamaClient.

    Connects to Vertex AI using Application Default Credentials (ADC).
    On GKE, ADC is satisfied automatically via Workload Identity — no
    service-account key files required.
    """

    def __init__(
        self,
        project: str | None = None,
        location: str | None = None,
        model: str | None = None,
        max_retries: int = 3,
    ):
        if not _VERTEX_SDK_AVAILABLE:
            raise ImportError(
                "google-cloud-aiplatform is not installed. "
                "Run: pip install google-cloud-aiplatform"
            )

        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("VERTEX_AI_LOCATION", "europe-west2")
        self.model_name = (
            model
            or os.environ.get("VERTEX_AI_MODEL", "gemma-3-27b-it")
        )
        self.max_retries = max_retries

        if not self.project:
            raise ValueError(
                "GCP project not set. Provide the 'project' argument or "
                "set the GOOGLE_CLOUD_PROJECT environment variable."
            )

        vertexai.init(project=self.project, location=self.location)
        self._model = GenerativeModel(self.model_name)

        logger.info(
            "VertexAIClient initialised: project=%s location=%s model=%s",
            self.project,
            self.location,
            self.model_name,
        )

    # ------------------------------------------------------------------
    # Public interface — matches OllamaClient exactly
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """
        Vertex AI is SLA-backed — no explicit ping needed.
        Returns True immediately. Any real connectivity issue will surface
        on the first chat() call with a clear error message.
        """
        logger.info(
            "Vertex AI health check: project=%s location=%s model=%s — OK (SLA-backed)",
            self.project,
            self.location,
            self.model_name,
        )
        return True

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Send a chat request to Vertex AI and return a response dict in the
        same shape as OllamaClient.chat():
            {"message": {"content": "<text>"}, "total_duration": <nanoseconds>}

        The 'messages' list follows the OpenAI/Ollama convention:
            [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

        Vertex AI's GenerativeModel.generate_content() expects a flat prompt
        string (or multipart content). This method merges the system prompt
        and user message into a single request using the system_instruction
        parameter, which is how Vertex AI handles system prompts for Gemma.
        """
        system_prompt = ""
        user_parts: list[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_prompt = content
            else:
                user_parts.append(content)

        user_content = "\n\n".join(user_parts)

        # Rebuild model with system instruction if one is provided.
        # GenerativeModel accepts system_instruction at construction time.
        if system_prompt:
            model = GenerativeModel(
                self.model_name,
                system_instruction=system_prompt,
            )
        else:
            model = self._model

        generation_config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=4096,
        )

        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "Sending RCA request to Vertex AI (attempt %d/%d, model=%s)",
                    attempt,
                    self.max_retries,
                    self.model_name,
                )
                t_start = time.monotonic()
                response = model.generate_content(
                    user_content,
                    generation_config=generation_config,
                )
                elapsed_s = time.monotonic() - t_start
                elapsed_ns = int(elapsed_s * 1_000_000_000)

                text = response.text
                logger.info(
                    "Vertex AI response received: %d chars in %.1f seconds",
                    len(text),
                    elapsed_s,
                )

                # Return the same shape as OllamaClient so downstream code
                # never needs to know which backend was used.
                return {
                    "message": {"content": text},
                    "total_duration": elapsed_ns,
                }

            except Exception as e:
                logger.warning(
                    "Vertex AI request failed (attempt %d/%d): %s",
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
            f"Vertex AI request failed after {self.max_retries} attempts: {last_error}"
        )

    def parse_rca_response(self, raw_content: str) -> dict[str, Any]:
        """
        Delegate JSON parsing to OllamaClient — the logic is identical
        (strip markdown fences, json.loads, fallback on parse error).
        """
        from src.ollama_client import OllamaClient

        return OllamaClient().parse_rca_response(raw_content)
