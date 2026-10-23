# Repositorio Dataform conectado a GitHub. GCP Dataform lee el proyecto desde la RAÍZ de la rama
# configurada; deploy.yml publica la carpeta dataform/ en la rama var.dataform_git_branch con
# `git subtree split` (ver docs/runbook.md).
resource "google_dataform_repository" "this" {
  provider        = google-beta
  name            = "conciliacion-victoriafoods"
  region          = var.dataform_region
  service_account = google_service_account.dataform.email
  labels          = local.labels

  git_remote_settings {
    url                                 = "https://github.com/${var.github_owner}/${var.github_repo}.git"
    default_branch                      = var.dataform_git_branch
    authentication_token_secret_version = "${google_secret_manager_secret.dataform_github_token.id}/versions/latest"
  }

  workspace_compilation_overrides {
    default_database = var.project_id
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.dataform_agent_git_token,
  ]
}

resource "google_dataform_repository_release_config" "main" {
  provider      = google-beta
  project       = var.project_id
  region        = var.dataform_region
  repository    = google_dataform_repository.this.name
  name          = "produccion"
  git_commitish = var.dataform_git_branch
  # recompila cada hora; run-all además compila al invocar
  cron_schedule = "0 * * * *"
  time_zone     = var.time_zone

  code_compilation_config {
    default_database = var.project_id
    default_location = var.bq_location
    assertion_schema = var.datasets["assertions"]
    vars = {
      dataset_raw   = var.datasets["raw"]
      export_bucket = google_storage_bucket.exports.name
    }
  }
}
