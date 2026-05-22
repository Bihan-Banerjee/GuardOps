# scripts/morning-start.ps1
#
# GuardOps Phase 7 - Morning Startup
#
# Provisions EKS, installs the full observability + runtime security stack,
# deploys the test app, and opens port-forwards ready to work.
#
# Run time: ~25-30 min total (terraform ~12 min, stacks ~10 min, deploy ~3 min)
# Cost: ~$5.28/day while running. Run night-shutdown.ps1 every evening.
#
# Usage: .\scripts\morning-start.ps1

$ErrorActionPreference = "Stop"
$Region      = "ap-south-1"
$RepoRoot    = "D:\EXTRA\GuardOps"
$TerraformDir = "$RepoRoot\infra\terraform"

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  GuardOps Phase 7 - Morning Startup  " -ForegroundColor Cyan
Write-Host "  Est. cost  : ~`$5.28/day            " -ForegroundColor Yellow
Write-Host "  Est. time  : ~25-30 min             " -ForegroundColor Yellow
Write-Host "  Run night-shutdown.ps1 when done!   " -ForegroundColor Red
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Terraform apply ───────────────────────────────────────────────────
Write-Host "[1/6] Running terraform apply..." -ForegroundColor Green
Set-Location $TerraformDir

terraform apply -auto-approve
if ($LASTEXITCODE -ne 0) {
    Write-Error "terraform apply failed. Check errors above."
    exit 1
}

# ── Step 2: Configure kubectl ─────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/6] Configuring kubectl for EKS..." -ForegroundColor Green

$ClusterName = terraform output -raw eks_cluster_name 2>$null
if (-not $ClusterName) {
    Write-Error "Could not get cluster name from terraform output."
    exit 1
}

aws eks update-kubeconfig --region $Region --name $ClusterName
if ($LASTEXITCODE -ne 0) {
    Write-Error "kubectl config update failed."
    exit 1
}

Write-Host "  Context set for: $ClusterName" -ForegroundColor Green

# ── Step 3: Wait for node Ready ───────────────────────────────────────────────
Write-Host ""
Write-Host "[3/6] Waiting for EKS node to become Ready (up to 5 min)..." -ForegroundColor Green

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
    Write-Host "  Node not ready after 5 min - continuing anyway (may need more time)" -ForegroundColor Yellow
} else {
    kubectl get nodes
    Write-Host "  Node is Ready" -ForegroundColor Green
}

# ── Step 4: Install observability stack ───────────────────────────────────────
Write-Host ""
Write-Host "[4/6] Installing observability stack (Prometheus + Grafana)..." -ForegroundColor Green
Set-Location $RepoRoot

.\scripts\setup-observability.ps1
if ($LASTEXITCODE -ne 0) {
    Write-Error "setup-observability.ps1 failed."
    exit 1
}

# ── Step 5: Install runtime security stack ────────────────────────────────────
Write-Host ""
Write-Host "[5/6] Installing runtime security stack (Loki + Promtail + Falco simulator)..." -ForegroundColor Green

.\scripts\setup-runtime-security.ps1
if ($LASTEXITCODE -ne 0) {
    Write-Error "setup-runtime-security.ps1 failed."
    exit 1
}

# Apply Falco simulator if not already present
$SimulatorExists = kubectl get cronjob falco-simulator -n monitoring --ignore-not-found 2>$null
if (-not $SimulatorExists) {
    Write-Host "  Applying Falco simulator CronJob..." -ForegroundColor Gray
    kubectl apply -f "$RepoRoot\k8s\falco\falco-simulator-configmap.yaml"
    kubectl apply -f "$RepoRoot\k8s\falco\falco-simulator-cronjob.yaml"
    Write-Host "  Falco simulator deployed (fires every 3 min)" -ForegroundColor Green
} else {
    Write-Host "  Falco simulator already present" -ForegroundColor Gray
}

# ── Step 6: Deploy test app ───────────────────────────────────────────────────
Write-Host ""
Write-Host "[6/6] Deploying test app to EKS..." -ForegroundColor Green
Set-Location "$RepoRoot\test-project"

guardops deploy --env prod --skip-sonarqube
if ($LASTEXITCODE -ne 0) {
    Write-Host "  guardops deploy failed - check output above" -ForegroundColor Yellow
    Write-Host "  You can retry manually: guardops deploy --env prod --skip-sonarqube" -ForegroundColor Yellow
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
Write-Host ""
Write-Host "  Quick checks:" -ForegroundColor Yellow
Write-Host "    guardops status"
Write-Host "    guardops runtime-status"
Write-Host "    kubectl get pods -n monitoring"
Write-Host ""
Write-Host "  Grafana  : http://localhost:3000  (admin / guardops-grafana-2024)" -ForegroundColor Cyan
Write-Host "  Prometheus: http://localhost:9090" -ForegroundColor Cyan
Write-Host ""
Write-Host "  STOP BILLING tonight:" -ForegroundColor Red
Write-Host "    .\scripts\night-shutdown.ps1" -ForegroundColor Red
Write-Host ""