# ---------------------------------------------------------------------------------------------
# Cuentas de servicio (mínimo privilegio)
# ---------------------------------------------------------------------------------------------
resource "google_service_account" "job" {
  account_id   = "conciliacion-job"
  display_name = "Conciliación: Cloud Run Job (sync SharePoint + carga raw)"
}

resource "google_service_account" "scheduler" {
  account_id   = "conciliacion-scheduler"
  display_name = "Conciliación: Cloud Scheduler (sólo invoca el job)"
}

resource "google_service_account" "dataform" {
  account_id   = "conciliacion-dataform"
  display_name = "Conciliación: ejecución de workflows Dataform"
}

resource "google_service_account" "deploy" {
  account_id   = "conciliacion-deploy"
  display_name = "Conciliación: despliegue desde GitHub Actions (WIF)"
}

# --- Job: lee/escribe sólo su bucket, carga raw, escribe manifiesto, lee el secreto de Graph ---
resource "google_storage_bucket_iam_member" "job_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectUser"
  member = google_service_account.job.member
}

resource "google_project_iam_member" "job_bq_jobuser" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = google_service_account.job.member
}

resource "google_bigquery_dataset_iam_member" "job_raw_editor" {
  dataset_id = google_bigquery_dataset.capas["raw"].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.job.member
}

resource "google_bigquery_dataset_iam_member" "job_ops_editor" {
  dataset_id = google_bigquery_dataset.capas["ops"].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.job.member
}

resource "google_secret_manager_secret_iam_member" "job_graph_secret" {
  secret_id = google_secret_manager_secret.graph_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.job.member
}

# El job dispara Dataform (compilación + invocación) al terminar la carga
resource "google_dataform_repository_iam_member" "job_dataform" {
  provider   = google-beta
  project    = var.project_id
  region     = var.dataform_region
  repository = google_dataform_repository.this.name
  role       = "roles/dataform.editor"
  member     = google_service_account.job.member
}

# --- Scheduler: sólo puede ejecutar el job ---
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  name     = google_cloud_run_v2_job.run_all.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.scheduler.member
}

# --- Dataform: lee raw/ops, escribe staging/core/marts/assertions y exportes ---
resource "google_project_iam_member" "dataform_bq_jobuser" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = google_service_account.dataform.member
}

resource "google_bigquery_dataset_iam_member" "dataform_lectura" {
  for_each   = toset(["raw", "ops"])
  dataset_id = google_bigquery_dataset.capas[each.value].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = google_service_account.dataform.member
}

resource "google_bigquery_dataset_iam_member" "dataform_escritura" {
  for_each   = toset(["staging", "core", "marts", "assertions"])
  dataset_id = google_bigquery_dataset.capas[each.value].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.dataform.member
}

resource "google_storage_bucket_iam_member" "dataform_exports" {
  bucket = google_storage_bucket.exports.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.dataform.member
}

# Agente de servicio de Dataform: actuar como la SA de ejecución y leer el token de GitHub
locals {
  dataform_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-dataform.iam.gserviceaccount.com"
}

resource "google_service_account_iam_member" "dataform_agent_token_creator" {
  service_account_id = google_service_account.dataform.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.dataform_agent
}

resource "google_secret_manager_secret_iam_member" "dataform_agent_git_token" {
  secret_id = google_secret_manager_secret.dataform_github_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.dataform_agent
}

# El job invoca workflows que corren como la SA de Dataform
resource "google_service_account_iam_member" "job_actua_como_dataform" {
  service_account_id = google_service_account.dataform.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.job.member
}

# --- Deploy (GitHub Actions): push de imagen y actualización del job ---
resource "google_artifact_registry_repository_iam_member" "deploy_writer" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deploy.member
}

resource "google_cloud_run_v2_job_iam_member" "deploy_developer" {
  name     = google_cloud_run_v2_job.run_all.name
  location = var.region
  role     = "roles/run.developer"
  member   = google_service_account.deploy.member
}

resource "google_service_account_iam_member" "deploy_actua_como_job" {
  service_account_id = google_service_account.job.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deploy.member
}
