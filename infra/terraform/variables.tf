variable "project_id" {
  description = "Proyecto GCP donde vive el pipeline."
  type        = string
}

variable "region" {
  description = "Región para Cloud Run, Scheduler, Artifact Registry y buckets."
  type        = string
  default     = "southamerica-west1"
}

variable "scheduler_region" {
  description = "Región de Cloud Scheduler si no está disponible en var.region. Vacío = var.region."
  type        = string
  default     = ""
}

variable "bq_location" {
  description = "Ubicación de los datasets BigQuery."
  type        = string
  default     = "southamerica-west1"
}

variable "dataform_region" {
  description = "Región del repositorio Dataform (verificar disponibilidad de Dataform en la región elegida)."
  type        = string
  default     = "southamerica-west1"
}

variable "environment" {
  description = "Nombre del entorno (dev, prod)."
  type        = string
  default     = "dev"
}

variable "raw_bucket_name" {
  description = "Bucket de archivos crudos. Vacío = <project_id>-conciliacion-raw."
  type        = string
  default     = ""
}

variable "exports_bucket_name" {
  description = "Bucket de exportes CSV (Innovaweb). Vacío = <project_id>-conciliacion-exports."
  type        = string
  default     = ""
}

variable "datasets" {
  description = "Datasets BigQuery por capa."
  type        = map(string)
  default = {
    raw        = "raw"
    staging    = "staging"
    core       = "core"
    marts      = "marts"
    ops        = "ops"
    assertions = "assertions"
  }
}

variable "github_owner" {
  description = "Organización/usuario de GitHub dueño del repositorio."
  type        = string
}

variable "github_repo" {
  description = "Nombre del repositorio de GitHub (sin owner)."
  type        = string
  default     = "conciliacion-victoriafoods"
}

variable "dataform_git_branch" {
  description = "Rama con el proyecto Dataform en la raíz (la publica deploy.yml con git subtree)."
  type        = string
  default     = "dataform-release"
}

variable "image" {
  description = "Imagen inicial del Cloud Run Job (deploy.yml la actualiza; Terraform ignora cambios posteriores)."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/job:latest"
}

variable "graph_tenant_id" {
  description = "Tenant de Microsoft Entra ID (no es secreto)."
  type        = string
}

variable "graph_client_id" {
  description = "Client ID de la app registrada para Graph (no es secreto)."
  type        = string
}

variable "sharepoint_hostname" {
  description = "Host de SharePoint, p. ej. victoriafoods.sharepoint.com"
  type        = string
}

variable "sharepoint_site_path" {
  description = "Ruta del sitio, p. ej. sites/Finanzas"
  type        = string
}

variable "sharepoint_drive" {
  description = "Biblioteca de documentos."
  type        = string
  default     = "Documentos"
}

variable "sharepoint_folder" {
  description = "Carpeta raíz a sincronizar dentro de la biblioteca."
  type        = string
  default     = "Conciliacion"
}

variable "time_zone" {
  description = "Zona horaria de Cloud Scheduler."
  type        = string
  default     = "America/Santiago"
}

variable "cron_diario" {
  description = "Ejecución diaria (sync + load + Dataform tag pya)."
  type        = string
  default     = "47 6 * * *"
}

variable "cron_lunes" {
  description = "Ejecución semanal de los lunes (cierre de semana; sync + load + Dataform completo)."
  type        = string
  default     = "17 11 * * 1"
}
