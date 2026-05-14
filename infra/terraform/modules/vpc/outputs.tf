# infra/terraform/modules/vpc/outputs.tf

output "vpc_id" {
  description = "VPC ID. Passed to the EKS module."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "List of private subnet IDs. EKS nodes live here."
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "List of public subnet IDs. Load balancers and NAT gateways live here."
  value       = aws_subnet.public[*].id
}
