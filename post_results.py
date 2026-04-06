#!/usr/bin/env python3
"""
Post RCA results as a PR comment on any open PRs associated with the failed commit.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("post-results")


def find_prs_for_commit(repo: str, sha: str, token: str) -> list[dict]:
    """Find open PRs that contain the given commit SHA."""
    url = f"https://api.github.com/repos/{repo}/commits/{sha}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 404:
        logger.info("No PRs found for commit %s", sha)
        return []
    resp.raise_for_status()
    return resp.json()


def post_pr_comment(repo: str, pr_number: int, body: str, token: str) -> None:
    """Post a comment on a GitHub PR."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.post(url, json={"body": body}, headers=headers, timeout=30)
    resp.raise_for_status()
    logger.info("Posted RCA comment on PR #%d", pr_number)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post RCA results to PRs")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--rca-file", required=True)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        logger.error("GITHUB_TOKEN is required")
        sys.exit(1)

    # Load the RCA report
    rca_path = Path(args.rca_file)
    if not rca_path.exists():
        logger.error("RCA file not found: %s", rca_path)
        sys.exit(1)

    report = json.loads(rca_path.read_text())
    rca = report.get("rca", {})
    metadata = report.get("metadata", {})

    # Generate markdown comment
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.output_formatter import rca_to_markdown
    md = rca_to_markdown(rca, metadata)

    # Find associated PRs
    prs = find_prs_for_commit(args.repo, args.sha, token)
    if not prs:
        logger.info("No open PRs associated with commit %s — skipping comment", args.sha)
        return

    for pr in prs:
        pr_number = pr["number"]
        try:
            post_pr_comment(args.repo, pr_number, md, token)
        except requests.RequestException as e:
            logger.warning("Failed to post comment on PR #%d: %s", pr_number, e)


if __name__ == "__main__":
    main()
