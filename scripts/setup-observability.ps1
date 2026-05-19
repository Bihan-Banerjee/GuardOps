# scripts/setup-observability.ps1
#
# Phase 5 — Install kube-prometheus-stack on EKS and wire up Grafana access.
#
# Prerequisites:
#   - EKS cluster running (run morning-start.ps1 first)
#   - kubectl configured for EKS (aws eks update-kubeconfig ...)
#   - Helm 3 installed
#   - terraform apply with Phase 5 eks/main.tf (EBS CSI enabled)
#
# Run this ONCE after cluster creation (or after terraform destroy + reapply).
# It is idempotent — safe to run multiple times.

$ErrorActionPreference = "Stop"
$CHART_PATH = "k8s/helm/guardops-app"
$MONITORING_NS = "monitoring"
$APP_NS = "default"

Write-Host ""
Write-Host "=== Phase 5: GuardOps Observability Setup ===" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Add Prometheus community Helm repo ────────────────────────────────
Write-Host "[1/6] Adding prometheus-community Helm repo..." -ForegroundColor Yellow
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>$null
helm repo update
Write-Host "      Done." -ForegroundColor Green

# ── Step 2: Install kube-prometheus-stack ─────────────────────────────────────
# Using `helm install` (not upgrade --install) to ensure our values are always
# applied fresh. Upgrade over an existing release can silently preserve old values.
Write-Host ""
Write-Host "[2/6] Installing kube-prometheus-stack (this takes 3-5 minutes)..." -ForegroundColor Yellow
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack `
    --namespace $MONITORING_NS `
    --create-namespace `
    --values k8s/observability/prometheus-values.yaml `
    --timeout 10m `
    --wait

Write-Host "      kube-prometheus-stack installed." -ForegroundColor Green

# ── Step 3: Wait for Prometheus and Grafana pods ──────────────────────────────
Write-Host ""
Write-Host "[3/6] Waiting for monitoring pods to be ready..." -ForegroundColor Yellow

kubectl rollout status deployment/kube-prometheus-stack-grafana `
    -n $MONITORING_NS --timeout=180s

kubectl rollout status statefulset/prometheus-kube-prometheus-stack-prometheus `
    -n $MONITORING_NS --timeout=180s

Write-Host "      All monitoring pods ready." -ForegroundColor Green

# ── Step 4: Deploy/upgrade guardops-app with monitoring enabled ───────────────
Write-Host ""
Write-Host "[4/6] Upgrading test-app with monitoring.enabled=true..." -ForegroundColor Yellow

# Get the current image from the running deployment
# Release name is "test-app", deployment name is "test-app"
# Use try/catch because $ErrorActionPreference = "Stop" turns kubectl's
# stderr (NotFound) into a terminating error before the assignment completes.
$CURRENT_IMAGE = $null
try {
    $CURRENT_IMAGE = & kubectl get deployment test-app -n $APP_NS `
        -o jsonpath='{.spec.template.spec.containers[0].image}' 2>$null
} catch {
    $CURRENT_IMAGE = $null
}

if (-not $CURRENT_IMAGE) {
    Write-Host "      WARNING: test-app not yet deployed. Run 'guardops deploy --env prod' first," -ForegroundColor Red
    Write-Host "      then re-run this script from step 4 onwards." -ForegroundColor Red
} else {
    # Split image into repo and tag on the LAST colon (handles ECR URLs with ports)
    $LAST_COLON = $CURRENT_IMAGE.LastIndexOf(":")
    $IMAGE_REPO = $CURRENT_IMAGE.Substring(0, $LAST_COLON)
    $IMAGE_TAG  = $CURRENT_IMAGE.Substring($LAST_COLON + 1)

    Write-Host "      Current image: ${IMAGE_REPO}:${IMAGE_TAG}" -ForegroundColor DarkGray

    helm upgrade test-app $CHART_PATH `
        --namespace $APP_NS `
        --set image.repository="$IMAGE_REPO" `
        --set image.tag="$IMAGE_TAG" `
        --set image.pullPolicy=Always `
        --set replicaCount=2 `
        --set monitoring.enabled=true `
        -f "$CHART_PATH/values-prod.yaml" `
        --wait --timeout 3m

    Write-Host "      test-app upgraded with ServiceMonitor enabled." -ForegroundColor Green
}

# ── Step 5: Verify ServiceMonitor is picked up ────────────────────────────────
Write-Host ""
Write-Host "[5/6] Checking ServiceMonitor..." -ForegroundColor Yellow
kubectl get servicemonitor -n $APP_NS
Write-Host ""
Write-Host "      If 'test-app-guardops-app' appears above, Prometheus will scrape it within 30s." -ForegroundColor Green

# ── Step 6: Access instructions ───────────────────────────────────────────────
Write-Host ""
Write-Host "[6/6] Access instructions:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Grafana (dashboards):" -ForegroundColor Cyan
Write-Host "    kubectl port-forward svc/kube-prometheus-stack-grafana 3000:80 -n $MONITORING_NS"
Write-Host "    Then open: http://localhost:3000"
Write-Host "    Username: admin"
Write-Host "    Password: guardops-grafana-2024  (change this!)"
Write-Host ""
Write-Host "  Prometheus (raw queries):" -ForegroundColor Cyan
Write-Host "    kubectl port-forward svc/kube-prometheus-stack-prometheus 9090:9090 -n $MONITORING_NS"
Write-Host "    Then open: http://localhost:9090"
Write-Host ""
Write-Host "  Useful PromQL queries to try in Grafana:" -ForegroundColor Cyan
Write-Host "    rate(guardops_requests_total[5m])          # request rate per path"
Write-Host "    guardops_request_duration_ms               # last request latency"
Write-Host "    guardops_app_info                          # build metadata"
Write-Host "    container_memory_usage_bytes{namespace='default'}  # pod memory"
Write-Host ""
Write-Host "=== Setup complete! ===" -ForegroundColor Green

# ── Optional: start port-forward to Grafana immediately ──────────────────────
$answer = Read-Host "Open Grafana port-forward now? (y/n)"
if ($answer -eq "y") {
    Write-Host "Starting port-forward to Grafana on http://localhost:3000 ..." -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
    kubectl port-forward svc/kube-prometheus-stack-grafana 3000:80 -n $MONITORING_NS
}