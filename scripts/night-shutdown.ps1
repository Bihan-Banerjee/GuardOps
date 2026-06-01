# scripts/night-shutdown.ps1
#
# GuardOps Phase 11 -- Nightly Shutdown
#
# Safely destroys all billable AWS resources while preserving ECR images,
# S3 reports, and DynamoDB state so the next morning-start.ps1 can restore
# the full stack in ~30-40 minutes.
#
# ORDER MATTERS:
#   1. Delete Ingress objects FIRST -- this signals the ALB controller to
#      delete the AWS Application Load Balancers.  If the ALBs still have
#      ENIs in the VPC subnets when terraform destroy runs, AWS will refuse
#      to delete the subnets and the whole destroy will fail.
#   2. Wait for ALBs to disappear from AWS before uninstalling Helm.
#   3. Delete Kyverno ClusterPolicies + uninstall all Helm releases (Kyverno
#      first so its admission webhooks are gone before teardown).
#   4. Delete PVCs to release EBS volumes before terraform destroy.
#   5. Scale node group to 0 (speeds up destroy -- nodes drain faster).
#   6. terraform destroy -- removes EKS, VPC, NAT gateways, IAM, Route53.
#   7. Clear alb_dns_name from tfvars -- ALB hostname changes on recreate;
#      morning-start.ps1 will discover and write the new one automatically.
#   8. Verify cleanup -- confirm no billable resources remain.
#
# What is NOT destroyed (intentional):
#   - ECR images          : avoids Docker rebuild on every cold start
#   - S3 reports bucket   : test result history
#   - GitHub OIDC provider + CI role : keeps CI (build/scan/push/sign) working
#                           while the cluster is down (detached from state, re-
#                           imported by morning-start.ps1)
#   - DynamoDB table      : Terraform state lock
#   - IAM OIDC provider   : morning-start.ps1 will create a new one for
#                           the new cluster if the OIDC URL changed
#   - guardops-alb-controller IAM role + policy : morning-start.ps1 will
#                           update the trust policy to match the new OIDC URL

$ErrorActionPreference = "Stop"

$Region       = "ap-south-1"
# Repo root = parent of the scripts/ directory this file lives in.
$RepoRoot     = Split-Path -Parent $PSScriptRoot
$TerraformDir = "$RepoRoot\infra\terraform"
$TfVarsFile   = "$TerraformDir\terraform.tfvars"

Write-Host ""
Write-Host "  +============================================+" -ForegroundColor Yellow
Write-Host "  |   GuardOps Phase 10 -- Night Shutdown     |" -ForegroundColor Yellow
Write-Host "  |   Destroys : EKS, VPC, NAT, IAM, Route53 |" -ForegroundColor Red
Write-Host "  |   Preserves: ECR, S3, DynamoDB, IAM role  |" -ForegroundColor Green
Write-Host "  +============================================+" -ForegroundColor Yellow
Write-Host ""

$Confirm = Read-Host "Type YES to proceed"
if ($Confirm -ne "YES") {
    Write-Host "Cancelled." -ForegroundColor Green
    exit 0
}

function Write-ShutdownStep {
    param([string]$Num, [string]$Total, [string]$Text)
    Write-Host ""
    Write-Host "  [$Num/$Total] $Text" -ForegroundColor Cyan
    Write-Host "  $('-' * 48)" -ForegroundColor DarkGray
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
    if (-not (Test-Path $TfVarsFile)) { return }
    $content = Get-Content $TfVarsFile -Raw
    $newLine = "$VarName = `"$Value`""
    if ($content -match "(?m)^\s*$VarName\s*=") {
        $content = $content -replace "(?m)^\s*$VarName\s*=.*$", $newLine
    } else {
        $content = $content.TrimEnd() + "`n$newLine`n"
    }
    Set-Content $TfVarsFile $content
}

# Verify kubectl is pointed at the right cluster before doing anything
$clusterOk = $false
try {
    $ctx = kubectl config current-context 2>$null
    if ($ctx) {
        aws eks update-kubeconfig --region $Region --name "guardops-prod-cluster" 2>&1 | Out-Null
        $clusterOk = ($LASTEXITCODE -eq 0)
    }
} catch { }

if (-not $clusterOk) {
    Write-Host "  >> kubectl not connected to cluster -- Helm/Ingress steps will be skipped" -ForegroundColor Yellow
    Write-Host "  >> Proceeding to terraform destroy..." -ForegroundColor Yellow
}

# ── Step 1/8: Delete Ingress + wait for ALB deletion ─────────────────────────
Write-ShutdownStep "1" "8" "Delete Ingress objects -- drain ALBs from AWS"

if ($clusterOk) {
    # Delete Ingress objects in all app namespaces.
    # The ALB controller watches for Ingress deletions and removes the
    # corresponding AWS Application Load Balancers automatically.
    $ingressCount = 0
    foreach ($ns in @("default", "staging")) {
        # try/catch: with $ErrorActionPreference=Stop, kubectl's "No resources
        # found" stderr otherwise terminates the whole script before destroy.
        $ingresses = $null
        try { $ingresses = kubectl get ingress -n $ns --no-headers 2>$null } catch { }
        if ($ingresses) {
            kubectl delete ingress --all -n $ns 2>$null | Out-Null
            $lines = ($ingresses | Measure-Object -Line).Lines
            Write-Host "    Deleted $lines Ingress object(s) in namespace '$ns'" -ForegroundColor Gray
            $ingressCount += $lines
        } else {
            Write-Host "    No Ingress objects in namespace '$ns'" -ForegroundColor Gray
        }
    }

    if ($ingressCount -gt 0) {
        # Poll until the ALB is fully deleted from AWS.
        # Skipping this wait is the most common cause of terraform destroy failures:
        # AWS refuses to delete a subnet that has an active ENI attached to an ALB.
        Write-Host "    Waiting for ALBs to be removed from AWS (up to 5 min)..." -ForegroundColor Gray
        $albWait = 0
        $albGone = $false
        while ($albWait -lt 300) {
            $albs = $null
            try {
                # Find any ALBs whose name starts with 'k8s-' (ALB controller naming convention)
                $albs = aws elbv2 describe-load-balancers --region $Region `
                    --query "LoadBalancers[?starts_with(LoadBalancerName,'k8s-')].LoadBalancerName" `
                    --output text 2>$null
            } catch { }
            if (-not $albs -or $albs.Trim() -eq "") {
                $albGone = $true
                break
            }
            Start-Sleep -Seconds 10
            $albWait += 10
            Write-Host "    ..  ${albWait}s -- ALBs still draining: $($albs.Trim())" -ForegroundColor Gray
        }
        if ($albGone) {
            Write-Host "    OK  ALBs deleted from AWS" -ForegroundColor Green
        } else {
            Write-Host "    >>  ALBs not fully deleted after 5 min -- proceeding anyway" -ForegroundColor Yellow
            Write-Host "        If terraform destroy fails on subnet deletion, run it again" -ForegroundColor Yellow
        }
    } else {
        Write-Host "    OK  No Ingress objects found -- no ALBs to drain" -ForegroundColor Green
    }
} else {
    Write-Host "    >>  Skipped (kubectl not connected)" -ForegroundColor Yellow
}

# ── Step 2/8: Uninstall Helm releases ────────────────────────────────────────
Write-ShutdownStep "2" "8" "Uninstall Helm releases"

# Order matters: uninstall app workloads before the controllers that manage them.
# cert-manager must come AFTER guardops-app (which has the Certificate resource).
# aws-load-balancer-controller comes last in kube-system (after Ingress is gone).
$HelmReleases = @(
    # Kyverno FIRST -- remove the admission webhooks before tearing down workloads
    # so a failurePolicy:Fail verify policy can't interfere with deletes/finalizers.
    @{ Name = "kyverno";                     Namespace = "kyverno"      },
    # App workloads next
    @{ Name = "guardops-app";                Namespace = "default"      },
    # NOTE: staging releases (guardops-app-staging and any blue/green slot
    # releases) are uninstalled dynamically below — see the staging sweep.
    # Observability stack
    @{ Name = "kube-prometheus-stack";       Namespace = "monitoring"   },
    @{ Name = "loki";                        Namespace = "monitoring"   },
    @{ Name = "promtail";                    Namespace = "monitoring"   },
    @{ Name = "falco";                       Namespace = "monitoring"   },
    # GitOps
    @{ Name = "argocd";                      Namespace = "argocd"       },
    # TLS / cert-manager (after app so Certificate objects are gone first)
    @{ Name = "cert-manager";                Namespace = "cert-manager" },
    # ALB controller last (Ingress already deleted and ALB drained in Step 1)
    @{ Name = "aws-load-balancer-controller"; Namespace = "kube-system" }
)

if ($clusterOk) {
    # Phase 11: remove Kyverno ClusterPolicies first so the admission/mutating
    # webhooks are deregistered before we start tearing workloads down.
    Write-Host "    Removing Kyverno ClusterPolicies (clears admission webhooks)..." -ForegroundColor Gray
    try { kubectl delete clusterpolicy -l app.kubernetes.io/managed-by=guardops --ignore-not-found 2>$null | Out-Null } catch { }

    # Blue-green deploys create extra slot releases in the staging namespace
    # (guardops-app-staging-blue / -green) that aren't in the static list above.
    # Enumerate and uninstall every release in 'staging' first so no app
    # workload is left holding PVCs/ENIs when terraform destroy runs.
    $stagingReleases = @()
    try { $stagingReleases = helm list -n staging -q 2>$null } catch { }
    foreach ($rel in $stagingReleases) {
        if ([string]::IsNullOrWhiteSpace($rel)) { continue }
        Write-Host "    Uninstalling $rel -n staging ..." -ForegroundColor Gray
        try {
            helm uninstall $rel -n staging --timeout 120s 2>$null | Out-Null
            Write-Host "    OK  $rel uninstalled" -ForegroundColor Green
        } catch {
            Write-Host "    >>  $rel uninstall had errors -- continuing" -ForegroundColor Yellow
        }
    }

    foreach ($Release in $HelmReleases) {
        $releaseKey = "$($Release.Name) -n $($Release.Namespace)"
        $exists = $null
        try { $exists = helm status $Release.Name -n $Release.Namespace 2>$null } catch { }

        if ($exists) {
            Write-Host "    Uninstalling $releaseKey ..." -ForegroundColor Gray
            try {
                helm uninstall $Release.Name -n $Release.Namespace --timeout 120s 2>$null | Out-Null
                Write-Host "    OK  $($Release.Name) uninstalled" -ForegroundColor Green
            } catch {
                Write-Host "    >>  $($Release.Name) uninstall had errors -- continuing" -ForegroundColor Yellow
            }
        } else {
            Write-Host "    --  $releaseKey not installed" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "    >>  Skipped (kubectl not connected)" -ForegroundColor Yellow
}

# ── Step 3/8: Delete PVCs (releases EBS volumes) ─────────────────────────────
Write-ShutdownStep "3" "8" "Delete PVCs -- release EBS volumes"

if ($clusterOk) {
    foreach ($ns in @("monitoring", "default", "staging")) {
        $pvcs = $null
        try { $pvcs = kubectl get pvc -n $ns --no-headers 2>$null } catch { }
        if ($pvcs) {
            kubectl delete pvc --all -n $ns 2>$null | Out-Null
            Write-Host "    OK  PVCs deleted in namespace '$ns'" -ForegroundColor Green
        } else {
            Write-Host "    --  No PVCs in namespace '$ns'" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "    >>  Skipped (kubectl not connected)" -ForegroundColor Yellow
}

Write-Host "    Waiting 30s for EBS volumes to detach..." -ForegroundColor Gray
Start-Sleep -Seconds 30

# ── Step 4/8: Scale node group to 0 ──────────────────────────────────────────
Write-ShutdownStep "4" "8" "Scale EKS node group to 0"

Set-Location $TerraformDir

$ClusterName = terraform output -raw eks_cluster_name 2>$null

if ($ClusterName) {
    $NodeGroup = aws eks list-nodegroups `
        --cluster-name $ClusterName `
        --region $Region `
        --query "nodegroups[0]" `
        --output text 2>$null

    if ($NodeGroup -and $NodeGroup -ne "None") {
        aws eks update-nodegroup-config `
            --cluster-name $ClusterName `
            --nodegroup-name $NodeGroup `
            --scaling-config minSize=0,maxSize=1,desiredSize=0 `
            --region $Region | Out-Null
        Write-Host "    OK  Node group scaling to 0 (async -- destroy will wait)" -ForegroundColor Green
    } else {
        Write-Host "    --  No node group found -- may already be destroyed" -ForegroundColor Gray
    }
} else {
    Write-Host "    >>  Could not read cluster name from Terraform output -- skipping" -ForegroundColor Yellow
}

# ── Step 5/8: terraform destroy ──────────────────────────────────────────────
Write-ShutdownStep "5" "8" "terraform destroy  (10-20 min -- do not close this window)"
Write-Host ""

# Preserve the Route53 hosted zone so the delegated nameservers survive the
# teardown. Without this, terraform deletes the zone and the next startup creates
# a NEW one with different NS, forcing re-delegation at the registrar.
# morning-start.ps1 re-imports the zone on the next run. Idempotent: safe if the
# zone was already detached or DNS/TLS is disabled.
$dnsTlsEnabled = Read-TfVar "enable_dns_tls"
if ($dnsTlsEnabled -eq "true") {
    Write-Host "    Preserving Route53 zone (detaching from Terraform state)..." -ForegroundColor Gray
    try { terraform state rm "module.dns_tls[0].aws_route53_zone.guardops" 2>&1 | Out-Null } catch { }
}

# Phase 6/11: preserve the GitHub Actions OIDC provider + CI role + inline policy
# across the nightly destroy so CI (build, scan, ECR push, cosign signing) keeps
# working while the cluster is down. The GitHub OIDC provider is an account-level
# singleton -- destroying it nightly is what causes the CI error
# "No OpenIDConnect provider found ... token.actions.githubusercontent.com".
# morning-start.ps1 re-imports these on the next apply (Import-GithubOidc).
Write-Host "    Preserving GitHub OIDC provider + CI role (detaching from state)..." -ForegroundColor Gray
foreach ($addr in @(
    "module.iam_oidc.aws_iam_role_policy.ci_policy",
    "module.iam_oidc.aws_iam_role.github_actions",
    "module.iam_oidc.aws_iam_openid_connect_provider.github"
)) {
    try { terraform state rm $addr 2>&1 | Out-Null } catch { }
}

# Drop cluster-resident resources from state before destroy. Step 2 already
# `helm uninstall`-ed the releases and the rest vanish with the EKS cluster, but
# Terraform's helm/kubernetes providers lose their cluster connection once EKS is
# mid-destroy ("Kubernetes cluster unreachable: no configuration has been
# provided"), which aborts the whole teardown. Removing them lets destroy handle
# only AWS infra; morning-start.ps1 recreates them on the fresh cluster.
# try/catch + the [0] index make absent modules (disabled features) harmless.
Write-Host "    Detaching cluster-resident resources (helm/k8s) from state..." -ForegroundColor Gray
foreach ($addr in @(
    "module.dns_tls[0].helm_release.aws_load_balancer_controller",
    "module.dns_tls[0].helm_release.cert_manager",
    "module.alertmanager_webhook[0]",
    "module.argocd[0]",
    "module.kyverno[0].helm_release.kyverno",
    "module.falco[0]"
)) {
    try { terraform state rm $addr 2>&1 | Out-Null } catch { }
}

terraform destroy -auto-approve

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  !! terraform destroy reported errors -- check output above." -ForegroundColor Red
    Write-Host "     Common causes:" -ForegroundColor Red
    Write-Host "       - ALB ENIs still in subnets (run night-shutdown again after ~5 min)" -ForegroundColor Red
    Write-Host "       - S3 bucket not empty (aws s3 rb s3://guardops-reports-ACCTID --force)" -ForegroundColor Red
    Write-Host "     Verify in AWS Console that EKS cluster and NAT Gateways are gone." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "    OK  terraform destroy complete" -ForegroundColor Green

# ── Step 6/8: Clean up cert-manager CRDs (terraform doesn't remove them) ─────
Write-ShutdownStep "6" "8" "Remove cert-manager CRDs (not managed by Terraform)"

if ($clusterOk) {
    # cert-manager leaves its CRDs behind after Helm uninstall.
    # If left, the next morning-start will see CRDs from the previous session
    # and cert-manager may fail to reconcile properly.
    # This step is best-effort: kubectl won't be connected after destroy, so
    # we do it before destroy in practice. Leaving this here as a reminder --
    # if you see cert-manager issues on next startup, run manually first:
    #   kubectl delete crd -l app.kubernetes.io/name=cert-manager
    Write-Host "    ..  cert-manager CRDs cleanup is handled by Helm uninstall in Step 2" -ForegroundColor Gray
    Write-Host "    ..  If cert-manager fails on next startup, run:" -ForegroundColor Gray
    Write-Host "        kubectl delete crd -l app.kubernetes.io/name=cert-manager" -ForegroundColor Gray
} else {
    Write-Host "    ..  Skipped (kubectl not connected after destroy)" -ForegroundColor Gray
}

# ── Step 7/8: Clear stale ALB DNS from tfvars ─────────────────────────────────
Write-ShutdownStep "7" "8" "Clear stale ALB hostname from terraform.tfvars"

# After destroy + recreate, the ALB controller creates a NEW load balancer with
# a DIFFERENT hostname.  If alb_dns_name still holds the old hostname, terraform
# apply would create a Route53 alias pointing at a non-existent ALB.
# Clearing it here forces morning-start.ps1 to wait for the new Ingress address
# and update Route53 with the correct value.
if (Test-Path $TfVarsFile) {
    $currentAlb = Read-TfVar "alb_dns_name"
    if ($currentAlb -and $currentAlb -ne "") {
        Set-TfVar "alb_dns_name" ""
        Write-Host "    OK  Cleared alb_dns_name (was: $currentAlb)" -ForegroundColor Green
        Write-Host "        morning-start.ps1 will discover and set the new ALB address" -ForegroundColor Gray
    } else {
        Write-Host "    --  alb_dns_name already empty" -ForegroundColor Gray
    }
} else {
    Write-Host "    >>  terraform.tfvars not found at $TfVarsFile" -ForegroundColor Yellow
}

# ── Step 8/8: Verify cleanup ─────────────────────────────────────────────────
Write-ShutdownStep "8" "8" "Verify cleanup"

$RunningInstances = aws ec2 describe-instances `
    --region $Region `
    --filters "Name=instance-state-name,Values=running" `
    --query "Reservations[].Instances[].InstanceId" `
    --output text 2>$null

$ActiveNatGateways = aws ec2 describe-nat-gateways `
    --region $Region `
    --filter "Name=state,Values=available" `
    --query "NatGateways[].NatGatewayId" `
    --output text 2>$null

$ActiveEBS = aws ec2 describe-volumes `
    --region $Region `
    --filters "Name=status,Values=in-use" `
    --query "Volumes[].VolumeId" `
    --output text 2>$null

$ActiveAlbs = aws elbv2 describe-load-balancers `
    --region $Region `
    --query "LoadBalancers[?starts_with(LoadBalancerName,'k8s-')].LoadBalancerName" `
    --output text 2>$null

Write-Host ""
Write-Host "  +============================================+" -ForegroundColor Green
Write-Host "  |   Shutdown complete                       |" -ForegroundColor Green
Write-Host "  +============================================+" -ForegroundColor Green
Write-Host ""

if ($RunningInstances) {
    Write-Host "  EC2 instances  : $RunningInstances" -ForegroundColor Red
    Write-Host "                   (unexpected -- check AWS console)" -ForegroundColor Red
} else {
    Write-Host "  EC2 instances  : NONE  (good)" -ForegroundColor Green
}

if ($ActiveNatGateways) {
    Write-Host "  NAT Gateways   : $ActiveNatGateways" -ForegroundColor Red
    Write-Host "                   (still deleting -- costs ~`$0.045/hr each)" -ForegroundColor Red
} else {
    Write-Host "  NAT Gateways   : NONE  (good)" -ForegroundColor Green
}

if ($ActiveEBS) {
    Write-Host "  EBS in-use     : $ActiveEBS" -ForegroundColor Yellow
    Write-Host "                   (may still be detaching -- recheck in 5 min)" -ForegroundColor Yellow
} else {
    Write-Host "  EBS volumes    : NONE in-use  (good)" -ForegroundColor Green
}

if ($ActiveAlbs) {
    Write-Host "  ALBs remaining : $ActiveAlbs" -ForegroundColor Red
    Write-Host "                   (still draining -- wait 5 min, then rerun)" -ForegroundColor Red
} else {
    Write-Host "  ALBs           : NONE  (good)" -ForegroundColor Green
}

Write-Host ""
Write-Host "  Preserved (zero cost):" -ForegroundColor Cyan
Write-Host "    ECR images, S3 reports, DynamoDB state"
Write-Host "    guardops-alb-controller IAM role + policy"
Write-Host "    (trust policy will be refreshed on next morning-start.ps1)"
Write-Host ""
Write-Host "  Billing        : ~`$0.00/hr until morning-start.ps1" -ForegroundColor Green
Write-Host ""
Write-Host "  Next morning:" -ForegroundColor Cyan
Write-Host "    .\scripts\morning-start.ps1" -ForegroundColor Cyan
Write-Host ""