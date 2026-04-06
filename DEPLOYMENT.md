# Deployment Guide — Build Failure RCA Agent

## Overview

This guide walks through deploying the Ollama + Gemma 27B RCA system on your GDC/GKE cluster
and configuring it with your Fabric2 GitHub repos.

---

## Part 1: Deploy Ollama on GDC/GKE

### Prerequisites

- GDC or GKE cluster with a GPU node pool (NVIDIA T4 or L4, minimum 24GB VRAM)
- NVIDIA GPU device plugin installed (`nvidia.com/gpu` resource available in the cluster)
- `kubectl` configured to access the cluster
- Sufficient PV storage (50Gi) for model files

### Step 1: Create the namespace and storage

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/pvc.yaml
```

Verify the PVC is bound:

```bash
kubectl get pvc -n ollama-rca
```

### Step 2: Deploy Ollama

Edit `k8s/deployment.yaml` if needed:
- Change `nodeSelector` to match your GPU node label
- Adjust resource requests/limits for your hardware

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

Wait for the pod to be ready:

```bash
kubectl get pods -n ollama-rca -w
```

### Step 3: Pull the Gemma model

**Option A: Online cluster (can reach Ollama registry)**

```bash
kubectl apply -f k8s/init-model-job.yaml
kubectl logs -f job/ollama-model-pull -n ollama-rca
```

**Option B: Air-gapped GDC cluster (no internet)**

On a machine with internet access:

```bash
# 1. Pull model locally
ollama pull gemma3:27b-it-qat

# 2. Find the model files
ls ~/.ollama/models/

# 3. Tar the model directory
tar czf ollama-models.tar.gz -C ~/.ollama models/

# 4. Transfer to a machine with cluster access
scp ollama-models.tar.gz user@gdc-bastion:/tmp/
```

On the GDC cluster:

```bash
# 5. Copy into the running Ollama pod
OLLAMA_POD=$(kubectl get pod -n ollama-rca -l app=ollama-rca -o jsonpath='{.items[0].metadata.name}')

# Extract into the pod's model directory
kubectl cp /tmp/ollama-models.tar.gz ollama-rca/$OLLAMA_POD:/tmp/
kubectl exec -n ollama-rca $OLLAMA_POD -- tar xzf /tmp/ollama-models.tar.gz -C /root/.ollama/
kubectl exec -n ollama-rca $OLLAMA_POD -- rm /tmp/ollama-models.tar.gz

# 6. Restart the pod to pick up the model
kubectl rollout restart deployment/ollama-rca -n ollama-rca
```

### Step 4: Verify the deployment

```bash
# Port-forward for testing
kubectl port-forward svc/ollama-rca -n ollama-rca 11434:11434 &

# Check server health
curl http://localhost:11434/api/tags

# Test a quick inference
curl http://localhost:11434/api/chat -d '{
  "model": "gemma3:27b-it-qat",
  "stream": false,
  "messages": [{"role": "user", "content": "Say hello in one word"}]
}'
```

---

## Part 2: Configure GitHub Actions

### Prerequisites

- Admin access to your Fabric2 GitHub repos
- A GitHub PAT with `repo` scope (for reading Actions logs and posting PR comments)

### Step 1: Create GitHub secrets

In each Fabric2 repo (or at the org level), create these secrets:

| Secret Name | Value |
|-------------|-------|
| `RCA_GITHUB_TOKEN` | GitHub PAT with `repo` scope |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL (optional) |

### Step 2: Set up a self-hosted runner

The RCA workflow **must** run on a self-hosted runner that has network access to the
Ollama ClusterIP service inside GDC. Options:

1. **Runner inside GDC cluster**: Deploy a GitHub Actions runner as a K8s pod in GDC
2. **Runner on a peered network**: VM with VPN/peering access to the GDC cluster network

Install the runner following GitHub's docs and add these labels:
- `self-hosted`
- `linux`
- `gdc`

### Step 3: Add the RCA workflow to your repos

Copy `.github/workflows/rca_trigger.yml` to each Fabric2 repo.

**Important**: The `workflow_run` trigger requires the workflow file to be on the
**default branch** (usually `main`). Merge this file to `main` first.

Edit the workflow names in `rca_trigger.yml` to match your actual workflow names:

```yaml
on:
  workflow_run:
    workflows:
      - "release_build"     # ← Must match EXACTLY
      - "snapshot_build"    # ← Must match EXACTLY
    types:
      - completed
```

### Step 4: Update the Ollama host URL

In `rca_trigger.yml`, set `OLLAMA_HOST` to the internal DNS of the Ollama service:

```yaml
env:
  OLLAMA_HOST: "http://ollama-rca.ollama-rca.svc.cluster.local:11434"
```

If your runner is NOT inside the K8s cluster, use the node IP or an internal load balancer.

### Step 5: Test the pipeline

Trigger a manual test:

```bash
# Create a deliberate test failure and push it
# Or re-run a known failed workflow from the GitHub UI
```

Watch the RCA workflow execution in the Actions tab.

---

## Part 3: Monitoring & Maintenance

### Check Ollama pod status

```bash
kubectl get pods -n ollama-rca
kubectl logs deployment/ollama-rca -n ollama-rca --tail=50
```

### GPU utilization

```bash
kubectl exec -n ollama-rca deployment/ollama-rca -- nvidia-smi
```

### Model updates

To update the Gemma model:

```bash
kubectl exec -n ollama-rca deployment/ollama-rca -- ollama pull gemma3:27b-it-qat
```

### Cost optimization

The Ollama pod keeps the model in GPU memory. To save costs when not in use:

```bash
# Scale down when not needed
kubectl scale deployment/ollama-rca -n ollama-rca --replicas=0

# Scale back up before builds
kubectl scale deployment/ollama-rca -n ollama-rca --replicas=1
```

Or set `OLLAMA_KEEP_ALIVE` to a shorter duration (e.g., `30m`) so the model
unloads from GPU memory after 30 minutes of inactivity.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Model not found | Run `ollama pull gemma3:27b-it-qat` inside the pod |
| OOM on GPU | Use `gemma3:12b` instead, or use Q2 quantization |
| Workflow not triggering | Ensure `rca_trigger.yml` is on the default branch |
| Runner can't reach Ollama | Check network peering/VPN between runner and GDC |
| Slow inference | Verify GPU is being used: `nvidia-smi` should show GPU util |
| Log download fails | Check PAT has `repo` scope, not just `read:actions` |
