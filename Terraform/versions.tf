terraform {
  # 1.5 is the floor for the `check` and import blocks the AWS provider 6.x line
  # assumes; the pinned provider versions below are what this configuration has
  # been validated against.
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 4.30 did not recognise Lambda runtimes past python3.9, so the deprecated
      # runtime could not be replaced without moving off it.
      version = "~> 6.62"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}
