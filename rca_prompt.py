"""
RCA prompt templates for Gemma model.
"""

SYSTEM_PROMPT = """You are a senior build/release engineer performing Root Cause Analysis (RCA) on CI/CD build failures. You are analyzing GitHub Actions workflow logs from a failed build.

Your job is to:
1. Identify the ROOT CAUSE of the build failure — not symptoms, but the actual cause.
2. Categorize the failure type.
3. Assess build timing — flag if total build time or any individual step exceeded expected thresholds.
4. Provide a specific, actionable recommendation to fix the issue.

IMPORTANT RULES:
- Be specific. Name the exact test, file, class, or dependency that caused the failure.
- If multiple tests failed, list all of them but identify the likely root cause (e.g., a shared dependency or setup issue).
- If the build timed out or was slow, identify which step consumed the most time.
- If you cannot determine the root cause with confidence, say so — do NOT guess.
- Keep your response structured and concise.

Respond ONLY in the following JSON format (no markdown, no backticks, just raw JSON):

{
    "root_cause": "Single sentence describing the root cause",
    "category": "one of: test_failure | compilation_error | dependency_issue | timeout | resource_exhaustion | infra_flake | config_error | unknown",
    "failed_components": ["list", "of", "specific", "files", "tests", "or", "modules"],
    "build_time_analysis": {
        "total_duration_minutes": <number or null>,
        "exceeded_threshold": <true/false>,
        "slowest_step": "name of the slowest step",
        "slowest_step_duration_minutes": <number or null>
    },
    "error_messages": ["key error messages extracted from logs"],
    "recommendation": "Specific actionable fix — what to change and where",
    "confidence": "high | medium | low",
    "additional_notes": "Any other relevant observations"
}"""


USER_PROMPT_TEMPLATE = """Analyze this failed GitHub Actions build:

## Build Metadata
- **Workflow**: {workflow_name}
- **Repository**: {repo}
- **Branch**: {branch}
- **Commit**: {sha}
- **Triggered by**: {actor}
- **Started at**: {started_at}
- **Ended at**: {updated_at}
- **Run URL**: {run_url}
- **Build time threshold**: {threshold_minutes} minutes

## Failed Jobs Summary
{jobs_summary}

## Build Logs (trimmed to relevant sections)
{logs_content}

Perform your RCA analysis and respond with the JSON structure specified in your instructions."""


def build_rca_prompt(
    workflow_name: str,
    repo: str,
    branch: str,
    sha: str,
    actor: str,
    run_url: str,
    started_at: str,
    updated_at: str,
    jobs_summary: str,
    logs_content: str,
    threshold_minutes: int = 20,
) -> list[dict[str, str]]:
    """Build the messages array for the Ollama /api/chat request."""
    user_content = USER_PROMPT_TEMPLATE.format(
        workflow_name=workflow_name,
        repo=repo,
        branch=branch,
        sha=sha,
        actor=actor,
        run_url=run_url,
        started_at=started_at,
        updated_at=updated_at,
        threshold_minutes=threshold_minutes,
        jobs_summary=jobs_summary,
        logs_content=logs_content,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
