resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "conciliacion"
  format        = "DOCKER"
  description   = "Imágenes del pipeline de conciliación"
  labels        = local.labels

  cleanup_policies {
    id     = "mantener-ultimas-20"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }

  depends_on = [google_project_service.apis]
}
