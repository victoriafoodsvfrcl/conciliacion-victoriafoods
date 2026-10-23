output "workload_identity_provider" {
  description = "Valor para la variable GCP_WIF_PROVIDER de GitHub Actions."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deploy_service_account" {
  description = "Valor para la variable GCP_DEPLOY_SA de GitHub Actions."
  value       = google_service_account.deploy.email
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "cloud_run_job" {
  value = google_cloud_run_v2_job.run_all.name
}

output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "exports_bucket" {
  value = google_storage_bucket.exports.name
}

output "dataform_repository" {
  value = google_dataform_repository.this.id
}
