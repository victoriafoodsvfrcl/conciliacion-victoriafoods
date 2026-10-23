# Los VALORES de los secretos NO están en el repo ni en el estado de Terraform:
# se cargan a mano (ver docs/runbook.md):
#   printf '%s' "$SECRETO" | gcloud secrets versions add graph-client-secret --data-file=-

resource "google_secret_manager_secret" "graph_client_secret" {
  secret_id = "graph-client-secret"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "dataform_github_token" {
  secret_id = "dataform-github-token"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}
