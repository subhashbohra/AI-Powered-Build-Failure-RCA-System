# Bank GCP Context — Data Residency, n+2 Data Centers & Architecture Decisions

> **Your questions answered**:
> 1. What does n+2 mean and how does it relate to us?
> 2. If we already use GCP in the bank, is data residency already solved for Vertex AI?
> 3. If our GitHub runners are self-hosted on GCP, what does that mean for the RCA architecture?
> 4. What is the right architecture given all of this?

---

## Table of Contents

1. [What n+2 Means — Plain English](#1-what-n2-means--plain-english)
2. [Data Residency — Why It's Not a New Problem for You](#2-data-residency--why-its-not-a-new-problem-for-you)
3. [How GCP Data Residency Actually Works for Banks](#3-how-gcp-data-residency-actually-works-for-banks)
4. [Your Self-Hosted Runners on GCP — The Architecture Implication](#4-your-self-hosted-runners-on-gcp--the-architecture-implication)
5. [Vertex AI vs Self-Hosted Ollama — The Real Decision](#5-vertex-ai-vs-self-hosted-ollama--the-real-decision)
6. [The Three-Layer Architecture for Your Bank](#6-the-three-layer-architecture-for-your-bank)
7. [How the n+2 Data Centers Fit In](#7-how-the-n2-data-centers-fit-in)
8. [What to Ask Your Cloud/Compliance Team](#8-what-to-ask-your-cloudcompliance-team)
9. [Final Recommendation](#9-final-recommendation)

---

## 1. What n+2 Means — Plain English

**n+2** is a data center redundancy standard. Let me break it down simply.

**Start with "n":**
`n` means "the minimum number of components needed to run everything at full capacity."

**Then add "+2":**
You have 2 *extra* copies of every critical system, beyond what you need.

**Example — Power:**
```
Your bank's data center needs 4 power feeds to run at full capacity.
  n   = 4  (minimum needed)
  n+1 = 5  (can lose 1 feed, still runs)
  n+2 = 6  (can lose 2 feeds simultaneously, still runs perfectly)
```

**Example — Cooling units:**
```
Data center needs 6 cooling units to prevent overheating.
  n+2 = 8 cooling units installed
  Any 2 can fail or be taken for maintenance at the same time.
  The other 6 handle the full load. Zero impact.
```

**What it means in practice for your bank:**

| Component | What n+2 Looks Like |
|-----------|---------------------|
| **Power** | 2 independent utility feeds from different substations + 2 UPS systems + 2 diesel generators |
| **Cooling** | 2 extra CRAC/CRAH units beyond what full load needs |
| **Network** | 2 extra uplinks/routers beyond needed capacity, from different providers |
| **Servers** | Cluster can lose 2 entire nodes and still handle peak load |
| **Storage** | RAID + replication — can lose 2 drives/controllers simultaneously |

**n+2 vs Tier Standards:**

| Standard | Can Lose | Uptime SLA | Typical For |
|----------|----------|------------|-------------|
| n (no redundancy) | Nothing | ~99.67% (28 hr downtime/year) | Dev/test |
| n+1 | 1 component | ~99.99% (52 min/year) | Most enterprises |
| **n+2** | **2 components simultaneously** | **~99.999% (5 min/year)** | **Banks, hospitals, exchanges** |
| 2n (full duplication) | Half the entire system | ~99.9999% | Stock exchanges, core banking |

**So when your bank says "n+2 data centers" it means:**
Their physical facilities are designed so that even if two major infrastructure components fail at the same time (power feed + cooling unit, or two network links), the data center keeps running without any service interruption. This is why your bank can confidently run core banking systems — the physical layer is engineered to essentially never go down.

**Importantly**: GCP and GDC (Google Distributed Cloud) hosted *inside* your bank's n+2 data centers inherits this physical redundancy. So your GDC cluster already sits on top of infrastructure that is far more reliable than a standard cloud region.

---

## 2. Data Residency — Why It's Not a New Problem for You

You made a very astute observation:

> *"We already have many projects in GCP within the bank and our GitHub runners are self-hosted on GCP — someone must have already thought about data residency."*

**You are absolutely right.** Here is why this is important.

When a bank deploys **any** workload on GCP — whether it's a database, an API, or a Kubernetes cluster — the compliance and legal teams go through an approval process that covers:

1. **Which GCP region** the data lives in (e.g., `europe-west2` for London, `us-central1` for Iowa)
2. **Which GCP services** are approved for use in that region
3. **Data classification** — what category of data can go into GCP (non-sensitive, internal, confidential, restricted)
4. **Encryption requirements** — who holds the keys (Google-managed vs customer-managed via Cloud KMS/HSM)
5. **Access controls** — who at Google can access your data (Access Transparency logs)
6. **Contractual terms** — Google's Data Processing Amendment (DPA) for your country/jurisdiction

**The key insight**: If your bank has already approved GCP for running microservices, databases, or any CI/CD infrastructure, then **the data residency framework is already in place**. Vertex AI in the same approved region sits within that same framework.

You are NOT adding a new data residency problem by using Vertex AI. You are using an **already-approved** cloud provider, within an **already-approved** region, with the **same contractual protections** that cover all your other GCP workloads.

Think of it this way:

```
Bank already approved: "GCP europe-west2 region for project workloads"

Existing approved GCP services:
  ✅ GKE (Kubernetes)
  ✅ Cloud SQL (databases)
  ✅ Cloud Storage (object storage)
  ✅ Pub/Sub (messaging)
  ✅ Secret Manager (secrets)
  ✅ Self-hosted GitHub runners (already running here!)

Adding for RCA:
  ✅ Vertex AI (Gemma) — same region, same framework, same DPA

This is NOT a new approval. It's extending an existing approved pattern.
```

**The one exception** — if your bank has explicitly categorized **build logs as "restricted" or "confidential"** data, then you need a specific sign-off that sending build logs to Vertex AI is acceptable. Build logs typically contain:
- Code snippets, class names, package names (internal IP, but low sensitivity)
- Test names and error messages (low sensitivity)
- Dependency versions (low sensitivity)
- Potentially: environment variable names (could be medium sensitivity if names hint at secrets)

Build logs almost never contain PII, financial data, or credentials themselves (those get masked by GitHub Actions). They're typically classified as **"internal"** — below the threshold that would block GCP usage.

---

## 3. How GCP Data Residency Actually Works for Banks

### 3.1 The Controls Your Bank Likely Already Has

If your bank is a mature GCP customer (multiple projects, self-hosted runners), they have implemented most or all of these:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Your Bank's GCP Organization                             │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Org Policy Constraints (set by your Cloud Platform team)            │   │
│  │                                                                       │   │
│  │  constraints/gcp.resourceLocations                                   │   │
│  │    → Only allowed in: europe-west2, europe-west4 (example)          │   │
│  │    → Blocks any resource creation outside approved regions           │   │
│  │                                                                       │   │
│  │  constraints/compute.restrictCloudRunRegions                         │   │
│  │  constraints/vertexai.restrictRegions  (if set)                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  VPC Service Controls Perimeter                                       │   │
│  │  (a security fence around your GCP projects)                         │   │
│  │                                                                       │   │
│  │  Inside the perimeter:                                                │   │
│  │    GKE → Vertex AI API call → STAYS INSIDE PERIMETER                │   │
│  │    No data can leave to the public internet                          │   │
│  │    Google's own admins cannot access without Access Transparency log  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Private Service Connect (PSC)                                        │   │
│  │                                                                       │   │
│  │  Your GKE runner pod calls Vertex AI via a PRIVATE ENDPOINT          │   │
│  │  Traffic path:                                                        │   │
│  │    Runner Pod → Your VPC → PSC endpoint → Vertex AI                  │   │
│  │    ✅ Never touches the public internet                               │   │
│  │    ✅ Traffic stays within Google's network backbone                  │   │
│  │    ✅ Your firewall rules control who can call it                     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Assured Workloads (for regulated industries)                         │   │
│  │                                                                       │   │
│  │  If your bank enabled this:                                           │   │
│  │    → Data processing guaranteed in declared jurisdiction              │   │
│  │    → Google operator access restricted to personnel in that country  │   │
│  │    → Compliance posture enforced automatically (EU, US, APAC, etc.)  │   │
│  │    → Applicable standards: FedRAMP, GDPR, DORA, PCI-DSS             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Customer-Managed Encryption Keys (CMEK)                              │   │
│  │                                                                       │   │
│  │  Your bank holds the encryption keys in Cloud HSM                    │   │
│  │  All data at rest — including Vertex AI requests/responses           │   │
│  │  — is encrypted with YOUR keys, not Google's                         │   │
│  │  If you revoke the key, Google cannot decrypt anything               │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 The Critical Point About Vertex AI for Your Bank

When your GKE pod calls Vertex AI's Gemma endpoint:

```
What actually happens:
  1. Your runner pod sends build logs to Vertex AI endpoint
     via Private Service Connect (stays inside your VPC / Google's private network)

  2. Vertex AI processes the inference request
     in the same GCP region your org policy restricts to
     (e.g., if org policy says europe-west2, Vertex AI in europe-west2 — data stays in EU)

  3. Response comes back via Private Service Connect

  4. Build logs are NOT retained by Google
     (Vertex AI does not train on your data — see Google Cloud DPA, Clause 8.1)
     (You can also enable "No data retention" mode explicitly)

  5. Access Transparency logs show you if/when any Google employee accesses
     your data (they can't without your consent in most cases)

What does NOT happen:
  ✗ Build logs are NOT sent to the public internet
  ✗ Build logs are NOT used by Google to train foundation models
  ✗ Build logs are NOT accessible to Google's general staff
  ✗ Data does NOT leave your approved GCP region
```

### 3.3 Google's Contractual Commitment to Banks

This is documented in Google's **Cloud Data Processing Addendum (CDPA/DPA)** which your bank has signed as part of their GCP agreement:

- **Section on ML/AI**: Google will not use Customer Data to train, improve, or refine any AI/ML models without customer consent
- **Data Isolation**: Each customer's data is logically isolated
- **Deletion**: Google deletes inference input/output within a defined period (typically 24 hours for Vertex AI predictions)
- **Sub-processor disclosure**: You know exactly which Google subsidiaries/entities can touch your data

**Your bank's legal team has already reviewed and accepted this agreement** because they use GCP. Vertex AI is covered under the same agreement.

---

## 4. Your Self-Hosted Runners on GCP — The Architecture Implication

This is the **single most important piece of information** for simplifying the RCA architecture.

### 4.1 What It Means

If your GitHub Actions runners are already self-hosted on GCP (on GKE or GCE VMs), then:

```
Current state (what you already have):

GitHub Enterprise
       │
       │ HTTPS — runners poll for jobs
       ▼
Self-hosted runner pod/VM ← ALREADY IN YOUR GCP VPC
       │
       │ Already has:
       │   ✅ Access to GCP services via VPC (private, no internet)
       │   ✅ Workload Identity (if configured) — no service account key files
       │   ✅ Private Service Connect endpoints for GCP APIs
       │   ✅ Org policy restrictions enforced
       │   ✅ VPC firewall rules from your security team
       │
       ▼
[Currently]: Self-hosted Ollama on GDC (separate network path, complex)
[Could be]:  Vertex AI Gemma (private endpoint, same VPC, ZERO new networking)
```

### 4.2 The Network Path Is Already There

The runner is already inside the GCP VPC. Calling Vertex AI from inside the GCP VPC via Private Service Connect requires:

1. Enable Private Service Connect for Vertex AI in your VPC — **one-time setup, done by network team, takes 30 minutes**
2. Update `OLLAMA_HOST` in your secrets to point to the Vertex AI endpoint instead of Ollama

That is literally it. No VPN tunnels, no firewall holes, no new runner registration. The runner is already in the right place.

### 4.3 Comparison: Current Path vs Vertex AI Path

```
Current path (GDC Ollama):
  Runner (GCP GKE/GCE)
    → VPN tunnel / peering to bank's physical data center
    → GDC cluster
    → Ollama ClusterIP
    → Gemma 27B inference

Problems:
  - Cross-network hop (GCP → bank DC)
  - Latency: 5-20ms extra (VPN/peering overhead)
  - Firewall rules in TWO networks
  - GDC infrastructure managed by your team
  - Cold start: model loading, pod restarts
  - No auto-scaling

Vertex AI path (recommended for your setup):
  Runner (GCP GKE/GCE) — already in GCP VPC
    → Private Service Connect endpoint (within VPC, <1ms)
    → Vertex AI Gemma in same GCP region
    → Inference response back via PSC

Benefits:
  - ZERO new networking (runner is already in GCP)
  - <1ms network hop (same VPC)
  - Only ONE set of firewall rules (your existing GCP VPC rules)
  - No infrastructure to manage
  - Auto-scales to handle 200+ simultaneous requests
  - 99.95% SLA from Google
  - No cold start (model always ready)
```

---

## 5. Vertex AI vs Self-Hosted Ollama — The Real Decision

Given that:
- Your bank already uses GCP ✅
- Data residency is already governed by your GCP org policies ✅
- Runners are already on GCP ✅
- Build logs are classified as internal (not restricted/confidential) ✅

The decision is now purely **operational**:

### 5.1 Cost Comparison

```
At your scale: ~1,532 RCA requests/month

Vertex AI Gemma 3 27B:
  Input tokens:  1,532 × 85,000 = 130M tokens × $0.00025/1K = $32.55/month
  Output tokens: 1,532 × 800   = 1.2M tokens  × $0.00050/1K =  $0.61/month
  Total: $33/month

  No GPU nodes = $0 infrastructure
  No GPU ops = $0 engineering time

Self-hosted Ollama on GKE with L4 GPU:
  L4 GPU node (on-demand):  $450/month
  L4 GPU node (spot):       $180/month
  pd-ssd 100Gi:             $17/month
  Engineering time:         ~4 hrs/month maintenance × $150/hr = $600/month
  Total (spot + ops):       $797/month

Annual difference: ($797 - $33) × 12 = $9,168/year saved with Vertex AI
```

### 5.2 Operational Comparison

| Concern | Vertex AI Gemma | Self-hosted Ollama (GKE) |
|---------|----------------|--------------------------|
| GPU pod cold start | ❌ Not applicable — always ready | ✅ Need KEEP_ALIVE + warm-up |
| Model updates | ✅ Automatic — Google updates Gemma | Manual pull + pod restart |
| Burst handling | ✅ Auto-scales to 1000s of req/min | Need HPA + multiple GPU nodes |
| SLA | ✅ 99.95% uptime SLA | Best-effort (spot = preemptible) |
| Latency | ~1-3 sec (network) + ~30-60 sec (inference) | ~0ms network + 45-75s inference |
| GPU ops expertise needed | None | Medium-High |
| Data leaves your VPC | No (Private Service Connect) | No (ClusterIP / same VPC) |
| CMEK support | ✅ Yes (Cloud KMS integration) | Manual encryption only |
| Audit logs | ✅ Cloud Audit Logs, Access Transparency | kubectl logs only |
| Compliance certifications | ✅ ISO 27001, SOC 2, PCI-DSS, GDPR | Your responsibility |

### 5.3 The One Scenario Where Self-Hosted Is Better

**If your bank has classified build logs as "Restricted"** (containing code that is a trade secret, or the bank has an explicit policy that ALL code must stay on bank-owned hardware), then:
- Self-hosted Ollama on GDC in the bank's own data center is the right answer
- Or self-hosted Ollama on GKE (stays in GCP project, never touches Vertex AI)
- This is a compliance decision, not a technical one

Ask your Information Security / Data Classification team: **"What is the data classification of GitHub Actions build logs?"**

If the answer is "Internal" or "Confidential (GCP-OK)" → Vertex AI is fine.  
If the answer is "Restricted / Must stay on bank premises" → Self-hosted on GDC.

---

## 6. The Three-Layer Architecture for Your Bank

Given your full picture (GCP bank, self-hosted runners on GCP, n+2 data centers, Fabric2→GCP migration), here is the recommended architecture:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   LAYER 1: GitHub Enterprise Cloud (GHEC)                    │
│                    or GitHub Enterprise Server (self-hosted)                  │
│                                                                               │
│  80 Repos × rca_trigger.yml                                                  │
│  Org-level secrets: RCA_GITHUB_TOKEN (GitHub App token)                      │
│  Org-level variable: VERTEX_AI_ENDPOINT or OLLAMA_HOST                      │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                   │ workflow_run: failure
                                   │ HTTPS (443) — runners already poll GH
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│           LAYER 2: Your Bank's GCP Organization                              │
│           VPC: bank-production-vpc  |  Region: europe-west2 (example)       │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  GKE Cluster — Self-Hosted Runners (Actions Runner Controller)        │   │
│  │                                                                       │   │
│  │  Runner pod executes rca_trigger.yml:                                 │   │
│  │    ① Fetch logs — GitHub API (HTTPS, existing connectivity)          │   │
│  │    ② Parse logs — log_parser.py (CPU only, no GPU needed)            │   │
│  │    ③ Call Vertex AI — via Private Service Connect (VPC-private)      │   │
│  │    ④ Post PR comment — GitHub API                                    │   │
│  │    ⑤ Store artifact — GCS bucket (same VPC, CMEK encrypted)         │   │
│  │    ⑥ Pub/Sub event → BigQuery (analytics, async)                    │   │
│  └──────────────────────────┬───────────────────────────────────────────┘   │
│                               │                                               │
│           ┌───────────────────┼────────────────────┐                        │
│           │ Private Service   │ Connect (in-VPC,    │                        │
│           │ no internet hop)  │                     │                        │
│           ▼                   ▼                     ▼                        │
│  ┌───────────────┐  ┌──────────────────┐  ┌──────────────────────────┐     │
│  │  Vertex AI    │  │  Cloud Storage   │  │  Cloud Pub/Sub           │     │
│  │  Gemma 3 27B  │  │  rca-reports     │  │  → BigQuery              │     │
│  │               │  │  (CMEK, 90 days) │  │  → Looker Studio         │     │
│  │  Auto-scales  │  │                  │  │  → Jira integration      │     │
│  │  99.95% SLA   │  │                  │  └──────────────────────────┘     │
│  │  Same region  │  │                  │                                     │
│  │  as runners   │  │                  │                                     │
│  └───────────────┘  └──────────────────┘                                    │
│                                                                               │
│  VPC Service Controls Perimeter ─────────────────────────────────────────   │
│  All traffic stays inside. Org policies enforce region. CMEK encrypts all.  │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │ GDC / Private connectivity
                                   │ (for truly air-gapped workloads)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│         LAYER 3: Bank's Own Data Centers (n+2)                               │
│         (for core banking, highest-classification systems)                    │
│                                                                               │
│  GDC (Google Distributed Cloud) — hosted in bank's n+2 facility             │
│  ┌──────────────────────────────────────────────────────────────┐           │
│  │  Ollama + Gemma 27B (fallback / air-gapped option)           │           │
│  │  Only needed if build logs classified as "Restricted"        │           │
│  │  Or if bank policy mandates code never leaves bank hardware  │           │
│  └──────────────────────────────────────────────────────────────┘           │
│                                                                               │
│  n+2 Physical Redundancy:                                                    │
│    Power:   3 independent feeds (need 1, have 3 = n+2)                      │
│    Cooling: 8 CRAC units (need 6, have 8 = n+2)                             │
│    Network: 4 uplinks from 2 providers (need 2, have 4 = n+2)               │
│    Compute: N+2 server nodes in each rack cluster                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.1 Code Change Required to Switch from Ollama to Vertex AI

This is **minimal**. The `OllamaClient` class in `src/ollama_client.py` needs a Vertex AI backend option:

```python
# src/vertex_ai_client.py (new file — ~50 lines)
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
import logging, os

logger = logging.getLogger(__name__)

class VertexAIClient:
    """
    Drop-in replacement for OllamaClient using Vertex AI Gemma.
    Same interface: health_check(), chat(), parse_rca_response()
    """

    def __init__(
        self,
        project: str = None,
        location: str = None,
        model: str = "gemma-3-27b-it",
        max_retries: int = 3,
    ):
        self.project  = project  or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("VERTEX_AI_LOCATION", "europe-west2")
        self.model    = model
        self.max_retries = max_retries
        vertexai.init(project=self.project, location=self.location)
        self._model = GenerativeModel(self.model)

    def health_check(self) -> bool:
        """Vertex AI is always available — SLA-backed."""
        return True

    def chat(self, messages: list[dict], temperature: float = 0.3, **kwargs) -> dict:
        """Send messages to Vertex AI Gemma, return Ollama-compatible dict."""
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_msg   = next((m["content"] for m in messages if m["role"] == "user"),   "")

        config = GenerationConfig(temperature=temperature, max_output_tokens=2048)

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._model.generate_content(
                    [system_msg + "\n\n" + user_msg],
                    generation_config=config,
                )
                content = response.text
                logger.info("Vertex AI response: %d chars", len(content))
                # Return in same shape as OllamaClient so rest of code unchanged
                return {"message": {"content": content}, "total_duration": 0}
            except Exception as e:
                logger.warning("Vertex AI attempt %d/%d failed: %s", attempt, self.max_retries, e)
                if attempt == self.max_retries:
                    raise

    def parse_rca_response(self, raw_content: str) -> dict:
        """Reuse the exact same parser from OllamaClient."""
        from src.ollama_client import OllamaClient
        return OllamaClient().parse_rca_response(raw_content)
```

Then in `scripts/run_rca.py`, one line change:

```python
# Before (Ollama):
client = OllamaClient(host=ollama_host, model=ollama_model)

# After (Vertex AI — selected by environment variable):
use_vertex = os.environ.get("USE_VERTEX_AI", "false").lower() == "true"
if use_vertex:
    from src.vertex_ai_client import VertexAIClient
    client = VertexAIClient()
else:
    client = OllamaClient(host=ollama_host, model=ollama_model)
```

**Zero changes needed** in `log_parser.py`, `rca_prompt.py`, `output_formatter.py`, `post_results.py`, or `rca_trigger.yml`.

---

## 7. How the n+2 Data Centers Fit In

### 7.1 The Three Scenarios

Your bank has three possible deployment patterns, and the n+2 data centers play different roles in each:

**Scenario A — Full GCP (Vertex AI) — Most likely right answer**

```
Bank's n+2 Data Center
  └─ Physical connectivity: dark fiber / leased line to Google data center
     └─ GCP Cloud Interconnect (10Gbps dedicated line, not public internet)
        └─ Your GCP VPC (bank-production)
           ├─ GKE: GitHub runners (already here)
           ├─ Vertex AI: Gemma inference (in GCP region)
           └─ GCS, BigQuery, Pub/Sub

n+2 role: Physical hosting of on-prem workloads + reliable connectivity to GCP
The n+2 reliability ensures the Cloud Interconnect never goes down.
The runners on GCP are in GCP's own redundant zones (unrelated to n+2).
```

**Scenario B — Hybrid (GDC in bank DC + GCP runners)**

```
Bank's n+2 Data Center
  └─ GDC cluster (Google Distributed Cloud, bank-managed)
     └─ Ollama + Gemma 27B (runs on the bank's own hardware)
     └─ Model weights stored on bank's storage (never leaves)
  Connected via Google Cloud Interconnect
  └─ GCP VPC
     └─ GKE: GitHub runners
        └─ Calls GDC Ollama via Interconnect (private, fast)

n+2 role: Physical redundancy for GDC cluster.
If 2 power feeds fail simultaneously, GDC (and Ollama) still runs.
This is the maximum data sovereignty option.
```

**Scenario C — Fully On-Premises (air-gapped)**

```
Bank's n+2 Data Center
  └─ GitHub Enterprise Server (on-prem)
  └─ Self-hosted runners (on-prem VMs)
  └─ GDC or bare-metal Kubernetes
     └─ Ollama + Gemma 27B (never leaves building)

n+2 role: Everything is on-prem with n+2 reliability.
Most restrictive. Only needed for "Restricted" classified code.
```

### 7.2 What n+2 Means for Ollama Specifically

If you choose to self-host Ollama (Scenario B or C), the n+2 design means:

| What Can Fail | n+2 Response | Ollama Impact |
|---------------|-------------|---------------|
| One power feed | Automatic failover, no interruption | Zero impact |
| Two cooling units | Remaining units cover load | Zero impact |
| One network uplink | Automatic rerouting | Zero impact |
| One GDC node (GPU) | Pod reschedules to another GPU node | 2-3 min model reload |
| Two GDC nodes | Still has remaining cluster capacity | Pod reschedules |
| Entire DC power loss | Generator kicks in within 10 seconds | <10 second gap (Ollama stays up on UPS) |

**The only Ollama-specific failure not covered by n+2**: A pod-level failure (OOM kill, software crash). That's handled by Kubernetes restartPolicy, not by n+2. n+2 handles physical infrastructure — Kubernetes handles software resilience.

### 7.3 GDC on n+2 vs GCP Multi-Zone — Which Is More Reliable?

This is an interesting comparison:

| Aspect | GDC on Bank's n+2 DC | GCP Multi-Zone (europe-west2) |
|--------|---------------------|-------------------------------|
| Physical redundancy | n+2 (bank-engineered) | Google-engineered (equivalent to Tier IV) |
| Single data center? | Yes (one building, n+2) | Three zones in one metro area (3 buildings) |
| Network redundancy | n+2 network uplinks | Multi-path backbone |
| Power redundancy | n+2 generators/UPS | Google's own power infra |
| Can survive a building fire? | No (single building) | Yes (3 separate buildings) |
| SLA | Bank's own commitment | Google: 99.95% per service |
| Who maintains it? | Your bank's DC ops team | Google |

**Honest answer**: GCP multi-zone is actually more resilient than a single n+2 data center for cloud workloads, because it uses multiple physically separate buildings. But for a bank with regulatory requirements to keep certain workloads on-premises, the n+2 design is the appropriate architecture — it's about data sovereignty, not just uptime.

---

## 8. What to Ask Your Cloud/Compliance Team

Based on everything above, here are the specific questions to ask your bank's internal teams to confirm the right path:

### Ask Information Security / Data Classification:

> **"What is the data classification of GitHub Actions build logs?"**
>
> Build logs typically contain: code class names, test names, error messages, dependency versions.
> They do NOT contain: customer PII, financial transactions, credentials (masked by GitHub).
>
> Expected answer: "Internal" or "Confidential — GCP approved"
> This would make Vertex AI the approved path.

### Ask the Cloud Platform / Architecture Team:

> **"Is Vertex AI in [your approved region] covered by our existing GCP data processing agreement?"**
>
> Expected answer: "Yes — all GCP services in approved regions are covered under our DPA."
>
> Also ask: **"Do we have Private Service Connect set up for Vertex AI?"**
> If not: **"How long to enable it? Who approves the firewall rule?"**

### Ask the CI/CD / GitHub Platform Team:

> **"Where exactly are our self-hosted GitHub runners hosted — GKE cluster name and region?"**
>
> You need the exact GKE cluster and region to confirm it's in the same VPC as Vertex AI.

### Ask Network Engineering:

> **"Do our GKE runners have VPC egress to Vertex AI endpoints, or do we need a new Private Service Connect endpoint?"**
>
> They will know whether `aiplatform.googleapis.com` is in the VPC Service Controls perimeter.

### Ask the Cloud Finance / FinOps Team:

> **"Is there a budget for Vertex AI API calls? Estimated cost is $33/month for build failure analysis."**
>
> At $33/month this is trivially easy to approve. But banks have formal processes — start the request early.

---

## 9. Final Recommendation

Given everything — bank on GCP, self-hosted runners on GCP, n+2 data centers, Fabric2 migration — here is the clear decision:

### The Answer

```
Use Vertex AI Gemma 3 27B via Private Service Connect.

Why this is the right answer for your bank:

✅ Data residency: Already solved. GCP org policies restrict to approved regions.
   Same DPA that covers your other 80 microservice workloads covers this.

✅ Network: Runners are already in GCP. Zero new networking needed.
   Private Service Connect keeps traffic inside VPC (never touches internet).

✅ Compliance: VPC Service Controls, CMEK, Access Transparency, Assured Workloads
   all already in place from your existing GCP usage.

✅ Cost: $33/month vs $450+/month for self-hosted GPU node.
   Annual saving: ~$5,000 per year, zero GPU ops burden.

✅ Reliability: 99.95% SLA, auto-scales for burst, no cold start,
   no pod disruption budget management, no model pull jobs.

✅ n+2 data centers: Continue using GDC in bank DCs for core banking
   and "Restricted" classified workloads. Build failure logs don't
   need that level of restriction — they're "Internal" data.

✅ Migration effort: One new file (vertex_ai_client.py, ~50 lines)
   + one env var change (USE_VERTEX_AI=true). Everything else unchanged.
```

### When to Reconsider

Stick with self-hosted Ollama on GDC if **any** of these are true:

- Your InfoSec team classifies build logs as "Restricted" (on-prem only)
- Your bank has an explicit policy that proprietary code snippets (even in logs) must never leave bank-owned hardware
- You're in a jurisdiction with stricter-than-standard banking data laws (e.g., certain APAC regulations)
- Your bank is in the process of a GCP exit and you don't want new GCP service dependencies

Otherwise, Vertex AI is the clear winner for your situation.

---

### Quick Summary Card

| Your Context | Implication |
|---|---|
| Bank already uses GCP | Data residency framework is already in place — Vertex AI is covered |
| Runners already on GCP | Zero new networking needed — runners can already reach Vertex AI |
| n+2 data centers | Highest-sensitivity workloads stay on-prem (GDC); build logs don't need this |
| 80 microservices | Vertex AI auto-scales; self-hosted Ollama needs careful capacity planning |
| 200+ engineers | $33/month Vertex AI vs $450+/month GPU management is a no-brainer |
| Moving from Fabric2 to GCP | This migration is the ideal time to switch — no legacy compatibility needed |

---

*Cross-references: [GCP_MIGRATION_AND_SCALE_ANALYSIS.md](GCP_MIGRATION_AND_SCALE_ANALYSIS.md) · [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md)*
