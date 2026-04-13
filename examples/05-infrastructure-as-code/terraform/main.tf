# Root Terraform configuration for cortex discovery demo.

terraform {
  required_version = ">= 1.5"
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

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

module "vpc" {
  source = "./modules/vpc"
}

module "ecs" {
  source     = "./modules/ecs"
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.subnet_ids
}
