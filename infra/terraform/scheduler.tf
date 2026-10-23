locals {
  run_uri = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.run_all.name}:run"
  ejecuciones = {
    diario = { cron = var.cron_diario, args = ["run-all", "--modo", "diario"] }
    lunes  = { cron = var.cron_lunes, args = ["run-all", "--modo", "semanal", "--reintentar-errores"] }
  }
}

resource "google_cloud_scheduler_job" "run_all" {
  for_each    = local.ejecuciones
  name        = "conciliacion-${each.key}"
  description = "Conciliación Victoria Foods: ejecución ${each.key}"
  region      = var.scheduler_region != "" ? var.scheduler_region : var.region
  schedule    = each.value.cron
  time_zone   = var.time_zone

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = local.run_uri
    body = base64encode(jsonencode({
      overrides = { containerOverrides = [{ args = each.value.args }] }
    }))
    headers = { "Content-Type" = "application/json" }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.apis]
}
