#!/usr/bin/env python3
"""
Fetch and save GitHub Actions workflow run logs.

Usage:
    python scripts/fetch_logs.py \\
        --repo owner/repo \\
        --run-id 12345 \\
        --output logs/

Environment variables:
    GITHUB_TOKEN       GitHub PAT with actions:read scope
    GITHUB_API_URL     GitHub API base URL (default: https://api.github.com)
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.log_parser import fetch_workflow_jobs, download_job_logs, parse_job_info

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("fetch-logs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch GitHub Actions workflow logs")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--run-id", required=True, type=int, help="Workflow run ID")
    parser.add_argument("--output", default="logs", help="Output directory")
    parser.add_argument(
        "--failed-only",
        action="store_true",
        default=True,
        help="Only fetch logs for failed jobs (default: True)",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        logger.error("GITHUB_TOKEN environment variable is required")
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Fetching jobs for run %d in %s", args.run_id, args.repo)
    raw_jobs = fetch_workflow_jobs(args.repo, args.run_id, token)
    jobs = [parse_job_info(j) for j in raw_jobs]

    logger.info("Found %d jobs total", len(jobs))

    target_jobs = [j for j in jobs if j.conclusion == "failure"] if args.failed_only else jobs
    if not target_jobs:
        logger.info("No failed jobs found — fetching all jobs")
        target_jobs = jobs

    for job in target_jobs:
        logger.info("Fetching logs for: %s [%s]", job.name, job.conclusion)
        try:
            log_text = download_job_logs(args.repo, job.job_id, token)
            safe_name = job.name.replace("/", "_").replace(" ", "_")
            log_file = output_dir / f"{safe_name}_{job.job_id}.log"
            log_file.write_text(log_text, encoding="utf-8")
            logger.info("Saved %d bytes to %s", len(log_text), log_file)
        except Exception as e:
            logger.error("Failed to fetch logs for job %s: %s", job.name, e)

    logger.info("Done. Logs saved to %s/", output_dir)


if __name__ == "__main__":
    main()
