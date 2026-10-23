terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.10"
    }
  }

  # Estado remoto en GCS. Configurar con:
  #   terraform init -backend-config="bucket=<bucket-estado>" -backend-config="prefix=conciliacion/<env>"
  backend "gcs" {}
}
