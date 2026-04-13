# ECS module for cortex discovery demo.

variable "vpc_id" {
  description = "VPC to deploy into"
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for ECS tasks"
  type        = list(string)
}

resource "aws_ecs_cluster" "main" {
  name = "demo-cluster"
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = "api:1"
  desired_count   = 2

  network_configuration {
    subnets = var.subnet_ids
  }
}
