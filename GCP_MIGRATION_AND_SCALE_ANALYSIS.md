# Build Failure RCA System — GCP Migration & Scale Analysis

> **Audience**: Engineering leadership, platform team, SREs  
> **Scope**: 80 microservices · 200+ engineers · 100+ releases/month  
> **Question**: What changes when we move from Fabric2 (GDC) to GCP, and can Gemma handle the load?

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current vs GCP Architecture — What Actually Changes](#2-current-vs-gcp-architecture--what-actually-changes)
3. [Load & Capacity Analysis](#3-load--capacity-analysis)
4. [Can Gemma 3 27B Handle This Scale?](#4-can-gemma-3-27b-handle-this-scale)
5. [GCP-Native Architecture (Recommended)](#5-gcp-native-architecture-recommended)
6. [Factors That Impact Performance](#6-factors-that-impact-performance)
7. [Factors That Impact Accuracy](#7-factors-that-impact-accuracy)
8. [Migration Phases](#8-migration-phases)
9. [Cost Analysis](#9-cost-analysis)
10. [Risk Register](#10-risk-register)
11. [Decision Matrix — Model & Deployment Options](#11-decision-matrix--model--deployment-options)
12. [Recommendations](#12-recommendations)

---

## 1. Executive Summary

**Short answer**: Gemma 3 27B on a single GPU pod can technically handle your average load (~51 RCA requests/day), but your peak scenarios and the 80-microservice diversity create risks that require architectural changes when moving to GCP.

**The migration from Fabric2 (GDC) to GCP is actually an opportunity** — you go from a constrained air-gapped design to a fully cloud-native architecture with auto-scaling, managed GPUs, better observability, and the option to use Vertex AI's managed Gemma instead of self-hosting Ollama.

**The three biggest concerns at your scale:**

| Concern | Risk Level | Mitigation |
|---------|-----------|------------|
| Simultaneous build failures (burst load) | **High** | Queue + multi-pod autoscaling |
| Diverse tech stacks across 80 microservices | **Medium** | Stack-aware prompt enrichment |
| Model accuracy degrading on complex failures | **Medium** | Feedback loop + prompt tuning per team |

---

## 2. Current vs GCP Architecture — What Actually Changes

### 2.1 Component-by-Component Comparison

| Component | Current (Fabric2 / GDC) | After GCP Migration | Change Required |
|-----------|------------------------|---------------------|-----------------|
| **GitHub** | GitHub Enterprise Server (Fabric2, on-prem) | GitHub Enterprise Cloud (GHEC) or GitHub.com | Update `GITHUB_API_URL` — but it's already env-var driven, so zero code change |
| **CI Runners** | Self-hosted runners on GDC VMs / ARC pods | GitHub-hosted runners (standard) OR self-hosted on GKE | If using GitHub-hosted: rethink Ollama network access entirely |
| **Ollama service** | GDC ClusterIP (air-gapped) | GKE ClusterIP + Internal Load Balancer, or Vertex AI | Full redesign of networking |
| **GPU nodes** | GDC node pool (T4/L4, physical) | GKE Autopilot GPU nodes or standard node pools (T4/L4/A100) | Node pool config change |
| **Model storage** | 50Gi PVC on GDC | GCS bucket + PVC on GKE, or Vertex AI Model Registry | Storage backend change |
| **Artifacts** | GitHub Actions artifact (~30-day) | GCS bucket (configurable retention) + GitHub artifact | Artifact upload destination |
| **Secrets** | GitHub Actions secrets | GitHub Actions secrets + Secret Manager, OR Workload Identity | Auth improvement available |
| **Monitoring** | kubectl logs, basic | Cloud Monitoring, Cloud Logging, Cloud Trace | Major improvement |
| **Networking** | Air-gapped, ClusterIP only | VPC-native GKE, internal LB options | Firewall rules simplified |

### 2.2 What Does NOT Change

These parts of the RCA system are **completely portable** with zero modification:

- All Python source code (`src/`, `scripts/`)
- The GitHub Actions workflow (`rca_trigger.yml`) — `${{ github.api_url }}` handles GHE → GHEC automatically
- The RCA prompt and JSON output schema
- Unit tests
- Error pattern library in `log_parser.py`

### 2.3 The Critical Network Problem

This is the **most important thing** to resolve before migration.

**Current flow (GDC):**
```
Runner (inside GDC network) ──► Ollama ClusterIP :11434 (direct)
```

**Post-migration options on GCP:**

```
Option A — Self-hosted runners on GKE (best for security):
  GKE runner pod ──► Ollama ClusterIP :11434 (same cluster, direct)
  Same as today, just on GCP. Zero networking change.

Option B — GitHub-hosted runners (simplest CI management):
  GitHub-hosted runner ──► ??? ──► Ollama on GKE
  Problem: GitHub-hosted runners are on GitHub's network, not your GCP VPC.
  Solutions:
    a) Expose Ollama via Cloud IAP + Internal Load Balancer + VPN (complex)
    b) Move from Ollama to Vertex AI Endpoints (managed, no VPC tunnel needed)
    c) Cloud Run with GPU (serverless Ollama, accessible via HTTPS)

Option C — Hybrid (recommended for transition):
  Self-hosted runners on GKE for RCA jobs only
  GitHub-hosted runners for regular build jobs
  RCA workflow: runs-on: [self-hosted, gke-rca]
```

**Recommendation**: Use **Option A** (self-hosted runners on GKE via Actions Runner Controller) for the RCA workflow. Keep GitHub-hosted runners for the build workflows themselves. This preserves the current architecture pattern with zero code changes.

---

## 3. Load & Capacity Analysis

### 3.1 Estimated RCA Request Volume

Based on your scale (80 microservices, 200 engineers, 100+ releases/month):

```
Engineers:              200
Active PRs/week:        ~600  (3 per engineer)
PR build failure rate:  18%   (industry average for active teams)
CI branch builds/day:   300   (200 engineers × 1.5 commits/day)
CI failure rate:        12%

Monthly breakdown:
  Failed PR builds:      432 / month
  Failed release builds:  20 / month  (100 releases × 20% failure)
  Failed CI builds:     1080 / month

Total RCA requests:    ~1,532 / month
Average daily:           ~51 / day
Peak day (sprint end):  ~153 / day  (3× average, conservatively)
Peak hour (large merge): ~15–25 simultaneous failures
```

### 3.2 Concurrency Model — The Real Bottleneck

This is where the current single-pod design shows its limits:

**Scenario: End-of-sprint merge wave (realistic)**

```
14:00 — 8 PRs merged to main simultaneously
14:01 — All 8 trigger release_build
14:04 — 3 of 8 builds fail
14:04 — 3 rca_trigger.yml workflows fire simultaneously
14:04 — All 3 try to POST to Ollama /api/chat

Current design (OLLAMA_NUM_PARALLEL=2, single pod):
  Request 1: starts immediately ──► completes at 14:05:15
  Request 2: starts immediately ──► completes at 14:05:45
  Request 3: queued ──────────────► starts at 14:05:15, completes at 14:06:30

Acceptable! 3 concurrent requests → all done in ~2.5 minutes.
```

**Scenario: Large release day — all 80 microservices releasing**

```
If release day for all services: 80 builds fire
Assume 20% failure: 16 simultaneous RCA requests

Current design (single pod, OLLAMA_NUM_PARALLEL=2):
  Slots available: 2
  Queue depth: 14
  Time to process all 16: 16 × 60s / 2 parallel = 480 seconds = 8 minutes
  Last engineer waits 8 minutes for their RCA. Acceptable.

If 40% failure (bad release day): 32 requests
  Time to process all 32: 32 × 60s / 2 = 960 seconds = 16 minutes
  Borderline acceptable. GitHub Actions job timeout (15 min) could trigger.
```

**Scenario: Thundering herd (worst case)**

```
Mass refactor merged → all 80 microservices fail simultaneously
  80 RCA requests × 60s each / 2 parallel = 2400 seconds = 40 minutes
  Most jobs time out (15-min timeout).
  Fallback reports written, but no AI analysis.
  This is the design limit of single-pod Ollama.
```

### 3.3 Single Pod Throughput vs Your Load

| GPU | Inference time (80K tokens) | Requests/day (single pod) | Meets avg load? | Meets peak? |
|-----|----------------------------|--------------------------|-----------------|-------------|
| T4 (16 GB) | ~75 sec | ~1,150 | ✅ Yes | ✅ Yes (153/day) |
| L4 (24 GB) | ~45 sec | ~1,920 | ✅ Yes | ✅ Yes |
| A100 (40 GB) | ~20 sec | ~4,320 | ✅ Yes | ✅ Yes |
| T4 with PARALLEL=2 | ~75 sec | ~2,300 | ✅ Yes | ✅ Yes |

**Verdict**: Single pod handles average load comfortably. Peak hourly bursts (15–25 simultaneous) are the concern, not daily volume. The fix is concurrency management, not raw throughput.

---

## 4. Can Gemma 3 27B Handle This Scale?

### 4.1 Yes — With Architecture Changes

Gemma 3 27B is well-suited for RCA at your scale **with the right setup**. Here is an honest breakdown:

**Where Gemma 3 27B excels:**
- Long-context reasoning (128K context → fits even very long logs)
- Pattern recognition across diverse build failure types
- Structured JSON output (consistent schema)
- Code and stack trace comprehension (trained on code datasets)
- Running fully on-prem / in GCP with no data egress

**Where it struggles at your scale:**
- **Simultaneous requests**: It's a large model — concurrency is limited by VRAM (2 requests max on T4)
- **Slow cold start**: Loading 16GB weights takes 2–3 minutes. If the pod restarts, the first few RCA requests after restart are slow
- **Diverse microservice stacks**: If your 80 services span Java, Go, Python, Node.js, Rust, and different test frameworks, a generic prompt may miss nuances
- **Long dependency chains**: Maven multi-module builds with 50+ submodules can produce logs where the root cause is buried 20 steps before the failure

### 4.2 Model Sizing Options for GCP

When you move to GCP, you have better model options:

```
Option A: Keep Gemma 3 27B QAT on L4 (current approach, proven)
  + Highest quality analysis
  + You already have prompts tuned for it
  - 45–75s per request, limited concurrency

Option B: Gemma 3 9B (smaller, faster)
  + 3× faster inference (~15–25s on L4)
  + Can run PARALLEL=4 on 24GB VRAM
  + Lower cost
  - Slightly lower accuracy on complex failures (~5–10% degradation)
  Recommended for: high-volume PR build failures (quick triage)

Option C: Two-tier model routing (BEST for your scale)
  Fast tier:  Gemma 3 9B  → classify failure category in <15 sec
  Deep tier:  Gemma 3 27B → full RCA only for high-priority failures
  
  Routing logic:
    if build == "release" or branch == "main": → 27B (deep analysis)
    if build == "PR" and failure_count > 1:   → 9B  (fast triage)
    if build == "PR" and first_failure:       → 9B  (fast triage)
    if category == "infra_flake":             → skip (no model call)
  
  This reduces 27B load by ~60%, cuts average response time to <20s
  for most engineers.

Option D: Vertex AI Gemma (managed by Google)
  + Auto-scales to 1000+ requests/second
  + No GPU infrastructure to manage
  + SLA-backed
  - Data sent to Google APIs (check data residency requirements)
  - ~$0.003–0.010 per 1K tokens (cost adds up at 1,532 requests/month)
  - Less control over model version, context, prompts
```

### 4.3 VRAM Math for Concurrent Requests

```
Gemma 3 27B QAT (Q4_0):
  Model weights:         ~16.0 GB
  KV cache (32K ctx):    ~2.0 GB per request
  Overhead:              ~1.5 GB

T4 (16 GB):
  Available after model: ~0 GB for KV cache → 1 request max safely
  With PARALLEL=2: swaps to RAM, severe slowdown. Avoid.

L4 (24 GB):
  Available after model: ~6.5 GB → fits 2–3 requests concurrently
  OLLAMA_NUM_PARALLEL=2: safe and stable
  OLLAMA_NUM_PARALLEL=3: possible, may cause OOM on long logs

A100 40GB:
  Available after model: ~22.5 GB → fits 10+ requests
  OLLAMA_NUM_PARALLEL=8: comfortable
  Best choice for burst workloads

Gemma 3 9B QAT:
  Weights: ~5.5 GB
  L4 (24 GB): can run 3× 9B models simultaneously or 27B + 9B together
  Enables the two-tier architecture above.
```

---

## 5. GCP-Native Architecture (Recommended)

### 5.1 Target Architecture for 80 Microservices

```
┌────────────────────────────────────────────────────────────────────────────┐
│                    GitHub Enterprise Cloud (GHEC)                           │
│                                                                              │
│  80 repos × (release_build + snapshot_build + rca_trigger.yml)             │
│                                                                              │
│  Org-level secrets: RCA_GITHUB_TOKEN · SLACK_WEBHOOK_URL · OLLAMA_HOST    │
└────────────────────────┬───────────────────────────────────────────────────┘
                         │ workflow_run: completed (failure)
                         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│           GKE Cluster — Actions Runner Controller (ARC)                     │
│           Self-hosted runner pods — namespace: github-runners               │
│                                                                              │
│  rca_trigger.yml runs here:                                                  │
│    ① Fetch logs (GitHub REST API)                                           │
│    ② Parse + classify failure type                                          │
│    ③ Route to fast or deep analysis tier                                    │
│    ④ POST to RCA Service                                                    │
│    ⑤ Write PR comment + artifact + Slack                                   │
└────────────────────────┬───────────────────────────────────────────────────┘
                         │ gRPC / HTTP to internal LB
                         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│              GKE Cluster — Namespace: rca-service                           │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Cloud Tasks Queue (RCA request buffer)                              │   │
│  │  Max concurrent: 10   Task timeout: 300s   Retry: 3×               │   │
│  └─────────────────────────┬───────────────────────────────────────────┘   │
│                             │                                               │
│           ┌─────────────────┼─────────────────────┐                       │
│           ▼                 ▼                       ▼                       │
│  ┌─────────────┐   ┌─────────────┐       ┌─────────────────────┐          │
│  │  Ollama     │   │  Ollama     │       │  Ollama             │          │
│  │  Pod #1     │   │  Pod #2     │  ...  │  Pod #N             │          │
│  │  Gemma 27B  │   │  Gemma 9B   │       │  (HPA auto-scale)   │          │
│  │  L4 GPU     │   │  L4 GPU     │       │                     │          │
│  │  Deep tier  │   │  Fast tier  │       │                     │          │
│  └──────┬──────┘   └──────┬──────┘       └──────────┬──────────┘          │
│         └─────────────────┴──────────────────────────┘                     │
│                             │                                               │
│  ┌──────────────────────────▼──────────────────────────────────────────┐   │
│  │  Shared PVC (ReadWriteMany) / GCS FUSE — model cache               │   │
│  │  gemma3:27b-it-qat  +  gemma3:9b-it-qat   (no re-download)        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└────────────────────────┬───────────────────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
  ┌──────────────┐ ┌──────────┐ ┌──────────────────┐
  │  GCS Bucket  │ │  GitHub  │ │  Slack / PagerDuty│
  │  rca-reports │ │  PR      │ │  Notifications    │
  │  (long-term) │ │  Comment │ └──────────────────┘
  └──────────────┘ └──────────┘
          │
          ▼
  ┌──────────────────────────────────────────────────┐
  │  BigQuery — rca_analytics dataset                 │
  │  • Failure category trends per microservice       │
  │  • Build time regression detection               │
  │  • Accuracy scores (thumbs up/down on PR)        │
  │  • Engineer MTTR before/after RCA                │
  └──────────────────────────────────────────────────┘
```

### 5.2 HPA Configuration for GPU Pods

```yaml
# Scale based on Cloud Tasks queue depth (custom metric)
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: ollama-27b-hpa
  namespace: rca-service
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: ollama-rca-27b
  minReplicas: 1      # Always 1 warm pod (avoid cold start delays)
  maxReplicas: 4      # 4× L4 = 96 GB VRAM total, handles any burst
  metrics:
    - type: External
      external:
        metric:
          name: cloudtasks.googleapis.com/queue/depth
          selector:
            matchLabels:
              queue_name: rca-requests-deep
        target:
          type: AverageValue
          averageValue: "3"  # Scale up when queue > 3 items
```

### 5.3 Two-Tier Request Routing

Add this routing layer to `scripts/run_rca.py` before the Ollama call:

```python
def select_analysis_tier(
    workflow_name: str,
    branch: str,
    failure_category_hint: str,  # quick pre-classification
    is_repeat_failure: bool,
) -> str:
    """
    Route to 27B (deep) or 9B (fast) model based on priority.
    Returns the OLLAMA_HOST to use.
    """
    deep_host = os.environ.get("OLLAMA_HOST_DEEP")   # 27B pod
    fast_host = os.environ.get("OLLAMA_HOST_FAST")   # 9B pod

    # Always use deep analysis for release/main branch failures
    if workflow_name in ("release_build",) or branch in ("main", "master", "release"):
        return deep_host

    # Infrastructure flakes don't benefit from 27B - save GPU time
    if failure_category_hint == "infra_flake":
        return fast_host

    # Repeat failures on same PR → already analyzed, quick update
    if is_repeat_failure:
        return fast_host

    # Default PR builds → fast tier (15-25 sec vs 45-75 sec)
    return fast_host
```

---

## 6. Factors That Impact Performance

### 6.1 GPU Cold Start — Silent Killer

**Problem**: When the Ollama pod restarts (node maintenance, OOM, scale-up), Gemma 27B takes **2–5 minutes to load** into VRAM. Any RCA request during this window waits in queue.

**Impact at your scale**: With 100+ releases/month, pod restarts will happen. If a restart coincides with a large release, engineers wait 5+ minutes for RCA.

**Fix**:
```yaml
# Keep model always loaded between requests
env:
  - name: OLLAMA_KEEP_ALIVE
    value: "-1"          # Never unload (GCP: you pay for the GPU anyway)

# Pod Disruption Budget — prevent accidental deletion
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: ollama-pdb
spec:
  minAvailable: 1         # Always keep 1 pod running during node upgrades
  selector:
    matchLabels:
      app: ollama-rca-27b
```

**Also**: Pre-warm the model on pod startup using an init container that sends a dummy inference request before the pod is marked Ready. The readinessProbe on `/api/tags` passes before the model is warm.

```yaml
lifecycle:
  postStart:
    exec:
      command:
        - /bin/sh
        - -c
        - |
          until curl -s http://localhost:11434/api/tags | grep -q gemma; do sleep 2; done
          # Force model load into VRAM before pod accepts traffic
          curl -s -X POST http://localhost:11434/api/chat \
            -d '{"model":"gemma3:27b-it-qat","stream":false,"messages":[{"role":"user","content":"hi"}]}'
```

### 6.2 Log Size Variability

**Problem**: Your 80 microservices will produce wildly different log sizes:
- Simple unit test failure: 500 lines
- Maven multi-module compilation error: 50,000 lines
- Full integration test suite with verbose logging: 200,000+ lines

**Current 80K token cap**: Good for most cases, but multi-module builds may produce truncated logs where the real error is in a dropped section.

**Fix — smarter trimming by failure type**:
```python
# src/log_parser.py enhancement
def smart_trim(log_text: str, failure_hint: str) -> str:
    if failure_hint == "compilation_error":
        # Compilation errors are near the beginning — keep more head
        return trim_head_heavy(log_text, head_ratio=0.6, max_tokens=80000)
    elif failure_hint == "test_failure":
        # Test failures are at the end — keep more tail
        return trim_tail_heavy(log_text, tail_ratio=0.7, max_tokens=80000)
    elif failure_hint == "timeout":
        # Need head (setup) + tail (what was running when timeout hit)
        return trim_balanced(log_text, max_tokens=80000)
    else:
        return trim_to_token_limit(log_text, max_tokens=80000)
```

### 6.3 GitHub API Rate Limits

**Problem**: With 200 engineers generating 1,532 RCA requests/month, you will hit GitHub API rate limits.

**GitHub API limits:**
- Authenticated requests: 5,000/hour per token
- Download logs endpoint: 1,000/hour per repo (not per token)
- At 1,532 requests/month = ~2/hour average → well within limits
- Peak burst: 30 requests × 3 API calls each = 90 calls in an hour → fine

**But**: If you use a single `RCA_GITHUB_TOKEN` for all 80 repos, all rate limits are shared across that token. At peak, 80 simultaneous requests could exhaust limits.

**Fix**: Use a **GitHub App** (not a PAT) for production. GitHub Apps have rate limits of 15,000 requests/hour per installation — 3× higher. They also auto-rotate tokens.

```python
# src/log_parser.py — GitHub App auth
def get_github_token_for_repo(repo: str) -> str:
    """Use GitHub App installation token instead of PAT."""
    app_id = os.environ["GITHUB_APP_ID"]
    private_key = os.environ["GITHUB_APP_PRIVATE_KEY"]
    installation_id = get_installation_id(repo, app_id, private_key)
    return get_installation_access_token(installation_id, app_id, private_key)
    # Installation tokens: 1-hour TTL, auto-rotate, 15K req/hour
```

### 6.4 Network Latency in GCP

**Current GDC**: Ollama is on the same physical cluster as the runner. Latency = microseconds.

**GCP**: Runner pod → Internal Load Balancer → Ollama pod. Latency = 1–5ms. For a 45-second inference, this is completely negligible.

**But**: Log download from GitHub API → runner. For large log ZIPs (50–100 MB), this adds 2–5 seconds. Not significant.

### 6.5 Disk I/O and PVC Performance

**Problem**: The 50Gi PVC storing the Gemma model is read on every pod start. On GDC, this was a local disk. On GCP, it's a Persistent Disk (pd-ssd).

| Storage Type | Sequential Read | Model Load Time | Cost |
|-------------|-----------------|-----------------|------|
| GDC local NVMe | ~3 GB/s | ~5 sec | Included |
| GCP pd-ssd | ~1.2 GB/s | ~13 sec | $0.17/GB/month |
| GCP pd-balanced | ~0.24 GB/s | ~65 sec | $0.10/GB/month |
| GCS FUSE | Variable | ~90 sec | $0.02/GB/month |
| RAM disk (tmpfs) | ~10 GB/s | N/A (must load fresh) | Free but volatile |

**Recommendation**: Use `pd-ssd` for the model PVC. The $8.50/month cost for 50Gi is worth the 5× speedup over `pd-balanced` for cold starts.

### 6.6 Memory Pressure from 80 Repos

**Problem**: With 80 microservices all potentially queuing RCA requests simultaneously, the Cloud Tasks queue could grow to 80 items. At 45s each with 2 parallel slots, the last item waits 30 minutes. GitHub Actions job timeout is 15 minutes.

**Fix**: Set a **timeout on the RCA job** that writes the fallback report rather than failing. Already implemented in the current code (`sys.exit(0)` on timeout). On GCP, also set a Cloud Tasks task deadline:

```python
# Task timeout: 10 minutes. After that, write fallback + exit.
task = {
    "http_request": {...},
    "dispatch_deadline": {"seconds": 600}  # 10 minutes
}
```

And in `run_rca.py`, add a wall-clock timeout:

```python
import signal

def _timeout_handler(sig, frame):
    logger.warning("RCA pipeline wall-clock timeout — writing fallback")
    _write_fallback_report(output_dir, metadata, "Pipeline wall-clock timeout")
    sys.exit(0)

signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(540)  # 9 minutes (leave 6 min before Actions timeout)
```

---

## 7. Factors That Impact Accuracy

### 7.1 The 80-Microservice Tech Stack Problem

With 80 microservices, you likely have:

| Tech Stack | % of services (estimated) | Accuracy of generic prompt |
|-----------|--------------------------|---------------------------|
| Java/Maven/Gradle (Spring Boot) | 40–50% | Very high — Gemma trained heavily on Java |
| Python/pytest | 15–20% | High |
| Node.js/npm | 10–15% | High |
| Go | 5–10% | Medium — less training data |
| Rust/Cargo | 1–5% | Medium |
| Terraform/IaC | 5–10% | Low — very different failure patterns |
| Docker/Container builds | 5–10% | Medium |
| Custom DSLs / internal frameworks | Variable | Low |

**Fix — stack-aware prompt enrichment**:

```python
# src/rca_prompt.py enhancement
STACK_CONTEXT = {
    "maven": "This is a Maven/Java build. Key patterns: [INFO] BUILD FAILURE, "
             "Tests run: X, Failures: Y, Errors: Z, Surefire test reports in "
             "target/surefire-reports/. Maven reactor: parent failure propagates "
             "to all child modules.",
    "gradle": "This is a Gradle build. Key patterns: FAILURE: Build failed with "
              "an exception, > Task :module:taskName FAILED, execution failed for task.",
    "pytest": "This is a Python/pytest build. Key patterns: FAILED tests/path.py::TestClass::method, "
              "E AssertionError, ImportError, fixture errors propagate to all tests using it.",
    "go": "This is a Go build. Key patterns: FAIL package/name, --- FAIL: TestName, "
          "panic:, build constraints, go.sum mismatch.",
    "terraform": "This is a Terraform/IaC deployment. Key patterns: Error:, "
                 "│ Error: Invalid value, Plan: X to add, state lock, provider errors.",
}

def detect_stack(log_text: str) -> str:
    if "BUILD FAILURE" in log_text and ("[INFO]" in log_text or "[ERROR]" in log_text):
        return "maven"
    if "> Task :" in log_text and "FAILED" in log_text:
        return "gradle"
    if "pytest" in log_text or "FAILED tests/" in log_text:
        return "pytest"
    if "go test" in log_text or "--- FAIL:" in log_text:
        return "go"
    if "terraform" in log_text.lower() or "│ Error:" in log_text:
        return "terraform"
    return "generic"
```

Then inject the stack context into the system prompt for every request. This alone can improve accuracy by **15–25%** for Go, Terraform, and Rust services.

### 7.2 Hallucination Risk on Unfamiliar Failures

Gemma 3 27B, like all LLMs, can **hallucinate confident-sounding but wrong recommendations** when the failure pattern is unfamiliar. Examples:

- **Custom internal framework failures**: Gemma invents a fix for `FabricDependencyResolutionException` that doesn't exist
- **Internal tool errors**: `fabric-ci: error code 42` → Gemma guesses what code 42 means
- **Obscure JVM errors**: Some JVM errors are rare enough that training data is thin

**Mitigation**:

1. **Confidence gating**: Only post PR comments for `confidence: high` or `medium`. For `confidence: low`, post a shorter comment saying "RCA confidence too low — logs attached for manual review."

2. **Add a "known unknowns" list to the system prompt**:
```
If you encounter error codes or tool names you don't recognize (e.g., internal 
tools, proprietary frameworks), explicitly state "unfamiliar tool/error" and 
set confidence to low rather than guessing.
```

3. **Feedback loop**: Add thumbs-up/thumbs-down buttons to PR comments via GitHub Reactions API. Store in BigQuery. After 500 samples, you'll know exactly which failure types have low accuracy.

### 7.3 Multi-Module Build Failures — Root Cause Hiding

**Problem**: In a Maven project with 20 submodules, the true root cause might be in module-core failing to compile, causing 19 downstream modules to also fail. The log tail (trimmed by default) shows 19 failures. The head (where the real error is) gets trimmed.

**Impact**: Gemma sees 19 `[ERROR] Compilation failed` messages without seeing the original `cannot find symbol` in module-core. It correctly identifies "compilation error" but may recommend fixing the wrong module.

**Fix — inter-module dependency awareness**:
```python
# Enhanced parse_build_logs() for multi-module builds
def extract_first_failure_in_reactor(log_text: str) -> str:
    """
    For Maven multi-module builds, find the FIRST module that failed
    in the reactor build order. All subsequent failures are cascades.
    """
    reactor_summary_pattern = re.compile(
        r"\[INFO\] Reactor Summary.*?\[INFO\] -{72}", re.DOTALL
    )
    first_failure_pattern = re.compile(
        r"\[INFO\]\s+(\S+)\s+\.\.\.\s+FAILURE\s+\[(.+?)\]"
    )
    # ... extract first failure module and prioritize its log section
```

### 7.4 Flaky Tests — False RCA

**Problem**: If a test is flaky (passes 90% of the time, fails 10% due to timing/concurrency), Gemma will confidently analyze the "failure" and recommend code changes that would break the test permanently.

**Impact at your scale**: With 80 microservices, you likely have dozens of flaky tests. Every time they fail, engineers get a PR comment saying "fix your test" when the test is actually fine.

**Fix — flakiness detection layer**:
```python
# Pre-processing step: check failure history before calling Gemma
def is_likely_flake(repo: str, sha: str, job_name: str, token: str) -> bool:
    """
    Check if this exact job failed then passed recently on different runs.
    If the same job has a recent pass → probable flake.
    """
    recent_runs = fetch_recent_runs_for_job(repo, job_name, token, limit=10)
    conclusions = [r["conclusion"] for r in recent_runs]
    # If 70%+ of recent runs passed: classify as probable flake
    pass_rate = conclusions.count("success") / len(conclusions) if conclusions else 0
    return pass_rate > 0.70

# In run_rca.py, before calling Ollama:
if is_likely_flake(repo, sha, job_name, token):
    rca = {
        "root_cause": "Probable flaky test — this job has passed in 7+ of last 10 runs",
        "category": "infra_flake",
        "confidence": "medium",
        "recommendation": "Re-run the build. If it fails consistently, investigate the test.",
    }
    # Write result directly, skip Gemma entirely (saves GPU time)
```

This skips Gemma for known flakes — saving GPU compute and stopping incorrect recommendations.

### 7.5 Prompt Drift Across 80 Repos

**Problem**: Each of your 80 repos has different build patterns, naming conventions, and error vocabularies. A single generic system prompt may produce inconsistent quality.

**Fix — per-team prompt configuration** (implement in Phase 3):
```yaml
# .rca-config.yml in each repo (optional override)
rca:
  build_system: maven
  primary_language: java
  test_framework: junit5
  known_flaky_tests:
    - "com.fabric2.payment.PaymentIntegrationTest"
    - "com.fabric2.auth.TokenRefreshIT"
  custom_context: |
    This service uses a custom Fabric2 messaging framework.
    FabricMessageException usually means the message broker is unavailable.
    DatabaseConnectionException in tests usually means testcontainers failed to start.
  threshold_minutes: 25   # This service has a longer normal build time
```

The RCA pipeline reads this file during checkout and injects it into the system prompt.

---

## 8. Migration Phases

### Phase 1 — Lift and Shift (Weeks 1–2)

**Goal**: RCA system running on GCP with zero code changes.

- Deploy GKE cluster with L4 GPU node pool
- Run Ollama + Gemma 27B on GKE (same as GDC, just different cloud)
- Set up Actions Runner Controller (ARC) for self-hosted runners on GKE
- Update `OLLAMA_HOST` in org-level secrets
- Test with 2–3 repos first, then roll out to all 80

**Risk**: Low. Architecture is identical to GDC.  
**Outcome**: RCA working on GCP, no functionality change.

### Phase 2 — Scale Hardening (Weeks 3–4)

**Goal**: Handle burst load reliably.

- Add Cloud Tasks queue between runner and Ollama
- Implement `OLLAMA_KEEP_ALIVE=-1` and pod warm-up
- Configure HPA on Ollama deployment (min 1, max 3 pods)
- Add pod disruption budget
- Set up Cloud Monitoring dashboard for queue depth + inference latency
- Add `pd-ssd` for model PVC
- Implement wall-clock timeout in `run_rca.py`

**Risk**: Low-Medium. Standard GCP patterns.  
**Outcome**: Handles 10–20 simultaneous failures without timeouts.

### Phase 3 — Intelligence Improvements (Month 2)

**Goal**: Improve accuracy for 80-microservice diversity.

- Implement two-tier model routing (27B for releases, 9B for PRs)
- Deploy Gemma 3 9B pod alongside 27B
- Add stack detection and stack-aware prompt enrichment
- Implement flakiness detection (GitHub API run history)
- Add `.rca-config.yml` per-repo support
- Implement confidence gating for PR comments

**Risk**: Medium. Requires testing per-repo configurations.  
**Outcome**: ~20% accuracy improvement, ~60% reduction in 27B GPU usage.

### Phase 4 — Analytics & Feedback Loop (Month 3)

**Goal**: Make RCA self-improving over time.

- Stream all RCA JSON outputs to BigQuery
- Add GitHub Reactions-based feedback (👍/👎 on PR comments)
- Build Looker Studio dashboard: failure trends per microservice, team, category
- Set up alerts for: "service X has had 5+ test_failure RCAs in 7 days" → create Jira epic
- Evaluate Gemma LoRA fine-tuning on your labeled data after 500+ samples

**Risk**: Low. Analytics only, no change to inference path.  
**Outcome**: Trending, early warning, and data for model improvement.

---

## 9. Cost Analysis

### Current GDC Cost (Baseline)

| Item | Cost |
|------|------|
| GPU node (T4/L4 on GDC) | Included in GDC infrastructure |
| PVC storage | Included |
| Runner VMs | Included |
| **Total additional cost** | **~$0/month** (using existing infrastructure) |

### GCP Cost (Post-Migration)

| Item | Config | Monthly Cost (est.) |
|------|--------|---------------------|
| L4 GPU node (always-on, 1 pod) | n2-standard-8 + L4 | ~$450 |
| L4 GPU node (burst, spot) | Spot instance, ~20% utilization | ~$90 |
| Gemma 9B node (fast tier, spot) | T4, ~30% utilization | ~$60 |
| pd-ssd PVC 100Gi | Model storage | ~$17 |
| Cloud Tasks | 1,532 tasks/month | ~$0 (free tier: 1M/month) |
| Cloud Monitoring | Basic metrics | ~$5 |
| GCS bucket | RCA report storage | ~$2 |
| BigQuery | Analytics | ~$5 |
| ARC runner pods (CPU only) | Small pods | ~$30 |
| **Total** | | **~$659/month** |

### Cost Comparison vs Vertex AI Managed Gemma

```
1,532 RCA requests/month
Average prompt: ~85,000 tokens (80K log + 5K system + metadata)
Average response: ~800 tokens

Total tokens/month: 1,532 × (85,000 + 800) = ~131 million tokens

Vertex AI Gemma pricing (approximate):
  Input:  $0.00025 / 1K tokens = $32.75/month
  Output: $0.0005  / 1K tokens =  $0.61/month
  Total:  ~$33/month

Vertex AI is MUCH cheaper for this volume.
But: data leaves your VPC (check compliance requirements).
```

**Recommendation**: If **data privacy** is paramount → self-hosted GKE (~$659/month). If data can go to Google APIs (within GCP boundary) → Vertex AI ($33/month + no ops overhead).

---

## 10. Risk Register

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| GPU pod cold start during large release | Medium | High | PDB + KEEP_ALIVE=-1 + warm-up |
| Thundering herd (all 80 repos fail simultaneously) | Low | High | Cloud Tasks queue + HPA + timeout fallback |
| GitHub API rate limit exhaustion | Low | Medium | Migrate to GitHub App (15K req/hr limit) |
| Gemma hallucination on internal framework errors | Medium | Medium | Confidence gating + per-repo context |
| Flaky test creates false "fix your code" comments | High | Medium | Flakiness detection layer |
| Model becomes stale as codebase evolves | Medium | Medium | Quarterly model updates + feedback loop |
| Cost overrun on GPU nodes | Low | Medium | Spot instances + HPA scale-to-zero (off-hours) |
| PVC corruption (model storage) | Very Low | High | Init job re-pull automation + model hash verification |
| Runner can't reach Ollama after GCP migration | Medium | High | Network validation in CI before cutover |
| Accuracy regression when switching to 9B model | Medium | Medium | A/B test 27B vs 9B on same failures before rollout |

---

## 11. Decision Matrix — Model & Deployment Options

| Option | Accuracy | Speed | Concurrency | Cost/month | Ops Burden | Data Privacy | Recommended For |
|--------|----------|-------|-------------|-----------|-----------|--------------|-----------------|
| Gemma 27B on L4 (current) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | $450 | High | ✅ Full | Release builds |
| Gemma 9B on T4 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | $150 | High | ✅ Full | PR builds |
| Two-tier (27B + 9B) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | $600 | High | ✅ Full | **Recommended** |
| Vertex AI Gemma (managed) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | $33 | None | ⚠️ GCP only | Cost-sensitive orgs |
| Cloud Run + Ollama (serverless) | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | $200 | Medium | ✅ Full | Variable load |
| Gemma 27B on A100 (MIG) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | $2,000+ | High | ✅ Full | Massive scale (500+ req/day) |

---

## 12. Recommendations

### Immediate (Before GCP Migration)

1. **Switch from PAT to GitHub App** now — higher rate limits, auto-rotating tokens, better audit trails. This change is independent of GCP migration.

2. **Add `OLLAMA_KEEP_ALIVE=-1`** to the current GDC deployment. The model should never unload between builds. This is the single highest-impact change for reliability.

3. **Implement flakiness detection** in `run_rca.py`. This will reduce unnecessary Gemma calls and stop false "fix your test" PR comments. Quick win.

### During GCP Migration (Phase 1)

4. **Use Actions Runner Controller (ARC)** on GKE for self-hosted runners. This is the cleanest path — preserves the current architecture, scales runner pods automatically, and eliminates VM management.

5. **Start with all 80 repos routing to a single 27B Gemma pod**. Validate that load is within capacity (it will be for average load). Only add the second pod (fast tier) if queue depth regularly exceeds 3.

6. **Use `pd-ssd` for model PVC**, not `pd-balanced`. The 5× faster load time on pod restart is worth the $8/month difference.

### Medium Term (Phase 2–3)

7. **Implement two-tier routing** after validating single-pod capacity. Route all `release_build` failures to 27B (deep analysis, most engineers care about release health). Route PR-level failures to 9B (fast feedback, 3× more volume).

8. **Add per-repo `.rca-config.yml`** for the 10–15 highest-failure-rate microservices first. Tailor the prompt with known flaky tests, internal framework context, and accurate build time thresholds. You'll see immediate accuracy improvement on those services.

9. **Stream RCA JSON to BigQuery** from day one on GCP. Even before you build dashboards, having the data available means you can retroactively analyze trends. The schema is already structured — just add a BigQuery writer to `output_formatter.py`.

### Long Term (Phase 4)

10. **Evaluate Gemma LoRA fine-tuning** after accumulating 500+ labeled samples (with developer feedback). A fine-tuned 9B model may outperform the generic 27B on your specific tech stack. Google provides fine-tuning infrastructure on Vertex AI for Gemma.

11. **Consider moving to Vertex AI Gemma** if data privacy requirements allow it. At $33/month vs $600/month for self-hosted, the cost difference funds multiple engineering-days of work per month. The managed service also removes all GPU infrastructure burden.

---

## Summary Table

| Dimension | Today (GDC/Fabric2) | After GCP Migration | Action Required |
|-----------|--------------------|--------------------|-----------------|
| GitHub API | GHE Server URL | GHEC / github.com URL | Zero — `${{ github.api_url }}` auto-handles |
| Ollama network | Air-gapped ClusterIP | GKE ClusterIP (same pattern) | Update `OLLAMA_HOST` secret |
| GPU | GDC T4/L4 | GKE L4 node pool | k8s manifest node selector update |
| Runner | Self-hosted GDC VM/pod | ARC on GKE | New ARC deployment |
| Model cold start | Fast (local disk) | Slower (pd-ssd, 13s) | Add KEEP_ALIVE=-1 + warm-up |
| Burst handling | Single pod, may timeout | Queue + HPA autoscaling | Cloud Tasks + HPA config |
| Accuracy (80 repos) | Generic prompt | Stack-aware + per-repo config | Phased enhancement |
| Cost | ~$0 (GDC included) | ~$600/month (self-hosted) or $33 (Vertex AI) | Budgeting |
| Monitoring | Basic kubectl | Cloud Monitoring + BigQuery | New dashboards |
| Daily RCA volume | ~51/day | ~51/day (same) | No change |
| Peak capacity | ~150/day (borderline) | 600+/day (with HPA) | Significant improvement |
| Model | Gemma 27B only | Gemma 27B (releases) + 9B (PRs) | Two-tier routing |

---

*Prepared for: Build Platform Team · Fabric2 → GCP Migration Planning*  
*References: [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) · [ENTERPRISE_ACCESS_AND_5DAY_PLAN.md](ENTERPRISE_ACCESS_AND_5DAY_PLAN.md)*
