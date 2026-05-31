# scripts/setup-runtime-security.ps1
#
# GuardOps Phase 7 - Runtime Security Setup
#
# Run AFTER:
#   1. terraform apply
#   2. aws eks update-kubeconfig --region ap-south-1 --name guardops-prod-cluster
#   3. .\scripts\setup-observability.ps1  (creates the monitoring namespace)

$ErrorActionPreference = "Stop"

$NAMESPACE        = "monitoring"
$FALCO_VERSION    = "4.3.0"
$LOKI_VERSION     = "6.6.2"
$PROMTAIL_VERSION = "6.15.5"

$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  GuardOps Phase 7 - Runtime Security Setup      " -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# -- Prerequisites ------------------------------------------------------------

Write-Host "[ 0/5 ] Checking prerequisites..." -ForegroundColor Yellow

foreach ($tool in @("helm", "kubectl")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "$tool not found in PATH. Aborting."
        exit 1
    }
}

$nsExists = kubectl get namespace $NAMESPACE --ignore-not-found 2>$null
if (-not $nsExists) {
    Write-Error "Namespace '$NAMESPACE' not found. Run .\scripts\setup-observability.ps1 first."
    exit 1
}

Write-Host "  Prerequisites OK" -ForegroundColor Green

# -- Helm repos ---------------------------------------------------------------

Write-Host ""
Write-Host "[ 1/5 ] Adding Helm repositories..." -ForegroundColor Yellow

helm repo add grafana       https://grafana.github.io/helm-charts   2>$null
helm repo add falcosecurity  https://falcosecurity.github.io/charts  2>$null

# Redirect stdout to suppress the full help text helm prints on repo update
helm repo update 2>&1 | Where-Object { $_ -match "Successfully|Update|Hang|skip" }

Write-Host "  Helm repos updated" -ForegroundColor Green

# -- Loki ---------------------------------------------------------------------

Write-Host ""
Write-Host "[ 2/5 ] Installing Loki $LOKI_VERSION..." -ForegroundColor Yellow

$LokiValues = Join-Path $RepoRoot "k8s\observability\loki-values.yaml"
if (-not (Test-Path $LokiValues)) {
    Write-Error "Not found: $LokiValues"
    exit 1
}

helm upgrade --install loki grafana/loki `
    --version $LOKI_VERSION `
    --namespace $NAMESPACE `
    --values $LokiValues `
    --timeout 5m `
    --wait

if ($LASTEXITCODE -ne 0) {
    Write-Error "Loki install failed (exit $LASTEXITCODE). Check: helm status loki -n $NAMESPACE"
    exit 1
}

Write-Host "  Loki installed" -ForegroundColor Green

# -- Promtail -----------------------------------------------------------------

Write-Host ""
Write-Host "[ 3/5 ] Installing Promtail $PROMTAIL_VERSION..." -ForegroundColor Yellow

$PromtailValues = Join-Path $RepoRoot "k8s\observability\promtail-values.yaml"
if (-not (Test-Path $PromtailValues)) {
    Write-Error "Not found: $PromtailValues"
    exit 1
}

helm upgrade --install promtail grafana/promtail `
    --version $PROMTAIL_VERSION `
    --namespace $NAMESPACE `
    --values $PromtailValues `
    --timeout 5m `
    --wait

if ($LASTEXITCODE -ne 0) {
    Write-Error "Promtail install failed (exit $LASTEXITCODE)."
    exit 1
}

Write-Host "  Promtail installed" -ForegroundColor Green

# -- Falco --------------------------------------------------------------------

Write-Host ""
Write-Host "[ 4/5 ] Installing Falco $FALCO_VERSION..." -ForegroundColor Yellow

$FalcoRulesPath = Join-Path $RepoRoot "k8s\falco\custom-rules.yaml"
if (-not (Test-Path $FalcoRulesPath)) {
    Write-Error "Not found: $FalcoRulesPath"
    exit 1
}

# Build temp values file — avoids multiline YAML escaping on the CLI
$TempValuesFile = [System.IO.Path]::GetTempFileName() + ".yaml"

$falcoHeader = @"
driver:
  kind: ebpf

falco:
  json_output: true
  json_include_output_property: true
  log_level: warning
  priority: warning
  metrics:
    enabled: true
    interval: 15s

serviceMonitor:
  create: true

resources:
  requests:
    memory: 64Mi
    cpu: 50m
  limits:
    memory: 256Mi
    cpu: 200m

customRules:
  guardops-rules.yaml: |
"@

$ruleLines = (Get-Content $FalcoRulesPath) | ForEach-Object { "    $_" }
$falcoFull  = $falcoHeader + "`n" + ($ruleLines -join "`n")

Set-Content -Path $TempValuesFile -Value $falcoFull -Encoding UTF8

try {
    helm upgrade --install falco falcosecurity/falco `
        --version $FALCO_VERSION `
        --namespace $NAMESPACE `
        --values $TempValuesFile `
        --timeout 10m `
        --wait

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Falco install failed (exit $LASTEXITCODE)."
        exit 1
    }
} finally {
    Remove-Item $TempValuesFile -Force -ErrorAction SilentlyContinue
}

Write-Host "  Falco installed" -ForegroundColor Green

# -- Verify pods --------------------------------------------------------------

Write-Host ""
Write-Host "[ 5/5 ] Verifying all Phase 7 pods are Ready..." -ForegroundColor Yellow

Start-Sleep -Seconds 15

Write-Host "  Waiting for Loki..." -ForegroundColor Gray
kubectl wait pod -l app.kubernetes.io/name=loki `
    --for=condition=Ready `
    --namespace $NAMESPACE `
    --timeout=180s

if ($LASTEXITCODE -ne 0) {
    Write-Host "  Loki pod not Ready yet - checking status:" -ForegroundColor Yellow
    kubectl get pods -n $NAMESPACE -l app.kubernetes.io/name=loki
    kubectl describe pod -n $NAMESPACE -l app.kubernetes.io/name=loki | Select-String -Pattern "Warning|Error|Reason" | Select-Object -First 10
}

Write-Host "  Waiting for Promtail..." -ForegroundColor Gray
kubectl wait pod -l app.kubernetes.io/name=promtail `
    --for=condition=Ready `
    --namespace $NAMESPACE `
    --timeout=120s

if ($LASTEXITCODE -ne 0) {
    Write-Host "  Promtail pod not Ready yet - checking status:" -ForegroundColor Yellow
    kubectl get pods -n $NAMESPACE -l app.kubernetes.io/name=promtail
}

Write-Host "  Waiting for Falco..." -ForegroundColor Gray
kubectl wait pod -l app.kubernetes.io/name=falco `
    --for=condition=Ready `
    --namespace $NAMESPACE `
    --timeout=300s

if ($LASTEXITCODE -ne 0) {
    Write-Host "  Falco pod not Ready yet - checking status:" -ForegroundColor Yellow
    kubectl get pods -n $NAMESPACE -l app.kubernetes.io/name=falco
    kubectl describe pod -n $NAMESPACE -l app.kubernetes.io/name=falco | Select-String -Pattern "Warning|Error|Reason" | Select-Object -First 10
}

Write-Host ""
Write-Host "  Pod summary:" -ForegroundColor Gray
kubectl get pods -n $NAMESPACE | Select-String -Pattern "loki|promtail|falco"

# -- Patch .guardops.yaml -----------------------------------------------------

Write-Host ""
Write-Host "  Patching test-project\.guardops.yaml..." -ForegroundColor Cyan

$LokiPortForwardUrl = "http://localhost:3100"
$ConfigPath = Join-Path $RepoRoot "test-project\.guardops.yaml"

if (Test-Path $ConfigPath) {
    $cfg = Get-Content $ConfigPath -Raw

    if ($cfg -match "loki_url: ''") {
        $cfg = $cfg -replace "loki_url: ''", "loki_url: '$LokiPortForwardUrl'"
        Set-Content $ConfigPath $cfg -Encoding UTF8
        Write-Host "    loki_url -> $LokiPortForwardUrl" -ForegroundColor Green
    } else {
        Write-Host "    loki_url already set - skipping" -ForegroundColor Gray
    }

    # Scope the replace to the runtime_security block only. A bare global
    # `-replace "enabled: false"` would also flip self_healing.enabled (which
    # defaults to false and expects a webhook this script never deploys).
    # The pattern matches "runtime_security:" then its first indented
    # "enabled:" key, tolerating comment/blank lines in between.
    $rtPattern = "(?m)^(runtime_security:[^\S\r\n]*\r?\n(?:[^\S\r\n]+.*\r?\n)*?[^\S\r\n]+enabled:[^\S\r\n]*)false"
    if ($cfg -match $rtPattern) {
        $cfg = $cfg -replace $rtPattern, '${1}true'
        Set-Content $ConfigPath $cfg -Encoding UTF8
        Write-Host "    runtime_security.enabled -> true" -ForegroundColor Green
    } else {
        Write-Host "    runtime_security already enabled - skipping" -ForegroundColor Gray
    }
} else {
    Write-Host "    .guardops.yaml not found at $ConfigPath - update manually" -ForegroundColor Yellow
}

# -- Summary ------------------------------------------------------------------

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Phase 7 runtime security stack installed        " -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next - open a NEW terminal and run:"
Write-Host "    kubectl port-forward svc/loki 3100:3100 -n $NAMESPACE" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Then test:"
Write-Host "    guardops runtime-status" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Trigger a CRITICAL test alert:"
Write-Host "    kubectl exec -it deploy/test-app-guardops-app -- sh" -ForegroundColor Yellow
Write-Host "    guardops runtime-status --since 5m" -ForegroundColor Yellow
Write-Host ""