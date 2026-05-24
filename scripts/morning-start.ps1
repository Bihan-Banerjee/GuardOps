# scripts/morning-start.ps1
#
# GuardOps Phase 8 - Morning Startup
#
# Provisions EKS, installs the full observability + runtime security stack,
# deploys the webhook handler, deploys the test app, and opens port-forwards.
#
# Run time: ~25-30 min total (terraform ~12 min, stacks ~10 min, deploy ~3 min)
# Cost: ~$5.28/day while running. Run night-shutdown.ps1 every evening.
#
# Usage: .\scripts\morning-start.ps1

$ErrorActionPreference = "Stop"
$Region       = "ap-south-1"
$RepoRoot     = "D:\EXTRA\GuardOps"
$TerraformDir = "$RepoRoot\infra\terraform"

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  GuardOps Phase 8 - Morning Startup  " -ForegroundColor Cyan
Write-Host "  Est. cost  : ~`$5.28/day            " -ForegroundColor Yellow
Write-Host "  Est. time  : ~25-30 min             " -ForegroundColor Yellow
Write-Host "  Run night-shutdown.ps1 when done!   " -ForegroundColor Red
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Terraform apply ───────────────────────────────────────────────────
Write-Host "[1/7] Running terraform apply..." -ForegroundColor Green
Set-Location $TerraformDir

$clusterExists = $null
try {
    $clusterExists = & aws eks describe-cluster --name guardops-prod-cluster --region $Region 2>$null
} catch {
    $clusterExists = $null
}

if (-not $clusterExists) {
    Write-Host "  EKS not found - running Phase 1 (AWS infra only)..." -ForegroundColor Yellow
    & terraform apply --target="module.vpc" --target="module.iam" --target="module.iam_oidc" --target="module.eks" --target="module.ecr" --target="module.s3" -auto-approve
    if ($LASTEXITCODE -ne 0) { Write-Error "terraform apply (phase 1) failed."; exit 1 }
    Write-Host ""
    Write-Host "  EKS provisioned. Re-run morning-start.ps1 to continue setup." -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "  EKS exists - running full apply..." -ForegroundColor Gray
    terraform apply -auto-approve
    if ($LASTEXITCODE -ne 0) { Write-Error "terraform apply failed."; exit 1 }
}

# Verify OIDC role exists and update GitHub secret
$roleArn = terraform output -raw github_actions_role_arn
Write-Host "GitHub Actions role: $roleArn"
gh secret set AWS_ROLE_ARN --body $roleArn

# ── Step 2: Configure kubectl ─────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/7] Configuring kubectl for EKS..." -ForegroundColor Green

$ClusterName = terraform output -raw eks_cluster_name 2>$null
if (-not $ClusterName) { Write-Error "Could not get cluster name from terraform output."; exit 1 }

aws eks update-kubeconfig --region $Region --name $ClusterName
if ($LASTEXITCODE -ne 0) { Write-Error "kubectl config update failed."; exit 1 }
Write-Host "  Context set for: $ClusterName" -ForegroundColor Green

# ── Step 3: Wait for node Ready ───────────────────────────────────────────────
Write-Host ""
Write-Host "[3/7] Waiting for EKS node to become Ready (up to 5 min)..." -ForegroundColor Green

$MaxAttempts = 30
$Attempt     = 0
$NodeReady   = $false

do {
    $Attempt++
    Start-Sleep -Seconds 10
    $NodeStatus = kubectl get nodes --no-headers 2>$null | Select-String "\bReady\b"
    if ($NodeStatus) { $NodeReady = $true; break }
    Write-Host "  Attempt $Attempt/$MaxAttempts - node not ready yet..."
} while ($Attempt -lt $MaxAttempts)

if (-not $NodeReady) {
    Write-Host "  Node not ready after 5 min - continuing anyway" -ForegroundColor Yellow
} else {
    kubectl get nodes
    Write-Host "  Node is Ready" -ForegroundColor Green
}

# ── Step 4: Install observability stack ───────────────────────────────────────
Write-Host ""
Write-Host "[4/7] Installing observability stack (Prometheus + Grafana)..." -ForegroundColor Green
Set-Location $RepoRoot

.\scripts\setup-observability.ps1
if ($LASTEXITCODE -ne 0) { Write-Error "setup-observability.ps1 failed."; exit 1 }

# ── Step 5: Install runtime security stack ────────────────────────────────────
Write-Host ""
Write-Host "[5/7] Installing runtime security stack (Loki + Promtail + Falco)..." -ForegroundColor Green

.\scripts\setup-runtime-security.ps1
if ($LASTEXITCODE -ne 0) { Write-Error "setup-runtime-security.ps1 failed."; exit 1 }

$SimulatorExists = kubectl get cronjob falco-simulator -n monitoring --ignore-not-found 2>$null
if (-not $SimulatorExists) {
    Write-Host "  Applying Falco simulator CronJob..." -ForegroundColor Gray
    kubectl apply -f "$RepoRoot\k8s\falco\falco-simulator-configmap.yaml"
    kubectl apply -f "$RepoRoot\k8s\falco\falco-simulator-cronjob.yaml"
    Write-Host "  Falco simulator deployed (fires every 3 min)" -ForegroundColor Green
} else {
    Write-Host "  Falco simulator already present" -ForegroundColor Gray
}

# ── Step 6: Wire Alertmanager self-healing (Phase 8) ─────────────────────────
Write-Host ""
Write-Host "[6/7] Wiring Alertmanager self-healing (Phase 8)..." -ForegroundColor Green

# The webhook handler pod was deployed by terraform apply above (enable_self_healing = true).
# This step applies the PrometheusRule + AlertmanagerConfig that route Prometheus alerts
# to the handler. These are plain K8s manifests, not Terraform resources.

$WebhookPod = kubectl get pods -n monitoring -l app=alertmanager-webhook --no-headers 2>$null | Select-String "Running"
if (-not $WebhookPod) {
    Write-Host "  Webhook handler pod not Running yet - waiting 30s..." -ForegroundColor Yellow
    Start-Sleep -Seconds 30
}

$WebhookPod = kubectl get pods -n monitoring -l app=alertmanager-webhook --no-headers 2>$null | Select-String "Running"
if ($WebhookPod) {
    kubectl apply -f "$RepoRoot\k8s\alertmanager\quarantine-webhook.yaml"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  quarantine-webhook.yaml apply failed - check output above" -ForegroundColor Yellow
        Write-Host "  Retry: kubectl apply -f k8s/alertmanager/quarantine-webhook.yaml" -ForegroundColor Yellow
    } else {
        Write-Host "  PrometheusRule + AlertmanagerConfig applied" -ForegroundColor Green
        Write-Host "  Quarantine self-healing is active" -ForegroundColor Green
    }
} else {
    Write-Host "  Webhook handler pod not Running - skipping quarantine-webhook.yaml" -ForegroundColor Yellow
    Write-Host "  enable_self_healing may be false in terraform.tfvars, or image not pushed yet." -ForegroundColor Yellow
    Write-Host "  Apply manually: kubectl apply -f k8s/alertmanager/quarantine-webhook.yaml" -ForegroundColor Yellow
}

# ── Step 7: Deploy test app ───────────────────────────────────────────────────
Write-Host ""
Write-Host "[7/7] Deploying test app to EKS..." -ForegroundColor Green
Set-Location "$RepoRoot\test-project"

guardops deploy --env prod --skip-sonarqube
if ($LASTEXITCODE -ne 0) {
    Write-Host "  guardops deploy failed - check output above" -ForegroundColor Yellow
    Write-Host "  Retry: guardops deploy --env prod --skip-sonarqube" -ForegroundColor Yellow
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "  Morning startup complete!            " -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Cluster  : $ClusterName" -ForegroundColor White
Write-Host ""
Write-Host "  Open these port-forwards in separate terminals:" -ForegroundColor Yellow
Write-Host "    kubectl port-forward svc/kube-prometheus-stack-grafana 3000:80 -n monitoring"
Write-Host "    kubectl port-forward svc/loki 3100:3100 -n monitoring"
Write-Host "    kubectl port-forward svc/kube-prometheus-stack-prometheus 9090:9090 -n monitoring"
Write-Host "    kubectl port-forward svc/kube-prometheus-stack-alertmanager 9093:9093 -n monitoring"
Write-Host "    kubectl port-forward svc/guardops-alertmanager-webhook 9095:9095 -n monitoring"
Write-Host ""
Write-Host "  Quick checks:" -ForegroundColor Yellow
Write-Host "    guardops status"
Write-Host "    guardops runtime-status"
Write-Host "    guardops quarantine-status -n monitoring"
Write-Host "    kubectl get pods -n monitoring"
Write-Host ""
Write-Host "  URLs:" -ForegroundColor Cyan
Write-Host "    Grafana     : http://localhost:3000  (admin / guardops-grafana-2024)"
Write-Host "    Prometheus  : http://localhost:9090"
Write-Host "    Alertmanager: http://localhost:9093"
Write-Host "    Webhook     : http://localhost:9095/healthz"
Write-Host ""
Write-Host "  STOP BILLING tonight:" -ForegroundColor Red
Write-Host "    .\scripts\night-shutdown.ps1" -ForegroundColor Red
Write-Host ""