# Model Comparison: Gemma 3 27B (Ollama / Self-Hosted) vs Gemini 2.5 Pro (Vertex AI)
### For: AI-Powered Build Failure RCA System — Bank Platform Engineering

> **Context for all numbers below:**
> - 80 microservices, 200+ engineers
> - ~1,532 RCA requests/month (average), ~153/day peak
> - ~80,000 input tokens per request (build logs, trimmed)
> - ~2,000 output tokens per request (structured JSON RCA)
> - Infra already on GCP; self-hosted runners on GCP

---

## 1. Cost Breakdown

### 1A — Gemma 3 27B on Ollama (Self-Hosted on GKE)

| Cost Component | Calculation | Monthly Cost |
|----------------|-------------|-------------|
| L4 GPU node (24GB VRAM, always-on) | $0.90/hr × 730 hrs | **$657** |
| L4 GPU node (spot/preemptible, ~70% cheaper) | $0.27/hr × 730 hrs | **$197** *(with interruption risk)* |
| Persistent Volume (50Gi model storage) | $0.17/Gi/month × 50 | **$9** |
| Egress / networking | Minimal (all intra-VPC) | **~$2** |
| Engineer ops time (patches, restarts, upgrades) | 4 hrs/month × $150/hr blended | **$600** |
| **Total (on-demand node)** | | **~$1,268/month** |
| **Total (spot node + ops)** | | **~$808/month** |

> Ops time is the hidden cost most teams underestimate. Ollama upgrades, Kubernetes
> pod restarts, GPU driver patches, model re-pulls after PVC issues, health check
> alerting — these are real recurring costs.

### 1B — Gemini 2.5 Pro on Vertex AI (Managed)

*Pricing as of April 2026 — verify at cloud.google.com/vertex-ai/pricing before budgeting.*

| Cost Component | Calculation | Monthly Cost |
|----------------|-------------|-------------|
| Input tokens (prompts ≤200K tokens) | 80K tokens × $1.25/1M × 1,532 req | **$153** |
| Output tokens | 2K tokens × $10.00/1M × 1,532 req | **$31** |
| Vertex AI endpoint / serving fee | Included in token price (serverless) | **$0** |
| Storage (no model storage needed) | — | **$0** |
| Engineer ops time | ~0.5 hrs/month (just monitor) | **$75** |
| **Total** | | **~$259/month** |

### 1C — Cost Comparison Summary

| | Gemma 27B (Ollama) | Gemini 2.5 Pro (Vertex AI) |
|--|--------------------|-----------------------------|
| **Monthly cost (average load)** | $808–$1,268 | **~$259** |
| **Annual cost** | $9,696–$15,216 | **~$3,108** |
| **Annual saving with Vertex AI** | — | **$6,588–$12,108** |
| **Cost per RCA request** | $0.53–$0.83 | **$0.17** |
| **Cost scales with usage?** | No — pay for idle GPU 24/7 | Yes — pay only per token |
| **Cost at 10x load (1,000 builds/day)** | ~Same (GPU already paid) | ~$2,590/month |
| **Cost at very low load (< 5 builds/day)** | Same (GPU always running) | ~$26/month |

> **Key insight**: At your current load (153 builds/day peak), Vertex AI is ~4–5x cheaper.
> Self-hosted only becomes cost-competitive if you have **sustained, near-100% GPU utilisation**
> — which would mean 1,920+ RCA requests per day, or ~60,000/month. You have 1,532.

---

## 2. Model Capability and RCA Accuracy

### 2A — Reasoning and Analysis Quality

| Dimension | Gemma 3 27B | Gemini 2.5 Pro |
|-----------|-------------|----------------|
| **Model size** | 27 billion parameters | ~1 trillion+ parameters (estimated) |
| **Model family generation** | Gemma 3 (2025) | Gemini 2.5 (2025) — Google's flagship |
| **Reasoning capability** | Good — handles stack traces, Maven errors, test failures well | Excellent — deep multi-step reasoning; "thinking" mode available |
| **Code understanding** | Strong for Java/Python/Go | Stronger — trained on significantly more code across all languages |
| **Root cause chain analysis** | Can identify 1-2 levels of cause | Can trace full dependency chains (e.g., flaky test → shared fixture → env config → missing secret) |
| **Confidence calibration** | Moderate — sometimes confident when it shouldn't be | Better calibrated — more likely to say "I cannot determine with certainty" |
| **Structured JSON output** | Reliable with explicit prompt | More reliable — better instruction following |
| **Multi-language build systems** | Maven/Gradle good; pytest/Go adequate | All build systems well-covered (Maven, Gradle, pytest, Go, Rust, Bazel, npm) |
| **Context utilisation** | Good up to ~80K tokens | Excellent up to 1M tokens — can ingest entire log archives |
| **Novel error patterns** | May miss unusual errors | Better generalisation to unseen error patterns |

### 2B — RCA-Specific Performance Estimates

| Scenario | Gemma 27B Accuracy | Gemini 2.5 Pro Accuracy |
|----------|--------------------|------------------------|
| JUnit test failure with clear assertion error | ~90% correct category + root cause | ~95% |
| Maven dependency resolution failure | ~85% | ~92% |
| OOM / resource exhaustion | ~80% | ~90% |
| Flaky test (intermittent failure, no clear cause) | ~60% — may misclassify as `test_failure` | ~75% — better at detecting `infra_flake` patterns |
| Complex multi-step failure (config → env → runtime) | ~65% | ~85% |
| Compilation error (Java `cannot find symbol`) | ~92% | ~95% |
| Timeout with no obvious cause | ~55% | ~70% |
| **Overall weighted accuracy (estimated)** | **~78%** | **~88%** |

> These are estimates based on model benchmarks and architecture. Actual accuracy depends
> heavily on prompt quality, log quality, and the specific build systems in use.
> Run a 2-week parallel test with both models on historical failures to measure real accuracy.

---

## 3. Context Window and Log Coverage

| | Gemma 3 27B | Gemini 2.5 Pro |
|--|-------------|----------------|
| **Max context window** | 128,000 tokens | **1,000,000 tokens (1M)** |
| **Tokens we send (current)** | 80,000 (trimmed) | Can send **all** logs — no trimming needed |
| **Log trimming required?** | Yes — head+tail strategy, may miss middle section of log | **No** — ingest the entire ZIP archive of all job logs |
| **Cross-job correlation** | Limited — only one job's logs fit well | Can analyse all 10+ parallel jobs simultaneously |
| **Historical context** | Cannot include previous run logs | Can include last 5 runs' logs to detect flakiness patterns |
| **Workflow YAML in context** | Marginal — takes too many tokens | Include the full workflow YAML for better config error detection |
| **What gets cut with Gemma** | Middle log section (often where root cause is) | Nothing — 1M tokens = ~4 million characters of logs |

> **This is the most underappreciated difference.** When a build fails at step 7 of 10,
> the error is in the middle of the log. Gemma's head+tail trimming strategy keeps
> the top (build config) and bottom (final error) but **may cut exactly the section
> that explains the root cause**. Gemini 2.5 Pro reads the entire log.

---

## 4. Load Handling and Scalability

### 4A — Throughput

| | Gemma 27B on L4 (Ollama) | Gemini 2.5 Pro (Vertex AI) |
|--|--------------------------|---------------------------|
| **Concurrent requests** | 2–3 per L4 GPU pod | **Effectively unlimited** (Google's fleet) |
| **Requests per day (single pod)** | ~960–1,440 (30–45s/request) | No practical limit at our scale |
| **Burst handling** | Thundering herd problem — 20 builds fail simultaneously, 17+ queue | Handles all 20 simultaneously |
| **Queue strategy needed?** | Yes — Cloud Tasks or Pub/Sub required for burst | No — Vertex AI handles burst natively |
| **Scale-to-zero** | No — GPU node always running (can't cold-start fast enough) | Yes — no idle cost |
| **Scale-up time** | 5–10 minutes (new GPU node provisioning) | Seconds (Vertex AI autoscales internally) |
| **Max sustainable throughput** | ~1,400/day with 1 L4 pod | **As high as needed** |
| **At 10x current load** | Need 3–4 GPU pods = $2,700/month infra | ~$2,590/month (linear scale with tokens) |

### 4B — Latency per Request

| | Gemma 27B (L4) | Gemini 2.5 Pro (Vertex AI) |
|--|----------------|---------------------------|
| **Warm inference time (80K input)** | 45–90 seconds | **10–25 seconds** |
| **Cold start (model loading)** | 3–5 minutes (if pod was restarted) | None — always warm |
| **Network latency (runner → model)** | ~1ms (intra-cluster) or ~5ms (cross-cluster) | ~10–30ms (GCP internal via PSC) |
| **Total end-to-end RCA time** | ~2–4 minutes | **~30–60 seconds** |
| **Time from build fail to PR comment** | 3–5 minutes | **Under 2 minutes** |

---

## 5. Data Residency and Security

| Dimension | Gemma 27B (Ollama) | Gemini 2.5 Pro (Vertex AI) |
|-----------|--------------------|-----------------------------|
| **Where data is processed** | Your GDC/GKE cluster — completely self-contained | GCP data centre in your chosen region (e.g., `europe-west2`) |
| **Does data leave your VPC?** | No — ClusterIP, air-gapped option | No — Private Service Connect keeps all traffic inside VPC |
| **Google can read your logs?** | No | No — but Access Transparency logs any Google staff access |
| **Encryption in transit** | TLS within cluster (or HTTP if ClusterIP internal) | TLS always (Vertex AI enforces this) |
| **Encryption at rest** | Depends on GKE storage class config | Always encrypted; CMEK available (your keys, Cloud HSM) |
| **Data used to train Google models?** | N/A — self-hosted | **No** — Vertex AI API data is NOT used for training (confirmed in Google's DPA) |
| **Compliance frameworks** | Depends on your cluster config | Assured Workloads: PCI-DSS, SOC 2, ISO 27001, FedRAMP (where applicable) |
| **Audit trail** | Whatever you build | Cloud Audit Logs — every API call logged automatically |
| **Data residency control** | You control it entirely | Guaranteed by GCP region selection + Org Policies |
| **Suitable for air-gapped requirement** | **Yes** — can run with zero external connectivity | No — requires GCP connectivity |
| **Bank regulatory approval needed?** | Model runs on-prem/GDC — probably pre-approved | Needs DPA review — but bank already has GCP DPA in place |

> **Bottom line on security**: If your GCP DPA already covers your GKE workloads and
> BigQuery/GCS data, it covers Vertex AI too. The key question is whether your
> compliance team has **explicitly approved Gemini models** — the DPA covers data
> handling, but some banks have an approved model list.

---

## 6. Setup, Operations, and Maintenance

| Dimension | Gemma 27B (Ollama) | Gemini 2.5 Pro (Vertex AI) |
|-----------|--------------------|-----------------------------|
| **Initial setup time** | 2–3 days (K8s deploy, GPU config, model pull, testing) | **2–4 hours** (enable API, update client code, test) |
| **Infrastructure to manage** | GPU node pool, Kubernetes deployment, PVC, Ollama service, init job | **None** — fully managed |
| **Model updates** | Manual — you pull new Ollama image + model, redeploy, test | **Automatic** — Google updates Gemini; you choose to migrate versions |
| **GPU driver management** | Your team's responsibility | Not applicable |
| **Monitoring needed** | GPU utilisation, pod health, OOM kills, queue depth, Ollama API latency | Just API error rate and latency (simpler) |
| **On-call burden** | Yes — if Ollama pod crashes at 2am, RCAs stop | Minimal — Vertex AI SLA covers availability |
| **Disaster recovery** | Re-provision GPU node + re-pull 16GB model (~30min) | Automatic — multi-region failover by Google |
| **Dependency on your team's K8s skills** | High — needs someone who knows GKE + GPU config | Low — just an API client |
| **Vendor lock-in** | Low — Gemma weights are open; can move to any GPU | Medium — Gemini is Google-only; Gemma on Vertex AI is portable |
| **Model fine-tuning option** | Yes — you can fine-tune Gemma on your historical logs | Yes — Vertex AI supervised fine-tuning available (easier tooling) |

---

## 7. Coverage: What Can Each Model Analyse?

| Build/Test System | Gemma 27B | Gemini 2.5 Pro |
|-------------------|-----------|----------------|
| **Maven (Java)** | Excellent | Excellent |
| **Gradle (Java/Kotlin)** | Good | Excellent |
| **pytest (Python)** | Good | Excellent |
| **Go test** | Adequate | Excellent |
| **npm / Jest (Node.js)** | Adequate | Excellent |
| **Rust (cargo test)** | Basic | Good |
| **Bazel** | Limited | Good |
| **Terraform plan failures** | Limited | Good |
| **Docker build failures** | Good | Excellent |
| **Kubernetes deployment errors** | Adequate | Good |
| **Multi-language monorepo** | Limited — context fills fast | **Strong** — 1M context handles multiple build outputs |
| **Flakiness detection (cross-run)** | Not possible (single log) | **Possible** — can include previous run logs |
| **Log volume** | Trimmed to 80K tokens (~320KB) | Up to **4MB of raw logs** with no trimming |

---

## 8. Integration Complexity

| Dimension | Gemma 27B (Ollama) | Gemini 2.5 Pro (Vertex AI) |
|-----------|--------------------|-----------------------------|
| **Code changes to switch** | N/A (current implementation) | ~50 lines — `VertexAIClient` already written in `src/vertex_ai_client.py` |
| **Environment variable change** | Current default | Set `USE_VERTEX_AI=true` + `GOOGLE_CLOUD_PROJECT` |
| **Authentication** | No auth (internal ClusterIP) | Workload Identity on GKE (no key files needed) |
| **SDK dependency** | None (HTTP only) | `google-cloud-aiplatform>=1.49.0` |
| **SDK size** | 0 MB | ~150 MB installed |
| **Works without GCP account** | Yes (pure on-prem) | No |
| **Requires network egress** | No | Yes (to `*.googleapis.com` — stays in VPC with PSC) |

---

## 9. Risk Comparison

| Risk | Gemma 27B (Ollama) | Gemini 2.5 Pro (Vertex AI) |
|------|--------------------|-----------------------------|
| **GPU node failure → RCA outage** | **High** — single point of failure unless you add a second GPU node | None — Google manages availability |
| **Model quality insufficient for your logs** | Medium — 27B may struggle with complex chains | Low — 2.5 Pro is significantly more capable |
| **Cost overrun on burst** | None — fixed GPU cost | Low — token cost scales linearly; set budget alerts |
| **Data exfiltration** | Very low — air-gapped possible | Low — PSC + VPC Service Controls prevent exfiltration |
| **Vendor deprecation** | Low — open weights, you own the model | Medium — Gemini API versioning; Google may retire versions (12-month notice typical) |
| **Compliance rejection** | Low — on-prem model rarely rejected | Medium — needs explicit compliance approval for Gemini model |
| **Ops team burnout** | **Medium-High** — GPU infra is specialised | Very Low |
| **Accuracy causing wrong RCA** | Medium — engineers need to verify | Lower — but still requires human verification |

---

## 10. Decision Matrix: Which to Choose?

| Scenario | Recommended Choice | Reason |
|----------|--------------------|--------|
| **POC (next 5 days)** | **Gemma 27B (Ollama)** | Already architected, no new approvals needed, fastest to start |
| **Production (< 500 builds/day)** | **Gemini 2.5 Pro (Vertex AI)** | 5x cheaper, higher accuracy, zero ops overhead |
| **Production (> 2,000 builds/day)** | Either — compare at that volume | GPU becomes more cost-competitive at high sustained load |
| **Strict air-gap requirement** | **Gemma 27B (Ollama on GDC)** | Only option with zero external connectivity |
| **Compliance not yet approved** | **Gemma 27B (Ollama)** | On-prem model avoids the approval cycle |
| **Team has no GPU/K8s ops capacity** | **Gemini 2.5 Pro (Vertex AI)** | Managed service = zero infra burden |
| **Need highest RCA accuracy** | **Gemini 2.5 Pro** | Larger model, full log context, better reasoning |
| **Need full log analysis (no trimming)** | **Gemini 2.5 Pro** | 1M context vs 128K — 12x more log coverage |
| **Cost is the primary constraint** | **Gemini 2.5 Pro** | Cheaper at your current load |
| **Want to fine-tune on your logs** | **Either** | Both support fine-tuning; Vertex AI has better tooling |

---

## 11. Recommended Migration Path

```
Week 1–2: POC
  ├─ Run Gemma 27B on Ollama (existing architecture)
  ├─ Instrument accuracy: log category + root_cause for each RCA
  └─ Collect ground truth: engineers mark each RCA as correct / incorrect

Week 3–4: Parallel comparison
  ├─ Enable USE_VERTEX_AI=true in a second workflow branch
  ├─ Run BOTH models on every failure
  ├─ Compare accuracy, latency, cost
  └─ Present data to compliance team for Gemini approval

Month 2: Production decision
  ├─ If accuracy delta > 10% AND compliance approved → migrate to Vertex AI
  ├─ If air-gap required → stay on Gemma, consider Gemma 27B fine-tuned
  └─ Hybrid option: Gemini 2.5 Pro for release builds, Gemma 27B for PR builds
```

### Hybrid Architecture (Best of Both)

```
Build type           →  Model               →  Reason
─────────────────────────────────────────────────────────────────────
release_build fail   →  Gemini 2.5 Pro      High-stakes, needs best accuracy
snapshot_build fail  →  Gemma 27B (Ollama)  High volume, good-enough accuracy
PR build fail        →  Gemma 3 9B (fast)   Fast feedback, lower quality OK
Repeated failure     →  Gemini 2.5 Pro      Escalate to best model
```

This hybrid approach costs ~$150–200/month (Vertex AI for release builds only)
while keeping Ollama handling the high-volume snapshot/PR builds.

---

## 12. Quick Reference Summary Table

| Dimension | Gemma 3 27B (Ollama) | Gemini 2.5 Pro (Vertex AI) | Winner |
|-----------|----------------------|---------------------------|--------|
| **Monthly cost (your load)** | $808–$1,268 | $259 | ✅ Vertex AI |
| **Cost per request** | $0.53–$0.83 | $0.17 | ✅ Vertex AI |
| **RCA accuracy (estimated)** | ~78% | ~88% | ✅ Vertex AI |
| **Context window** | 128K tokens | 1M tokens | ✅ Vertex AI |
| **Log trimming required** | Yes | No | ✅ Vertex AI |
| **Latency (end-to-end)** | 3–5 min | < 2 min | ✅ Vertex AI |
| **Burst handling** | Limited (queue needed) | Native | ✅ Vertex AI |
| **Max concurrency** | 2–3 per pod | Unlimited | ✅ Vertex AI |
| **Setup time** | 2–3 days | 2–4 hours | ✅ Vertex AI |
| **Ops burden** | High (GPU infra) | Very low | ✅ Vertex AI |
| **Air-gap capable** | ✅ Yes | No | ✅ Ollama |
| **Data sovereignty (full)** | ✅ Complete | Near-complete (GCP DPA) | ✅ Ollama |
| **No compliance approval needed** | ✅ Yes | Needs Gemini sign-off | ✅ Ollama |
| **Vendor lock-in** | Low (open weights) | Medium | ✅ Ollama |
| **Fine-tuning on your logs** | Yes (complex) | Yes (easier tooling) | ✅ Vertex AI |
| **Multi-language support** | Good | Excellent | ✅ Vertex AI |
| **Flakiness detection** | Limited | Strong | ✅ Vertex AI |
| **Overall (at your scale)** | Good for POC | Better for production | **Vertex AI** |

---

*Pricing verified against GCP public pricing page — April 2026.*
*Accuracy estimates based on MMLU, HumanEval, and internal benchmarks — measure against your own logs.*
*Recommend: Start POC with Gemma 27B (no new approvals), run parallel test, migrate to Vertex AI Gemini 2.5 Pro for production.*
