# AI-Powered Build Failure RCA System
## Step-by-Step Implementation Guide — Enterprise Edition

> **System**: Ollama + Gemma 3 27B on GDC/GKE | GitHub Actions (Fabric2) | Self-Hosted Runners
> **Author**: Build Platform Team | **Last Updated**: 2026-04-06

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Benefits & Business Value](#2-benefits--business-value)
3. [Architecture Deep Dive](#3-architecture-deep-dive)
4. [Prerequisites](#4-prerequisites)
5. [Phase 1 — Infrastructure Setup (Day 1–2)](#5-phase-1--infrastructure-setup-day-12)
6. [Phase 2 — Application Deployment (Day 2–3)](#6-phase-2--application-deployment-day-23)
7. [Phase 3 — GitHub Actions Integration (Day 3–4)](#7-phase-3--github-actions-integration-day-34)
8. [Phase 4 — Testing & Validation (Day 4–5)](#8-phase-4--testing--validation-day-45)
9. [Enterprise Configuration Reference](#9-enterprise-configuration-reference)
10. [Best Practices](#10-best-practices)
11. [Troubleshooting](#11-troubleshooting)
12. [Production Hardening Roadmap](#12-production-hardening-roadmap)

---

## 1. System Overview

The Build Failure RCA Agent automatically analyzes GitHub Actions build failures using a self-hosted AI model, eliminating the need for engineers to manually parse hundreds of lines of CI logs.

**How it works:**

```
Build fails
    │
    ▼
rca_trigger.yml fires (workflow_run event)
    │
    ├─► Fetch logs via GitHub REST API
    │       GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs
    │
    ├─► Parse & extract error context
    │       Pattern matching for errors, stack traces, test failures
    │       Trim to 80K tokens max (Gemma context window)
    │
    ├─► POST to Ollama /api/chat
    │       Gemma 3 27B analyzes logs as a "senior build engineer"
    │       Returns structured JSON: root_cause, category, recommendation
    │
    ├─► Post RCA as PR comment
    │       Formatted markdown with confidence badge, failed components
    │
    ├─► Upload JSON artifact
    │       Retained 30 days for audit/trending
    │
    └─► Send Slack notification (optional)
```

**Key design decisions:**

| Decision | Rationale |
|----------|-----------|
| Self-hosted Ollama on GDC | Air-gapped cluster; no external API calls; data never leaves the org |
| Gemma 3 27B QAT | Best open-source quality at 24GB VRAM; 128K context for large logs |
| workflow_run trigger | Decoupled from build workflows; doesn't add latency to the build itself |
| Never crash the workflow | Always writes a fallback report; build workflow is never blocked by RCA |
| GITHUB_API_URL env var | Supports both github.com and GitHub Enterprise Server with zero code changes |

---

## 2. Benefits & Business Value

### For Developers
- **Time saved**: From 15–45 minutes of manual log digging → 30-second AI analysis
- **Instant actionability**: Root cause + specific recommendation surfaced directly on the PR
- **Pattern recognition**: AI identifies non-obvious patterns (e.g., shared test fixtures, flaky infra)
- **Confidence scoring**: Know whether to trust the analysis (high/medium/low)

### For Engineering Managers
- **Reduced MTTR**: Mean time to resolution for build failures drops significantly
- **Trending data**: Structured JSON artifacts enable dashboards showing failure categories over time
- **Build time monitoring**: Automatic alerting when builds exceed thresholds
- **Fewer interruptions**: On-call engineers spend less time on "which test broke and why"

### For the Platform Team
- **Data sovereignty**: Model runs 100% on-premises on GDC; build logs never sent to external APIs
- **No per-query cost**: One-time GPU infrastructure cost vs. per-call LLM API pricing
- **Audit trail**: Every RCA stored as a versioned artifact for 30 days
- **Extensible**: JSON output can feed dashboards, ticketing systems, or ML pipelines

### Quantified Impact (estimated for a team with 50 build failures/month)
| Metric | Before | After |
|--------|--------|-------|
| Time to identify root cause | 20–45 min | <2 min |
| Developer context-switch cost | High | Near-zero |
| Manual log review % | 100% | ~20% (low-confidence RCAs) |
| Build failure documentation | Ad-hoc | Automatic, structured |

---

## 3. Architecture Deep Dive

### Component Map

```
┌────────────────────────────────────────���────────────────────────────┐
│                     GitHub Enterprise (Fabric2)                      │
│                                                                       │
│  ┌──────────────────┐   on failure   ┌──────────────────────────┐   │
│  │  release_build   │───────────────►│    rca_trigger.yml       │   │
│  │  snapshot_build  │                │  (workflow_run trigger)  │   │
│  └──────────────────┘                └───────────┬──────────────┘   │
│                                                   │                  │
│  ┌──────────────────┐                             │                  │
│  │   PR Comment     │◄────────────────────────────┤                  │
│  │  (RCA Markdown)  │                             │                  │
│  └──────────────────┘                             │                  │
└─────────────────────────────────���────────────────┼──────────────────┘
                                                    │ runs on
                                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Self-Hosted Runner (GDC/GKE network)                    │
│                                                                       │
│  Python 3.11 · scripts/run_rca.py orchestrator                      │
│                                                                       │
│  ① fetch_logs    → GitHub REST API (actions:read)                    │
│  ② parse_logs    → error pattern extraction + token trimming         │
│  ③ build_prompt  → system prompt + metadata + trimmed log            │
│  ④ ollama_chat   → POST /api/chat (no streaming)                     │
│  ⑤ format_output → markdown PR comment + JSON artifact              │
└───────────────────────────────────────┬─────────────────────────────┘
                                        │ HTTP 11434
                                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│              GDC/GKE Cluster — Namespace: ollama-rca                 │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Service: ollama-rca (ClusterIP :11434)                     │    │
│  │      │                                                       │    │
│  │      ▼                                                       │    │
│  │  Pod: ollama/ollama:latest                                   │    │
│  │      ├── GPU: NVIDIA T4 or L4 (24GB VRAM)                   │    │
│  │      ├── Model: gemma3:27b-it-qat (~16GB loaded)            │    │
│  │      └── PVC: 50Gi (model storage)                          │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### Data Flow Detail

| Step | Component | API / Method | Output |
|------|-----------|--------------|--------|
| 1 | `src/log_parser.py` | `GET /repos/{repo}/actions/runs/{run_id}/jobs` | Failed job list |
| 2 | `src/log_parser.py` | `GET /repos/{repo}/actions/jobs/{job_id}/logs` | Raw log ZIP |
| 3 | `src/log_parser.py` | Pattern matching + trim | `ParsedLogs` object |
| 4 | `src/rca_prompt.py` | Template formatting | Messages array |
| 5 | `src/ollama_client.py` | `POST /api/chat` | Raw JSON string |
| 6 | `src/output_formatter.py` | JSON + Markdown | PR comment + artifact |
| 7 | `scripts/post_results.py` | `POST /repos/{repo}/issues/{pr}/comments` | PR comment |

---

## 4. Prerequisites

### Infrastructure Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| GPU VRAM | 24GB (T4) | 48GB (L4 or A100) |
| CPU (Ollama pod) | 2 cores | 8 cores |
| RAM (Ollama pod) | 8GB | 32GB |
| PVC storage | 50GB | 100GB (for model + future versions) |
| K8s version | 1.24+ | 1.28+ |
| NVIDIA device plugin | Required | Required |

### Access Requirements

| Resource | Permission Needed | Who to Request From |
|----------|------------------|---------------------|
| GitHub PAT | `actions:read`, `pull_requests:write` | GitHub Enterprise admin |
| Repo secrets | `admin` or `maintain` on repo | Team lead / repo owner |
| K8s namespace | `create` on target cluster | Platform / DevOps team |
| GPU node pool | Exists with T4/L4 | Infra / Cloud team |
| Self-hosted runner | Can register at org or repo level | GitHub Enterprise admin |
| Firewall (if NodePort) | Port 30434 runner→GDC nodes | Network / Infra team |

### Local Development Requirements

```bash
# Check Python version (3.11+ required)
python3 --version

# Install dependencies
pip install -r requirements.txt

# Run tests (no external services needed)
python -m pytest tests/ -v
```

---

## 5. Phase 1 — Infrastructure Setup (Day 1–2)

### Step 1.1 — Request GitHub PAT

**Fine-Grained PAT (recommended)**:

1. Go to `GitHub Enterprise` → Your Profile → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Click **Generate new token**
3. Set Resource owner to your **Fabric2 org**
4. Set Expiry to **90 days** (rotate to GitHub App for production)
5. Set Repository access to the Fabric2 repos you want to monitor
6. Set permissions:
   - **Actions** → Read
   - **Contents** → Read
   - **Pull Requests** → Read and write
   - **Issues** → Read and write
   - **Metadata** → Read (auto-required)

**Classic PAT (fallback)**:

If fine-grained PAT requires admin approval that takes too long:
1. Go to Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Scopes: `repo`, `workflow`

Store the token — you'll use it as the `RCA_GITHUB_TOKEN` secret.

### Step 1.2 — Verify GITHUB_TOKEN Workflow Permissions

In your GitHub org settings:
- Go to `Organization Settings` → Actions → General → Workflow permissions
- Ensure "Read and write permissions" is selected, OR the rca_trigger.yml's explicit `permissions:` block is respected

### Step 1.3 — Deploy Kubernetes Resources

```bash
# 1. Create namespace
kubectl apply -f k8s/namespace.yaml

# 2. Create PVC (model storage — do this before deployment)
kubectl apply -f k8s/pvc.yaml

# 3. Verify PVC is bound
kubectl get pvc -n ollama-rca
# Expected: ollama-model-storage   Bound   ...   50Gi

# 4. Deploy Ollama (will schedule on GPU node)
kubectl apply -f k8s/deployment.yaml

# 5. Watch pod come up
kubectl get pods -n ollama-rca -w
# Expected: ollama-rca-xxxxx   0/1   Pending → Running

# 6. Deploy service (ClusterIP for air-gapped, NodePort if runner is external)
kubectl apply -f k8s/service.yaml      # OR k8s/service-nodeport.yaml

# 7. Verify service
kubectl get svc -n ollama-rca
```

**Troubleshooting pod stuck in Pending:**
```bash
kubectl describe pod -n ollama-rca <pod-name>
# Look for: "0/1 nodes are available: 1 Insufficient nvidia.com/gpu"
# Fix: Check GPU node pool and device plugin
kubectl get nodes -l cloud.google.com/gke-accelerator
kubectl get pods -n kube-system | grep nvidia
```

### Step 1.4 — Pull the Gemma Model

```bash
# Apply the one-time model pull Job
kubectl apply -f k8s/init-model-job.yaml

# Watch the pull progress (model is ~16GB, may take 10-20 min)
kubectl logs job/ollama-model-pull -n ollama-rca -f

# Verify model is loaded
kubectl port-forward svc/ollama-rca -n ollama-rca 11434:11434 &
curl http://localhost:11434/api/tags | python3 -m json.tool
# Should show gemma3:27b-it-qat in the models list
```

**Air-gapped cluster note**: If the cluster cannot pull from registry.ollama.ai, you must:
1. Pull the model on a machine with internet access: `ollama pull gemma3:27b-it-qat`
2. Export it: `ollama export gemma3:27b-it-qat > gemma3-27b.tar`
3. Load into the PVC via a helper pod that mounts the same PVC

---

## 6. Phase 2 — Application Deployment (Day 2–3)

### Step 2.1 — Set Up Project Repository

**Option A: Dedicated RCA repository**
```bash
# Create a new repo in your Fabric2 org for the RCA scripts
# Copy this entire project into it
git add -A
git commit -m "feat: initial build failure RCA agent"
git push origin main
```

**Option B: Add to an existing Fabric2 repo**
```bash
# Copy only the necessary files
cp -r src/           your-repo/src/rca/
cp -r scripts/       your-repo/scripts/rca/
cp requirements.txt  your-repo/rca-requirements.txt
```

### Step 2.2 — Configure Repository Secrets

Go to: `Repo` → Settings → Secrets and variables → Actions

| Secret | Value | Required |
|--------|-------|----------|
| `RCA_GITHUB_TOKEN` | PAT from Step 1.1 | Yes |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL | Optional |

**Org-level secrets** (if you have multiple Fabric2 repos):
- Go to `Org Settings` → Secrets and variables → Actions → New organization secret
- Select which repos can access it

### Step 2.3 — Run Preflight Check

Copy the project to the runner machine and run:

```bash
# Set environment variables first
export RCA_GITHUB_TOKEN="ghp_your_token"
export GITHUB_API_URL="https://api.github.com"   # or GHE URL
export OLLAMA_HOST="http://localhost:11434"        # or GDC URL

# Run check (all items should PASS before proceeding)
bash scripts/preflight_check.sh
```

Expected output when ready:
```
[PASS] Python 3.11+
[PASS] pip available
[PASS] requests library
[PASS] pytest available
[PASS] GitHub API reachable
[PASS] Actions API accessible
[PASS] Ollama server reachable
[PASS] Gemma model loaded
[PASS] Ollama inference works
[PASS] src/log_parser.py exists
...
ALL CHECKS PASSED (12 passed, 0 failed)
```

### Step 2.4 — Test Log Parsing Locally

```bash
# Install deps
pip install -r requirements.txt

# Test with a sample log
python scripts/parse_logs.py --input tests/sample_logs/build_failure.log

# Test with a real failed build (requires GITHUB_TOKEN)
export GITHUB_TOKEN="$RCA_GITHUB_TOKEN"
python -c "
from src.log_parser import parse_build_logs
result = parse_build_logs('your-org/your-repo', FAILED_RUN_ID_HERE, '$GITHUB_TOKEN')
print(f'Jobs: {len(result.jobs)}')
print(f'Duration: {result.total_duration_minutes:.1f} min')
print(f'Error lines extracted: {len(result.error_lines)}')
print(result.jobs_summary)
"
```

### Step 2.5 — Test Ollama Integration

```bash
export OLLAMA_HOST="http://localhost:11434"   # or port-forward first

# Connection test
python scripts/analyze_with_ollama.py --test
# Expected: "Ollama connection test PASSED"

# Full analysis test with sample log
python scripts/analyze_with_ollama.py --log-file tests/sample_logs/build_failure.log
# Should return JSON with root_cause, category, recommendation
```

### Step 2.6 — Run Full Pipeline Locally

```bash
export GITHUB_TOKEN="$RCA_GITHUB_TOKEN"
export OLLAMA_HOST="http://localhost:11434"

python scripts/run_rca.py \
    --repo your-org/your-repo \
    --run-id <FAILED_RUN_ID> \
    --workflow-name "release_build" \
    --branch "main" \
    --sha "abc123" \
    --actor "your-username"

# Review outputs
cat rca_output/rca_report.md
cat rca_output/rca_report.json
```

---

## 7. Phase 3 — GitHub Actions Integration (Day 3–4)

### Step 3.1 — Update rca_trigger.yml

Edit `.github/workflows/rca_trigger.yml` before committing:

```yaml
# 1. Match your actual workflow names EXACTLY (case-sensitive)
on:
  workflow_run:
    workflows:
      - "release_build"    # Must match `name:` field in release_build.yml
      - "snapshot_build"   # Must match `name:` field in snapshot_build.yml

# 2. Match your enterprise runner labels
runs-on: [self-hosted, linux, gdc]

# 3. Set correct Ollama URL for your runner scenario
env:
  OLLAMA_HOST: "http://ollama-rca.ollama-rca.svc.cluster.local:11434"
  # OR: "http://<GDC_NODE_IP>:30434"  (NodePort for external runner)
```

### Step 3.2 — Commit to Default Branch

**Critical**: The `workflow_run` trigger ONLY activates from the default branch.

```bash
git add .github/workflows/rca_trigger.yml
git commit -m "feat: add build failure RCA workflow"
git push origin main   # MUST be main/master, not a feature branch
```

Verify it's on the default branch:
```bash
git log main --oneline -- .github/workflows/rca_trigger.yml | head -1
```

### Step 3.3 — Enable Actions Allowlist (if needed)

If your enterprise restricts Actions to an allowlist, ask your admin to add:
- `actions/checkout@v4`
- `actions/setup-python@v5`
- `actions/upload-artifact@v4`

Or pin to SHA for stricter environments:
```yaml
uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
```

### Step 3.4 — First End-to-End Test

**Option A: Wait for a real failure** — Push a commit that breaks the build.

**Option B: Re-run a past failure** — Go to Actions → find a failed build → click "Re-run jobs".

**Option C: Manual trigger** — Temporarily add `workflow_dispatch` to rca_trigger.yml:
```yaml
on:
  workflow_dispatch:
    inputs:
      run_id:
        description: 'Failed run ID to analyze'
        required: true
  workflow_run:
    ...
```
Then trigger: Actions → Build Failure RCA → Run workflow → enter run_id.

### Step 3.5 — Verify End-to-End

After a test run:

1. **Check the RCA workflow ran**: Actions tab → "Build Failure RCA" → should show a run
2. **Download the artifact**: Click the run → Artifacts section → download `rca-report-{run_id}`
3. **Check the PR comment**: If the failed build was from a PR, the RCA comment should appear
4. **Check Slack** (if configured): The notification should arrive

---

## 8. Phase 4 — Testing & Validation (Day 4–5)

### Run Unit Tests

```bash
python -m pytest tests/ -v

# Expected output:
# tests/test_log_parser.py::TestExtractErrorContext::test_finds_error_lines PASSED
# tests/test_log_parser.py::TestExtractErrorContext::test_finds_test_failures PASSED
# ... (all 25+ tests should pass)
```

### Test Coverage Report

```bash
python -m pytest tests/ --cov=src --cov-report=term-missing
```

### Test With All Sample Logs

```bash
for log in tests/sample_logs/*.log; do
    echo "=== Testing: $log ==="
    python scripts/parse_logs.py --input "$log"
done
```

### Validate RCA Quality

Run the pipeline against 5–10 real past failures and check:
- [ ] **test_failure** category: Does it name the specific failing test?
- [ ] **compilation_error**: Does it identify the file and line number?
- [ ] **timeout**: Does it identify which step timed out?
- [ ] **resource_exhaustion**: Does it mention OOM and suggest heap increase?
- [ ] **Confidence accuracy**: High confidence = usually correct?

---

## 9. Enterprise Configuration Reference

### GITHUB_API_URL

Always read from environment — never hardcode:
```python
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
```

In the workflow, this is automatically set to the correct value:
```yaml
env:
  GITHUB_API_URL: ${{ github.api_url }}
```

For GitHub Enterprise Server, `github.api_url` resolves to:
`https://YOUR_GHE_HOST/api/v3`

### Enterprise CA Certificate

If runners get SSL certificate errors connecting to GitHub Enterprise:

```bash
# Option 1: System trust store (persistent)
sudo cp enterprise-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates

# Option 2: Python requests env var (workflow-scoped)
export REQUESTS_CA_BUNDLE=/path/to/enterprise-ca.crt

# Option 3: In the workflow (add to env block)
env:
  REQUESTS_CA_BUNDLE: /etc/ssl/certs/ca-bundle.crt
```

### Corporate Proxy

If the runner uses a proxy to reach GitHub:

```bash
export HTTP_PROXY=http://proxy.corp.com:8080
export HTTPS_PROXY=http://proxy.corp.com:8080
# CRITICAL: Ollama must bypass the proxy
export NO_PROXY=localhost,127.0.0.1,.svc.cluster.local,<GDC_NODE_IP>
```

### Runner Networking Scenarios

| Your Setup | Runner Location | Ollama URL to Use | Action Needed |
|------------|-----------------|-------------------|---------------|
| Scenario A | Inside GDC K8s (ARC) | `http://ollama-rca.ollama-rca.svc.cluster.local:11434` | Nothing |
| Scenario B | VM peered with GDC | `http://<GDC_NODE_IP>:30434` | Apply service-nodeport.yaml |
| Scenario C | Separate network | Deploy a runner inside GDC | Register new runner |

### All Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` | — | **Required.** PAT with actions:read, pull-requests:write |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub API base URL |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama service URL |
| `OLLAMA_MODEL` | `gemma3:27b-it-qat` | Model name |
| `MAX_LOG_TOKENS` | `80000` | Max tokens to send to Gemma |
| `BUILD_TIME_THRESHOLD_MINUTES` | `20` | Flag if build exceeds this |
| `SLACK_WEBHOOK_URL` | — | Optional Slack webhook |
| `REQUESTS_CA_BUNDLE` | — | Enterprise CA cert path |

---

## 10. Best Practices

### Security

1. **Use fine-grained PATs** with minimal scope. Avoid classic PATs with full `repo` access in production.
2. **Rotate PATs every 90 days**. Migrate to a GitHub App for production — installation tokens auto-rotate.
3. **Never log the GitHub token** in workflow output. The scripts use it only in HTTP headers.
4. **Ollama stays internal**. Never expose the Ollama ClusterIP externally. The air-gapped design is intentional.
5. **Secrets at org level** if you have multiple repos — avoids per-repo secret sprawl.

### Reliability

1. **Never fail the build workflow**. The RCA pipeline always writes a fallback report and exits 0. Build outcomes are determined by the build, not the RCA analysis.
2. **Set timeouts**. The workflow has a 15-minute cap. Ollama requests use 180s timeout with 3 retries + exponential backoff.
3. **Health check before inference**. `OllamaClient.health_check()` is called before sending 80K tokens to verify the model is loaded.
4. **Keep Gemma always loaded**: `OLLAMA_KEEP_ALIVE=24h` prevents the model from being unloaded between builds.

### Log Parsing Quality

1. **Prefer error extracts over full logs**. `extract_error_context()` pulls only lines matching error patterns + 10 lines of surrounding context. This focuses the model's attention and reduces token usage.
2. **Head + tail trimming**. If logs are still too long, the trimmer keeps the first third (build setup) and last two-thirds (failure output) — the most important sections.
3. **Add error patterns for your stack**. If your builds use a non-standard framework, add its error signatures to `ERROR_PATTERNS` in `src/log_parser.py`.

### Prompt Engineering

1. **Low temperature (0.3)**. RCA is factual analysis, not creative writing. Low temperature = more deterministic, reproducible output.
2. **Structured JSON output**. The prompt specifies exact JSON schema. The response parser handles `json` code fences gracefully.
3. **Context limit headroom**. Logs are trimmed to 80K tokens max. `num_ctx: 32768` in the request (the current configured value) should match actual Gemma deployment context.

> **Note**: If you increase `MAX_LOG_TOKENS`, also increase `num_ctx` proportionally. Current setting: 80K token logs, 32K context window.
> To use the full Gemma 128K context: set `num_ctx: 131072` and `MAX_LOG_TOKENS: 100000`.

### Operational

1. **Run preflight check before every deployment** to a new environment.
2. **Monitor GPU memory**: `kubectl top pod -n ollama-rca`. Gemma 27B uses ~16GB VRAM; ensure T4 (16GB) with no other GPU workloads, or use L4 (24GB).
3. **Archive RCA artifacts**: 30-day retention is good for the POC. Increase to 90 days for production analytics.
4. **Test workflow_run trigger with exact name matching**: `workflows: ["release_build"]` is case-sensitive and must match the `name:` field exactly.

---

## 11. Troubleshooting

### RCA workflow doesn't trigger after build failure

**Symptom**: Build fails but "Build Failure RCA" workflow never appears in Actions tab.

**Causes and fixes**:
```
1. Workflow file not on default branch
   → git log main -- .github/workflows/rca_trigger.yml | head -1

2. workflow_run.workflows names don't match exactly
   → Check spelling and case in rca_trigger.yml vs the build workflow `name:` field

3. GitHub Actions disabled for the repo
   → Settings → Actions → General → "Allow all actions"
```

### "Waiting for a runner to pick up this job"

**Symptom**: RCA workflow is queued but never starts.

**Fixes**:
- Check `runs-on` labels in rca_trigger.yml match your runner's labels
- Run: `curl -H "Authorization: token $RCA_GITHUB_TOKEN" "$GITHUB_API_URL/repos/$REPO/actions/runners"`
- Verify the runner is Online (not Offline or Idle-for-too-long)

### "Resource not accessible by integration"

**Symptom**: Steps fail with HTTP 403 when posting PR comments.

**Fix**: The `GITHUB_TOKEN` default may be read-only in your enterprise. The workflow already uses `RCA_GITHUB_TOKEN` (your PAT). Verify the secret is set:
```
Repo → Settings → Secrets and variables → Actions → RCA_GITHUB_TOKEN should be listed
```

### Connection refused to Ollama

**Symptom**: `OllamaClient.health_check()` returns False, logs show connection error.

**Debug steps**:
```bash
# From the runner, test direct connectivity
curl http://ollama-rca.ollama-rca.svc.cluster.local:11434/api/tags
# If that fails, try NodePort:
curl http://<GDC_NODE_IP>:30434/api/tags
# If NodePort works, update OLLAMA_HOST in the workflow
```

### "SSL: CERTIFICATE_VERIFY_FAILED"

**Symptom**: Python requests throws SSL error when calling GitHub Enterprise API.

**Fix**: See [Enterprise CA Certificate](#enterprise-ca-certificate) section above.

### Model not found / Wrong model name

**Symptom**: Health check passes but inference fails, or returns empty response.

**Fix**:
```bash
# Check what model is actually loaded
kubectl exec -n ollama-rca deployment/ollama-rca -- ollama list
# Example output: gemma3:27b-it-qat   7f...   16 GB
# Update OLLAMA_MODEL in rca_trigger.yml to match exactly
```

### RCA quality is poor

**Symptom**: Gemma returns "unknown" category or generic recommendations.

**Possible causes**:
- Log extraction is missing errors (add more patterns to `ERROR_PATTERNS`)
- Logs are being over-trimmed (increase `MAX_LOG_TOKENS`)
- Build uses a framework Gemma doesn't recognize (add tech-stack context to system prompt)
- Model is running out of context (check `num_ctx` vs actual prompt size)

---

## 12. Production Hardening Roadmap

After the POC succeeds, prioritize these improvements:

### Security Hardening
- [ ] **Migrate to GitHub App** — installation tokens auto-rotate, better audit trail
- [ ] **mTLS for Ollama** — put NGINX ingress with mTLS in front of Ollama service
- [ ] **Secret scanning** — ensure no secrets leak into RCA artifacts (log masking)

### Reliability
- [ ] **Ollama pod disruption budget** — `minAvailable: 1` to prevent accidental deletion
- [ ] **Pod resource limits tuning** — profile actual GPU/RAM usage and right-size
- [ ] **Dead letter queue** — if RCA fails 3x, create a GitHub Issue for manual review
- [ ] **Multi-zone node pool** — if build load is high across time zones

### Observability
- [ ] **Prometheus metrics** — instrument OllamaClient with inference latency, success rate
- [ ] **Grafana dashboard** — RCA category distribution, confidence trends, build time trends
- [ ] **Alert on Ollama pod down** — PagerDuty/Slack alert if the pod is unhealthy for >5 min

### Scale & Quality
- [ ] **Expand to all Fabric2 repos** — copy rca_trigger.yml; scripts live in shared repo
- [ ] **RCA accuracy tracking** — developers rate the RCA quality (was it helpful?)
- [ ] **Fine-tune Gemma** — LoRA fine-tuning on your historical build failures for higher accuracy
- [ ] **Trending dashboard** — which repos fail most? Which failure category is increasing?
- [ ] **Jira/ServiceNow integration** — auto-create tickets for repeated failures

### Model Management
- [ ] **Model versioning** — track which Gemma version produced each RCA
- [ ] **A/B testing** — run two models in parallel and compare quality scores
- [ ] **Quarterly model updates** — Gemma releases new versions; evaluate for improvements

---

*For access requests and day-by-day execution plan, see [ENTERPRISE_ACCESS_AND_5DAY_PLAN.md](ENTERPRISE_ACCESS_AND_5DAY_PLAN.md).*
*For Kubernetes deployment details, see all YAML files in [k8s/](k8s/).*
