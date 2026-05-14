# scripts/night-shutdown.ps1
#
# Run this EVERY EVENING before you stop working.
# Destroys VPC + IAM + EKS. Keeps ECR + S3 (free tier, safe to leave).
#
# After destroy: $0.00/hour. ECR and S3 cost pennies/month at this scale.
#
# Usage: .\scripts\night-shutdown.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "======================================" -ForegroundColor Yellow
Write-Host "  GuardOps — Night Shutdown" -ForegroundColor Yellow
Write-Host "  This will DESTROY EKS, VPC, IAM." -ForegroundColor Red
Write-Host "  ECR and S3 are preserved." -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Yellow
Write-Host ""

# Safety confirmation
$Confirm = Read-Host "Type YES to proceed with destroy"
if ($Confirm -ne "YES") {
    Write-Host "Cancelled." -ForegroundColor Green
    exit 0
}

Set-Location "D:\EXTRA\GuardOps\infra\terraform"

# ── Step 1: Scale node group to 0 first (faster than waiting for EKS destroy) ─
Write-Host ""
Write-Host "[1/2] Scaling node group to 0 to speed up destroy..." -ForegroundColor Green

$ClusterName = terraform output -raw eks_cluster_name 2>$null
$Region = "ap-south-1"

if ($ClusterName) {
    # Get node group name and scale to 0
    $NodeGroup = aws eks list-nodegroups --cluster-name $ClusterName --region $Region `
        --query "nodegroups[0]" --output text 2>$null

    if ($NodeGroup -and $NodeGroup -ne "None") {
        aws eks update-nodegroup-config `
            --cluster-name $ClusterName `
            --nodegroup-name $NodeGroup `
            --scaling-config minSize=0,maxSize=1,desiredSize=0 `
            --region $Region | Out-Null
        Write-Host "  Node group scaling to 0 — terraform destroy will finish the rest."
    }
}

# ── Step 2: Terraform destroy ─────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/2] Running terraform destroy..." -ForegroundColor Green
Write-Host "  This takes 10-15 minutes. Do not close this window." -ForegroundColor Yellow
Write-Host ""

terraform destroy -auto-approve
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "terraform destroy had errors. Check above output." -ForegroundColor Red
    Write-Host "Manually verify in AWS console that EKS and NAT Gateway are gone." -ForegroundColor Red
    exit 1
}

# ── Verify nothing is running ─────────────────────────────────────────────────
Write-Host ""
Write-Host "Verifying no EC2 instances or NAT Gateways remain..." -ForegroundColor Green

$Instances = aws ec2 describe-instances --region $Region `
    --filters "Name=instance-state-name,Values=running" `
    --query "Reservations[].Instances[].InstanceId" `
    --output text 2>$null

$NatGateways = aws ec2 describe-nat-gateways --region $Region `
    --filter "Name=state,Values=available" `
    --query "NatGateways[].NatGatewayId" `
    --output text 2>$null

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "  Shutdown complete" -ForegroundColor Green
if ($Instances) {
    Write-Host "  WARNING: EC2 instances still running: $Instances" -ForegroundColor Red
    Write-Host "  Check AWS Console → EC2 and terminate manually!" -ForegroundColor Red
} else {
    Write-Host "  EC2 instances: NONE (good)" -ForegroundColor Green
}
if ($NatGateways) {
    Write-Host "  WARNING: NAT Gateways still active: $NatGateways" -ForegroundColor Red
    Write-Host "  Check AWS Console → VPC → NAT Gateways and delete manually!" -ForegroundColor Red
} else {
    Write-Host "  NAT Gateways: NONE (good)" -ForegroundColor Green
}
Write-Host "  Billing: `$0.00/hour until morning-start.ps1" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
