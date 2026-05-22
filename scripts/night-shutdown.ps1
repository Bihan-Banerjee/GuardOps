# scripts/night-shutdown.ps1
#
# GuardOps Phase 7 — Nightly Shutdown

$ErrorActionPreference = "Stop"
$Region       = "ap-south-1"
$RepoRoot     = "D:\EXTRA\GuardOps"
$TerraformDir = "$RepoRoot\infra\terraform"
$Namespace    = "monitoring"

Write-Host ""
Write-Host "======================================" -ForegroundColor Yellow
Write-Host "  GuardOps Phase 7 - Night Shutdown   " -ForegroundColor Yellow
Write-Host "  Destroys : EKS, VPC, NAT, IAM       " -ForegroundColor Red
Write-Host "  Preserves: ECR, S3, DynamoDB        " -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Yellow
Write-Host ""

$Confirm = Read-Host "Type YES to proceed"
if ($Confirm -ne "YES") {
    Write-Host "Cancelled." -ForegroundColor Green
    exit 0
}

# Step 1: Uninstall Helm releases
Write-Host ""
Write-Host "[1/5] Uninstalling Helm releases..." -ForegroundColor Green

$HelmReleases = @(
    @{ Name = "kube-prometheus-stack"; Namespace = $Namespace },
    @{ Name = "loki"; Namespace = $Namespace },
    @{ Name = "promtail"; Namespace = $Namespace },
    @{ Name = "falco"; Namespace = $Namespace },
    @{ Name = "guardops-app"; Namespace = "default" }
)

foreach ($Release in $HelmReleases) {
    $Exists = helm status $Release.Name -n $Release.Namespace 2>$null

    if ($Exists) {
        Write-Host "  Uninstalling $($Release.Name)..." -ForegroundColor Gray
        helm uninstall $Release.Name -n $Release.Namespace
        Write-Host "  $($Release.Name) uninstalled" -ForegroundColor Green
    }
    else {
        Write-Host "  $($Release.Name) not installed - skipping" -ForegroundColor Gray
    }
}

# Step 2: Delete PVCs
Write-Host ""
Write-Host "[2/5] Deleting PVCs (releases EBS volumes)..." -ForegroundColor Green

$PVCs = kubectl get pvc -n $Namespace --no-headers 2>$null

if ($PVCs) {
    kubectl delete pvc --all -n $Namespace
    Write-Host "  All PVCs in $Namespace deleted" -ForegroundColor Green
}
else {
    Write-Host "  No PVCs found in $Namespace" -ForegroundColor Gray
}

Write-Host "  Waiting 30s for EBS volumes to detach..." -ForegroundColor Gray
Start-Sleep -Seconds 30

# Step 3: Scale node group to 0
Write-Host ""
Write-Host "[3/5] Scaling node group to 0..." -ForegroundColor Green

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

        Write-Host "  Node group scaling to 0 (async - destroy will wait)" -ForegroundColor Green
    }
    else {
        Write-Host "  No node group found - may already be destroyed" -ForegroundColor Gray
    }
}
else {
    Write-Host "  Could not read cluster name from Terraform output - skipping scale-down" -ForegroundColor Yellow
}

# Step 4: Terraform destroy
Write-Host ""
Write-Host "[4/5] Running terraform destroy (10-15 min - do not close this window)..." -ForegroundColor Green
Write-Host ""

terraform destroy -auto-approve

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "terraform destroy reported errors - check output above." -ForegroundColor Red
    Write-Host "Verify in AWS Console that EKS cluster and NAT Gateways are gone." -ForegroundColor Red
    Write-Host "You may need to run terraform destroy again." -ForegroundColor Red
    exit 1
}

# Step 5: Verify cleanup
Write-Host ""
Write-Host "[5/5] Verifying cleanup..." -ForegroundColor Green

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

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "  Shutdown complete                   " -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green

if ($RunningInstances) {
    Write-Host "  EC2 running    : $RunningInstances" -ForegroundColor Red
}
else {
    Write-Host "  EC2 instances  : NONE (good)" -ForegroundColor Green
}

if ($ActiveNatGateways) {
    Write-Host "  NAT Gateways   : $ActiveNatGateways" -ForegroundColor Red
}
else {
    Write-Host "  NAT Gateways   : NONE (good)" -ForegroundColor Green
}

if ($ActiveEBS) {
    Write-Host "  EBS in-use     : $ActiveEBS" -ForegroundColor Yellow
}
else {
    Write-Host "  EBS volumes    : NONE in-use (good)" -ForegroundColor Green
}

Write-Host "  Billing        : ~`$0.00/hr until morning-start.ps1" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""