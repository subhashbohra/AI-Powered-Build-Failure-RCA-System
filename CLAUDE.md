# CLAUDE.md — Build Failure RCA Agent (Ollama + Gemma 27B)

## Project Overview

An AI-powered Root Cause Analysis (RCA) system that automatically analyzes GitHub Actions build failures using a self-hosted Ollama instance running Google's Gemma 3 27B model. When `release_build` or `snapshot_build` workflows fail in our Fabric2 GitHub repos, this system fetches the runner logs, feeds them to Gemma for analysis, and posts structured RCA feedback to the PR/Slack.

## Architecture

```
GitHub Actions (Fabric2 repos)
  ├── release_build.yml   ──┐
  ├── snapshot_build.yml  ──┤── on failure ──► rca_trigger.yml
  └── rca_trigger.yml ──────┘
        │
        ├── 1. Fetch logs via GitHub REST API (download workflow run logs ZIP)
        ├── 2. Parse & trim logs (extract errors, test failures, timing data)
        ├── 3. POST to Ollama /api/chat (Gemma 3 27B on GDC/GKE ClusterIP)
        ├── 4. Post RCA summary as PR comment + Slack notification
        └── 5. Upload structured RCA JSON as workflow artifact

Ollama Service (GDC/GKE — air-gapped cluster)
  ├── Deployment: ollama/ollama:latest with GPU (T4/L4)
  ├── Model: gemma3:27b-it-qat (Q4 quantized, ~16GB)
  ├── Service: ClusterIP on port 11434 (no external exposure)
  └── PVC: 50Gi for model storage
```

## Key Constraints

- **Air-gapped environment**: GDC cluster has NO external GCP API access. Everything must run locally on the cluster.
- **Fabric2 repos**: Our GitHub org uses Fabric2. The RCA workflow lives in each repo under `.github/workflows/rca_trigger.yml`.
- **Self-hosted runner**: The GitHub runner that executes the RCA workflow MUST have network access to the Ollama ClusterIP service inside the GDC cluster. This means using a self-hosted runner deployed inside or peered with the GDC network.
- **Model size**: Gemma 3 27B QAT Q4_0 is ~16GB. The GPU node needs at least 24GB VRAM (T4 or L4).
- **Context window**: Gemma 3 27B supports 128K context. Build logs should be trimmed to ~80K tokens max to leave room for the system prompt and response.

## Directory Structure

```
build-rca-agent/
├── CLAUDE.md                          # This file
├── .github/
│   └── workflows/
│       └── rca_trigger.yml            # GitHub Actions workflow (copy to each Fabric2 repo)
├── k8s/
│   ├── namespace.yaml                 # ollama-rca namespace
│   ├── pvc.yaml                       # PersistentVolumeClaim for model storage
│   ├── deployment.yaml                # Ollama Deployment with GPU
│   ├── service.yaml                   # ClusterIP Service
│   └── init-model-job.yaml            # One-time Job to pull gemma3:27b-it-qat
├── scripts/
│   ├── fetch_logs.py                  # Download and extract workflow run logs
│   ├── parse_logs.py                  # Parse raw logs into structured failure data
│   ├── analyze_with_ollama.py         # Send parsed logs to Ollama and get RCA
│   ├── post_results.py                # Post RCA to PR comment and/or Slack
│   └── run_rca.py                     # Main orchestrator script
├── src/
│   ├── __init__.py
│   ├── log_parser.py                  # Log parsing utilities
│   ├── ollama_client.py               # Ollama API client wrapper
│   ├── rca_prompt.py                  # System prompts and prompt templates
│   └── output_formatter.py           # Format RCA into markdown/JSON
├── tests/
│   ├── test_log_parser.py
│   ├── test_ollama_client.py
│   ├── test_rca_prompt.py
│   └── sample_logs/                   # Sample build failure logs for testing
├── docs/
│   └── DEPLOYMENT.md                  # Step-by-step deployment guide
└── requirements.txt
```

## Tech Stack

- **Python 3.11+**: All scripts
- **Ollama**: LLM inference server (v0.6+)
- **Gemma 3 27B QAT**: Google's open model, Q4 quantized for 24GB GPU
- **GitHub REST API**: Fetch workflow run/job logs, post PR comments
- **Kubernetes**: Deployment on GDC/GKE
- **Requests library**: HTTP calls to Ollama and GitHub APIs

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -v

# Test log parsing locally with a sample log
python scripts/parse_logs.py --input tests/sample_logs/build_failure.log

# Test Ollama connection (assumes Ollama is running locally or port-forwarded)
python scripts/analyze_with_ollama.py --test

# Run full RCA pipeline locally (needs GITHUB_TOKEN and OLLAMA_HOST env vars)
GITHUB_TOKEN=ghp_xxx OLLAMA_HOST=http://localhost:11434 \
  python scripts/run_rca.py --repo owner/repo --run-id 12345

# Deploy to GDC/GKE cluster
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/init-model-job.yaml

# Port-forward for local testing
kubectl port-forward svc/ollama-rca -n ollama-rca 11434:11434

# Verify Ollama is running
curl http://localhost:11434/api/tags
```

## Environment Variables

| Variable | Description | Where Set |
|----------|-------------|-----------|
| `GITHUB_TOKEN` | GitHub PAT with `repo` scope (read actions, write comments) | GitHub Actions secret |
| `OLLAMA_HOST` | Ollama service URL (e.g., `http://ollama-rca.ollama-rca.svc.cluster.local:11434`) | GitHub Actions env / runner config |
| `OLLAMA_MODEL` | Model name (default: `gemma3:27b-it-qat`) | GitHub Actions env |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook for notifications (optional) | GitHub Actions secret |
| `MAX_LOG_TOKENS` | Max tokens to send to Gemma (default: 80000) | GitHub Actions env |
| `BUILD_TIME_THRESHOLD_MINUTES` | Alert if build exceeds this (default: 20) | GitHub Actions env |

## Ollama API Usage

The system uses the `/api/chat` endpoint with streaming disabled:

```python
POST http://ollama-rca.ollama-rca.svc.cluster.local:11434/api/chat
{
    "model": "gemma3:27b-it-qat",
    "stream": false,
    "messages": [
        {"role": "system", "content": "<RCA system prompt>"},
        {"role": "user", "content": "<parsed build logs + metadata>"}
    ],
    "options": {
        "temperature": 0.3,
        "num_ctx": 32768
    }
}
```

## GitHub Actions Log Retrieval

Logs are fetched using two API calls:

1. **Get failed jobs**: `GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs` — filters for jobs with `conclusion: failure`
2. **Download job logs**: `GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs` — returns a ZIP archive of log files

The ZIP is extracted, and each log file is parsed for error patterns, test failures, and timing data.

## Log Parsing Strategy

The parser extracts:
- **Build duration**: From job `started_at` and `completed_at` timestamps
- **Failed steps**: Steps with `conclusion: failure` and their log output
- **Error patterns**: Stack traces, compilation errors, test assertion failures
- **Test results**: JUnit/Surefire XML if available, or grep for `FAILED`, `ERROR`, `BUILD FAILURE`
- **Dependency issues**: Maven/Gradle resolution failures, network timeouts
- **Resource issues**: OOM kills, disk space, timeout patterns

## RCA Prompt Design

The system prompt instructs Gemma to act as a senior build engineer and produce:

1. **Root cause**: Single-sentence summary of why the build failed
2. **Category**: One of `test_failure | compilation_error | dependency_issue | timeout | resource_exhaustion | infra_flake | config_error | unknown`
3. **Details**: Specific files, tests, or components that failed
4. **Build time analysis**: Flag if build time exceeded threshold, identify slow stages
5. **Recommendation**: Actionable fix suggestion
6. **Confidence**: `high | medium | low`

## Coding Conventions

- Python 3.11+ with type hints everywhere
- Use `logging` module, not `print()` — level INFO for normal flow, DEBUG for verbose
- All HTTP calls must have timeout (30s for GitHub API, 120s for Ollama)
- Retry logic: 3 retries with exponential backoff for transient failures
- Error handling: Never crash the workflow — always produce a "manual review needed" fallback
- Config via environment variables, with sensible defaults
- No external API calls from within GDC cluster — everything stays internal

## Testing

- Unit tests for log parsing with sample log files
- Mock-based tests for Ollama client (don't require running Ollama)
- Integration test script that runs against a real Ollama instance
- Sample logs in `tests/sample_logs/` covering: test failures, compilation errors, timeout, OOM

## Enterprise-Specific Configuration (Fabric2)

- **GitHub type**: GitHub Enterprise (confirm: Server or Cloud)
- **GitHub API Base URL**: Always use `os.environ.get("GITHUB_API_URL", "https://api.github.com")` — never hardcode
- **Runner labels**: Update `runs-on` in `rca_trigger.yml` to match your enterprise runner labels
- **Ollama endpoint**: Already deployed on GDC as ClusterIP. Confirm reachability from runner.
- **Enterprise CA cert**: If runner gets SSL errors, set `REQUESTS_CA_BUNDLE` env var
- **Proxy**: If runner uses corporate proxy, set `NO_PROXY` to include Ollama hostname
- **PAT type**: Prefer fine-grained PAT. Fall back to classic PAT with `repo` scope if blocked.
- **All API calls in `src/log_parser.py` and `scripts/post_results.py`**: Must use `GITHUB_API_URL` env var, not hardcoded `api.github.com`

## Deployment Checklist

1. [ ] GPU node pool exists in GDC/GKE cluster (T4 or L4 with 24GB+ VRAM)
2. [ ] NVIDIA device plugin installed (`nvidia.com/gpu` resource available)
3. [ ] Ollama K8s resources deployed (`kubectl apply -f k8s/`)
4. [ ] Model pulled successfully (check `kubectl logs job/ollama-model-pull -n ollama-rca`)
5. [ ] ClusterIP service reachable from self-hosted GitHub runner
6. [ ] GitHub PAT created with `repo` scope, stored as Actions secret `RCA_GITHUB_TOKEN`
7. [ ] `rca_trigger.yml` committed to default branch of each Fabric2 repo
8. [ ] Slack webhook configured (optional)
9. [ ] Dry-run test: manually trigger `rca_trigger.yml` with a known failed run ID
