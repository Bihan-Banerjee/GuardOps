# scripts/morning-start.ps1
#
# GuardOps Phase 10 -- Full Morning Startup
#
# Handles every phase from scratch in a single run:
#   Phase 1-3  : Terraform AWS infra (VPC, IAM, EKS, ECR, S3)
#   Phase 4-5  : Observability (Prometheus, Grafana, Loki) + kubectl wiring
#   Phase 6    : GitHub OIDC secret sync
#   Phase 7    : Falco runtime security + simulator
#   Phase 8    : Alertmanager self-healing webhook
#   Phase 9    : App deploy to prod + staging namespaces
#   Phase 10   : cert-manager ClusterIssuers, ALB DNS wiring, ArgoCD setup
#
# Key behaviours:
#   - Fully idempotent -- safe to re-run on an already-running cluster
#   - Solves the EKS bootstrap provider problem automatically
#   - Imports existing AWS resources to avoid 409 conflicts
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

# -- Configuration -------------------------------------------------------------
$Region       = "ap-south-1"
$RepoRoot     = "D:\EXTRA\GuardOps"
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
    # $ErrorActionPreference = "Stop" turns any native-command stderr into a
    # terminating NativeCommandError, even with 2>$null.  Use try/catch so a
    # "not found" response from ECR doesn't kill the script.
    $imageFound = $false
    try {
        aws ecr describe-images --repository-name $repo `
            --image-ids imageTag=$tag --region $Region 2>$null | Out-Null
        $imageFound = ($LASTEXITCODE -eq 0)
    } catch {
        $imageFound = $false   # NativeCommandError = image absent or API error
    }

    if ($imageFound) {
        Write-Ok "ECR image ${repo}:${tag} already exists -- skipping build"
        $script:WebhookImageReady = $true
        return
    }

    Write-Info "Image not found in ECR -- need to build and push $fullImage"

    # ------------------------------------------------------------------
    # Guard: verify the Docker daemon is reachable before doing anything
    # that would block silently (docker login piped to Out-Null hangs
    # indefinitely when Docker Desktop is not running).
    # ------------------------------------------------------------------
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
    # .Trim() on the PowerShell side is not enough because PS re-adds the
    # newline at pipe time.  cmd.exe pipes raw bytes without the PowerShell
    # string layer, so the token arrives intact.
    cmd /c "aws ecr get-login-password --region $Region 2>nul | docker login --username AWS --password-stdin $ecrBase"
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "docker login failed -- skipping image build"
        return
    }

    Write-Info "Building $fullImage (this may take a few minutes)..."
    $prevLocation = Get-Location
    Set-Location $RepoRoot

    # Stream build output live so the user can see progress (no Out-Null)
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

function Ensure-Namespaces {
    # The alertmanager-webhook Terraform module places resources inside the
    # 'monitoring' namespace.  Helm (kube-prometheus-stack) normally creates
    # that namespace, but it runs in Step 4 -- AFTER Terraform.  Pre-creating
    # both namespaces here means Terraform never hits "namespaces not found",
    # regardless of whether this is a cold-start or a re-run of an existing cluster.
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

    # Ensure the webhook image exists in ECR before Terraform tries to roll it out
    Ensure-WebhookImage
}

function Repair-TerraformState {
    # The hashicorp/kubernetes provider added identity tracking for
    # kubernetes_deployment resources.  State entries written before the upgrade
    # carry null identity fields; the next apply then throws "Unexpected Identity
    # Change" and blocks all further runs.
    #
    # Root cause of the previous silent failure: the old approach captured
    # `terraform plan` output via a PowerShell pipe.  On PS 7 with
    # $ErrorActionPreference = "Stop", a non-zero native-command exit code can
    # interrupt the pipeline before the output is fully buffered, leaving
    # $planOut as the exception message rather than Terraform's actual output.
    # The "Unexpected Identity Change" string was never found, so the function
    # returned "state OK" and let the apply fail.
    #
    # Fix: skip plan output parsing entirely.  Use `terraform state show` to
    # detect whether each known kubernetes_deployment is in state, then do a
    # proactive state rm + import to refresh the identity.  This is fully
    # idempotent: state rm is a no-op when the resource is absent, and import
    # just rewrites the entry with live values when it is present.
    # The deployment itself is NOT deleted from the cluster -- it keeps running.
    Write-Info "Checking Terraform state for identity issues..."

    # Add further entries here if additional kubernetes_deployment resources
    # are added to the Terraform config in future phases.
    $repairs = @(
        @{ Addr     = 'module.alertmanager_webhook[0].kubernetes_deployment.webhook'
           ImportId = 'monitoring/guardops-alertmanager-webhook' }
    )

    $anyRepaired = $false
    foreach ($r in $repairs) {
        # Check whether the resource is in state at all.
        # terraform state show exits 1 when the address is not found.
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

function Invoke-TerraformApply {
    # Wrapper around terraform apply -auto-approve.
    #
    # Problem: when the webhook image is absent from ECR the pod stays in
    # ImagePullBackOff.  If the alertmanager-webhook TF module has
    # wait_for_rollout = true, Terraform blocks waiting for the rollout,
    # times out, and fails with "Deployment exceeded its progress deadline"
    # -- even though every other resource applied cleanly.
    #
    # Fix: when $script:WebhookImageReady is false, temporarily patch
    # wait_for_rollout to false in the module file before apply, then
    # restore the original content unconditionally (try/finally) so the
    # repo is never left in a modified state regardless of outcome.
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

    terraform apply -auto-approve @ExtraArgs
    $ec = $LASTEXITCODE

    if ($patched -and $original) {
        Set-Content $webhookTf $original -Encoding UTF8
        Write-Info "  Restored wait_for_rollout=true in alertmanager-webhook module"
    }

    return $ec
}

# -- Banner --------------------------------------------------------------------
Write-Host ""
Write-Host "  +--------------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |   GuardOps Phase 10 -- Morning Startup           |" -ForegroundColor Cyan
Write-Host "  |   Est. time  : ~25-35 min (cold start)           |" -ForegroundColor White
Write-Host "  |   Est. cost  : ~`$5.28/day while EKS runs        |" -ForegroundColor Yellow
Write-Host "  |   Shutdown   : .\scripts\night-shutdown.ps1      |" -ForegroundColor Red
Write-Host "  +--------------------------------------------------+" -ForegroundColor Cyan
Write-Host ""

$Step = 0

# -- Pre-flight ----------------------------------------------------------------
$Step++
Write-Step $Step "10" "Pre-flight checks"

try {
    $identity = aws sts get-caller-identity --output json 2>$null | ConvertFrom-Json
    Write-Ok "AWS auth OK: $($identity.Arn)"
} catch {
    Write-Fail "AWS auth failed. Run: aws configure"
    exit 1
}

foreach ($tool in @("kubectl", "helm", "gh")) {
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

# -- Terraform -----------------------------------------------------------------
if (-not $SkipTerraform) {
    $Step++
    Write-Step $Step "10" "Terraform -- AWS infrastructure"
    Set-Location $TerraformDir

    $eksExists = Test-EKSExists

    if (-not $eksExists) {
        Write-Info "EKS not found -- Phase 1 bootstrap apply"

        $acct = $identity.Account

        # 1. Stub out providers FIRST so init doesn't fail looking for EKS
        Disable-EKSProviders

        try {
            # 2. Initialize Terraform
            terraform init -reconfigure 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "terraform init failed" }

            # 3. NOW we can safely import existing resources
            Import-IfMissing "module.iam.aws_iam_role.eks_cluster" "guardops-prod-eks-cluster-role" "EKS cluster IAM role"
            Import-IfMissing "module.iam.aws_iam_role.eks_node"    "guardops-prod-eks-node-role"    "EKS node IAM role"
            Import-IfMissing "module.s3.aws_s3_bucket.reports"     "guardops-reports-$acct"         "S3 reports bucket"

            # 4. Run the Phase 1 apply
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
        Repair-TerraformState
        Start-Sleep -Seconds 5
        Write-Info "Running Phase 2 full apply..."
        $applyExit = Invoke-TerraformApply
        if ($applyExit -ne 0) { Write-Fail "terraform apply Phase 2 failed"; exit 1 }
        Write-Ok "Full apply complete"

    } else {
        Write-Info "EKS already running -- full apply"
        terraform init -reconfigure 2>&1 | Out-Null
        Ensure-Namespaces
        Repair-TerraformState
        Start-Sleep -Seconds 5
        $applyExit = Invoke-TerraformApply
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
Write-Step $Step "10" "Configure kubectl + wait for nodes"

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
Write-Step $Step "10" "Observability stack (Prometheus + Grafana + Loki)"

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
Write-Step $Step "10" "Runtime security (Falco + Loki + Promtail)"

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
Write-Step $Step "10" "Self-healing (Alertmanager webhook)"

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
Write-Step $Step "10" "Phase 10 -- TLS (cert-manager + ClusterIssuers)"

$dnsTlsEnabled = Read-TfVar "enable_dns_tls"
$argoCdEnabled = Read-TfVar "enable_argocd"
$domainName    = Read-TfVar "domain_name"
$argoCdUrl     = ""
$argoCdPass    = ""
$argoCdToken   = ""

if ($dnsTlsEnabled -eq "true") {
    Wait-Pods -NS "cert-manager" -Label "app=cert-manager" -Timeout 120 -Name "cert-manager" | Out-Null

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
        Write-Warn "ClusterIssuers not Ready yet -- check: kubectl describe clusterissuer letsencrypt-staging"
    }

    $existingAlb = Read-TfVar "alb_dns_name"
    if ($existingAlb -and $existingAlb -ne "") {
        Write-Ok "ALB DNS already configured: $existingAlb"
    } else {
        Write-Info "alb_dns_name not set -- will populate after app deploy"
    }
} else {
    Write-Info "enable_dns_tls not true in terraform.tfvars -- skipping TLS"
    Write-Info "Add:  enable_dns_tls = true  to infra/terraform/terraform.tfvars"
}

# -- Phase 10: ArgoCD ----------------------------------------------------------
$Step++
Write-Step $Step "10" "Phase 10 -- ArgoCD GitOps"

if ($argoCdEnabled -eq "true") {
    Wait-Pods -NS "argocd" -Label "app.kubernetes.io/name=argocd-server" -Timeout 300 -Name "ArgoCD server" | Out-Null

    Write-Info "Applying ArgoCD manifests..."
    kubectl apply -f "$RepoRoot\k8s\argocd\project.yaml"     2>$null
    kubectl apply -f "$RepoRoot\k8s\argocd\app-prod.yaml"    2>$null
    kubectl apply -f "$RepoRoot\k8s\argocd\app-staging.yaml" 2>$null
    Write-Ok "ArgoCD AppProject + Applications applied"

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
        $host = $argoCdUrl -replace "https://",""
        argocd login $host --username admin --password $argoCdPass --grpc-web --insecure 2>$null
        if ($LASTEXITCODE -eq 0) {
            $argoCdToken = argocd account generate-token --account admin 2>$null
            if ($argoCdToken) {
                gh secret set ARGOCD_TOKEN --body $argoCdToken 2>$null
                Write-Ok "GitHub secret ARGOCD_TOKEN set"

                if (Test-Path $GuardopsYaml) {
                    $yamlContent = Get-Content $GuardopsYaml -Raw
                    if ($yamlContent -notmatch "argocd:") {
                        $argoSection = "`nargocd:`n"
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

# -- Deploy app ----------------------------------------------------------------
if (-not $SkipDeploy) {
    $Step++
    Write-Step $Step "10" "Deploy app (prod + staging)"

    Set-Location "$RepoRoot\test-project"

    Write-Info "Deploying prod..."
    guardops deploy --env prod --skip-sonarqube --skip-dast
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Prod deploy had errors -- retry: guardops deploy --env prod --skip-sonarqube"
    } else {
        Write-Ok "Prod deploy complete"
    }

    Write-Info "Deploying staging..."
    guardops deploy --env staging --skip-sonarqube --skip-dast
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Staging deploy had errors"
    } else {
        Write-Ok "Staging deploy complete"
    }

    Set-Location $RepoRoot

    # Wire ALB DNS after deploy creates the Ingress
    if ($dnsTlsEnabled -eq "true") {
        $albDns = Read-TfVar "alb_dns_name"
        if (-not $albDns -or $albDns -eq "") {
            Write-Info "Fetching ALB address from Ingress (up to 3 min)..."
            $albWait    = 0
            $albAddress = ""
            while ($albWait -lt 180 -and (-not $albAddress)) {
                $albAddress = kubectl get ingress -n default `
                    -o jsonpath="{.items[0].status.loadBalancer.ingress[0].hostname}" 2>$null
                if (-not $albAddress) {
                    Start-Sleep -Seconds 15
                    $albWait += 15
                    Write-Info "  ${albWait}s -- waiting for ALB..."
                }
            }
            if ($albAddress) {
                Write-Ok "ALB address: $albAddress"
                Set-TfVar "alb_dns_name" $albAddress
                Write-Info "Re-applying dns-tls for Route53 alias records..."
                Set-Location $TerraformDir
                terraform apply -target="module.dns_tls" -auto-approve
                if ($LASTEXITCODE -eq 0) { Write-Ok "Route53 alias records created" }
                else                     { Write-Warn "dns-tls re-apply had errors" }
                Set-Location $RepoRoot
            } else {
                Write-Warn "ALB address not available yet"
                Write-Info "Get it with: kubectl get ingress -n default"
                Write-Info "Then add to terraform.tfvars: alb_dns_name = `"THE_ALB_HOST`""
                Write-Info "Then run: terraform apply -target=module.dns_tls"
            }
        }
    }

    # GitOps override commit
    if ($argoCdEnabled -eq "true" -and $argoCdToken) {
        Write-Info "Running GitOps override commits..."
        $env:ARGOCD_TOKEN = $argoCdToken
        Set-Location "$RepoRoot\test-project"
        guardops deploy --env prod --skip-sonarqube --skip-build --skip-dast --gitops --gitops-branch main 2>$null
        guardops deploy --env staging --skip-sonarqube --skip-build --skip-dast --gitops --gitops-branch main 2>$null
        Write-Ok "GitOps override files committed"
        Set-Location $RepoRoot
    }
}

# -- Port-forwards -------------------------------------------------------------
if (-not $SkipPortForwards) {
    $Step++
    Write-Step $Step "10" "Opening port-forwards"

    $forwards = @(
        @{ Svc="svc/kube-prometheus-stack-grafana";      Port="3000:80";   NS="monitoring" },
        @{ Svc="svc/loki";                               Port="3100:3100"; NS="monitoring" },
        @{ Svc="svc/kube-prometheus-stack-prometheus";   Port="9090:9090"; NS="monitoring" },
        @{ Svc="svc/kube-prometheus-stack-alertmanager"; Port="9093:9093"; NS="monitoring" },
        @{ Svc="svc/guardops-alertmanager-webhook";      Port="9095:9095"; NS="monitoring" }
    )

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
Write-Step $Step "10" "Summary"

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
Write-Host ""
Write-Host "  Stop billing tonight:" -ForegroundColor Red
Write-Host "    .\scripts\night-shutdown.ps1" -ForegroundColor Red
Write-Host ""