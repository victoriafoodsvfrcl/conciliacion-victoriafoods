provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {}

locals {
  raw_bucket     = var.raw_bucket_name != "" ? var.raw_bucket_name : "${var.project_id}-conciliacion-raw"
  exports_bucket = var.exports_bucket_name != "" ? var.exports_bucket_name : "${var.project_id}-conciliacion-exports"
  job_name       = "conciliacion-run-all"
  labels = {
    sistema = "conciliacion-victoriafoods"
    entorno = var.environment
  }
  apis = [
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudscheduler.googleapis.com",
    "dataform.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}
