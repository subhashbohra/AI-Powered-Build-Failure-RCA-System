"""
Log parsing utilities for GitHub Actions build logs.

Handles:
- Downloading and extracting log ZIP archives
- Parsing structured job/step metadata from the GitHub API
- Extracting error-relevant sections from raw log text
- Trimming logs to fit within the model's context window
"""

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# Patterns that indicate important log sections
ERROR_PATTERNS = [
    r"(?i)\bERROR\b",
    r"(?i)\bFAILED\b",
    r"(?i)\bFAILURE\b",
    r"(?i)\bException\b",
    r"(?i)\bTraceback\b",
    r"(?i)BUILD FAILURE",
    r"(?i)compilation error",
    r"(?i)cannot find symbol",
    r"(?i)NoSuchMethodError",
    r"(?i)ClassNotFoundException",
    r"(?i)OutOfMemoryError",
    r"(?i)OOMKilled",
    r"(?i)Tests run:.*Failures: [1-9]",
    r"(?i)Tests run:.*Errors: [1-9]",
    r"(?i)FAIL!",
    r"(?i)assert.*failed",
    r"(?i)AssertionError",
    r"(?i)Could not resolve dependencies",
    r"(?i)Connection timed out",
    r"(?i)npm ERR!",
    r"(?i)pip.*error",
    r"(?i)exit code [1-9]",
    r"(?i)Process completed with exit code [1-9]",
]

COMPILED_PATTERNS = [re.compile(p) for p in ERROR_PATTERNS]

# Lines to always skip (noise)
SKIP_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^##\[debug\]"),
    re.compile(r"^##\[group\]"),
    re.compile(r"^##\[endgroup\]"),
]


@dataclass
class JobInfo:
    """Parsed information about a single job in a workflow run."""

    job_id: int
    name: str
    status: str
    conclusion: str
    started_at: str
    completed_at: str
    duration_minutes: float
    steps: list[dict] = field(default_factory=list)
    failed_steps: list[dict] = field(default_factory=list)


@dataclass
class ParsedLogs:
    """Result of parsing build logs."""

    jobs: list[JobInfo]
    total_duration_minutes: float
    error_lines: list[str]
    trimmed_log_content: str
    jobs_summary: str


def fetch_workflow_jobs(
    repo: str, run_id: int, token: str
) -> list[dict]:
    """Fetch all jobs for a workflow run from GitHub API."""
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    all_jobs = []
    page = 1

    while True:
        resp = requests.get(
            url, headers=headers, params={"page": page, "per_page": 100}, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])
        all_jobs.extend(jobs)

        if len(jobs) < 100:
            break
        page += 1

    logger.info("Fetched %d jobs for run %s", len(all_jobs), run_id)
    return all_jobs


def download_job_logs(
    repo: str, job_id: int, token: str
) -> str:
    """Download and extract logs for a specific job."""
    url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    # Response might be a ZIP or plain text depending on the endpoint
    content_type = resp.headers.get("content-type", "")

    if "zip" in content_type or resp.content[:2] == b"PK":
        # It's a ZIP archive
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                log_texts = []
                for name in sorted(zf.namelist()):
                    with zf.open(name) as f:
                        log_texts.append(
                            f"--- {name} ---\n{f.read().decode('utf-8', errors='replace')}"
                        )
                return "\n".join(log_texts)
        except zipfile.BadZipFile:
            logger.warning("Response was not a valid ZIP, treating as plain text")
            return resp.text
    else:
        return resp.text


def download_run_logs(
    repo: str, run_id: int, token: str
) -> str:
    """Download and extract logs for an entire workflow run."""
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/logs"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            log_texts = []
            for name in sorted(zf.namelist()):
                with zf.open(name) as f:
                    log_texts.append(
                        f"--- {name} ---\n{f.read().decode('utf-8', errors='replace')}"
                    )
            return "\n".join(log_texts)
    except zipfile.BadZipFile:
        return resp.text


def parse_job_info(job_data: dict) -> JobInfo:
    """Parse a single job API response into a JobInfo object."""
    started = job_data.get("started_at", "")
    completed = job_data.get("completed_at", "")
    duration = 0.0

    if started and completed:
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
            duration = (end_dt - start_dt).total_seconds() / 60.0
        except (ValueError, TypeError):
            pass

    steps = job_data.get("steps", [])
    failed_steps = [s for s in steps if s.get("conclusion") == "failure"]

    return JobInfo(
        job_id=job_data["id"],
        name=job_data.get("name", "unknown"),
        status=job_data.get("status", "unknown"),
        conclusion=job_data.get("conclusion", "unknown"),
        started_at=started,
        completed_at=completed,
        duration_minutes=round(duration, 2),
        steps=steps,
        failed_steps=failed_steps,
    )


def extract_error_context(log_text: str, context_lines: int = 10) -> list[str]:
    """Extract lines around error patterns with surrounding context."""
    lines = log_text.split("\n")
    error_indices: set[int] = set()

    for i, line in enumerate(lines):
        # Skip noise lines
        if any(sp.search(line) for sp in SKIP_PATTERNS):
            continue
        if any(p.search(line) for p in COMPILED_PATTERNS):
            # Add context window around the error
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            error_indices.update(range(start, end))

    if not error_indices:
        return []

    # Build contiguous blocks
    sorted_indices = sorted(error_indices)
    result_lines = []
    prev_idx = -2

    for idx in sorted_indices:
        if idx > prev_idx + 1:
            result_lines.append("\n... [gap] ...\n")
        result_lines.append(lines[idx])
        prev_idx = idx

    return result_lines


def trim_to_token_limit(text: str, max_tokens: int = 80000) -> str:
    """
    Rough trim to stay within token limits.
    Approximation: 1 token ≈ 4 characters for English text.
    """
    max_chars = max_tokens * 4

    if len(text) <= max_chars:
        return text

    # Keep first third and last two-thirds of the budget
    head_size = max_chars // 3
    tail_size = max_chars - head_size - 100  # leave room for separator

    head = text[:head_size]
    tail = text[-tail_size:]

    return f"{head}\n\n... [TRIMMED — {len(text) - max_chars} chars removed] ...\n\n{tail}"


def format_jobs_summary(jobs: list[JobInfo]) -> str:
    """Format job information into a readable summary for the prompt."""
    lines = []
    for job in jobs:
        status_emoji = "FAILED" if job.conclusion == "failure" else job.conclusion.upper()
        lines.append(
            f"- **{job.name}** [{status_emoji}] — {job.duration_minutes} min"
        )
        for step in job.failed_steps:
            step_name = step.get("name", "unknown")
            lines.append(f"  - Failed step: {step_name}")

    return "\n".join(lines)


def parse_build_logs(
    repo: str,
    run_id: int,
    token: str,
    max_tokens: int = 80000,
) -> ParsedLogs:
    """
    Full log parsing pipeline:
    1. Fetch job metadata from GitHub API
    2. Download logs for failed jobs
    3. Extract error-relevant sections
    4. Trim to token limit
    5. Return structured ParsedLogs
    """
    # Step 1: Get job metadata
    raw_jobs = fetch_workflow_jobs(repo, run_id, token)
    jobs = [parse_job_info(j) for j in raw_jobs]

    total_duration = max((j.duration_minutes for j in jobs), default=0.0)

    # Step 2: Download logs for failed jobs (prioritize) then others
    failed_jobs = [j for j in jobs if j.conclusion == "failure"]
    if not failed_jobs:
        failed_jobs = jobs  # If no explicit failures, analyze all

    all_log_text = []
    error_lines: list[str] = []

    for job in failed_jobs:
        try:
            logger.info("Downloading logs for job: %s (id=%s)", job.name, job.job_id)
            log_text = download_job_logs(repo, job.job_id, token)
            all_log_text.append(f"\n{'='*60}\nJOB: {job.name}\n{'='*60}\n{log_text}")

            # Extract error context
            errors = extract_error_context(log_text)
            error_lines.extend(errors)
        except requests.RequestException as e:
            logger.warning("Failed to download logs for job %s: %s", job.name, e)
            all_log_text.append(f"\n[Failed to download logs for {job.name}: {e}]\n")

    # Step 3: Build the log content — prefer error extracts, fall back to full logs
    if error_lines:
        log_content = "\n".join(error_lines)
        logger.info("Extracted %d error-context lines", len(error_lines))
    else:
        log_content = "\n".join(all_log_text)
        logger.info("No error patterns found, using full log text")

    # Step 4: Trim to fit
    trimmed = trim_to_token_limit(log_content, max_tokens)

    # Step 5: Build summary
    jobs_summary = format_jobs_summary(jobs)

    return ParsedLogs(
        jobs=jobs,
        total_duration_minutes=total_duration,
        error_lines=error_lines,
        trimmed_log_content=trimmed,
        jobs_summary=jobs_summary,
    )
