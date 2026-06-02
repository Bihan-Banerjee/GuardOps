<#
.SYNOPSIS
  Build, push, and deploy the GuardOps web dashboard (Phase 13).

.DESCRIPTION
  Mirrors scripts/setup-runtime-security.ps1 / setup-admission-control.ps1:
    1. Builds Dockerfile.dashboard and pushes it to ECR as :dashboard-latest.
    2. Renders k8s/dashboard manifests (substituting the ECR image, S3 bucket,
       and region placeholders) and applies them.
    3. Creates the basic-auth Secret from the supplied credentials.

  Findings are read from the S3 export bridge — publish them with:
    guardops db export --to-s3

  Prerequisites: a running EKS cluster (morning-start.ps1), the prod ClusterIssuer
  (k8s/tls/clusterissuer-letsencrypt-prod.yaml) applied, and the app.guardops.live
  Route53 record (created by the dns-tls module once alb_dns_name is set).

  Run from the repo root: .\scripts\setup-dashboard.ps1 -S3Bucket <bucket> -DashboardPassword <pw>

.PARAMETER S3Bucket
  S3 bucket holding the metadata export (e.g. the guardops-reports bucket).

.PARAMETER SkipBuild
  Skip the docker build/push and only (re)apply the Kubernetes manifests.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $S3Bucket,
    [string] $AccountId = "236796665744",
    [string] $Region = "ap-south-1",
    [string] $DashboardUser = "admin",
    [string] $DashboardPassword = "",
    [switch] $SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$registry = "$AccountId.dkr.ecr.$Region.amazonaws.com"
$ecrRepo  = "$registry/guardops-app"
$image    = "${ecrRepo}:dashboard-latest"

Write-Host "== GuardOps Dashboard setup ==" -ForegroundColor Cyan
Write-Host "  Image:   $image"
Write-Host "  Bucket:  $S3Bucket"
Write-Host "  Region:  $Region"

# ── 1. Build + push the dashboard image ───────────────────────────────────────
if (-not $SkipBuild) {
    Write-Host "`n[1/3] Building and pushing $image ..." -ForegroundColor Cyan
    $pw = aws ecr get-login-password --region $Region
    $pw | docker login --username AWS --password-stdin $registry
    docker build -t $image -f Dockerfile.dashboard .
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
    docker push $image
    if ($LASTEXITCODE -ne 0) { throw "docker push failed" }
} else {
    Write-Host "`n[1/3] Skipping image build (-SkipBuild)." -ForegroundColor Yellow
}

# ── 2. Render + apply manifests ────────────────────────────────────────────────
Write-Host "`n[2/3] Applying Kubernetes manifests ..." -ForegroundColor Cyan

function Apply-Rendered([string] $path) {
    $text = Get-Content $path -Raw
    $text = $text.Replace("__ECR_IMAGE__", $image)
    $text = $text.Replace("__S3_BUCKET__", $S3Bucket)
    $text = $text.Replace("__AWS_REGION__", $Region)
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $text -Encoding utf8
    kubectl apply -f $tmp
    Remove-Item $tmp -Force
}

Apply-Rendered "k8s/dashboard/configmap.yaml"
Apply-Rendered "k8s/dashboard/deployment.yaml"
kubectl apply -f "k8s/dashboard/service.yaml"
kubectl apply -f "k8s/dashboard/ingress.yaml"

# ── 3. Basic-auth Secret ───────────────────────────────────────────────────────
Write-Host "`n[3/3] Creating the dashboard auth Secret ..." -ForegroundColor Cyan
if (-not $DashboardPassword) {
    Write-Host "  No -DashboardPassword supplied; leaving any existing Secret in place." -ForegroundColor Yellow
    Write-Host "  Create it manually (see k8s/dashboard/secret.example.yaml)." -ForegroundColor Yellow
} else {
    # Idempotent upsert via dry-run | apply.
    $secretYaml = kubectl create secret generic guardops-dashboard-secret `
        --namespace default `
        --from-literal=GUARDOPS_DASHBOARD_USER=$DashboardUser `
        --from-literal=GUARDOPS_DASHBOARD_PASSWORD=$DashboardPassword `
        --dry-run=client -o yaml
    $secretYaml | kubectl apply -f -
    kubectl rollout restart deployment/guardops-dashboard -n default
}

Write-Host "`nDone. Dashboard will be available at https://app.guardops.live once:" -ForegroundColor Green
Write-Host "  - the ALB Ingress gets an ADDRESS (kubectl get ingress guardops-dashboard -n default)"
Write-Host "  - cert-manager issues guardops-dashboard-tls (kubectl get certificate -n default)"
Write-Host "  - app.guardops.live resolves (Route53 alias from the dns-tls module)"
Write-Host "`nPublish findings for the dashboard with:  guardops db export --to-s3"
