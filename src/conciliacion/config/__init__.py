"""Configuración: parámetros de infraestructura (variables de entorno) y de negocio (YAML)."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def config_negocio() -> dict[str, Any]:
    texto = resources.files("conciliacion.config").joinpath("default.yaml").read_text("utf-8")
    cfg = yaml.safe_load(texto)
    extra = os.environ.get("CONCILIACION_CONFIG")
    if extra:
        cfg = _merge(cfg, yaml.safe_load(Path(extra).read_text("utf-8")) or {})
    return cfg


def _env(nombre: str, defecto: str | None = None) -> str | None:
    v = os.environ.get(nombre)
    return v if v not in (None, "") else defecto


@dataclass
class Settings:
    """Parámetros de infraestructura. Ningún valor por defecto es un secreto."""

    project_id: str | None = field(default_factory=lambda: _env("GCP_PROJECT_ID"))
    region: str = field(default_factory=lambda: _env("GCP_REGION", "southamerica-west1"))
    bucket: str | None = field(default_factory=lambda: _env("GCS_BUCKET"))
    bq_location: str = field(default_factory=lambda: _env("BQ_LOCATION", "southamerica-west1"))
    dataset_raw: str = field(default_factory=lambda: _env("BQ_DATASET_RAW", "raw"))
    dataset_ops: str = field(default_factory=lambda: _env("BQ_DATASET_OPS", "ops"))
    # Microsoft Graph (client credentials). El secreto llega como variable de entorno inyectada
    # por Cloud Run desde Secret Manager; nunca se escribe en el repo.
    graph_tenant_id: str | None = field(default_factory=lambda: _env("GRAPH_TENANT_ID"))
    graph_client_id: str | None = field(default_factory=lambda: _env("GRAPH_CLIENT_ID"))
    graph_client_secret: str | None = field(default_factory=lambda: _env("GRAPH_CLIENT_SECRET"))
    sharepoint_hostname: str | None = field(default_factory=lambda: _env("SHAREPOINT_HOSTNAME"))
    sharepoint_site_path: str | None = field(default_factory=lambda: _env("SHAREPOINT_SITE_PATH"))
    sharepoint_drive: str = field(default_factory=lambda: _env("SHAREPOINT_DRIVE", "Documentos"))
    sharepoint_folder: str = field(default_factory=lambda: _env("SHAREPOINT_FOLDER", "Conciliacion"))
    # Dataform (opcional: si está, run-all dispara la ejecución tras cargar)
    dataform_repository: str | None = field(default_factory=lambda: _env("DATAFORM_REPOSITORY"))
    dataform_release_config: str | None = field(default_factory=lambda: _env("DATAFORM_RELEASE_CONFIG"))

    def requerir(self, *nombres: str) -> None:
        faltan = [n for n in nombres if not getattr(self, n)]
        if faltan:
            raise RuntimeError(f"Faltan parámetros de configuración: {', '.join(faltan)}")
