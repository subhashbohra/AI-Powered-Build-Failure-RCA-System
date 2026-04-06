#!/usr/bin/env python3
"""
Main RCA pipeline orchestrator.

Usage:
    python scripts/run_rca.py \\
        --repo owner/repo \\
        --run-id 12345 \\
        --workflow-name "release_build" \\
        --branch "main" \\
        --sha "abc123" \\
        --actor "username" \\
        --run-url "https://github.com/..." \\
        --started-at "2025-01-01T00:00:00Z" \\
        --updated-at "2025-01-01T00:30:00Z"

Environment variables:
    GITHUB_TOKEN                  GitHub PAT with repo scope
    GITHUB_API_URL                GitHub API base URL (auto-set by Actions: ${{ github.api_url }})
    OLLAMA_HOST                   Ollama service URL
    OLLAMA_MODEL                  Model name (default: gemma3:27b-it-qat)
    MAX_LOG_TOKENS                Max tokens to send to model (default: 80000)
    BUILD_TIME_THRESHOLD_MINUTES  Alert threshold in minutes (default: 20)
    SLACK_WEBHOOK_URL             Optional Slack webhook URL

    USE_VERTEX_AI                 Set to "true" to use Vertex AI instead of Ollama.
                                  When enabled, OLLAMA_HOST and OLLAMA_MODEL are ignored.
    GOOGLE_CLOUD_PROJECT          GCP project ID (required when USE_VERTEX_AI=true)
    VERTEX_AI_LOCATION            GCP region (default: europe-west2)
    VERTEX_AI_MODEL               Vertex AI model name (default: gemma-3-27b-it)
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.log_parser import parse_build_logs
from src.ollama_client import OllamaClient
from src.output_formatter import rca_to_json, rca_to_markdown, rca_to_slack_payload
from src.rca_prompt import build_rca_prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rca-pipeline")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Failure RCA Pipeline")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--run-id", required=True, type=int, help="Workflow run ID")
    parser.add_argument("--workflow-name", default="unknown")
    parser.add_argument("--branch", default="unknown")
    parser.add_argument("--sha", default="unknown")
    parser.add_argument("--actor", default="unknown")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--started-at", default="")
    parser.add_argument("--updated-at", default="")
    args = parser.parse_args()

    # Config from environment
    github_token = os.environ.get("GITHUB_TOKEN", "")
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "gemma3:27b-it-qat")
    max_tokens = int(os.environ.get("MAX_LOG_TOKENS", "80000"))
    threshold_minutes = int(os.environ.get("BUILD_TIME_THRESHOLD_MINUTES", "20"))
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    use_vertex_ai = os.environ.get("USE_VERTEX_AI", "false").lower() == "true"

    if not github_token:
        logger.error("GITHUB_TOKEN is required")
        sys.exit(1)

    output_dir = Path("rca_output")
    output_dir.mkdir(exist_ok=True)

    metadata = {
        "workflow_name": args.workflow_name,
        "repo": args.repo,
        "run_id": str(args.run_id),
        "branch": args.branch,
        "sha": args.sha,
        "actor": args.actor,
        "run_url": args.run_url,
        "started_at": args.started_at,
        "updated_at": args.updated_at,
    }

    # ── Step 1: Parse build logs ──────────────────────────────────────
    logger.info("Step 1: Fetching and parsing build logs...")
    try:
        parsed = parse_build_logs(
            repo=args.repo,
            run_id=args.run_id,
            token=github_token,
            max_tokens=max_tokens,
        )
        logger.info(
            "Parsed %d jobs, total duration: %.1f min, %d error lines extracted",
            len(parsed.jobs),
            parsed.total_duration_minutes,
            len(parsed.error_lines),
        )
    except Exception as e:
        logger.error("Failed to parse build logs: %s", e)
        _write_fallback_report(output_dir, metadata, str(e))
        sys.exit(0)  # Don't fail the workflow — fallback report is written

    # ── Step 2: Build RCA prompt ──────────────────────────────────────
    logger.info("Step 2: Building RCA prompt...")
    messages = build_rca_prompt(
        workflow_name=args.workflow_name,
        repo=args.repo,
        branch=args.branch,
        sha=args.sha,
        actor=args.actor,
        run_url=args.run_url,
        started_at=args.started_at,
        updated_at=args.updated_at,
        jobs_summary=parsed.jobs_summary,
        logs_content=parsed.trimmed_log_content,
        threshold_minutes=threshold_minutes,
    )

    prompt_chars = sum(len(m["content"]) for m in messages)
    logger.info("Prompt built: %d total characters (~%d tokens)", prompt_chars, prompt_chars // 4)

    # ── Step 3: Call model (Ollama or Vertex AI) ──────────────────────
    if use_vertex_ai:
        logger.info("Step 3: Sending to Vertex AI (USE_VERTEX_AI=true)...")
        try:
            from src.vertex_ai_client import VertexAIClient
            client = VertexAIClient()
        except (ImportError, ValueError) as e:
            logger.error("Failed to initialise Vertex AI client: %s", e)
            _write_fallback_report(output_dir, metadata, str(e))
            sys.exit(0)
    else:
        logger.info("Step 3: Sending to Ollama (%s)...", ollama_host)
        client = OllamaClient(host=ollama_host, model=ollama_model, timeout=180)

    if not client.health_check():
        logger.error("Model health check failed — service may be unavailable")
        _write_fallback_report(output_dir, metadata, "Model service unavailable")
        sys.exit(0)

    try:
        response = client.chat(messages, temperature=0.3, num_ctx=32768)
        raw_content = response.get("message", {}).get("content", "")
        rca = client.parse_rca_response(raw_content)
        logger.info(
            "RCA analysis complete: category=%s, confidence=%s",
            rca.get("category"),
            rca.get("confidence"),
        )
    except Exception as e:
        logger.error("Model request failed: %s", e)
        _write_fallback_report(output_dir, metadata, str(e))
        sys.exit(0)

    # ── Step 4: Write outputs ─────────────────────────────────────────
    logger.info("Step 4: Writing RCA outputs...")

    json_report = rca_to_json(rca, metadata)
    (output_dir / "rca_report.json").write_text(json_report)

    md_report = rca_to_markdown(rca, metadata)
    (output_dir / "rca_report.md").write_text(md_report)

    logger.info("RCA reports written to %s", output_dir)

    # ── Step 5: Slack notification (optional) ─────────────────────────
    if slack_webhook:
        logger.info("Step 5: Sending Slack notification...")
        try:
            import requests
            payload = rca_to_slack_payload(rca, metadata)
            resp = requests.post(slack_webhook, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Slack notification sent")
        except Exception as e:
            logger.warning("Slack notification failed (non-fatal): %s", e)

    print("\n" + "=" * 60)
    print("RCA SUMMARY")
    print("=" * 60)
    print(f"Root Cause: {rca.get('root_cause', 'unknown')}")
    print(f"Category:   {rca.get('category', 'unknown')}")
    print(f"Confidence: {rca.get('confidence', 'unknown')}")
    print(f"Fix:        {rca.get('recommendation', 'N/A')}")
    print("=" * 60)


def _write_fallback_report(output_dir: Path, metadata: dict, error: str) -> None:
    """Write a fallback report when the pipeline fails."""
    fallback_rca = {
        "root_cause": "RCA pipeline could not complete analysis — manual review required",
        "category": "unknown",
        "failed_components": [],
        "build_time_analysis": {
            "total_duration_minutes": None,
            "exceeded_threshold": False,
            "slowest_step": "unknown",
            "slowest_step_duration_minutes": None,
        },
        "error_messages": [error],
        "recommendation": "Review build logs manually at the run URL",
        "confidence": "low",
        "additional_notes": f"RCA pipeline error: {error}",
    }

    json_report = rca_to_json(fallback_rca, metadata)
    (output_dir / "rca_report.json").write_text(json_report)

    md_report = rca_to_markdown(fallback_rca, metadata)
    (output_dir / "rca_report.md").write_text(md_report)

    logger.info("Fallback RCA report written to %s", output_dir)


if __name__ == "__main__":
    main()
