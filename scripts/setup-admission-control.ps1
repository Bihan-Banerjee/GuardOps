# scripts/setup-admission-control.ps1
#
# GuardOps Phase 11 - Admission Control Setup (Kyverno)
#
# Installs/verifies Kyverno and applies the GuardOps ClusterPolicies:
#   - keyless image-signature verification (cosign / Sigstore)
#   - signed CycloneDX SBOM attestation requirement
#   - pod-security + image-hygiene best-practice pack
#
# Kyverno is normally installed by Terraform (module.kyverno, enable_kyverno=true)
# and the policies are applied by morning-start.ps1. Use this script to (re)apply
# the policies on demand, or to flip Audit -> Enforce with -Enforce.
#
# Run AFTER:
#   1. terraform apply  (enable_kyverno = true)  -- or this script installs Kyverno
#      WITHOUT the IRSA role; for private-ECR signature reads prefer Terraform.
#   2. aws eks update-kubeconfig --region ap-south-1 --name guardops-prod-cluster
#
# Usage:
#   .\scripts\setup-admission-control.ps1            # apply policies in Audit
#   .\scripts\setup-admission-control.ps1 -Enforce   # apply policies in Enforce (block)

param(
    [switch]$Enforce,
    [string]$GithubRepo = "Bihan-Banerjee/GuardOps"
)

$ErrorActionPreference = "Stop"

$NAMESPACE             = "kyverno"
$KYVERNO_CHART_VERSION = "3.4.6"

$RepoRoot  = Split-Path -Parent $PSScriptRoot
$PolicyDir = Join-Path $RepoRoot "k8s\kyverno"

$PolicyAction = if ($Enforce) { "Enforce" } else { "Audit" }
$CiIssuer     = "https://token.actions.githubusercontent.com"
$CiSubject    = "https://github.com/$GithubRepo/.github/workflows/ci.yaml@refs/heads/*"
# Kyverno forbids mutateDigest in Audit, so pin the digest only under Enforce.
$DigestPin    = if ($Enforce) { "true" } else { "false" }

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  GuardOps Phase 11 - Admission Control (Kyverno) " -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Policy action: $PolicyAction" -ForegroundColor $(if ($Enforce) { "Red" } else { "Yellow" })
Write-Host ""

# -- Prerequisites ------------------------------------------------------------

Write-Host "[ 0/4 ] Checking prerequisites..." -ForegroundColor Yellow

foreach ($tool in @("helm", "kubectl")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "$tool not found in PATH. Aborting."
        exit 1
    }
}

kubectl cluster-info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "kubectl cannot reach the cluster. Run: aws eks update-kubeconfig --region ap-south-1 --name guardops-prod-cluster"
    exit 1
}

if (-not (Test-Path $PolicyDir)) {
    Write-Error "Policy directory not found: $PolicyDir"
    exit 1
}

Write-Host "  Prerequisites OK" -ForegroundColor Green

# -- Ensure Kyverno is installed ----------------------------------------------

Write-Host ""
Write-Host "[ 1/4 ] Ensuring Kyverno is installed..." -ForegroundColor Yellow

$kyvernoInstalled = $false
try {
    helm status kyverno -n $NAMESPACE 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $kyvernoInstalled = $true }
} catch { }

if ($kyvernoInstalled) {
    Write-Host "  Kyverno already installed (managed by Terraform) - skipping install" -ForegroundColor Green
} else {
    Write-Host "  Kyverno not found - installing chart $KYVERNO_CHART_VERSION" -ForegroundColor Gray
    Write-Host "  NOTE: installing WITHOUT the IRSA role. Private-ECR signature reads" -ForegroundColor Yellow
    Write-Host "        need 'terraform apply' with enable_kyverno=true (or the node role)." -ForegroundColor Yellow
    helm repo add kyverno https://kyverno.github.io/kyverno/ 2>$null
    helm repo update kyverno 2>&1 | Where-Object { $_ -match "Successfully|Update|Hang|skip" }
    # Disable the blocking policyReportsCleanup hook (pointless on a nightly
    # cluster). Chart 3.4.x pulls cleanup images from reg.kyverno.io/alpine, so the
    # bitnami/kubectl:1.28.5 purge that broke 3.2.x no longer applies.
    helm upgrade --install kyverno kyverno/kyverno `
        --version $KYVERNO_CHART_VERSION `
        --namespace $NAMESPACE `
        --create-namespace `
        --set admissionController.replicas=1 `
        --set policyReportsCleanup.enabled=false `
        --timeout 10m `
        --wait
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Kyverno install failed (exit $LASTEXITCODE). Check: helm status kyverno -n $NAMESPACE"
        exit 1
    }
    Write-Host "  Kyverno installed" -ForegroundColor Green
}

# -- Wait for the admission controller ----------------------------------------

Write-Host ""
Write-Host "[ 2/4 ] Waiting for the Kyverno admission controller..." -ForegroundColor Yellow

kubectl wait pod -l app.kubernetes.io/component=admission-controller `
    --for=condition=Ready `
    --namespace $NAMESPACE `
    --timeout=300s

if ($LASTEXITCODE -ne 0) {
    Write-Host "  Admission controller not Ready - current pods:" -ForegroundColor Yellow
    kubectl get pods -n $NAMESPACE
    Write-Error "Kyverno admission controller did not become Ready."
    exit 1
}

Write-Host "  Kyverno admission controller Ready" -ForegroundColor Green

# -- Apply ClusterPolicies ----------------------------------------------------

Write-Host ""
Write-Host "[ 3/4 ] Applying GuardOps ClusterPolicies (action=$PolicyAction)..." -ForegroundColor Yellow

# networkpolicy* files are reference-only (apply manually when hardening egress).
$policyFiles = Get-ChildItem "$PolicyDir\*.yaml" | Where-Object { $_.Name -notlike "networkpolicy*" }

foreach ($f in $policyFiles) {
    $body = (Get-Content $f.FullName -Raw).
        Replace("__CI_ISSUER__",     $CiIssuer).
        Replace("__CI_SUBJECT__",    $CiSubject).
        Replace("__POLICY_ACTION__", $PolicyAction).
        Replace("__DIGEST_PIN__",    $DigestPin)

    # In Enforce mode, also make the signature/SBOM webhooks hard-fail so a
    # Sigstore/ECR outage blocks admission instead of silently allowing unsigned
    # images. (In Audit we keep failurePolicy: Ignore so nothing is ever blocked.)
    if ($Enforce) {
        $body = $body.Replace("failurePolicy: Ignore", "failurePolicy: Fail")
    }

    Write-Host "    apply $($f.Name)" -ForegroundColor Gray
    $body | kubectl apply -f -
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to apply $($f.Name)"
        exit 1
    }
}

Write-Host "  ClusterPolicies applied" -ForegroundColor Green

# -- Record state in test-project\.guardops.yaml ------------------------------

Write-Host ""
Write-Host "[ 4/4 ] Recording admission-control state in test-project\.guardops.yaml..." -ForegroundColor Yellow

$ConfigPath = Join-Path $RepoRoot "test-project\.guardops.yaml"
if (Test-Path $ConfigPath) {
    $cfg = Get-Content $ConfigPath -Raw
    if ($cfg -notmatch "(?m)^admission_control:") {
        $section  = "`nadmission_control:`n"
        $section += "  enabled: true`n"
        $section += "  engine: kyverno`n"
        $section += "  policy_action: `"$PolicyAction`"`n"
        Add-Content $ConfigPath $section
        Write-Host "    added admission_control section (policy_action=$PolicyAction)" -ForegroundColor Green
    } else {
        # policy_action only appears in the admission_control block, so a line
        # replace is safe.
        $cfg = $cfg -replace '(?m)^(\s*policy_action:\s*).*$', "`${1}`"$PolicyAction`""
        Set-Content $ConfigPath $cfg -Encoding UTF8
        Write-Host "    admission_control.policy_action -> $PolicyAction" -ForegroundColor Green
    }
} else {
    Write-Host "    .guardops.yaml not found at $ConfigPath - skipping" -ForegroundColor Yellow
}

# -- Summary ------------------------------------------------------------------

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Admission control configured ($PolicyAction)" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Inspect policies and reports:"
Write-Host "    kubectl get clusterpolicy" -ForegroundColor Yellow
Write-Host "    kubectl get polr -A" -ForegroundColor Yellow
Write-Host "    kubectl get cpolr" -ForegroundColor Yellow
Write-Host ""
if (-not $Enforce) {
    Write-Host "  Currently in AUDIT (nothing blocked). When the reports look clean, enforce:"
    Write-Host "    .\scripts\setup-admission-control.ps1 -Enforce" -ForegroundColor Yellow
} else {
    Write-Host "  ENFORCE active -- unsigned/non-compliant pods in default/staging are rejected."
    Write-Host "  Roll back to audit with:"
    Write-Host "    .\scripts\setup-admission-control.ps1" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  Negative test (Audit: reported, Enforce: rejected):"
Write-Host "    kubectl run rogue --image=nginx:latest -n staging" -ForegroundColor Yellow
Write-Host ""
