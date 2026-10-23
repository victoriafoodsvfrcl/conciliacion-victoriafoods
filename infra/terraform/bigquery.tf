resource "google_bigquery_dataset" "capas" {
  for_each      = var.datasets
  dataset_id    = each.value
  location      = var.bq_location
  description   = "Conciliación Victoria Foods — capa ${each.key}"
  labels        = local.labels
  friendly_name = "conciliacion_${each.key}"

  depends_on = [google_project_service.apis]
}

# Manifiesto de archivos ingestados (idempotencia por sha256). Mismo esquema que
# src/conciliacion/ingest/manifiesto.py::RegistroArchivo y gcp.TIPOS_MANIFIESTO.
resource "google_bigquery_table" "archivos_ingestados" {
  dataset_id          = google_bigquery_dataset.capas["ops"].dataset_id
  table_id            = "archivos_ingestados"
  deletion_protection = true
  labels              = local.labels

  time_partitioning {
    type  = "MONTH"
    field = "ingestado_en"
  }
  clustering = ["source_id", "estado"]

  schema = jsonencode([
    { name = "archivo_id", type = "STRING", mode = "REQUIRED", description = "sha256 del contenido" },
    { name = "sha256", type = "STRING", mode = "REQUIRED" },
    { name = "source_id", type = "STRING", mode = "REQUIRED" },
    { name = "nombre", type = "STRING", mode = "REQUIRED" },
    { name = "ruta_origen", type = "STRING", mode = "NULLABLE" },
    { name = "gcs_uri", type = "STRING", mode = "REQUIRED" },
    { name = "tamano_bytes", type = "INT64", mode = "NULLABLE" },
    { name = "estado", type = "STRING", mode = "REQUIRED", description = "INGESTADO | CARGADO | ERROR | SIN_ADAPTADOR" },
    { name = "origen", type = "STRING", mode = "NULLABLE" },
    { name = "origen_item_id", type = "STRING", mode = "NULLABLE" },
    { name = "origen_etag", type = "STRING", mode = "NULLABLE" },
    { name = "modificado_en", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "ingestado_en", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "cargado_en", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "schema_version", type = "STRING", mode = "NULLABLE" },
    { name = "filas_cargadas", type = "STRING", mode = "NULLABLE", description = "JSON {tabla: filas}" },
    { name = "mensaje", type = "STRING", mode = "NULLABLE" },
  ])
}
