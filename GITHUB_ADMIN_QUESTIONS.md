# GitHub Admin Questions — Build Failure RCA POC

**Requestor:** [Your name / team]
**Project:** AI-Powered Build Failure RCA System
**Purpose:** When a GitHub Actions build fails, an automated workflow fetches the
build logs, sends them to a self-hosted AI model (running entirely within our
own infrastructure — no external API calls), and posts a Root Cause Analysis
comment on the Pull Request. Everything stays inside our network.

---

## Section 1 — GitHub Environment Basics

These answers determine which API base URL to use and whether standard
documentation applies or we need enterprise-specific workarounds.

| # | Question | Why We Need It |
|---|----------|----------------|
| 1.1 | Are our Fabric2 repos on **GitHub Enterprise Server (GHES)** or **GitHub Enterprise Cloud (GHEC)**? | The API base URL is different: GHES = `https://<hostname>/api/v3`, GHEC = `https://api.github.com` |
| 1.2 | If GHES — what is the **hostname** (e.g., `ghe.corp.com`)? | Hardcoded in every API call and workflow YAML |
| 1.3 | What is the **current GHES version** (e.g., 3.11, 3.12)? | Some Actions API endpoints were added in specific GHES versions |
| 1.4 | Is the **GitHub Actions feature** enabled on our enterprise/org? | Actions API endpoints return 404 if Actions is disabled at org level |
| 1.5 | Is there an **IP allowlist** or firewall rule controlling who can call the GitHub API? | Our self-hosted runner (on GCP/GDC) needs to be in the allowlist |

---

## Section 2 — Authentication: PAT or GitHub App?

The system needs to authenticate to two APIs: read build logs and write PR comments.

### 2A — Personal Access Token (simpler, good for POC)

| # | Question | Why We Need It |
|---|----------|----------------|
| 2A.1 | Can I create a **fine-grained Personal Access Token (PAT)** scoped to specific repos? | Fine-grained PATs are more secure than classic PATs — prefer these if GHES ≥ 3.10 or GHEC |
| 2A.2 | If fine-grained PATs are **not available or not approved**, can I use a **classic PAT with `repo` scope**? | Classic PAT is the fallback; `repo` scope covers everything we need |
| 2A.3 | Is there a **PAT approval workflow** — do I need manager or security team sign-off before creating one? | We want to start this process early to avoid delays |
| 2A.4 | What is the **maximum allowed PAT expiry**? (e.g., 90 days, 1 year, no-expiry) | We need to plan token rotation into the operational runbook |
| 2A.5 | Is there an **org-level SSO requirement** — do PATs need to be SSO-authorised before they can access org repos? | If SAML SSO is enforced, the PAT must be authorised after creation or all API calls return 403 |
| 2A.6 | Which **service account / bot account** should this PAT be created under? (Personal accounts are not recommended for automation) | Using a personal account means the token breaks when the person leaves |

### 2B — GitHub App (recommended for production, ask if this is an option)

| # | Question | Why We Need It |
|---|----------|----------------|
| 2B.1 | Can we register a **GitHub App** on the org? | Apps have 15,000 req/hr vs 5,000 for PATs; tokens auto-rotate every 1 hour |
| 2B.2 | Who has permission to **install a GitHub App** on org-level repos? | App installation requires org owner or admin approval |
| 2B.3 | Is there a **security review process** for GitHub Apps? | Good to know upfront even for POC — we can start with PAT and migrate to App later |

---

## Section 3 — Exact API Endpoints and Permissions Required

This is the full list of every GitHub API call the system makes. Please confirm
each endpoint is accessible and the authentication method above grants the
required permission.

### 3A — Reading Workflow Run Data (needed to fetch logs)

| Endpoint | HTTP Method | Required Permission | What We Use It For |
|----------|-------------|--------------------|--------------------|
| `/repos/{owner}/{repo}/actions/runs/{run_id}/jobs` | `GET` | `actions: read` | List the jobs in a failed run; identify which job(s) failed |
| `/repos/{owner}/{repo}/actions/jobs/{job_id}/logs` | `GET` | `actions: read` | Download the raw log text for a failed job |
| `/repos/{owner}/{repo}/actions/runs/{run_id}` | `GET` | `actions: read` | Get run metadata (start time, end time, conclusion) for build duration analysis |

> **For classic PAT:** the `repo` scope covers all three.
> **For fine-grained PAT:** need `Actions: Read` repository permission.
> **Note:** The log download endpoint (`/jobs/{job_id}/logs`) returns a **redirect to a time-limited signed URL** — the client must follow HTTP 302 redirects. Please confirm there is no firewall rule blocking redirect-following on API calls from our runner IP.

### 3B — Writing PR Comments (needed to post the RCA)

| Endpoint | HTTP Method | Required Permission | What We Use It For |
|----------|-------------|--------------------|--------------------|
| `/repos/{owner}/{repo}/pulls?state=open&head={branch}` | `GET` | `pull-requests: read` | Find the open PR associated with the failed build's branch |
| `/repos/{owner}/{repo}/issues/{pr_number}/comments` | `POST` | `pull-requests: write` | Post the RCA analysis as a comment on the PR |

> **For classic PAT:** the `repo` scope covers both.
> **For fine-grained PAT:** need `Pull requests: Read and write` repository permission.
> **Note:** GitHub's PR comment API is under `/issues/` — this is correct, not a mistake.

### 3C — Workflow Trigger (the RCA workflow itself)

| # | Question | Why We Need It |
|---|----------|----------------|
| 3C.1 | Can workflows use the **`workflow_run` trigger**? This trigger fires when a named workflow (e.g., `release_build`) completes with `conclusion: failure` | This is the core trigger for the entire system — without it, we need a different approach |
| 3C.2 | Does the `workflow_run` trigger work across **repos** (i.e., can a workflow in Repo A trigger on a `workflow_run` event from Repo B)? | Our current design puts the RCA workflow inside the same repo — this question is for future multi-repo deployment |
| 3C.3 | Is the `workflow_run` trigger **blocked by any org policy**? Some enterprises disable certain trigger types. | Need to know before committing to this architecture |

---

## Section 4 — Self-Hosted Runners

The RCA workflow must run on a self-hosted runner that has network access to
our Ollama model service (on GDC/GKE). GitHub-hosted runners cannot reach
our internal cluster.

| # | Question | Why We Need It |
|---|----------|----------------|
| 4.1 | Are **self-hosted runners** allowed on our GitHub org / repos? | Org admins can disable self-hosted runners entirely |
| 4.2 | Who can **register a new self-hosted runner** — do I do it, or does the GitHub admin team register it? | The registration token is obtained from `Settings → Actions → Runners → New self-hosted runner` at the repo or org level |
| 4.3 | Should the runner be registered at **repo level** (only works for one repo) or **org level** (shared across all Fabric2 repos)? | For a POC, repo-level is fine. For production across 80 microservices, org-level is better. |
| 4.4 | What **runner labels** are currently used on self-hosted runners in the org? (e.g., `self-hosted`, `linux`, `gdc`, `gcp`) | The `runs-on:` field in our workflow YAML must exactly match the labels assigned to the runner |
| 4.5 | Is there an **approval step** before a self-hosted runner is allowed to pick up jobs? (Some orgs require admin approval for new runners) | |
| 4.6 | Can the runner be a **Kubernetes pod** (Actions Runner Controller / ARC)? Or must it be a dedicated VM? | ARC on GKE is the cleanest approach for auto-scaling runners |
| 4.7 | Are there any **runner security policies** — e.g., must runners be in a specific VPC, must they have a certain OS image, must they be approved by infosec? | |

---

## Section 5 — Workflow and Secrets Configuration

| # | Question | Why We Need It |
|---|----------|----------------|
| 5.1 | Can I add **Actions Secrets** to a repository? (`Settings → Secrets and variables → Actions`) | We need to store `RCA_GITHUB_TOKEN` (the PAT) as a secret — it must never be in code |
| 5.2 | Is there an **org-level secrets** feature we should use instead of repo-level secrets? | Org-level secrets can be shared across all Fabric2 repos — better for production |
| 5.3 | Are there any **workflow YAML restrictions** — e.g., must all workflows be approved by a code owner before they run? | Some enterprises require CODEOWNERS review of `.github/workflows/` changes |
| 5.4 | Is the `GITHUB_TOKEN` (the automatic token created by Actions for each run) granted **`pull-requests: write`** permission by default, or has the org restricted it to read-only? | If the default `GITHUB_TOKEN` is restricted, we must use our PAT secret for the comment step |
| 5.5 | Can workflows set **explicit `permissions:` blocks** in YAML? (This is how we grant only the minimum required permissions) | Best practice — we already do this in our workflow |
| 5.6 | Is there a **required workflow** or **org-level Actions policy** that would conflict with adding `rca_trigger.yml` to each repo? | |

---

## Section 6 — Network Access (Runner ↔ Ollama)

The runner executing the RCA workflow needs to reach our Ollama service
on the internal cluster.

| # | Question | Why We Need It |
|---|----------|----------------|
| 6.1 | Does our self-hosted runner have **network connectivity to the GDC/GKE cluster**? Specifically, can it reach the Ollama ClusterIP service on port 11434? | This is the most critical networking question for the POC |
| 6.2 | If the runner is on GCP and Ollama is on GDC — is there a **VPN or interconnect** between the GCP VPC and the GDC network? | |
| 6.3 | Does the runner use a **corporate HTTP proxy** for outbound connections? If yes, what is the proxy hostname? | We must set `NO_PROXY` to exclude the Ollama hostname so inference traffic bypasses the proxy |
| 6.4 | Does the runner trust our **internal CA certificate**? If not, GitHub API calls over HTTPS may fail with SSL errors. | We can set `REQUESTS_CA_BUNDLE` env var if needed — but we need the CA cert path |
| 6.5 | Can the runner make **outbound calls to the GitHub API** (`api.github.com` or the GHES hostname)? | The RCA workflow fetches logs and posts comments — these are outbound calls from the runner |

---

## Section 7 — Rate Limits and API Quotas

With 80 microservices and ~100 releases/month, we need to stay within rate limits.

| # | Question | Why We Need It |
|---|----------|----------------|
| 7.1 | What is the **Actions API rate limit** for authenticated requests on our GHES/GHEC instance? (Standard: 5,000/hr for PAT, 15,000/hr for GitHub App) | At peak load (153 builds/day), we'll make ~4–6 API calls per build = ~900 calls/day — well within limits, but good to confirm |
| 7.2 | Is there a **secondary rate limit** on the `POST /issues/{number}/comments` endpoint? (GitHub imposes limits on write operations separately) | We post one comment per failed build |
| 7.3 | Are there any **org-level API throttle policies** beyond the standard GitHub rate limits? | Some large enterprises add additional API gateways with their own limits |
| 7.4 | Can we see **API usage metrics** for our org? (To monitor we're not approaching limits) | |

---

## Section 8 — Log Access and Data Classification

| # | Question | Why We Need It |
|---|----------|----------------|
| 8.1 | Are GitHub Actions **workflow run logs classified** at a specific data sensitivity level? | This affects whether logs can be sent to the AI model — though our model is self-hosted, compliance may require classification review |
| 8.2 | Is there a **data retention policy** for Actions logs? (GitHub default: 90 days; GHES configurable) | We store the RCA JSON output for 30 days as a workflow artifact — does this comply? |
| 8.3 | Do build logs contain any **secrets or credentials** that could be printed to stdout? (e.g., does the build print env vars?) | If yes, we need log scrubbing before sending to the AI model — the parser already trims to 80K tokens but doesn't scrub secrets |
| 8.4 | Is there any **DLP (Data Loss Prevention) policy** that scans workflow artifacts? | Our RCA JSON artifact contains error messages from build logs |

---

## Section 9 — POC Scope Agreement

To agree on scope before starting the 5-day POC:

| # | Question / Agreement Needed |
|---|-----------------------------|
| 9.1 | Which **specific repos** should we use for the POC? (Suggest 1–2 Fabric2 repos that have frequent `release_build` failures) |
| 9.2 | Can we have **temporary admin access** to those repos during the POC to configure secrets and runners without waiting for change tickets? |
| 9.3 | Is there a **change management process** (CAB, JIRA ticket, etc.) required before adding a new workflow file to a production repo? |
| 9.4 | Who should be the **point of contact** if the RCA workflow itself fails or causes a problem during the POC? |
| 9.5 | Should the RCA PR comment be **visible to all engineers**, or should we post it to a restricted channel (e.g., Slack only) during POC to avoid confusion? |

---

## Summary — Minimum Requirements for POC Day 1

To unblock the POC, we need answers to these items first:

| Priority | Item | Blocking? |
|----------|------|-----------|
| **P0** | GitHub type: GHES or GHEC? And API base URL | Yes — without this we cannot make a single API call |
| **P0** | PAT created with `repo` scope (classic) OR `Actions: read` + `Pull requests: write` (fine-grained), SSO-authorised if required | Yes — without this all API calls return 401/403 |
| **P0** | Self-hosted runner registered and reachable, with labels known | Yes — without this the workflow cannot run at all |
| **P0** | Runner can reach Ollama on port 11434 (or NodePort 30434) | Yes — without this the AI analysis step fails |
| **P1** | `workflow_run` trigger is not blocked by org policy | Yes — can work around with manual trigger for POC if needed |
| **P1** | `RCA_GITHUB_TOKEN` secret can be added to the POC repo | Yes — without this the PAT cannot be injected securely |
| **P2** | Confirm default `GITHUB_TOKEN` permissions (read vs write) | No — we can use our PAT secret as fallback |
| **P2** | IP allowlist updated to include runner IP (if allowlist exists) | Only if allowlist is enforced |
| **P3** | Org-level secrets / GitHub App (production hardening, not POC) | No — PAT is sufficient for POC |

---

## What We Will NOT Need (to address common concerns)

- **We do not send any data to external AI APIs.** The Gemma 3 27B model runs
  entirely on our own GDC/GKE cluster. Build logs never leave our network.
- **We do not need write access to code.** The PAT only needs to write PR
  *comments* — not commit code, merge PRs, or modify repo settings.
- **We do not need org admin access.** Repo-level `Actions: read` +
  `Pull requests: write` is sufficient for the POC.
- **We do not store logs permanently.** The RCA workflow artifact expires in
  30 days (configurable). Raw logs are processed in memory and never written
  to disk outside the runner's temporary workspace.

---

*Document version: 1.0 — created for POC planning. Update after admin responses.*
