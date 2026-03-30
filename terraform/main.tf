# terraform/main.tf
# ───────────────────
# Provisions the EKS cluster for Smart Multi-Cloud Workload Migrator
#
# Account : 408258691668
# Region  : ap-south-1 (Mumbai)
#
# Usage:
#   cd ~/smart-migrator/terraform
#   terraform init
#   terraform apply

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── Variables ────────────────────────────────────────────────────────────────
variable "aws_region"   { default = "ap-south-1" }
variable "cluster_name" { default = "smart-migrator-cluster" }
variable "eks_version"  { default = "1.29" }

# ── VPC ──────────────────────────────────────────────────────────────────────
# ap-south-1 has 3 AZs: a, b, c
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.1.0"

  name = "${var.cluster_name}-vpc"
  cidr = "10.0.0.0/16"

  # Using ap-south-1a and ap-south-1b (both always available)
  azs             = ["ap-south-1a", "ap-south-1b"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway = true
  single_nat_gateway = true   # costs ~$32/month — set false for production HA

  tags = {
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
    Project = "smart-migrator"
    Region  = "ap-south-1"
  }

  public_subnet_tags = {
    "kubernetes.io/role/elb"          = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}

# ── EKS Cluster ──────────────────────────────────────────────────────────────
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "20.0.0"

  cluster_name    = var.cluster_name
  cluster_version = var.eks_version

  vpc_id                         = module.vpc.vpc_id
  subnet_ids                     = module.vpc.private_subnets
  cluster_endpoint_public_access = true

  # t3.medium is available in ap-south-1 and is cost-effective for research
  eks_managed_node_groups = {
    default = {
      instance_types = ["t3.medium"]
      min_size       = 2
      max_size       = 5
      desired_size   = 2
      labels = {
        cloud  = "aws"
        region = "ap-south-1"
      }
    }
  }

  cluster_addons = {
    aws-ebs-csi-driver = { most_recent = true }
    coredns            = { most_recent = true }
    kube-proxy         = { most_recent = true }
    vpc-cni            = { most_recent = true }
  }

  tags = {
    Project = "smart-migrator"
    Cloud   = "aws"
    Region  = "ap-south-1"
  }
}

# ── CloudWatch Log Group ──────────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "migrator" {
  name              = "/aws/eks/${var.cluster_name}/migrator"
  retention_in_days = 14
  tags = { Project = "smart-migrator" }
}

# ── ECR Repository ────────────────────────────────────────────────────────────
resource "aws_ecr_repository" "app" {
  name                 = "smart-migrator-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Project = "smart-migrator" }
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "ecr_repository_uri" {
  value       = aws_ecr_repository.app.repository_url
  description = "Paste this into config/settings.py → ECR_REPO"
}

output "configure_kubectl" {
  value       = "aws eks update-kubeconfig --region ap-south-1 --name smart-migrator-cluster"
  description = "Run this after terraform apply to connect kubectl"
}

output "cost_estimate" {
  value = "Approx cost: EKS control plane $0.10/hr + 2x t3.medium $0.052/hr + NAT $0.045/hr ≈ $0.25/hr (~₹21/hr)"
}
