resource "google_cloud_run_v2_job" "run_all" {
  name                = local.job_name
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  template {
    task_count = 1
    template {
      service_account = google_service_account.job.email
      timeout         = "3600s"
      max_retries     = 1

      containers {
        image = var.image
        args  = ["run-all", "--modo", "diario"]

        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi"
          }
        }

        dynamic "env" {
          for_each = {
            GCP_PROJECT_ID          = var.project_id
            GCP_REGION              = var.region
            BQ_LOCATION             = var.bq_location
            GCS_BUCKET              = google_storage_bucket.raw.name
            BQ_DATASET_RAW          = var.datasets["raw"]
            BQ_DATASET_OPS          = var.datasets["ops"]
            GRAPH_TENANT_ID         = var.graph_tenant_id
            GRAPH_CLIENT_ID         = var.graph_client_id
            SHAREPOINT_HOSTNAME     = var.sharepoint_hostname
            SHAREPOINT_SITE_PATH    = var.sharepoint_site_path
            SHAREPOINT_DRIVE        = var.sharepoint_drive
            SHAREPOINT_FOLDER       = var.sharepoint_folder
            DATAFORM_REPOSITORY     = google_dataform_repository.this.id
            DATAFORM_RELEASE_CONFIG = google_dataform_repository_release_config.main.name
          }
          content {
            name  = env.key
            value = env.value
          }
        }

        # Secreto inyectado por Cloud Run desde Secret Manager (nunca en el repo)
        env {
          name = "GRAPH_CLIENT_SECRET"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.graph_client_secret.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    # deploy.yml actualiza la imagen; Terraform no la revierte
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_project_service.apis, google_secret_manager_secret_iam_member.job_graph_secret]
}
