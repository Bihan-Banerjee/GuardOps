# scripts/morning-start.ps1
#
# GuardOps Phase 11 -- Full Morning Startup
#
# Handles every phase from scratch in a single run:
#   Phase 1-3  : Terraform AWS infra (VPC, IAM, EKS, ECR, S3)
#   Phase 4-5  : Observability (Prometheus, Grafana, Loki) + kubectl wiring
#   Phase 6    : GitHub OIDC secret sync
#   Phase 7    : Falco runtime security + simulator
#   Phase 8    : Alertmanager self-healing webhook
#   Phase 9    : App deploy to prod + staging namespaces
#   Phase 10   : OIDC provider, ALB IAM role, subnet tag repair,
#                cert-manager ClusterIssuers, ALB DNS wiring, ArgoCD setup
#   Phase 11   : Kyverno admission control -- apply image-signature +
#                best-practice ClusterPolicies (gated by enable_kyverno)
#
# Key behaviours:
#   - Fully idempotent -- safe to re-run on an already-running cluster
#   - Solves the EKS bootstrap provider problem automatically
#   - Imports existing AWS resources to avoid 409 conflicts
#   - After destroy+recreate: re-associates OIDC provider, updates ALB role
#     trust policy to match the new OIDC URL, repairs subnet cluster tags
#   - Phase 10 steps gated by terraform.tfvars flags
#   - Opens all port-forwards in separate PowerShell windows
#
# Usage:
#   .\scripts\morning-start.ps1
#   .\scripts\morning-start.ps1 -SkipTerraform
#   .\scripts\morning-start.ps1 -SkipDeploy
#   .\scripts\morning-start.ps1 -SkipPortForwards

param(
    [switch]$SkipTerraform,
    [switch]$SkipDeploy,
    [switch]$SkipPortForwards
)

$ErrorActionPreference = "Stop"

# Tracks whether the alertmanager-webhook Docker image is confirmed available in
# ECR.  Set by Ensure-WebhookImage.  Read by Invoke-TerraformApply to decide
# whether to patch wait_for_rollout = false in the alertmanager-webhook module
# before apply -- prevents "Deployment exceeded its progress deadline" when the
# image is absent and the pod stays in ImagePullBackOff.
$script:WebhookImageReady = $false

# Phase 13: dashboard build/deploy state, read by the summary section.
$script:DashboardImageReady = $false
$script:DashboardDeployed   = $false
$script:DashboardUser       = "admin"
$script:DashboardPassword   = ""

# -- Configuration -------------------------------------------------------------
$Region       = "ap-south-1"
# Repo root = parent of the scripts/ directory this file lives in.
$RepoRoot     = Split-Path -Parent $PSScriptRoot
$TerraformDir = "$RepoRoot\infra\terraform"
$ClusterName  = "guardops-prod-cluster"
$TfVarsFile   = "$TerraformDir\terraform.tfvars"
$MainTf       = "$TerraformDir\main.tf"
$GuardopsYaml = "$RepoRoot\.guardops.yaml"

# -- Helper functions ----------------------------------------------------------

function Write-Step {
    param([string]$Num, [string]$Total, [string]$Text)
    Write-Host ""
    Write-Host "  [$Num/$Total] $Text" -ForegroundColor Cyan
    Write-Host "  $('-' * 52)" -ForegroundColor DarkGray
}

function Write-Ok   { param([string]$M) Write-Host "    OK  $M" -ForegroundColor Green }
function Write-Warn { param([string]$M) Write-Host "    >>  $M" -ForegroundColor Yellow }
function Write-Info { param([string]$M) Write-Host "    ..  $M" -ForegroundColor Gray }
function Write-Fail { param([string]$M) Write-Host "    !!  $M" -ForegroundColor Red }

function Test-EKSExists {
    try {
        aws eks describe-cluster --name $ClusterName --region $Region 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Read-TfVar {
    param([string]$VarName)
    if (-not (Test-Path $TfVarsFile)) { return $null }
    foreach ($line in (Get-Content $TfVarsFile)) {
        if ($line -match "^\s*$VarName\s*=\s*(.+)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Set-TfVar {
    param([string]$VarName, [string]$Value)
    if (-not (Test-Path $TfVarsFile)) { New-Item $TfVarsFile -ItemType File | Out-Null }
    $content = Get-Content $TfVarsFile -Raw
    $newLine = "$VarName = `"$Value`""
    if ($content -match "(?m)^\s*$VarName\s*=") {
        $content = $content -replace "(?m)^\s*$VarName\s*=.*$", $newLine
    } else {
        $content = $content.TrimEnd() + "`n$newLine`n"
    }
    Set-Content $TfVarsFile $content
}

function Wait-Pods {
    param(
        [string]$NS,
        [string]$Label,
        [int]$Timeout = 300,
        [string]$Name = "pods"
    )
    Write-Info "Waiting for $Name in $NS (up to ${Timeout}s)..."
    $elapsed = 0
    while ($elapsed -lt $Timeout) {
        $all = $null
        try { $all = kubectl get pods -n $NS -l $Label --no-headers 2>$null } catch { }
        $ok  = $null
        $bad = $null
        if ($all) {
            $ok  = $all | Select-String "\bRunning\b"
            $bad = $all | Where-Object { $_ -notmatch "\bRunning\b" -and $_.Trim() -ne "" }
        }
        if ($ok -and -not $bad) { Write-Ok "$Name are Running"; return }
        Start-Sleep -Seconds 10
        $elapsed += 10
        Write-Info "  ${elapsed}s elapsed..."
    }
    Write-Warn "$Name not Ready after ${Timeout}s -- continuing anyway"
}

function Import-EksOidcProvider {
    # Phase 11: the cluster IRSA OIDC provider is now Terraform-managed
    # (module.eks.aws_iam_openid_connect_provider.eks). If a prior run created it
    # imperatively (the Phase 10 ALB IRSA path below, or eksctl), it exists in AWS
    # but not in Terraform state, so the full apply would fail with
    # EntityAlreadyExists. Import it first. No-op when already in state (the normal
    # cold-start path, where the Phase 1 apply created and recorded it).
    param([string]$AccountId)

    $issuer = ""
    try { $issuer = (aws eks describe-cluster --name $ClusterName --region $Region `
        --query "cluster.identity.oidc.issuer" --output text 2>$null) } catch { }
    if (-not $issuer -or $issuer -eq "None") { return }

    $url = ($issuer -replace "https://", "").Trim()
    $arn = "arn:aws:iam::${AccountId}:oidc-provider/${url}"

    # 2>&1 + try/catch: a not-in-state address makes terraform write to stderr,
    # which PowerShell 5.1 turns into a terminating error under ErrorAction Stop.
    try { terraform state show "module.eks.aws_iam_openid_connect_provider.eks" 2>&1 | Out-Null } catch { }
    if ($LASTEXITCODE -eq 0) { return }   # already managed -- nothing to do

    try { aws iam get-open-id-connect-provider --open-id-connect-provider-arn $arn 2>&1 | Out-Null } catch { }
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Importing pre-existing EKS OIDC provider into Terraform state..."
        try { terraform import "module.eks.aws_iam_openid_connect_provider.eks" $arn 2>&1 | Out-Null } catch { }
        if ($LASTEXITCODE -eq 0) { Write-Ok "EKS OIDC provider imported" }
        else { Write-Warn "EKS OIDC provider import failed -- run it manually if apply errors" }
    }
}

function Import-IfMissing {
    param([string]$Addr, [string]$AwsId, [string]$Desc)
    Write-Info "Checking state for $Desc..."
    
    try {
        terraform state show $Addr 2>&1 | Out-Null
    } catch {
        # Catch and ignore PowerShell's NativeCommandError
    }
    
    if ($LASTEXITCODE -eq 0) {
        Write-Info "$Desc already in state"
        return
    }
    
    Write-Info "Importing $Desc..."
    try {
        terraform import $Addr $AwsId 2>&1 | Out-Null
    } catch {
        # Catch and ignore PowerShell's NativeCommandError
    }
    
    if ($LASTEXITCODE -eq 0) { Write-Ok "Imported $Desc" }
    else                     { Write-Warn "Import of $Desc skipped (may not exist yet)" }
}

function Import-GithubOidc {
    # Phase 6/11: re-attach the GitHub Actions OIDC provider + CI role + inline
    # policy that night-shutdown.ps1 detaches from state before destroy (so CI —
    # build, scan, ECR push, cosign signing — keeps working while the cluster is
    # down). The GitHub OIDC provider is an account-level singleton; recreating it
    # nightly leaves a window where CI fails with "No OpenIDConnect provider
    # found". They still exist in AWS; ARNs/names are stable so AWS_ROLE_ARN is
    # unaffected. Without this the apply would fail with EntityAlreadyExists.
    # Idempotent via Import-IfMissing (skips whatever is already in state).
    param([string]$AccountId)

    Import-IfMissing "module.iam_oidc.aws_iam_openid_connect_provider.github" `
        "arn:aws:iam::${AccountId}:oidc-provider/token.actions.githubusercontent.com" `
        "GitHub OIDC provider (preserved across shutdown)"
    Import-IfMissing "module.iam_oidc.aws_iam_role.github_actions" `
        "guardops-github-actions-role" `
        "GitHub Actions CI role (preserved across shutdown)"
    Import-IfMissing "module.iam_oidc.aws_iam_role_policy.ci_policy" `
        "guardops-github-actions-role:guardops-ci-policy" `
        "GitHub Actions CI policy (preserved across shutdown)"
}

function Ensure-HelmRepos {
    # The Terraform helm provider downloads charts through the shared helm CLI
    # repository cache (HELM_REPOSITORY_CACHE, here under %TEMP%\helm). On a fresh
    # machine or after %TEMP% is cleared, that cache has no *-index.yaml files and
    # the full apply fails with:
    #   "could not download chart: no cached repo found (try 'helm repo update')".
    # Pre-add every repo the terraform modules pull charts from, then update ALL
    # configured repos (also refreshes a stale ingress-nginx index from local k3d).
    if (-not (Get-Command helm -ErrorAction SilentlyContinue)) {
        Write-Warn "helm not found -- skipping repo cache warm-up (charts may fail to download)"
        return
    }
    Write-Info "Warming helm chart repository cache (for the terraform helm provider)..."
    $repos = [ordered]@{
        "eks-charts"           = "https://aws.github.io/eks-charts"          # aws-load-balancer-controller
        "jetstack"             = "https://charts.jetstack.io"                # cert-manager
        "argo"                 = "https://argoproj.github.io/argo-helm"      # argo-cd
        "falcosecurity"        = "https://falcosecurity.github.io/charts"    # falco
        "grafana"              = "https://grafana.github.io/helm-charts"     # loki, promtail
        "kyverno"              = "https://kyverno.github.io/kyverno/"        # kyverno (Phase 11)
        "prometheus-community" = "https://prometheus-community.github.io/helm-charts"
    }
    foreach ($name in $repos.Keys) {
        try { helm repo add $name $repos[$name] 2>&1 | Out-Null } catch { }
    }
    try { helm repo update 2>&1 | Out-Null } catch { }
    Write-Ok "Helm chart repositories cached"
}

function Import-KyvernoRelease {
    # If a 'kyverno' helm release exists in the cluster but is not in Terraform
    # state (a prior interrupted apply, or setup-admission-control.ps1's standalone
    # install), the full apply fails with "cannot re-use a name that is still in
    # use". Adopt it so terraform manages it. Only relevant when enable_kyverno=true.
    if ((Read-TfVar "enable_kyverno") -ne "true") { return }

    # 2>&1 + try/catch: a not-in-state address makes terraform write to stderr,
    # which PowerShell 5.1 turns into a terminating error under ErrorAction Stop.
    try { terraform state show "module.kyverno[0].helm_release.kyverno" 2>&1 | Out-Null } catch { }
    if ($LASTEXITCODE -eq 0) { return }   # already managed

    try { helm status kyverno -n kyverno 2>&1 | Out-Null } catch { }
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Adopting existing 'kyverno' helm release into Terraform state..."
        try { terraform import "module.kyverno[0].helm_release.kyverno" "kyverno/kyverno" 2>&1 | Out-Null } catch { }
        if ($LASTEXITCODE -eq 0) { Write-Ok "Kyverno helm release imported" }
        else { Write-Warn "Kyverno import failed -- if apply still errors, run: helm uninstall kyverno -n kyverno" }
    }
}

function Import-Route53Zone {
    # Re-attach the Route53 hosted zone that night-shutdown.ps1 detaches from
    # Terraform state before destroy (so the registrar nameserver delegation
    # survives the nightly teardown). The zone still exists in AWS; without this,
    # the full apply would create a DUPLICATE zone with new NS, breaking DNS.
    # Looked up by name so the zone ID is never hardcoded. Idempotent via
    # Import-IfMissing (skips if already managed).
    if (-not $domainName) { return }
    $zoneId = $null
    try {
        $zoneId = (aws route53 list-hosted-zones-by-name --dns-name $domainName `
            --query "HostedZones[0].Id" --output text 2>$null)
    } catch { }
    if ($zoneId) { $zoneId = $zoneId.Trim() -replace "/hostedzone/", "" }
    if ($zoneId -and $zoneId -ne "None") {
        Import-IfMissing "module.dns_tls[0].aws_route53_zone.guardops" $zoneId "Route53 hosted zone (preserved across shutdown)"
    } else {
        Write-Info "No existing Route53 zone for $domainName -- apply will create it"
    }
}

function Disable-EKSProviders {
    Write-Info "Stubbing EKS-dependent providers for bootstrap apply..."
    Copy-Item $MainTf "$MainTf.full"
    $lines  = Get-Content $MainTf
    $out    = [System.Collections.Generic.List[string]]::new()
    $skip   = $false
    $depth  = 0
    $pats   = @(
        '^\s*data "aws_eks_cluster" "guardops"',
        '^\s*data "aws_eks_cluster_auth" "guardops"',
        '^\s*provider "helm"',
        '^\s*provider "kubernetes"'
    )
    foreach ($ln in $lines) {
        if (-not $skip) {
            $hit = $false
            foreach ($p in $pats) { if ($ln -match $p) { $hit = $true; break } }
            if ($hit) {
                $skip  = $true
                $depth = 0
                $out.Add("# STUB: $ln")
                $depth += ($ln.ToCharArray() | Where-Object { $_ -eq '{' }).Count
            } else {
                $out.Add($ln)
            }
        } else {
            $out.Add("# STUB: $ln")
            $depth += ($ln.ToCharArray() | Where-Object { $_ -eq '{' }).Count
            $depth -= ($ln.ToCharArray() | Where-Object { $_ -eq '}' }).Count
            if ($depth -le 0 -and $ln -match '^\s*}') {
                $skip  = $false
                $depth = 0
            }
        }
    }
    $out | Set-Content $MainTf
}

function Restore-EKSProviders {
    if (Test-Path "$MainTf.full") {
        Copy-Item "$MainTf.full" $MainTf
        Remove-Item "$MainTf.full"
        Write-Ok "Restored full main.tf"
    }
}

function Ensure-WebhookImage {
    # Build and push the webhook image to ECR when the tag is missing.
    # This happens on every cold start because the nightly destroy does NOT
    # remove ECR images, but if the repo was manually cleared (or this is a
    # completely fresh account) the tag won't be there and the Deployment will
    # never become Ready, timing out after 10 min.
    $acctId    = (aws sts get-caller-identity --query Account --output text 2>$null).Trim()
    $ecrBase   = "$acctId.dkr.ecr.$Region.amazonaws.com"
    $repo      = "guardops-app"
    $tag       = "webhook-latest"
    $fullImage = "$ecrBase/${repo}:${tag}"

    Write-Info "Checking ECR for ${repo}:${tag}..."
    $imageFound = $false
    try {
        aws ecr describe-images --repository-name $repo `
            --image-ids imageTag=$tag --region $Region 2>$null | Out-Null
        $imageFound = ($LASTEXITCODE -eq 0)
    } catch {
        $imageFound = $false
    }

    if ($imageFound) {
        Write-Ok "ECR image ${repo}:${tag} already exists -- skipping build"
        $script:WebhookImageReady = $true
        return
    }

    Write-Info "Image not found in ECR -- need to build and push $fullImage"

    Write-Info "Checking Docker daemon..."
    $dockerOk = $false
    try {
        docker info 2>$null | Out-Null
        $dockerOk = ($LASTEXITCODE -eq 0)
    } catch {
        $dockerOk = $false
    }

    if (-not $dockerOk) {
        Write-Warn "Docker daemon is not running -- cannot build webhook image automatically"
        Write-Host ""
        Write-Host "  To fix, either:" -ForegroundColor Yellow
        Write-Host "    1. Start Docker Desktop, then re-run .\scripts\morning-start.ps1" -ForegroundColor Yellow
        Write-Host "    2. Push the image manually from a machine with Docker:" -ForegroundColor Yellow
        Write-Host "       aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ecrBase" -ForegroundColor Gray
        Write-Host "       docker build -f Dockerfile.webhook -t $fullImage ." -ForegroundColor Gray
        Write-Host "       docker push $fullImage" -ForegroundColor Gray
        Write-Host ""
        Write-Warn "Continuing -- Terraform will create the Deployment but the pod will stay in ImagePullBackOff until the image is pushed"
        return
    }

    Write-Info "Docker daemon OK -- logging into ECR..."

    # Use cmd.exe to pipe the ECR token directly to docker login.
    # PowerShell's pipe operator appends a newline when converting a string to
    # native-command stdin; that extra byte makes ECR return 400 Bad Request.
    cmd /c "aws ecr get-login-password --region $Region 2>nul | docker login --username AWS --password-stdin $ecrBase"
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "docker login failed -- skipping image build"
        return
    }

    Write-Info "Building $fullImage (this may take a few minutes)..."
    $prevLocation = Get-Location
    Set-Location $RepoRoot

    docker build -f Dockerfile.webhook -t $fullImage .
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Docker build failed -- check Dockerfile.webhook"
        Set-Location $prevLocation
        return
    }

    Write-Info "Pushing $fullImage ..."
    docker push $fullImage
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Pushed $fullImage"
        $script:WebhookImageReady = $true
    } else {
        Write-Warn "docker push failed -- pod may stay in ImagePullBackOff"
    }

    Set-Location $prevLocation
}

function Ensure-DashboardImage {
    # Phase 13: build/push the dashboard image (Dockerfile.dashboard) to ECR when
    # the :dashboard-latest tag is missing. Mirrors Ensure-WebhookImage. The image
    # is FROM guardops-app:latest, so that base must already exist in ECR.
    # Returns the full image reference for manifest substitution.
    $acctId    = (aws sts get-caller-identity --query Account --output text 2>$null).Trim()
    $ecrBase   = "$acctId.dkr.ecr.$Region.amazonaws.com"
    $repo      = "guardops-app"
    $tag       = "dashboard-latest"
    $fullImage = "$ecrBase/${repo}:${tag}"

    Write-Info "Checking ECR for ${repo}:${tag}..."
    $imageFound = $false
    try {
        aws ecr describe-images --repository-name $repo `
            --image-ids imageTag=$tag --region $Region 2>$null | Out-Null
        $imageFound = ($LASTEXITCODE -eq 0)
    } catch { $imageFound = $false }

    if ($imageFound) {
        Write-Ok "ECR image ${repo}:${tag} already exists -- skipping build"
        $script:DashboardImageReady = $true
        return $fullImage
    }

    Write-Info "Image not found in ECR -- building and pushing $fullImage"
    $dockerOk = $false
    try { docker info 2>$null | Out-Null; $dockerOk = ($LASTEXITCODE -eq 0) } catch { $dockerOk = $false }
    if (-not $dockerOk) {
        Write-Warn "Docker daemon not running -- dashboard pod will stay in ImagePullBackOff until pushed"
        return $fullImage
    }

    cmd /c "aws ecr get-login-password --region $Region 2>nul | docker login --username AWS --password-stdin $ecrBase"
    if ($LASTEXITCODE -ne 0) { Write-Warn "docker login failed -- skipping dashboard image build"; return $fullImage }

    $prevLocation = Get-Location
    Set-Location $RepoRoot
    Write-Info "Building $fullImage (this may take a few minutes)..."
    docker build -f Dockerfile.dashboard -t $fullImage .
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Docker build failed -- check Dockerfile.dashboard"
        Set-Location $prevLocation
        return $fullImage
    }
    docker push $fullImage
    if ($LASTEXITCODE -eq 0) { Write-Ok "Pushed $fullImage"; $script:DashboardImageReady = $true }
    else { Write-Warn "docker push failed -- pod may stay in ImagePullBackOff" }
    Set-Location $prevLocation
    return $fullImage
}

function Ensure-Namespaces {
    # Pre-create namespaces so Terraform never hits "namespaces not found".
    Write-Info "Configuring kubeconfig for namespace pre-creation..."
    aws eks update-kubeconfig --region $Region --name $ClusterName 2>&1 | Out-Null
    foreach ($ns in @("monitoring", "staging")) {
        $exists = kubectl get namespace $ns --ignore-not-found 2>$null
        if (-not $exists) {
            kubectl create namespace $ns 2>&1 | Out-Null
            Write-Info "  Pre-created namespace: $ns"
        } else {
            Write-Info "  Namespace already exists: $ns"
        }
    }

    Ensure-WebhookImage
}

function Repair-TerraformState {
    # The hashicorp/kubernetes provider added identity tracking for
    # kubernetes_deployment resources.  State entries written before the upgrade
    # carry null identity fields; the next apply then throws "Unexpected Identity
    # Change" and blocks all further runs.
    Write-Info "Checking Terraform state for identity issues..."

    $repairs = @(
        @{ Addr     = 'module.alertmanager_webhook[0].kubernetes_deployment.webhook'
           ImportId = 'monitoring/guardops-alertmanager-webhook' }
    )

    $anyRepaired = $false
    foreach ($r in $repairs) {
        try { terraform state show $r.Addr 2>&1 | Out-Null } catch { }
        if ($LASTEXITCODE -ne 0) {
            Write-Info "  $($r.Addr) not in state -- skipping"
            continue
        }

        Write-Info "  Refreshing identity: $($r.Addr)..."
        try { terraform state rm $r.Addr     2>&1 | Out-Null } catch { }
        try { terraform import  $r.Addr $r.ImportId 2>&1 | Out-Null } catch { }

        if ($LASTEXITCODE -eq 0) {
            Write-Ok "  Identity refreshed: $($r.Addr)"
            $anyRepaired = $true
        } else {
            Write-Warn "  Re-import skipped for $($r.Addr) -- resource may not exist yet"
        }
    }

    if (-not $anyRepaired) {
        Write-Ok "Terraform state OK"
    }
}

function Ensure-AlbControllerRole {
    # Ensures the guardops-alb-controller IRSA role exists and its trust policy
    # matches the current cluster's OIDC URL.
    #
    # WHY THIS IS NEEDED:
    #   - This role is not managed by Terraform (it's created externally because
    #     of a chicken-and-egg: dns_tls module needs the ARN, but the ARN depends
    #     on the OIDC URL which only exists after EKS is created).
    #   - After terraform destroy + recreate, EKS gets a NEW OIDC issuer URL with
    #     a different hash, so the old trust policy must be updated or the ALB
    #     controller will get AccessDenied from STS.
    #   - This function is idempotent and safe to call on every startup.
    param([string]$AccountId)

    $RoleName   = "guardops-alb-controller"
    $PolicyName = "AWSLoadBalancerControllerIAMPolicy"
    $PolicyArn  = "arn:aws:iam::${AccountId}:policy/${PolicyName}"
    $TmpDir     = $TerraformDir

    Write-Info "Phase 10 pre-req: OIDC provider + ALB controller IAM role..."

    # ── 1. Discover the cluster's OIDC issuer URL ─────────────────────────────
    $oidcIssuer = $null
    try {
        $oidcIssuer = (aws eks describe-cluster --name $ClusterName --region $Region `
            --query "cluster.identity.oidc.issuer" --output text 2>$null).Trim()
    } catch { }

    if (-not $oidcIssuer -or $oidcIssuer -eq "None") {
        Write-Warn "Could not get OIDC issuer from EKS cluster -- skipping ALB role setup"
        return
    }

    $oidcUrl = $oidcIssuer -replace "https://", ""
    $oidcArn = "arn:aws:iam::${AccountId}:oidc-provider/${oidcUrl}"
    Write-Info "  Cluster OIDC URL: $oidcUrl"

    # ── 2. Ensure the OIDC provider is registered in IAM ─────────────────────
    $providerExists = $false
    try {
        aws iam get-open-id-connect-provider --open-id-connect-provider-arn $oidcArn 2>$null | Out-Null
        $providerExists = ($LASTEXITCODE -eq 0)
    } catch { }

    if (-not $providerExists) {
        Write-Info "  OIDC provider not found -- associating..."
        if (Get-Command "eksctl" -ErrorAction SilentlyContinue) {
            eksctl utils associate-iam-oidc-provider `
                --region $Region --cluster $ClusterName --approve 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Ok "  OIDC provider associated via eksctl" }
            else                     { Write-Warn "  eksctl OIDC association failed -- trying AWS CLI" }
        }

        # AWS CLI fallback (or if eksctl failed)
        try {
            aws iam get-open-id-connect-provider --open-id-connect-provider-arn $oidcArn 2>$null | Out-Null
        } catch { }
        if ($LASTEXITCODE -ne 0) {
            # The EKS OIDC root CA thumbprint is static for *.amazonaws.com
            $thumbprint = "9e99a48a9960b14926bb7f3b02e22da2b0ab7280"
            aws iam create-open-id-connect-provider `
                --url "https://$oidcUrl" `
                --client-id-list "sts.amazonaws.com" `
                --thumbprint-list $thumbprint 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Ok "  OIDC provider registered via AWS CLI" }
            else                     { Write-Warn "  OIDC provider registration failed -- check IAM console" }
        }
    } else {
        Write-Ok "  OIDC provider already registered"
    }

    # ── 3. Build the trust policy JSON (no BOM -- AWS CLI rejects BOM) ────────
    $trustPolicyContent = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "$oidcArn"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${oidcUrl}:sub": "system:serviceaccount:kube-system:aws-load-balancer-controller",
          "${oidcUrl}:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
"@

    # ── 4. Check if the IAM role already exists ───────────────────────────────
    $roleExists = $false
    try {
        aws iam get-role --role-name $RoleName 2>$null | Out-Null
        $roleExists = ($LASTEXITCODE -eq 0)
    } catch { }

    if ($roleExists) {
        # Role exists -- verify the trust policy references the CURRENT OIDC URL.
        # After destroy+recreate the hash changes and the old policy is stale.
        $currentTrust = ""
        try {
            $currentTrust = (aws iam get-role --role-name $RoleName `
                --query "Role.AssumeRolePolicyDocument" --output json 2>$null).Trim()
        } catch { }

        if ($currentTrust -match [regex]::Escape($oidcUrl)) {
            Write-Ok "  ALB controller role exists with correct OIDC trust -- no update needed"
        } else {
            Write-Warn "  Trust policy has stale OIDC URL (cluster was recreated) -- updating..."
            $trustFile = "$TmpDir\alb-trust-policy.json"
            [System.IO.File]::WriteAllText($trustFile, $trustPolicyContent)
            aws iam update-assume-role-policy `
                --role-name $RoleName `
                --policy-document file://$trustFile 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "  Trust policy updated to new OIDC URL"
                # ALB controller must restart to pick up the refreshed STS token
                $script:AlbRoleUpdated = $true
            } else {
                Write-Warn "  Trust policy update failed -- ALB controller may not work"
            }
            Remove-Item $trustFile -ErrorAction SilentlyContinue
        }
        return
    }

    # ── 5. Role does not exist -- create it from scratch ─────────────────────
    Write-Info "  Creating ALB controller IAM role '$RoleName'..."

    # 5a. Download the official policy JSON if not already cached
    $policyFile = "$TmpDir\alb-iam-policy.json"
    if (-not (Test-Path $policyFile)) {
        Write-Info "  Downloading ALB controller IAM policy..."
        try {
            Invoke-WebRequest `
                -Uri "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.7.2/docs/install/iam_policy.json" `
                -OutFile $policyFile `
                -UseBasicParsing
        } catch {
            Write-Warn "  Could not download policy JSON -- check internet connectivity"
            return
        }
    }

    # 5b. Create the IAM policy (skip if it already exists)
    $policyExistCheck = $false
    try {
        aws iam get-policy --policy-arn $PolicyArn 2>$null | Out-Null
        $policyExistCheck = ($LASTEXITCODE -eq 0)
    } catch { }

    if (-not $policyExistCheck) {
        Write-Info "  Creating IAM policy $PolicyName..."
        aws iam create-policy `
            --policy-name $PolicyName `
            --policy-document file://$policyFile `
            --description "IAM policy for AWS Load Balancer Controller" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "  Failed to create IAM policy -- ALB controller will not work"
            return
        }
        Write-Ok "  IAM policy $PolicyName created"
    } else {
        Write-Ok "  IAM policy $PolicyName already exists"
    }

    # 5c. Write trust policy without BOM and create the role
    $trustFile = "$TmpDir\alb-trust-policy.json"
    [System.IO.File]::WriteAllText($trustFile, $trustPolicyContent)

    aws iam create-role `
        --role-name $RoleName `
        --assume-role-policy-document file://$trustFile `
        --description "IRSA role for AWS Load Balancer Controller on $ClusterName" 2>&1 | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "  Failed to create IAM role $RoleName"
        Remove-Item $trustFile -ErrorAction SilentlyContinue
        return
    }

    # 5d. Attach policy
    aws iam attach-role-policy `
        --role-name $RoleName `
        --policy-arn $PolicyArn 2>&1 | Out-Null

    if ($LASTEXITCODE -eq 0) {
        Write-Ok "  ALB controller role '$RoleName' created and policy attached"
        $script:AlbRoleUpdated = $true
    } else {
        Write-Warn "  Role created but policy attachment failed -- attach manually"
    }

    Remove-Item $trustFile -ErrorAction SilentlyContinue
}

function Repair-SubnetClusterTags {
    # Tags all VPC subnets with the correct cluster name so the ALB controller
    # can auto-discover them for load balancer provisioning.
    #
    # WHY THIS IS NEEDED:
    #   The VPC Terraform module already tags subnets with
    #   "kubernetes.io/cluster/${name_prefix}-cluster" = "shared", which matches
    #   the EKS cluster name "guardops-prod-cluster". This function is a defensive
    #   safety net for clusters created before that tag was corrected, or when a
    #   stale "guardops-prod" tag (without the -cluster suffix) lingers in a subnet
    #   from older state. It is idempotent and a no-op when tags are already right.
    Write-Info "Repairing subnet cluster tags for ALB auto-discovery..."

    $vpcId = $null
    try {
        $vpcId = (aws eks describe-cluster --name $ClusterName --region $Region `
            --query "cluster.resourcesVpcConfig.vpcId" --output text 2>$null).Trim()
    } catch { }

    if (-not $vpcId -or $vpcId -eq "None") {
        Write-Warn "  Could not determine VPC ID -- skipping subnet tag repair"
        return
    }

    $subnetIdsRaw = $null
    try {
        $subnetIdsRaw = (aws ec2 describe-subnets `
            --filters "Name=vpc-id,Values=$vpcId" `
            --query "Subnets[*].SubnetId" `
            --output text --region $Region 2>$null).Trim()
    } catch { }

    if (-not $subnetIdsRaw) {
        Write-Warn "  No subnets found in VPC $vpcId"
        return
    }

    $subnetIds = ($subnetIdsRaw -split '\s+') | Where-Object { $_ }

    # Correct tag (must match the exact EKS cluster name)
    $correctTagKey = "kubernetes.io/cluster/$ClusterName"
    # Stale tag written by the current Terraform VPC module (missing -cluster suffix)
    $staleTagKey   = "kubernetes.io/cluster/$($ClusterName -replace '-cluster$', '')"

    # Apply the correct tag (idempotent)
    aws ec2 create-tags `
        --resources @subnetIds `
        --tags "Key=$correctTagKey,Value=shared" `
        --region $Region 2>&1 | Out-Null

    if ($LASTEXITCODE -eq 0) {
        Write-Ok "  Subnet tag set: ${correctTagKey}=shared ($($subnetIds.Count) subnets)"
    } else {
        Write-Warn "  Failed to update subnet cluster tags -- ALB subnet discovery may fail"
        return
    }

    # Remove the stale tag if it differs from the correct tag
    if ($staleTagKey -ne $correctTagKey) {
        aws ec2 delete-tags `
            --resources @subnetIds `
            --tags "Key=$staleTagKey" `
            --region $Region 2>&1 | Out-Null
        Write-Info "  Removed stale tag '$staleTagKey' from subnets (if present)"
    }
}

function Invoke-TerraformApply {
    # Wrapper around terraform apply -auto-approve.
    # Temporarily patches wait_for_rollout=false when the webhook image is absent
    # so Terraform doesn't time out waiting for an ImagePullBackOff pod.
    param([string[]]$ExtraArgs = @())

    $webhookTf = "$TerraformDir\modules\alertmanager-webhook\main.tf"
    $original  = $null
    $patched   = $false

    if (-not $script:WebhookImageReady -and (Test-Path $webhookTf)) {
        $original = Get-Content $webhookTf -Raw
        if ($original -match '(?i)wait_for_rollout\s*=\s*true') {
            $patched = $true
            Write-Warn "Webhook image not in ECR -- temporarily setting wait_for_rollout=false"
            ($original -replace '(?i)(wait_for_rollout\s*=\s*)true', '${1}false') |
                Set-Content $webhookTf -Encoding UTF8
        }
    }

    # CRITICAL BUG FIX: the callers do `$applyExit = Invoke-TerraformApply`, which captures this
    # function's SUCCESS (stdout) stream. Bare `terraform apply` writes its plan / "Apply
    # complete!" / Outputs to STDOUT, so those lines were captured INTO $applyExit alongside the
    # `return $ec`. Two consequences: (1) terraform's output never showed on screen -- it was
    # swallowed by the assignment; (2) $applyExit became an array of text lines + the int, so
    # `$applyExit -ne 0` was ALWAYS truthy -- a perfectly successful apply (exit 0) was reported
    # as "terraform apply failed". That is exactly why a manual apply always worked but the
    # scripted one never did, with no output and no retry messages (it "succeeds" on attempt 0).
    #
    # Fix: pipe terraform to Out-Host so its output is DISPLAYED, not captured -- the function
    # then returns ONLY $ec. ErrorActionPreference=Continue keeps terraform's stderr progress
    # from tripping the global "Stop". The retry loop also rides out transient busy-node races.
    $ec = 1
    $delays = @(0, 30, 75)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        for ($attempt = 0; $attempt -lt $delays.Count; $attempt++) {
            if ($delays[$attempt] -gt 0) {
                Write-Warn ("terraform apply failed -- retry {0}/{1} in {2}s (transient busy-node race)..." -f $attempt, ($delays.Count - 1), $delays[$attempt])
                Start-Sleep -Seconds $delays[$attempt]
            }
            terraform apply -auto-approve @ExtraArgs | Out-Host
            $ec = $LASTEXITCODE
            if ($ec -eq 0) { break }
        }
    } finally {
        $ErrorActionPreference = $prevEap
    }

    if ($patched -and $original) {
        Set-Content $webhookTf $original -Encoding UTF8
        Write-Info "  Restored wait_for_rollout=true in alertmanager-webhook module"
    }

    return $ec
}

# -- Banner --------------------------------------------------------------------
Write-Host ""
Write-Host "  +--------------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |   GuardOps Phase 11 -- Morning Startup           |" -ForegroundColor Cyan
Write-Host "  |   Est. time  : ~30-40 min (cold start)           |" -ForegroundColor White
Write-Host "  |   Est. cost  : ~`$5.28/day while EKS runs        |" -ForegroundColor Yellow
Write-Host "  |   Shutdown   : .\scripts\night-shutdown.ps1      |" -ForegroundColor Red
Write-Host "  +--------------------------------------------------+" -ForegroundColor Cyan
Write-Host ""

$Step = 0

# Tracks whether the ALB role was created or its trust policy was updated.
# If true, the ALB controller needs a rollout restart after it's running.
$script:AlbRoleUpdated = $false

# -- Pre-flight ----------------------------------------------------------------
$Step++
Write-Step $Step "12" "Pre-flight checks"

try {
    $identity = aws sts get-caller-identity --output json 2>$null | ConvertFrom-Json
    Write-Ok "AWS auth OK: $($identity.Arn)"
} catch {
    Write-Fail "AWS auth failed. Run: aws configure"
    exit 1
}

foreach ($tool in @("kubectl", "helm", "gh", "eksctl")) {
    if (Get-Command $tool -ErrorAction SilentlyContinue) { Write-Ok "$tool found" }
    else { Write-Warn "$tool not found -- some steps may be skipped" }
}

if (-not $env:VIRTUAL_ENV) {
    $venvPath = "$RepoRoot\.venv\Scripts\Activate.ps1"
    if (Test-Path $venvPath) { & $venvPath; Write-Ok "Activated .venv" }
    else { Write-Warn ".venv not found -- guardops CLI may not work" }
} else {
    Write-Ok "Virtual environment active"
}

# Read Phase 10 flags early -- needed to gate OIDC/ALB setup inside Terraform block
$dnsTlsEnabled = Read-TfVar "enable_dns_tls"
$argoCdEnabled = Read-TfVar "enable_argocd"
$domainName    = Read-TfVar "domain_name"

# -- Terraform -----------------------------------------------------------------
if (-not $SkipTerraform) {
    $Step++
    Write-Step $Step "12" "Terraform -- AWS infrastructure"
    Set-Location $TerraformDir

    $eksExists = Test-EKSExists
    $acct      = $identity.Account

    if (-not $eksExists) {
        Write-Info "EKS not found -- Phase 1 bootstrap apply"

        # 1. Stub out providers FIRST so init doesn't fail looking for EKS
        Disable-EKSProviders

        try {
            # 2. Initialize Terraform
            terraform init -reconfigure 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "terraform init failed" }

            # 3. Import existing resources to avoid 409 conflicts
            Import-IfMissing "module.iam.aws_iam_role.eks_cluster" "guardops-prod-eks-cluster-role" "EKS cluster IAM role"
            Import-IfMissing "module.iam.aws_iam_role.eks_node"    "guardops-prod-eks-node-role"    "EKS node IAM role"
            Import-IfMissing "module.s3.aws_s3_bucket.reports"     "guardops-reports-$acct"         "S3 reports bucket"
            # ECR repo preserved by night-shutdown (non-empty, so it survives the
            # destroy). Only the repository itself 409s on create; the lifecycle
            # policy and the S3 bucket sub-resources are idempotent config applies.
            Import-IfMissing 'module.ecr.aws_ecr_repository.repos["guardops-app"]' "guardops-app" "ECR repository (preserved across shutdown)"

            # Adopt the GitHub OIDC provider + CI role preserved by night-shutdown
            # BEFORE the Phase 1 apply recreates module.iam_oidc (avoids 409s).
            Import-GithubOidc -AccountId $acct

            # 4. Phase 1 apply: infra only, no Helm/Kubernetes providers
            terraform apply `
                -target="module.vpc" `
                -target="module.iam" `
                -target="module.iam_oidc" `
                -target="module.eks" `
                -target="module.ecr" `
                -target="module.s3" `
                -auto-approve
            if ($LASTEXITCODE -ne 0) { throw "terraform apply Phase 1 failed" }
            Write-Ok "Phase 1 infra created"
        } finally {
            Restore-EKSProviders
        }

        Write-Info "Waiting for EKS cluster to become ACTIVE..."
        $waited = 0
        while ($waited -lt 600) {
            $status = aws eks describe-cluster --name $ClusterName --region $Region `
                --query "cluster.status" --output text 2>$null
            if ($status -eq "ACTIVE") { Write-Ok "EKS ACTIVE"; break }
            Start-Sleep -Seconds 15
            $waited += 15
            Write-Info "  ${waited}s -- status: $status"
        }

        terraform init -reconfigure 2>&1 | Out-Null
        Ensure-Namespaces

        # Phase 11: adopt a pre-existing cluster OIDC provider into Terraform state
        # (no-op on the normal path where Phase 1 just created it).
        Import-EksOidcProvider -AccountId $acct

        # Phase 10 pre-requisites: OIDC provider + ALB IAM role + subnet tags
        # Must run BEFORE the full apply which deploys the ALB controller via Helm.
        if ($dnsTlsEnabled -eq "true") {
            Ensure-AlbControllerRole -AccountId $acct
            Repair-SubnetClusterTags
            Import-Route53Zone
        }

        Repair-TerraformState
        Start-Sleep -Seconds 5
        Ensure-HelmRepos
        Import-KyvernoRelease
        Write-Info "Running Phase 2 full apply..."
        $applyExit = Invoke-TerraformApply
        if ($applyExit -ne 0) {
            Write-Warn "terraform apply failed -- retrying once in 20s (transient helm/webhook rollout races are common on a busy node)..."
            Start-Sleep -Seconds 20
            $applyExit = Invoke-TerraformApply
        }
        if ($applyExit -ne 0) { Write-Fail "terraform apply Phase 2 failed"; exit 1 }
        Write-Ok "Full apply complete"

    } else {
        Write-Info "EKS already running -- full apply"
        terraform init -reconfigure 2>&1 | Out-Null
        Ensure-Namespaces

        # Phase 11: adopt a pre-existing cluster OIDC provider into Terraform state
        # so the full apply never fails with EntityAlreadyExists.
        Import-EksOidcProvider -AccountId $acct

        # Adopt the GitHub OIDC provider + CI role preserved by night-shutdown
        # (idempotent — already in state on a normal warm start).
        Import-GithubOidc -AccountId $acct

        # Phase 10 pre-requisites: OIDC provider + ALB IAM role + subnet tags
        # On warm start: verify trust policy is current, repair subnets if needed.
        if ($dnsTlsEnabled -eq "true") {
            Ensure-AlbControllerRole -AccountId $acct
            Repair-SubnetClusterTags
            Import-Route53Zone
        }

        Repair-TerraformState
        Start-Sleep -Seconds 5
        Ensure-HelmRepos
        Import-KyvernoRelease
        $applyExit = Invoke-TerraformApply
        if ($applyExit -ne 0) {
            Write-Warn "terraform apply failed -- retrying once in 20s (transient helm/webhook rollout races are common on a busy node)..."
            Start-Sleep -Seconds 20
            $applyExit = Invoke-TerraformApply
        }
        if ($applyExit -ne 0) { Write-Fail "terraform apply failed"; exit 1 }
        Write-Ok "terraform apply complete"
    }

    $roleArn = terraform output -raw github_actions_role_arn 2>$null
    if ($roleArn) {
        gh secret set AWS_ROLE_ARN --body $roleArn 2>$null
        Write-Ok "GitHub secret AWS_ROLE_ARN updated"
    }

    Set-Location $RepoRoot
}

# -- kubectl -------------------------------------------------------------------
$Step++
Write-Step $Step "12" "Configure kubectl + wait for nodes"

aws eks update-kubeconfig --region $Region --name $ClusterName
if ($LASTEXITCODE -ne 0) { Write-Fail "kubectl config failed"; exit 1 }
Write-Ok "kubectl context set"

$nodeReady = $false
for ($i = 0; $i -lt 30; $i++) {
    $ns = kubectl get nodes --no-headers 2>$null | Select-String "\bReady\b"
    if ($ns) { $nodeReady = $true; break }
    Start-Sleep -Seconds 10
    Write-Info "Attempt $($i+1)/30 -- waiting for node..."
}
if ($nodeReady) { Write-Ok "Node Ready"; kubectl get nodes }
else            { Write-Warn "No node Ready after 5 min -- continuing" }

$stagingNs = kubectl get namespace staging --ignore-not-found 2>$null
if (-not $stagingNs) {
    kubectl create namespace staging
    Write-Ok "Created staging namespace"
} else {
    Write-Info "staging namespace exists"
}

# -- Observability -------------------------------------------------------------
$Step++
Write-Step $Step "12" "Observability stack (Prometheus + Grafana + Loki)"

$grafPods = $null
try {
    $grafPods = kubectl get pods -n monitoring -l "app.kubernetes.io/name=grafana" --no-headers 2>$null |
        Select-String "Running"
} catch { }
if ($grafPods) {
    Write-Info "Grafana already running -- skipping install"
} else {
    Set-Location $RepoRoot
    .\scripts\setup-observability.ps1
    if ($LASTEXITCODE -ne 0) { Write-Warn "setup-observability.ps1 had errors" }
}

Wait-Pods -NS "monitoring" -Label "app.kubernetes.io/name=grafana" -Timeout 180 -Name "Grafana" | Out-Null
Wait-Pods -NS "monitoring" -Label "app=loki"                       -Timeout 120 -Name "Loki"    | Out-Null

$grafSecret = kubectl get secret kube-prometheus-stack-grafana -n monitoring `
    -o jsonpath="{.data.admin-password}" 2>$null
if ($grafSecret) {
    $grafanaPassword = [System.Text.Encoding]::UTF8.GetString(
        [System.Convert]::FromBase64String($grafSecret))
} else {
    $grafanaPassword = "run: kubectl get secret kube-prometheus-stack-grafana -n monitoring -o jsonpath=`"{.data.admin-password}`" | base64 -d"
}

# -- Runtime security ----------------------------------------------------------
$Step++
Write-Step $Step "12" "Runtime security (Falco + Loki + Promtail)"

$falcoPods = $null
try {
    $falcoPods = kubectl get pods -n monitoring -l "app=falco" --no-headers 2>$null |
        Select-String "Running"
} catch { }
if ($falcoPods) {
    Write-Info "Falco already running -- skipping install"
} else {
    Set-Location $RepoRoot
    .\scripts\setup-runtime-security.ps1
    if ($LASTEXITCODE -ne 0) { Write-Warn "setup-runtime-security.ps1 had errors" }
}

Wait-Pods -NS "monitoring" -Label "app=falco" -Timeout 180 -Name "Falco" | Out-Null

$simExists = $null
try { $simExists = kubectl get cronjob falco-simulator -n monitoring --ignore-not-found 2>$null } catch { }
if (-not $simExists) {
    kubectl apply -f "$RepoRoot\k8s\falco\falco-simulator-configmap.yaml" 2>$null
    kubectl apply -f "$RepoRoot\k8s\falco\falco-simulator-cronjob.yaml"  2>$null
    Write-Ok "Falco simulator deployed"
} else {
    Write-Info "Falco simulator already present"
}

# -- Self-healing --------------------------------------------------------------
$Step++
Write-Step $Step "12" "Self-healing (Alertmanager webhook)"

$webhookRunning = $null
try {
    $webhookRunning = kubectl get pods -n monitoring -l "app=alertmanager-webhook" `
        --no-headers 2>$null | Select-String "Running"
} catch { }
if ($webhookRunning) {
    kubectl apply -f "$RepoRoot\k8s\alertmanager\quarantine-webhook.yaml" 2>$null
    Write-Ok "PrometheusRule + AlertmanagerConfig applied"
} else {
    Write-Warn "Webhook pod not Running -- enable_self_healing may be false in terraform.tfvars"
    Write-Info "Manual fix: kubectl apply -f k8s/alertmanager/quarantine-webhook.yaml"
}

# -- Phase 10: TLS -------------------------------------------------------------
$Step++
Write-Step $Step "12" "Phase 10 -- TLS (cert-manager + ALB controller + ClusterIssuers)"

if ($dnsTlsEnabled -eq "true") {

    # ── Wait for cert-manager (all 3 components must be Running) ─────────────
    Wait-Pods -NS "cert-manager" -Label "app=cert-manager"           -Timeout 180 -Name "cert-manager" | Out-Null
    Wait-Pods -NS "cert-manager" -Label "app=cainjector"             -Timeout 60  -Name "cainjector"   | Out-Null
    Wait-Pods -NS "cert-manager" -Label "app=webhook"                -Timeout 60  -Name "cert-manager-webhook" | Out-Null

    # ── Wait for ALB controller ───────────────────────────────────────────────
    Wait-Pods -NS "kube-system" -Label "app.kubernetes.io/name=aws-load-balancer-controller" `
              -Timeout 120 -Name "ALB controller" | Out-Null

    # ── If the ALB role was created/updated this run, restart the controller
    #    so it picks up a fresh STS token with the correct trust ───────────────
    if ($script:AlbRoleUpdated) {
        Write-Info "ALB role was updated this run -- restarting ALB controller..."
        kubectl rollout restart deployment aws-load-balancer-controller -n kube-system 2>$null | Out-Null
        kubectl rollout status  deployment aws-load-balancer-controller -n kube-system `
            --timeout=120s 2>$null | Out-Null
        Write-Ok "ALB controller restarted"
    }

    # ── Apply ClusterIssuers (cert-manager CRDs must exist first) ─────────────
    Write-Info "Applying ClusterIssuers..."
    kubectl apply -f "$RepoRoot\k8s\tls\clusterissuer-letsencrypt-staging.yaml"
    kubectl apply -f "$RepoRoot\k8s\tls\clusterissuer-letsencrypt-prod.yaml"

    $issuerWait = 0
    while ($issuerWait -lt 60) {
        $s = kubectl get clusterissuer letsencrypt-staging `
            -o jsonpath="{.status.conditions[0].status}" 2>$null
        if ($s -eq "True") { Write-Ok "ClusterIssuers Ready"; break }
        Start-Sleep -Seconds 5
        $issuerWait += 5
    }
    if ($issuerWait -ge 60) {
        Write-Warn "ClusterIssuers not Ready yet"
        Write-Info "  Check: kubectl describe clusterissuer letsencrypt-staging"
    }

    # ── Report existing ALB DNS config ────────────────────────────────────────
    $existingAlb = Read-TfVar "alb_dns_name"
    if ($existingAlb -and $existingAlb -ne "") {
        Write-Ok "ALB DNS already in tfvars: $existingAlb"
    } else {
        Write-Info "alb_dns_name not set -- will populate after app deploy"
    }

    # ── Check certificate status (informational) ──────────────────────────────
    $certReady = kubectl get certificate -n default -o jsonpath="{.items[*].status.conditions[?(@.type=='Ready')].status}" 2>$null
    if ($certReady -eq "True") {
        Write-Ok "TLS certificate Ready"
    } elseif ($certReady) {
        Write-Warn "TLS certificate not Ready yet -- will complete after DNS propagation"
        Write-Info "  Monitor: kubectl get certificate -n default -w"
        Write-Info "  Debug:   kubectl describe certificate -n default"
    } else {
        Write-Info "No certificate found yet -- will be issued after app deploy + DNS setup"
    }

} else {
    Write-Info "enable_dns_tls not true in terraform.tfvars -- skipping TLS"
    Write-Info "Add:  enable_dns_tls = true  to infra/terraform/terraform.tfvars"
}

# -- Phase 10: ArgoCD ----------------------------------------------------------
$Step++
Write-Step $Step "12" "Phase 10 -- ArgoCD GitOps"

$argoCdUrl   = ""
$argoCdPass  = ""
$argoCdToken = ""

if ($argoCdEnabled -eq "true") {
    Wait-Pods -NS "argocd" -Label "app.kubernetes.io/name=argocd-server" -Timeout 300 -Name "ArgoCD server" | Out-Null

    Write-Info "Applying ArgoCD manifests..."
    kubectl apply -f "$RepoRoot\k8s\argocd\project.yaml"     2>$null
    kubectl apply -f "$RepoRoot\k8s\argocd\app-prod.yaml"    2>$null
    kubectl apply -f "$RepoRoot\k8s\argocd\app-staging.yaml" 2>$null
    # v1.0.0: the UI Ingress on the shared ALB (replaces the chart's nginx ingress).
    if ($dnsTlsEnabled -eq "true") {
        kubectl apply -f "$RepoRoot\k8s\argocd\ingress.yaml" 2>$null
        Write-Ok "ArgoCD AppProject + Applications + Ingress (argocd.$domainName) applied"
    } else {
        Write-Ok "ArgoCD AppProject + Applications applied (Ingress skipped — enable_dns_tls not true)"
    }

    Set-Location $TerraformDir
    $argoCdUrl = terraform output -raw argocd_server_url 2>$null
    Set-Location $RepoRoot

    $passB64 = kubectl get secret argocd-initial-admin-secret -n argocd `
        -o jsonpath="{.data.password}" 2>$null
    if ($passB64) {
        $argoCdPass = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String($passB64))
    }

    if ((Get-Command "argocd" -ErrorAction SilentlyContinue) -and $argoCdUrl -and $argoCdPass) {
        Write-Info "Logging into ArgoCD CLI..."
        $argoHost = $argoCdUrl -replace "https://",""
        argocd login $argoHost --username admin --password $argoCdPass --grpc-web --insecure 2>$null
        if ($LASTEXITCODE -eq 0) {
            $argoCdToken = argocd account generate-token --account admin 2>$null
            if ($argoCdToken) {
                gh secret set ARGOCD_TOKEN --body $argoCdToken 2>$null
                Write-Ok "GitHub secret ARGOCD_TOKEN set"

                if (Test-Path $GuardopsYaml) {
                    $yamlContent = Get-Content $GuardopsYaml -Raw
                    if ($yamlContent -notmatch "argocd:") {
                        $argoSection  = "`nargocd:`n"
                        $argoSection += "  url: `"$argoCdUrl`"`n"
                        $argoSection += "  app_name_staging: `"guardops-app-staging`"`n"
                        $argoSection += "  app_name_prod: `"guardops-app-prod`"`n"
                        $argoSection += "  token_env_var: `"ARGOCD_TOKEN`"`n"
                        Add-Content $GuardopsYaml $argoSection
                        Write-Ok "Updated .guardops.yaml with argocd section"
                    } else {
                        Write-Info ".guardops.yaml argocd section already present"
                    }
                }
            } else {
                Write-Warn "Token generation returned empty -- generate manually"
            }
        } else {
            Write-Warn "ArgoCD CLI login failed"
        }
    } else {
        Write-Warn "argocd CLI not found or URL/password missing"
    }

    if (-not $argoCdToken) {
        Write-Info "To set up ArgoCD token manually after startup:"
        Write-Info "  argocd login $($argoCdUrl -replace 'https://','') --username admin --password YOUR_PASS"
        Write-Info "  argocd account generate-token --account admin"
        Write-Info "  gh secret set ARGOCD_TOKEN --body YOUR_TOKEN"
    }
} else {
    Write-Info "enable_argocd not true in terraform.tfvars -- skipping ArgoCD"
    Write-Info "Add:  enable_argocd = true  to infra/terraform/terraform.tfvars"
}

# -- Phase 11: Admission Control (Kyverno) -------------------------------------
$Step++
Write-Step $Step "12" "Phase 11 -- Admission Control (Kyverno verifyImages)"

$kyvernoEnabled = Read-TfVar "enable_kyverno"
if ($kyvernoEnabled -eq "true") {
    # Kyverno itself is installed by Terraform (module.kyverno). Here we wait for
    # it to be Ready, then apply the ClusterPolicies from k8s/kyverno/ with the CI
    # identity + policy action substituted in. networkpolicy* files are reference
    # only (see k8s/kyverno/networkpolicy-egress.yaml) and are skipped.
    Wait-Pods -NS "kyverno" -Label "app.kubernetes.io/component=admission-controller" -Timeout 300 -Name "Kyverno admission controller" | Out-Null

    $policyAction = Read-TfVar "kyverno_policy_action"
    if (-not $policyAction) { $policyAction = "Audit" }

    $ciIssuer  = "https://token.actions.githubusercontent.com"
    $ciSubject = "https://github.com/Bihan-Banerjee/GuardOps/.github/workflows/ci.yaml@refs/heads/*"
    # Kyverno forbids mutateDigest in Audit, so pin the digest only under Enforce.
    $digestPin = if ($policyAction -eq "Enforce") { "true" } else { "false" }

    Write-Info "Applying Kyverno ClusterPolicies (action=$policyAction)..."
    foreach ($f in (Get-ChildItem "$RepoRoot\k8s\kyverno\*.yaml" | Where-Object { $_.Name -notlike "networkpolicy*" })) {
        $body = (Get-Content $f.FullName -Raw).
            Replace("__CI_ISSUER__",     $ciIssuer).
            Replace("__CI_SUBJECT__",    $ciSubject).
            Replace("__POLICY_ACTION__", $policyAction).
            Replace("__DIGEST_PIN__",    $digestPin)
        try { $body | kubectl apply -f - 2>&1 | Out-Null } catch { }
        if ($LASTEXITCODE -eq 0) { Write-Ok "  $($f.Name)" }
        else { Write-Warn "  $($f.Name) failed -- check: kubectl get clusterpolicy" }
    }
    Write-Ok "Kyverno ClusterPolicies applied (action=$policyAction)"
    Write-Info "Verify:  kubectl get clusterpolicy ; kubectl get polr -A"
    Write-Info "Enforce: .\scripts\setup-admission-control.ps1 -Enforce"
} else {
    Write-Info "enable_kyverno not true in terraform.tfvars -- skipping admission control"
    Write-Info "Add:  enable_kyverno = true  to infra/terraform/terraform.tfvars"
}

# -- Deploy app ----------------------------------------------------------------
if (-not $SkipDeploy) {
    $Step++
    Write-Step $Step "12" "Deploy app (prod + staging)"

    Set-Location "$RepoRoot\test-project"

    # --skip-scan: the demo test-app ships intentional CVEs/findings to exercise the
    # scanner, so a gated deploy ALWAYS blocks here (6 CRITICAL + 10 HIGH). Scanning is a
    # CI concern, not a startup concern (see README); without this the app never deploys,
    # so no Ingress is created and the ALB/DNS wiring below aborts on a NotFound.
    Write-Info "Deploying prod..."
    guardops deploy --env prod --skip-scan --skip-dast
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Prod deploy had errors -- retry: guardops deploy --env prod --skip-scan --skip-dast"
    } else {
        Write-Ok "Prod deploy complete"
    }

    Write-Info "Deploying staging..."
    guardops deploy --env staging --skip-scan --skip-dast
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Staging deploy had errors"
    } else {
        Write-Ok "Staging deploy complete"
    }

    Set-Location $RepoRoot

    # ── Phase 10: Wire ALB DNS after deploy creates the Ingress ───────────────
    if ($dnsTlsEnabled -eq "true") {

        # Ensure the Ingress has the internet-facing annotation.
        # The Helm values should have this, but patch it as a safety net in case
        # they don't (avoids the "2 tagged for other cluster" subnet error).
        Write-Info "Verifying Ingress annotations for ALB..."
        $currentScheme = kubectl get ingress guardops-app -n default `
            -o jsonpath="{.metadata.annotations.alb\.ingress\.kubernetes\.io/scheme}" 2>$null
        if ($currentScheme -ne "internet-facing") {
            Write-Warn "  Ingress missing internet-facing scheme -- patching..."
            kubectl annotate ingress guardops-app -n default `
                "alb.ingress.kubernetes.io/scheme=internet-facing" `
                "alb.ingress.kubernetes.io/target-type=ip" `
                --overwrite 2>$null | Out-Null
            Write-Ok "  Ingress annotated: internet-facing / target-type=ip"

            # Restart ALB controller to clear its internal cache
            Write-Info "  Restarting ALB controller to pick up new annotation..."
            kubectl rollout restart deployment aws-load-balancer-controller -n kube-system 2>$null | Out-Null
            kubectl rollout status  deployment aws-load-balancer-controller -n kube-system `
                --timeout=120s 2>$null | Out-Null
        } else {
            Write-Ok "  Ingress annotation correct: internet-facing"
        }

        # Wait for ALB address and update tfvars if it changed.
        # Always compare current address vs tfvars -- after destroy+recreate the
        # ALB hostname changes even if tfvars still has the old (stale) value.
        Write-Info "Waiting for ALB address from Ingress (up to 3 min)..."
        $albWait    = 0
        $albAddress = ""
        while ($albWait -lt 180 -and (-not $albAddress)) {
            $albAddress = kubectl get ingress guardops-app -n default `
                -o jsonpath="{.status.loadBalancer.ingress[0].hostname}" 2>$null
            if (-not $albAddress) {
                Start-Sleep -Seconds 15
                $albWait += 15
                Write-Info "  ${albWait}s -- waiting for ALB..."
            }
        }

        if ($albAddress) {
            $tfAlbDns = Read-TfVar "alb_dns_name"
            if ($albAddress -ne $tfAlbDns) {
                Write-Ok "ALB address: $albAddress (updating tfvars)"
                Set-TfVar "alb_dns_name" $albAddress
                Write-Info "Re-applying dns-tls module for Route53 alias records..."
                Set-Location $TerraformDir
                terraform apply -target="module.dns_tls" -auto-approve
                if ($LASTEXITCODE -eq 0) { Write-Ok "Route53 alias records updated" }
                else                     { Write-Warn "dns-tls re-apply had errors -- check terraform output" }
                Set-Location $RepoRoot

                # Show NS records once per session (registrar delegation reminder)
                Write-Host ""
                Write-Host "  DOMAIN SETUP REMINDER:" -ForegroundColor Yellow
                Write-Host "  Once you have registered $domainName, delegate it by setting" -ForegroundColor Yellow
                Write-Host "  these nameservers at your registrar:" -ForegroundColor Yellow
                Set-Location $TerraformDir
                terraform output name_servers 2>$null
                Set-Location $RepoRoot
                Write-Host ""
            } else {
                Write-Ok "ALB address unchanged: $albAddress"
            }
        } else {
            Write-Warn "ALB address not available after 3 min"
            Write-Info "  Check controller logs: kubectl logs -n kube-system -l app.kubernetes.io/name=aws-load-balancer-controller --tail=20"
            Write-Info "  Check ingress events:  kubectl describe ingress guardops-app -n default"
            Write-Info "  Once address appears:  Update alb_dns_name in terraform.tfvars, then:"
            Write-Info "                         terraform apply -target=module.dns_tls"
        }
    }

    # ── GitOps override commit ────────────────────────────────────────────────
    if ($argoCdEnabled -eq "true" -and $argoCdToken) {
        Write-Info "Running GitOps override commits..."
        $env:ARGOCD_TOKEN = $argoCdToken
        Set-Location "$RepoRoot\test-project"
        guardops deploy --env prod    --skip-sonarqube --skip-build --skip-dast --gitops --gitops-branch main 2>$null
        guardops deploy --env staging --skip-sonarqube --skip-build --skip-dast --gitops --gitops-branch main 2>$null
        Write-Ok "GitOps override files committed"
        Set-Location $RepoRoot
    }
}

# -- Phase 13: Web dashboard ---------------------------------------------------
if (-not $SkipDeploy) {
    $Step++
    Write-Step $Step "12" "Phase 13 -- Web dashboard (app.guardops.live)"

    $dashboardImage = Ensure-DashboardImage

    # Findings source: the guardops-reports S3 bucket (the export bridge).
    $dashBucket = $env:GUARDOPS_S3_BUCKET
    if (-not $dashBucket) {
        try {
            $dashBucket = (aws s3api list-buckets `
                --query "Buckets[?starts_with(Name,'guardops-reports')].Name | [0]" `
                --output text 2>$null).Trim()
        } catch { }
    }
    if (-not $dashBucket -or $dashBucket -eq "None") {
        $dashBucket = ""
        Write-Warn "No guardops-reports bucket found -- dashboard starts with empty findings"
        Write-Info "  Publish data later with: guardops db export --to-s3 --bucket <bucket>"
    } else {
        Write-Ok "Findings bucket: $dashBucket"
    }

    # Render + apply the workload manifests (placeholders substituted).
    foreach ($rel in @("k8s\dashboard\configmap.yaml", "k8s\dashboard\deployment.yaml")) {
        $text = (Get-Content "$RepoRoot\$rel" -Raw).
            Replace("__ECR_IMAGE__",  $dashboardImage).
            Replace("__S3_BUCKET__",  $dashBucket).
            Replace("__AWS_REGION__", $Region)
        # Write UTF-8 WITHOUT a BOM. Set-Content -Encoding utf8 on Windows PowerShell
        # 5.1 prepends a BOM that kubectl rejects: "control characters are not allowed".
        $tmp = New-TemporaryFile
        [System.IO.File]::WriteAllText($tmp.FullName, $text, (New-Object System.Text.UTF8Encoding($false)))
        kubectl apply -f $tmp.FullName | Out-Null
        Remove-Item $tmp -Force
    }
    kubectl apply -f "$RepoRoot\k8s\dashboard\service.yaml" | Out-Null

    # Auth Secret: reuse GUARDOPS_DASHBOARD_* env if set, else generate a password.
    if ($env:GUARDOPS_DASHBOARD_USER)     { $script:DashboardUser     = $env:GUARDOPS_DASHBOARD_USER }
    if ($env:GUARDOPS_DASHBOARD_PASSWORD) { $script:DashboardPassword = $env:GUARDOPS_DASHBOARD_PASSWORD }
    if (-not $script:DashboardPassword) {
        $script:DashboardPassword = -join ((48..57) + (65..90) + (97..122) |
            Get-Random -Count 20 | ForEach-Object { [char]$_ })
    }
    $secretYaml = kubectl create secret generic guardops-dashboard-secret `
        --namespace default `
        --from-literal=GUARDOPS_DASHBOARD_USER=$($script:DashboardUser) `
        --from-literal=GUARDOPS_DASHBOARD_PASSWORD=$($script:DashboardPassword) `
        --dry-run=client -o yaml
    $secretYaml | kubectl apply -f - 2>$null | Out-Null
    kubectl rollout restart deployment/guardops-dashboard -n default 2>$null | Out-Null

    # Ingress joins the shared ALB group -- only when the TLS/ALB stack is enabled.
    if ($dnsTlsEnabled -eq "true") {
        kubectl apply -f "$RepoRoot\k8s\dashboard\ingress.yaml" 2>$null | Out-Null
        Write-Ok "Dashboard Ingress applied (app.$domainName, shared ALB group 'guardops')"
    } else {
        Write-Info "enable_dns_tls not true -- skipping dashboard Ingress (use the port-forward)"
    }

    Wait-Pods -NS "default" -Label "app.kubernetes.io/name=guardops-dashboard" -Timeout 120 -Name "Dashboard" | Out-Null
    $script:DashboardDeployed = $true
    Write-Ok "Dashboard deployed (login user: $($script:DashboardUser))"

    # v1.0.0: publish a fresh static snapshot so dashboard.guardops.live renders the
    # latest data even after tonight's teardown. Reads durable data from the same S3
    # export the dashboard serves. Best-effort -- never fail startup.
    if ($dashBucket) {
        $prevBackend = $env:GUARDOPS_METADATA_BACKEND
        $env:GUARDOPS_METADATA_BACKEND = "s3"
        try {
            Push-Location $RepoRoot
            guardops dashboard snapshot --to-s3 --bucket $dashBucket 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Ok "Published dashboard snapshot (offline fallback)" }
            else { Write-Warn "Dashboard snapshot publish failed (continuing)" }
        } catch {
            Write-Warn "Dashboard snapshot publish error (continuing)"
        } finally {
            Pop-Location
            if ($null -eq $prevBackend) {
                Remove-Item Env:\GUARDOPS_METADATA_BACKEND -ErrorAction SilentlyContinue
            } else {
                $env:GUARDOPS_METADATA_BACKEND = $prevBackend
            }
        }
    }
}

# -- Port-forwards -------------------------------------------------------------
if (-not $SkipPortForwards) {
    $Step++
    Write-Step $Step "12" "Opening port-forwards"

    $forwards = @(
        @{ Svc="svc/kube-prometheus-stack-grafana";      Port="3000:80";   NS="monitoring" },
        @{ Svc="svc/loki";                               Port="3100:3100"; NS="monitoring" },
        @{ Svc="svc/kube-prometheus-stack-prometheus";   Port="9090:9090"; NS="monitoring" },
        @{ Svc="svc/kube-prometheus-stack-alertmanager"; Port="9093:9093"; NS="monitoring" },
        @{ Svc="svc/guardops-alertmanager-webhook";      Port="9095:9095"; NS="monitoring" }
    )

    # Phase 13: expose the dashboard locally too (works even without dns-tls).
    if ($script:DashboardDeployed) {
        $forwards += @{ Svc="svc/guardops-dashboard"; Port="8081:80"; NS="default" }
    }

    foreach ($fwd in $forwards) {
        $cmd = "kubectl port-forward $($fwd.Svc) $($fwd.Port) -n $($fwd.NS)"
        try {
            Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd -WindowStyle Normal
            $localPort = $fwd.Port.Split(':')[0]
            Write-Ok "Port-forward: localhost:$localPort  ($($fwd.Svc))"
        } catch {
            Write-Warn "Could not open window for $($fwd.Svc)"
            Write-Info "Run manually: $cmd"
        }
        Start-Sleep -Milliseconds 400
    }
}

# -- Summary -------------------------------------------------------------------
$Step++
Write-Step $Step "12" "Summary"

Write-Host ""
Write-Host "  +--------------------------------------------------+" -ForegroundColor Green
Write-Host "  |   GuardOps -- Startup Complete                   |" -ForegroundColor Green
Write-Host "  +--------------------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Host "  Cluster : $ClusterName" -ForegroundColor White
Write-Host ""
Write-Host "  URLs:" -ForegroundColor Cyan
Write-Host "    Grafana      : http://localhost:3000  (admin / $grafanaPassword)"
Write-Host "    Prometheus   : http://localhost:9090"
Write-Host "    Alertmanager : http://localhost:9093"
Write-Host "    Webhook      : http://localhost:9095/healthz"
if ($dnsTlsEnabled -eq "true" -and $domainName) {
    Write-Host "    App (prod)   : https://$domainName"
    Write-Host "    App (staging): https://staging.$domainName"
}
if ($script:DashboardDeployed) {
    if ($dnsTlsEnabled -eq "true" -and $domainName) {
        Write-Host "    Dashboard    : https://app.$domainName  (or http://localhost:8081)"
    } else {
        Write-Host "    Dashboard    : http://localhost:8081"
    }
    Write-Host "    Dashboard auth : $($script:DashboardUser) / $($script:DashboardPassword)" -ForegroundColor Yellow
}
if ($argoCdEnabled -eq "true") {
    Write-Host "    ArgoCD       : $argoCdUrl"
    if ($argoCdPass) {
        Write-Host "    ArgoCD login : admin / $argoCdPass" -ForegroundColor Yellow
    }
}
Write-Host ""
Write-Host "  Quick checks:" -ForegroundColor Cyan
Write-Host "    guardops status"
Write-Host "    guardops runtime-status"
Write-Host "    guardops quarantine-status"
Write-Host "    guardops sync-status --env prod"
Write-Host "    kubectl get pods -n default"
Write-Host "    kubectl get pods -n staging"
Write-Host "    kubectl get pods -n monitoring"
Write-Host "    kubectl get certificate -A"
Write-Host "    kubectl get ingress -A"
Write-Host ""
if ($dnsTlsEnabled -eq "true") {
    $certStatus = kubectl get certificate -n default -o jsonpath="{.items[*].status.conditions[?(@.type=='Ready')].status}" 2>$null
    if ($certStatus -eq "True") {
        Write-Host "  TLS : Certificate Ready -- https://$domainName is live" -ForegroundColor Green
    } else {
        Write-Host "  TLS : Certificate not Ready yet" -ForegroundColor Yellow
        Write-Host "        Requires domain registered + NS delegated to Route53" -ForegroundColor Yellow
        Write-Host "        Monitor: kubectl get certificate -n default -w" -ForegroundColor Gray
    }
    Write-Host ""
}
Write-Host "  Stop billing tonight:" -ForegroundColor Red
Write-Host "    .\scripts\night-shutdown.ps1" -ForegroundColor Red
Write-Host ""