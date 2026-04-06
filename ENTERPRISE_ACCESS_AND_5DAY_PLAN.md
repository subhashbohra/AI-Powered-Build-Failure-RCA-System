# Build Failure RCA Agent — Enterprise Access Guide & 5-Day POC Plan

> **Context**: Ollama + Gemma is already running on GDC (air-gapped). This document covers everything needed on the GitHub Enterprise side — access, tokens, runners, networking, SSL, and a day-by-day execution plan.

---

## Part 1: Access & Permissions Checklist

### 1.1 GitHub Personal Access Token (PAT)

You need a PAT that can read workflow logs, read job metadata, and post PR comments. In an enterprise setup this is the hardest thing to get — plan for 1-2 days of approval.

**Option A: Fine-Grained PAT (recommended)**

Go to: `GitHub Enterprise` → Your Profile → `Settings` → `Developer settings` → `Personal access tokens` → `Fine-grained tokens` → `Generate new token`

Required permissions (repository-scoped to your Fabric2 repos):

| Permission | Access Level | Why |
|------------|-------------|-----|
| **Actions** | Read | Download workflow run logs, list jobs and steps |
| **Contents** | Read | Checkout RCA scripts in the workflow |
| **Pull Requests** | Read & Write | Post RCA comment on associated PRs |
| **Metadata** | Read | Required by default for all fine-grained tokens |
| **Issues** | Read & Write | PR comments use the Issues API internally |

Set the resource owner to your **GitHub Enterprise organization** (e.g., `fabric2-org`). If your org requires admin approval for PATs, submit the request early — this is your Day 1 blocker.

Token expiry: Set to 90 days for the POC. You'll rotate to a GitHub App for production later.

**Option B: Classic PAT (fallback if fine-grained is blocked)**

Go to: `Settings` → `Developer settings` → `Personal access tokens` → `Tokens (classic)` → `Generate new token`

Required scopes:

| Scope | Why |
|-------|-----|
| `repo` | Full repo access — logs, PRs, comments (this is coarse but classic PATs don't allow finer control) |
| `workflow` | Required to read workflow run data in some enterprise configs |

**Option C: GitHub App (production-grade, not needed for POC)**

For production, create a GitHub App with the same permissions listed in Option A. Install it on your Fabric2 org. This gives you an installation token that auto-rotates and has better audit trails. Skip this for the POC.

**Who to ask**: Your GitHub Enterprise admin or DevOps/platform team. The request is: "I need a fine-grained PAT with Actions (read) + Pull Requests (read/write) scoped to [repo names], for a build failure analysis automation."

### 1.2 GITHUB_TOKEN Permissions in Workflow

The built-in `GITHUB_TOKEN` that GitHub Actions automatically provides to every workflow has limited permissions by default in enterprise setups. You need to check and potentially configure:

Check your org's default token permissions:
- Go to: `Organization Settings` → `Actions` → `General` → `Workflow permissions`
- It should be set to **"Read and write permissions"** or you need to explicitly set permissions in the workflow file

In the workflow YAML, add explicit permissions:

```yaml
permissions:
  actions: read          # Read workflow run logs
  contents: read         # Checkout code
  pull-requests: write   # Post RCA comments
  issues: write          # PR comments use issues API
```

**Common enterprise blocker**: Some orgs restrict `GITHUB_TOKEN` to read-only at the enterprise level. If you hit this, you'll use your PAT (stored as a secret `RCA_GITHUB_TOKEN`) for all API calls instead of `GITHUB_TOKEN`. The RCA workflow already uses `RCA_GITHUB_TOKEN` for this reason.

### 1.3 Repository Secrets

You need to create these secrets in each Fabric2 repo (or at org level if your enterprise allows org-level secrets):

Go to: `Repo` → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

| Secret Name | Value | Notes |
|-------------|-------|-------|
| `RCA_GITHUB_TOKEN` | Your PAT from step 1.1 | Used for API calls (log download, PR comment) |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL | Optional — only if you want Slack alerts |

**Who to ask**: You need `admin` or `maintain` role on the repo to create secrets. If you don't have this, ask your repo admin or team lead.

**Org-level secrets** (to avoid setting per-repo): `Org Settings` → `Secrets and variables` → `Actions` → `New organization secret`. Select which repos can access it.

### 1.4 Actions Permissions on the Repository

Check that GitHub Actions is enabled for your repos and that the workflow can run:

Go to: `Repo` → `Settings` → `Actions` → `General`

Verify:
- **Actions permissions**: "Allow all actions and reusable workflows" or at minimum allow `actions/checkout`, `actions/setup-python`, `actions/upload-artifact`
- **Workflow permissions**: "Read and write permissions" (or you rely on your PAT)
- **Fork pull request workflows**: Doesn't matter for this POC (we trigger on `workflow_run`, not PRs)

**Common enterprise blocker**: Some enterprise configs restrict Actions to only "Allow enterprise actions" or a specific allowlist. If so, you need to get `actions/checkout@v4`, `actions/setup-python@v5`, and `actions/upload-artifact@v4` added to the allowlist.

**Who to ask**: Your GitHub Enterprise admin or the team that manages the Actions policy.

---

## Part 2: Self-Hosted Runner Setup

### 2.1 Understanding Your Runner Landscape

Your enterprise likely already has self-hosted runners for `release_build` and `snapshot_build`. The RCA workflow needs to run on a runner that has:

1. **Network access to GitHub API** (to download logs) — your existing runners already have this
2. **Network access to Ollama ClusterIP** (`ollama-rca.ollama-rca.svc.cluster.local:11434`) — this is the new requirement

**Three scenarios**:

| Scenario | Runner Location | Network Path to Ollama | Effort |
|----------|----------------|----------------------|--------|
| **A: Runner is inside GDC K8s cluster** | Pod in GDC | Direct ClusterIP access | Easiest |
| **B: Runner is on a VM peered with GDC** | VM on same VPC/network | ClusterIP via kube-proxy or NodePort | Medium |
| **C: Runner is completely separate** | Different network | Need VPN/tunnel or expose Ollama externally | Hardest |

**Find out first**: Where do your current `release_build` / `snapshot_build` runners live? Run this in any workflow to check:

```yaml
- name: Check runner info
  run: |
    echo "Hostname: $(hostname)"
    echo "IP: $(hostname -I)"
    echo "Can reach GDC? $(curl -s -o /dev/null -w '%{http_code}' http://ollama-rca.ollama-rca.svc.cluster.local:11434/api/tags || echo 'NO')"
```

### 2.2 Scenario A: Runner Inside GDC Cluster (Best Case)

If your runners are Kubernetes pods inside GDC (e.g., using Actions Runner Controller — ARC), ClusterIP just works. Set:

```yaml
env:
  OLLAMA_HOST: "http://ollama-rca.ollama-rca.svc.cluster.local:11434"
```

No additional networking needed.

### 2.3 Scenario B: Runner on Peered VM

If the runner VM is on the same VPC but outside the K8s cluster, ClusterIP won't resolve. Options:

**Option 1: NodePort Service (simplest)**

Change the Ollama service from ClusterIP to NodePort:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: ollama-rca
  namespace: ollama-rca
spec:
  type: NodePort
  selector:
    app: ollama-rca
  ports:
    - port: 11434
      targetPort: 11434
      nodePort: 30434    # Pick a port in 30000-32767
      protocol: TCP
```

Then set `OLLAMA_HOST` to any GDC node IP:

```yaml
env:
  OLLAMA_HOST: "http://<GDC_NODE_IP>:30434"
```

**Option 2: kubectl port-forward (dev/POC only)**

On the runner VM, set up a persistent port-forward:

```bash
# Run as a systemd service or screen session on the runner
kubectl port-forward svc/ollama-rca -n ollama-rca 11434:11434 --address 0.0.0.0
```

Then `OLLAMA_HOST=http://localhost:11434` works.

### 2.4 Scenario C: Separate Network

If the runner has zero network path to GDC, you need to either:

1. **Register a new runner inside GDC** specifically for RCA jobs (see below)
2. **Set up a VPN/tunnel** between the runner network and GDC
3. **Expose Ollama via an internal load balancer** (ask your platform team)

**Registering a new runner inside GDC** (for the POC):

```bash
# On a GDC VM or inside a pod with Docker access
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.321.0/actions-runner-linux-x64-2.321.0.tar.gz
tar xzf actions-runner-linux-x64.tar.gz

# Get registration token from GitHub
# Go to: Repo → Settings → Actions → Runners → New self-hosted runner
# Or use API:
curl -X POST \
  -H "Authorization: token YOUR_PAT" \
  -H "Accept: application/vnd.github+json" \
  https://YOUR_GITHUB_ENTERPRISE_HOST/api/v3/repos/OWNER/REPO/actions/runners/registration-token

# Configure the runner
./config.sh \
  --url https://YOUR_GITHUB_ENTERPRISE_HOST/OWNER/REPO \
  --token REGISTRATION_TOKEN \
  --name "gdc-rca-runner" \
  --labels "self-hosted,linux,gdc,rca" \
  --work "_work"

# Start the runner
./run.sh  # Or install as service: sudo ./svc.sh install && sudo ./svc.sh start
```

**Enterprise runner registration**: In enterprise setups, you may need to register at the **org level** instead of repo level. This requires `admin:org` scope on the PAT. Ask your enterprise admin if self-hosted runner registration is allowed at the repo level.

### 2.5 Runner Labels

Update the `runs-on` in `rca_trigger.yml` to match your runner labels:

```yaml
# If using existing enterprise runners that can reach GDC:
runs-on: [self-hosted, linux]

# If you registered a dedicated runner with custom labels:
runs-on: [self-hosted, linux, gdc, rca]

# If your enterprise uses runner groups, specify the group:
runs-on:
  group: gdc-runners
  labels: [self-hosted, linux]
```

---

## Part 3: Networking & SSL

### 3.1 Network Flow Diagram

```
GitHub Enterprise Server / github.com
    │
    │ HTTPS (443) — outbound from runner
    │
Self-Hosted Runner (VM or Pod)
    │
    │ HTTP (11434) — to Ollama ClusterIP/NodePort
    │
GDC K8s Cluster
    └── ollama-rca Service → Ollama Pod (GPU)
```

### 3.2 Firewall Rules Needed

| From | To | Port | Protocol | Purpose |
|------|----|------|----------|---------|
| Runner | GitHub Enterprise | 443 | HTTPS | Poll for jobs, download logs, post comments |
| Runner | Ollama Service | 11434 | HTTP | Send logs for RCA analysis |
| Runner | GitHub API | 443 | HTTPS | REST API calls for log download |

**Runners only need outbound connections**. GitHub Actions runners use HTTPS long-polling (outbound 443) to receive jobs — no inbound firewall holes needed.

For the Ollama connection, if ClusterIP doesn't work from the runner, use NodePort (30434) and open that port between the runner network and GDC nodes.

### 3.3 SSL/TLS Considerations

**Runner ↔ GitHub**: Already HTTPS. Enterprise setups often use custom CA certificates. If your runner gets SSL errors when calling the GitHub API:

```bash
# Check if custom CA is needed
curl -v https://YOUR_GITHUB_ENTERPRISE_HOST/api/v3

# If SSL verification fails, add the enterprise CA cert:
# Option 1: System trust store
sudo cp enterprise-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates

# Option 2: In Python requests
export REQUESTS_CA_BUNDLE=/path/to/enterprise-ca.crt

# Option 3: In the workflow env
env:
  REQUESTS_CA_BUNDLE: /etc/ssl/certs/ca-certificates.crt
```

**Runner ↔ Ollama**: Plain HTTP (port 11434) within the cluster. This is fine for a POC since it's internal traffic. For production, you'd put Ollama behind an NGINX ingress with mTLS.

### 3.4 GitHub Enterprise Server vs GitHub.com

If you're on **GitHub Enterprise Server** (self-hosted), the API base URL is different:

```python
# GitHub.com
GITHUB_API = "https://api.github.com"

# GitHub Enterprise Server
GITHUB_API = "https://YOUR_GHE_HOST/api/v3"
```

Update `src/log_parser.py` — change all `https://api.github.com` references to use an environment variable:

```yaml
# In rca_trigger.yml, add:
env:
  GITHUB_API_URL: ${{ github.api_url }}   # Auto-detects GHE vs github.com
```

The workflow already provides `${{ github.api_url }}` which resolves correctly for both.

### 3.5 Proxy Configuration

If your runner goes through a corporate proxy to reach GitHub:

```bash
# Set on the runner machine
export HTTP_PROXY=http://proxy.corp.com:8080
export HTTPS_PROXY=http://proxy.corp.com:8080
export NO_PROXY=localhost,127.0.0.1,ollama-rca.ollama-rca.svc.cluster.local,.internal

# In the runner's .env file:
echo "HTTP_PROXY=http://proxy.corp.com:8080" >> .env
echo "HTTPS_PROXY=http://proxy.corp.com:8080" >> .env
echo "NO_PROXY=localhost,127.0.0.1,.svc.cluster.local,.internal" >> .env
```

**Critical**: The `NO_PROXY` must include the Ollama service hostname so RCA requests go direct and don't hit the proxy.

---

## Part 4: Pre-Flight Verification Script

Run this script on your runner machine to verify everything is in place before starting the POC:

```bash
#!/bin/bash
# save as: preflight_check.sh

echo "========================================="
echo "  Build RCA Agent — Pre-Flight Check"
echo "========================================="

PASS=0
FAIL=0

check() {
    if eval "$2" > /dev/null 2>&1; then
        echo "  ✅ $1"
        PASS=$((PASS+1))
    else
        echo "  ❌ $1"
        FAIL=$((FAIL+1))
    fi
}

echo ""
echo "1. System Requirements"
check "Python 3.11+" "python3 --version | grep -E '3\.(11|12|13|14)'"
check "pip available" "pip3 --version"
check "curl available" "curl --version"
check "unzip available" "unzip -v"
check "jq available" "jq --version"

echo ""
echo "2. GitHub API Access"
GH_HOST="${GITHUB_API_URL:-https://api.github.com}"
GH_TOKEN="${RCA_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -n "$GH_TOKEN" ]; then
    check "GitHub API reachable" "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: token $GH_TOKEN' $GH_HOST/user | grep 200"
    check "Can list repos" "curl -s -H 'Authorization: token $GH_TOKEN' $GH_HOST/user/repos?per_page=1 | jq '.[] | .name'"
else
    echo "  ⚠️  No GITHUB_TOKEN or RCA_GITHUB_TOKEN set — skipping API checks"
    FAIL=$((FAIL+1))
fi

echo ""
echo "3. Ollama Connectivity"
OLLAMA="${OLLAMA_HOST:-http://localhost:11434}"
check "Ollama server reachable" "curl -s -o /dev/null -w '%{http_code}' $OLLAMA/api/tags | grep 200"
check "Gemma model loaded" "curl -s $OLLAMA/api/tags | jq -r '.models[].name' | grep -i gemma"
check "Ollama inference works" "curl -s -X POST $OLLAMA/api/chat -d '{\"model\":\"gemma3:27b-it-qat\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"say ok\"}]}' | jq -r '.message.content'"

echo ""
echo "4. Network"
check "DNS resolves GitHub" "nslookup ${GH_HOST#https://}"
check "Outbound HTTPS works" "curl -s -o /dev/null -w '%{http_code}' https://github.com | grep -E '(200|301)'"

echo ""
echo "========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================="
```

---

## Part 5: The 5-Day POC Plan

### Day 1: Access & Approvals (Monday)

**Goal**: Get all permissions and tokens in place. This is the blocker for everything else.

**Morning (2-3 hours)**:
- [ ] **Request PAT**: Create fine-grained PAT with Actions (read) + Pull Requests (read/write). If your enterprise requires admin approval, submit it NOW. This is your critical path item.
- [ ] **Identify your runner situation**: Find out where your `release_build` / `snapshot_build` runners are hosted. Talk to your platform/DevOps team. Determine if it's Scenario A, B, or C from Part 2.
- [ ] **Identify the GitHub Enterprise API URL**: Is it `https://api.github.com` or `https://github.yourcompany.com/api/v3`?
- [ ] **Check Actions policy**: Verify that `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4` are allowed in your enterprise.

**Afternoon (2-3 hours)**:
- [ ] **Create repo secrets**: Once PAT is approved, add `RCA_GITHUB_TOKEN` and optionally `SLACK_WEBHOOK_URL` to your target Fabric2 repo.
- [ ] **Test API access manually**: From your laptop or the runner machine, run:
  ```bash
  # Replace with your values
  export GH_TOKEN="ghp_your_token"
  export GH_API="https://api.github.com"  # or your GHE URL
  export REPO="your-org/your-repo"

  # Test: Can I read workflow runs?
  curl -H "Authorization: token $GH_TOKEN" "$GH_API/repos/$REPO/actions/runs?per_page=1" | jq '.workflow_runs[0].id'

  # Test: Can I read job logs? (use a real run_id from above)
  RUN_ID=<paste_run_id>
  curl -H "Authorization: token $GH_TOKEN" "$GH_API/repos/$REPO/actions/runs/$RUN_ID/jobs" | jq '.jobs[] | {name, conclusion}'
  ```
- [ ] **Verify Ollama is healthy**: From a machine on the GDC network:
  ```bash
  curl http://ollama-rca.ollama-rca.svc.cluster.local:11434/api/tags
  ```

**Deliverable**: PAT created (or approval submitted), runner location identified, API access tested manually.

**Escalation points**: If PAT approval takes more than 24 hours, use a classic PAT with `repo` scope as a temporary workaround (less secure but unblocks you). If Actions policy blocks required actions, ask your admin to add them to the allowlist.

---

### Day 2: Runner Networking & Connectivity (Tuesday)

**Goal**: Ensure the runner can reach both GitHub API and Ollama, and set up the project repo.

**Morning (3-4 hours)**:
- [ ] **Test runner → Ollama connectivity**: If your runners are inside GDC (Scenario A), just test ClusterIP. If not, set up NodePort:
  ```bash
  # Apply NodePort service (if needed)
  kubectl apply -f k8s/service-nodeport.yaml

  # From runner, test:
  curl http://<GDC_NODE_IP>:30434/api/tags
  ```
- [ ] **Handle SSL issues**: If the runner gets certificate errors calling the GitHub API, install the enterprise CA cert (see Part 3.3).
- [ ] **Handle proxy issues**: If the runner uses a corporate proxy, configure `NO_PROXY` for the Ollama hostname (see Part 3.5).
- [ ] **Run the pre-flight check script** (Part 4) on the runner machine. Fix all failures.

**Afternoon (2-3 hours)**:
- [ ] **Set up the project in your Fabric2 repo**: Clone the `build-rca-agent` scaffold into your repo:
  ```bash
  # Option A: Dedicated repo for RCA scripts
  git clone <your-rca-repo>
  cp -r build-rca-agent/* .
  git add -A && git commit -m "feat: add build failure RCA agent scaffold"
  git push

  # Option B: Add to an existing Fabric2 repo
  cp -r build-rca-agent/scripts your-repo/scripts/rca/
  cp -r build-rca-agent/src your-repo/src/rca/
  cp build-rca-agent/requirements.txt your-repo/rca-requirements.txt
  cp build-rca-agent/.github/workflows/rca_trigger.yml your-repo/.github/workflows/
  ```
- [ ] **Update `rca_trigger.yml`** with your actual values:
  - `OLLAMA_HOST` — the URL your runner can reach
  - `runs-on` labels — match your enterprise runner labels
  - Workflow names under `workflow_run.workflows` — must match EXACTLY
- [ ] **Commit to the default branch** (usually `main`). The `workflow_run` trigger only works from the default branch.

**Deliverable**: Runner can reach both GitHub API and Ollama. Project files committed to repo. Pre-flight check passes.

---

### Day 3: Log Parsing & Ollama Integration (Wednesday)

**Goal**: Test the full pipeline locally — fetch real logs from a past failed build, parse them, send to Ollama, get RCA.

**Morning (3-4 hours)**:
- [ ] **Find a recent failed build**: In your Fabric2 repo, go to Actions tab, find a `release_build` or `snapshot_build` that failed. Note the `run_id`.
- [ ] **Test log fetching locally**:
  ```bash
  cd your-repo
  pip install -r requirements.txt  # or rca-requirements.txt

  export GITHUB_TOKEN="your_pat"
  export GITHUB_API_URL="https://api.github.com"  # or GHE URL

  # Test: fetch and parse logs for a known failed run
  python -c "
  from src.log_parser import parse_build_logs
  result = parse_build_logs('your-org/your-repo', RUN_ID_HERE, '$GITHUB_TOKEN')
  print(f'Jobs: {len(result.jobs)}')
  print(f'Duration: {result.total_duration_minutes} min')
  print(f'Error lines: {len(result.error_lines)}')
  print(result.jobs_summary)
  print('---LOG PREVIEW---')
  print(result.trimmed_log_content[:2000])
  "
  ```
- [ ] **Fix log parser if needed**: If your builds use a different test framework (e.g., Gradle instead of Maven, pytest instead of JUnit), add appropriate error patterns to `ERROR_PATTERNS` in `src/log_parser.py`.
- [ ] **Handle GHE API URL**: If `fetch_workflow_jobs()` fails, update the base URL in `src/log_parser.py` to use `os.environ.get("GITHUB_API_URL", "https://api.github.com")`.

**Afternoon (2-3 hours)**:
- [ ] **Test Ollama integration**: Port-forward Ollama if needed, then run:
  ```bash
  export OLLAMA_HOST="http://localhost:11434"  # or actual URL
  export OLLAMA_MODEL="gemma3:27b-it-qat"

  # Quick inference test
  python -c "
  from src.ollama_client import OllamaClient
  client = OllamaClient()
  print('Health:', client.health_check())
  resp = client.chat([{'role':'user','content':'Say hello in one word'}])
  print('Response:', resp['message']['content'])
  "
  ```
- [ ] **Run the full pipeline locally** against the real failed build:
  ```bash
  export GITHUB_TOKEN="your_pat"
  export OLLAMA_HOST="http://localhost:11434"

  python scripts/run_rca.py \
    --repo your-org/your-repo \
    --run-id <FAILED_RUN_ID> \
    --workflow-name "release_build" \
    --branch "main" \
    --sha "abc123" \
    --actor "you"
  ```
- [ ] **Review the RCA output**: Check `rca_output/rca_report.md` and `rca_output/rca_report.json`. Is the analysis accurate? Does it identify the real root cause?
- [ ] **Tune the prompt if needed**: If Gemma's analysis is off, adjust `src/rca_prompt.py` — add more context about your build system, common failure patterns, or specific test frameworks.

**Deliverable**: Full pipeline runs locally end-to-end. RCA output is reviewed and reasonably accurate.

---

### Day 4: GitHub Actions Integration (Thursday)

**Goal**: Get the workflow triggering automatically on build failures in your Fabric2 repo.

**Morning (3-4 hours)**:
- [ ] **Verify `rca_trigger.yml` is on default branch**: The `workflow_run` trigger ONLY works from the default branch. Confirm with:
  ```bash
  git log main --oneline -- .github/workflows/rca_trigger.yml | head -1
  ```
- [ ] **Trigger a test failure**: Either:
  - Push a commit that you know will break the build (e.g., a syntax error in a test)
  - Re-run a previously failed workflow from the GitHub Actions UI (⟳ button)
  - Use `workflow_dispatch` to manually trigger the RCA workflow for testing:
    ```yaml
    # Add this temporarily to rca_trigger.yml for testing:
    on:
      workflow_dispatch:
        inputs:
          run_id:
            description: 'Failed run ID to analyze'
            required: true
      workflow_run:
        workflows: ["release_build", "snapshot_build"]
        types: [completed]
    ```
- [ ] **Watch the workflow**: Go to Actions tab → "Build Failure RCA" → watch the run in real time.
- [ ] **Debug common failures**:

  | Symptom | Fix |
  |---------|-----|
  | "Waiting for a runner to pick up this job" | Runner labels don't match `runs-on`. Check labels. |
  | "Error: Resource not accessible by integration" | `GITHUB_TOKEN` permissions too restrictive. Use `RCA_GITHUB_TOKEN` PAT instead. |
  | "Connection refused" on Ollama call | Runner can't reach Ollama. Check networking (Part 2). |
  | "SSL: CERTIFICATE_VERIFY_FAILED" | Enterprise CA cert not installed on runner. See Part 3.3. |
  | "Model not found" | Ollama has the model but with a different name. Run `ollama list` on the pod. |
  | Workflow doesn't trigger at all | `workflow_run.workflows` names don't match exactly. Check capitalization and spelling. |

**Afternoon (2-3 hours)**:
- [ ] **Verify the RCA artifact**: After a successful run, check Actions → the RCA run → Artifacts. Download `rca-report-<run_id>` and review.
- [ ] **Test PR comment posting**: If the failed build was triggered by a PR, check that the RCA comment appeared on the PR.
- [ ] **Test Slack notification** (if configured): Verify the Slack message was posted to the correct channel.
- [ ] **Run 2-3 more tests** with different failure types (if you have them in your history) to validate the system works across scenarios.

**Deliverable**: End-to-end workflow triggers automatically on build failure, produces RCA report, posts to PR.

---

### Day 5: Polish, Documentation & Demo (Friday)

**Goal**: Clean up, document findings, prepare a demo for your team/leadership.

**Morning (2-3 hours)**:
- [ ] **Remove the `workflow_dispatch` testing trigger** from `rca_trigger.yml` (unless you want to keep it for manual reruns).
- [ ] **Review and tune**:
  - Are the RCA categories accurate for your build types?
  - Is the build time threshold (20 min) appropriate for your builds?
  - Should you add more error patterns for your specific tech stack?
- [ ] **Add to more Fabric2 repos**: Copy `rca_trigger.yml` to other repos. The scripts can live in a shared repo or be duplicated.
- [ ] **Document what you learned**: Capture any enterprise-specific quirks, network configs, or workarounds you had to use.

**Afternoon (2-3 hours)**:
- [ ] **Prepare demo materials**:
  - Screenshot of a failed build triggering the RCA workflow
  - Screenshot of the RCA comment on a PR
  - Screenshot of the Slack notification
  - Sample RCA JSON output showing the structured analysis
  - Before/after comparison: "Previously, developers had to manually dig through 500 lines of logs. Now Gemma identifies the root cause in 30 seconds."
- [ ] **Present to your team/manager**: Quick 15-min demo showing the end-to-end flow.
- [ ] **Create a backlog** for production hardening:
  - Migrate from PAT to GitHub App for better security
  - Add monitoring/alerting for Ollama pod health
  - Add RCA accuracy tracking (was the suggestion helpful?)
  - Consider Gemma model fine-tuning on your build logs
  - Set up OLLAMA_KEEP_ALIVE auto-scaling to save GPU costs

**Deliverable**: POC complete, demoed to team, documented, production backlog created.

---

## Part 6: Quick Reference — Who To Talk To

| Need | Who to Ask | What to Say |
|------|-----------|-------------|
| PAT approval | GitHub Enterprise admin | "I need a fine-grained PAT with Actions read + PR write for build automation" |
| Actions allowlist | Platform/DevOps team | "Can you add actions/checkout, actions/setup-python, actions/upload-artifact to the Actions allowlist?" |
| Runner registration | Platform/DevOps team | "I need to register a self-hosted runner that can reach both GitHub and our GDC cluster" |
| Firewall rule (NodePort) | Network/Infra team | "I need port 30434 TCP open from the runner network to GDC node IPs for internal LLM inference" |
| Repo admin access | Team lead / repo owner | "I need maintain/admin access to add workflow files and secrets to [repo name]" |
| Slack webhook | Team Slack admin | "I need an incoming webhook URL for a build-failure-alerts channel" |

---

## Part 7: CLAUDE.md Addendum for Claude Code

Add this section to your `CLAUDE.md` when you start building with Claude Code:

```markdown
## Enterprise-Specific Configuration

- **GitHub API Base URL**: Use `os.environ.get("GITHUB_API_URL", "https://api.github.com")`
- **Runner labels**: `[self-hosted, linux, <your-labels>]`
- **Ollama endpoint**: `http://<your-ollama-host>:<port>`
- **Enterprise CA cert**: Set `REQUESTS_CA_BUNDLE` env var if needed
- **Proxy**: Set `NO_PROXY` to include Ollama hostname

## Known Enterprise Blockers & Workarounds

- If fine-grained PAT is blocked, use classic PAT with `repo` scope
- If `workflow_run` doesn't trigger, verify workflow names match EXACTLY (case-sensitive)
- If GITHUB_TOKEN is read-only, all API calls use RCA_GITHUB_TOKEN secret
- If Actions allowlist is restrictive, pin to specific action SHA instead of tag
```
