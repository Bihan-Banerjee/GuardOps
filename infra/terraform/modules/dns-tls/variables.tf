# infra/terraform/modules/dns-tls/variables.tf

variable "project_name" {
  description = "Short project name used as a tag on Route53 resources."
  type        = string
}

variable "aws_region" {
  description = "AWS region — used for the ALB controller."
  type        = string
  default     = "ap-south-1"
}

variable "domain_name" {
  description = "Root domain for the Route53 hosted zone. Example: guardops.live"
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name — passed to the AWS Load Balancer Controller."
  type        = string
}

variable "alb_controller_role_arn" {
  description = "IAM role ARN for the AWS Load Balancer Controller IRSA. Created by the iam module (alb_controller_role_arn output). Example: arn:aws:iam::123456789012:role/guardops-alb-controller"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for the AWS Load Balancer Controller subnet auto-discovery."
  type        = string
}

variable "alb_dns_name" {
  description = "DNS name of the ALB provisioned by the nginx Ingress controller. Only available after the first Helm deploy creates an Ingress. Leave empty on first apply. Get the value with: kubectl get ingress -n default -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}' then set here and re-apply."
  type        = string
  default     = ""
}

variable "alb_hosted_zone_id" {
  description = "ELB hosted zone ID for the ALB alias record. Region-specific value maintained by AWS — NOT your Route53 hosted zone ID. ap-south-1: ZP97RAFLXTNZK | us-east-1: Z35SXDOTRQ7X7K | eu-west-1: Z32O12XQLNTSW2. Full list: https://docs.aws.amazon.com/general/latest/gr/elb.html"
  type        = string
  default     = "ZP97RAFLXTNZK"
}

variable "enable_cert_manager_metrics" {
  description = "Enable cert-manager Prometheus metrics. Requires kube-prometheus-stack (Phase 5) to be running. Set to true after the Phase 5 observability stack is deployed."
  type        = bool
  default     = false
}
