# scripts/morning-start.ps1
#
# Run this when you sit down to work on GuardOps.
# Provisions VPC + IAM + EKS (~15 minutes).
# Cost starts immediately — remember to run night-shutdown.ps1 when done.
#
# Usage: .\scripts\morning-start.ps1

$ErrorActionPreference = "Stop"
$Region = "ap-south-1"

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  GuardOps — Morning Startup" -ForegroundColor Cyan
Write-Host "  Estimated cost: ~`$4.50/day" -ForegroundColor Yellow
Write-Host "  Remember to run night-shutdown.ps1!" -ForegroundColor Yellow
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Terraform apply ───────────────────────────────────────────────────
Write-Host "[1/3] Running terraform apply..." -ForegroundColor Green
Set-Location "D:\EXTRA\GuardOps\infra\terraform"

terraform apply -auto-approve
if ($LASTEXITCODE -ne 0) {
    Write-Host "terraform apply failed. Check errors above." -ForegroundColor Red
    exit 1
}

# ── Step 2: Get cluster name from outputs ─────────────────────────────────────
Write-Host ""
Write-Host "[2/3] Configuring kubectl for EKS..." -ForegroundColor Green

$ClusterName = terraform output -raw eks_cluster_name
if (-not $ClusterName) {
    Write-Host "Could not get cluster name from terraform output." -ForegroundColor Red
    exit 1
}

aws eks update-kubeconfig --region $Region --name $ClusterName
if ($LASTEXITCODE -ne 0) {
    Write-Host "kubectl config update failed." -ForegroundColor Red
    exit 1
}

# ── Step 3: Verify cluster is ready ──────────────────────────────────────────
Write-Host ""
Write-Host "[3/3] Verifying cluster node is Ready..." -ForegroundColor Green

# Wait up to 5 minutes for the node to become Ready
$MaxAttempts = 30
$Attempt = 0
do {
    $Attempt++
    Start-Sleep -Seconds 10
    $NodeStatus = kubectl get nodes --no-headers 2>$null | Select-String "Ready"
    Write-Host "  Attempt $Attempt/$MaxAttempts — waiting for node to be Ready..."
} while (-not $NodeStatus -and $Attempt -lt $MaxAttempts)

if (-not $NodeStatus) {
    Write-Host "Node not ready after 5 minutes. Check AWS console." -ForegroundColor Yellow
} else {
    Write-Host ""
    kubectl get nodes
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "  Cluster ready: $ClusterName" -ForegroundColor Green
Write-Host ""
Write-Host "  Deploy your app:" -ForegroundColor White
Write-Host "    cd D:\EXTRA\GuardOps\test-project" -ForegroundColor White
Write-Host "    guardops deploy --env prod --skip-sonarqube" -ForegroundColor White
Write-Host ""
Write-Host "  STOP BILLING tonight:" -ForegroundColor Yellow
Write-Host "    .\scripts\night-shutdown.ps1" -ForegroundColor Yellow
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
