#!/usr/bin/env python3
"""
Send a pre-parsed log file to Ollama for RCA analysis.
Useful for testing the Ollama integration independently.

Usage:
    # Connection test only
    python scripts/analyze_with_ollama.py --test

    # Analyze a pre-parsed log file
    python scripts/analyze_with_ollama.py --log-file logs/build_job_123.log

Environment variables:
    OLLAMA_HOST     Ollama server URL (default: http://localhost:11434)
    OLLAMA_MODEL    Model name (default: gemma3:27b-it-qat)
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ollama_client import OllamaClient
from src.rca_prompt import build_rca_prompt
from src.log_parser import extract_error_context, trim_to_token_limit
from src.output_formatter import rca_to_markdown

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("analyze-with-ollama")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Ollama RCA analysis")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run connection + inference test only",
    )
    parser.add_argument(
        "--log-file",
        help="Path to a log file to analyze",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=80000,
        help="Maximum tokens to send (default: 80000)",
    )
    args = parser.parse_args()

    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "gemma3:27b-it-qat")

    client = OllamaClient(host=ollama_host, model=ollama_model, timeout=180)

    # --- Connection test ---
    logger.info("Connecting to Ollama at %s", ollama_host)
    if not client.health_check():
        logger.error(
            "Ollama health check failed. Ensure Ollama is running and model '%s' is loaded.",
            ollama_model,
        )
        logger.error("To load the model: ollama pull %s", ollama_model)
        sys.exit(1)
    logger.info("Ollama connection OK. Model '%s' is available.", ollama_model)

    if args.test:
        # Quick inference smoke test
        logger.info("Running inference smoke test...")
        resp = client.chat(
            [{"role": "user", "content": "Reply with exactly one word: ready"}],
            temperature=0.1,
            num_ctx=256,
        )
        response_text = resp.get("message", {}).get("content", "")
        logger.info("Smoke test response: %s", response_text.strip())
        print("\nOllama connection test PASSED")
        return

    if not args.log_file:
        parser.error("Either --test or --log-file is required")

    log_path = Path(args.log_file)
    if not log_path.exists():
        logger.error("Log file not found: %s", log_path)
        sys.exit(1)

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    logger.info("Read %d bytes from %s", len(log_text), log_path)

    # Extract and trim
    error_lines = extract_error_context(log_text)
    if error_lines:
        content = "\n".join(error_lines)
        logger.info("Using %d error-context lines", len(error_lines))
    else:
        content = log_text
        logger.info("No error patterns found, using full log")

    trimmed = trim_to_token_limit(content, args.max_tokens)
    logger.info(
        "Sending %d chars (~%d tokens) to Ollama",
        len(trimmed),
        len(trimmed) // 4,
    )

    messages = build_rca_prompt(
        workflow_name="manual_analysis",
        repo="local/test",
        branch="main",
        sha="unknown",
        actor="manual",
        run_url="",
        started_at="",
        updated_at="",
        jobs_summary="(manual log analysis — no job metadata)",
        logs_content=trimmed,
    )

    logger.info("Sending to Ollama (this may take 30-120 seconds)...")
    response = client.chat(messages, temperature=0.3, num_ctx=32768)
    raw_content = response.get("message", {}).get("content", "")
    rca = client.parse_rca_response(raw_content)

    print("\n" + "=" * 60)
    print("RCA RESULT (JSON)")
    print("=" * 60)
    print(json.dumps(rca, indent=2))

    print("\n" + "=" * 60)
    print("RCA RESULT (Markdown)")
    print("=" * 60)
    metadata = {
        "workflow_name": "manual_analysis",
        "repo": "local/test",
        "branch": "main",
        "sha": "unknown",
        "run_url": "",
    }
    print(rca_to_markdown(rca, metadata))


if __name__ == "__main__":
    main()
