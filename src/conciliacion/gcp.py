"""Implementaciones sobre Google Cloud (GCS, BigQuery, Dataform). Requiere el extra ``[gcp]``.

Notas de diseño:
* El manifiesto usa DML (INSERT/UPDATE) y NO streaming inserts: las filas en el streaming buffer
  no se pueden actualizar durante ~30 min y el loader necesita cambiar el estado enseguida.
* Las tablas raw se crean en la primera carga con el esquema del adaptador, particionadas por
  ``_ingestado_en`` y clusterizadas por ``_archivo_id``. Columnas nuevas se agregan con
  ``ALLOW_FIELD_ADDITION`` (subir ``schema_version`` del adaptador cuando cambie el contrato).
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import pandas as pd

from conciliacion.adapters.base import Columna
from conciliacion.config import Settings
from conciliacion.ingest.manifiesto import ERROR, INGESTADO, RegistroArchivo

log = logging.getLogger(__name__)

CAMPOS_MANIFIESTO = list(RegistroArchivo.__dataclass_fields__)


class GcsAlmacen:
    def __init__(self, settings: Settings):
        from google.cloud import storage

        settings.requerir("bucket")
        self.cliente = storage.Client(project=settings.project_id)
        self.bucket = self.cliente.bucket(settings.bucket)

    def subir(self, contenido: bytes, ruta: str, metadata: dict | None = None) -> str:
        blob = self.bucket.blob(ruta)
        blob.metadata = {k: str(v) for k, v in (metadata or {}).items()}
        # if_generation_match=0: nunca sobreescribe (el nombre incluye el sha256)
        try:
            blob.upload_from_string(contenido, if_generation_match=0)
        except Exception as e:  # PreconditionFailed: ya existe, es el mismo contenido
            if "412" not in str(e) and "Precondition" not in type(e).__name__:
                raise
        return f"gs://{self.bucket.name}/{ruta}"

    def descargar(self, uri: str) -> bytes:
        ruta = uri.removeprefix(f"gs://{self.bucket.name}/")
        return self.bucket.blob(ruta).download_as_bytes()

    def leer_json(self, ruta: str) -> dict | None:
        blob = self.bucket.blob(ruta)
        return json.loads(blob.download_as_text()) if blob.exists() else None

    def escribir_json(self, ruta: str, datos: dict) -> None:
        self.bucket.blob(ruta).upload_from_string(json.dumps(datos), content_type="application/json")


# Tipos BQ de ops.archivos_ingestados (mismo esquema que infra/terraform/bigquery.tf)
TIPOS_MANIFIESTO = {
    "archivo_id": "STRING",
    "sha256": "STRING",
    "source_id": "STRING",
    "nombre": "STRING",
    "ruta_origen": "STRING",
    "gcs_uri": "STRING",
    "tamano_bytes": "INT64",
    "estado": "STRING",
    "origen": "STRING",
    "origen_item_id": "STRING",
    "origen_etag": "STRING",
    "modificado_en": "TIMESTAMP",
    "ingestado_en": "TIMESTAMP",
    "cargado_en": "TIMESTAMP",
    "schema_version": "STRING",
    "filas_cargadas": "STRING",
    "mensaje": "STRING",
}
assert set(TIPOS_MANIFIESTO) == set(CAMPOS_MANIFIESTO)


class BigQueryManifiesto:
    def __init__(self, settings: Settings):
        from google.cloud import bigquery

        self.bq = bigquery
        self.cliente = bigquery.Client(project=settings.project_id, location=settings.bq_location)
        self.tabla = f"`{settings.project_id}.{settings.dataset_ops}.archivos_ingestados`"

    def _q(self, sql: str, params: dict, arrays: dict | None = None) -> list:
        qp = [
            self.bq.ScalarQueryParameter(k, TIPOS_MANIFIESTO.get(k, "STRING"), v) for k, v in params.items()
        ]
        qp += [self.bq.ArrayQueryParameter(k, "STRING", v) for k, v in (arrays or {}).items()]
        job = self.cliente.query(sql, job_config=self.bq.QueryJobConfig(query_parameters=qp))
        return list(job.result())

    def existe_sha(self, sha256: str) -> bool:
        return bool(self._q(f"SELECT 1 FROM {self.tabla} WHERE sha256 = @sha256 LIMIT 1", {"sha256": sha256}))

    def registrar(self, registro: RegistroArchivo) -> None:
        fila = registro.fila()
        self._q(
            f"INSERT INTO {self.tabla} ({', '.join(fila)}) VALUES ({', '.join('@' + k for k in fila)})", fila
        )

    def pendientes(self, incluir_errores: bool = False) -> list[RegistroArchivo]:
        estados = [INGESTADO, ERROR] if incluir_errores else [INGESTADO]
        filas = self._q(
            f"SELECT * FROM {self.tabla} WHERE estado IN UNNEST(@estados) ORDER BY ingestado_en",
            {},
            {"estados": estados},
        )
        return [RegistroArchivo(**{k: f.get(k) for k in CAMPOS_MANIFIESTO}) for f in filas]

    def actualizar(self, archivo_id: str, **campos) -> None:
        sets = ", ".join(f"{k} = @{k}" for k in campos)
        self._q(
            f"UPDATE {self.tabla} SET {sets} WHERE archivo_id = @archivo_id",
            {**campos, "archivo_id": archivo_id},
        )


def _schema_bq(esquema: list[Columna]):
    from google.cloud import bigquery

    return [
        bigquery.SchemaField(c.nombre, c.tipo, mode="NULLABLE", description=c.descripcion or None)
        for c in esquema
    ]


def _preparar(df: pd.DataFrame, esquema: list[Columna]) -> pd.DataFrame:
    out = df[[c.nombre for c in esquema]].copy()
    for c in esquema:
        if c.tipo == "NUMERIC":
            out[c.nombre] = out[c.nombre].map(
                lambda x: None if pd.isna(x) else Decimal(str(round(float(x), 9)))
            )
        elif c.tipo == "TIMESTAMP":
            out[c.nombre] = pd.to_datetime(out[c.nombre], utc=True)
    return out


class EscritorBigQuery:
    def __init__(self, settings: Settings):
        from google.cloud import bigquery

        self.bq = bigquery
        self.cliente = bigquery.Client(project=settings.project_id, location=settings.bq_location)
        self.dataset = f"{settings.project_id}.{settings.dataset_raw}"

    def escribir(self, tabla: str, df: pd.DataFrame, esquema: list[Columna], archivo_id: str) -> int:
        bq = self.bq
        destino = f"{self.dataset}.{tabla}"
        schema = _schema_bq(esquema)
        try:
            self.cliente.get_table(destino)
            existe = True
        except Exception:
            existe = False
        if existe:
            self.cliente.query(
                f"DELETE FROM `{destino}` WHERE _archivo_id = @id",
                job_config=bq.QueryJobConfig(
                    query_parameters=[bq.ScalarQueryParameter("id", "STRING", archivo_id)]
                ),
            ).result()
        cfg = bq.LoadJobConfig(
            schema=schema,
            write_disposition=bq.WriteDisposition.WRITE_APPEND,
            schema_update_options=[bq.SchemaUpdateOption.ALLOW_FIELD_ADDITION] if existe else None,
            time_partitioning=None if existe else bq.TimePartitioning(field="_ingestado_en"),
            clustering_fields=None if existe else ["_archivo_id"],
        )
        job = self.cliente.load_table_from_dataframe(_preparar(df, esquema), destino, job_config=cfg)
        job.result()
        log.info("cargadas %s filas en %s", len(df), destino)
        return len(df)


def invocar_dataform(settings: Settings, etiquetas: list[str] | None = None) -> str | None:
    """Compila desde el release config y lanza una invocación del workflow. Devuelve su nombre."""
    if not settings.dataform_repository:
        log.info("DATAFORM_REPOSITORY no configurado: se omite la ejecución de Dataform")
        return None
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credenciales, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    s = AuthorizedSession(credenciales)
    base = f"https://dataform.googleapis.com/v1beta1/{settings.dataform_repository}"
    cuerpo = (
        {"releaseConfig": f"{settings.dataform_repository}/releaseConfigs/{settings.dataform_release_config}"}
        if settings.dataform_release_config
        else {"gitCommitish": "main"}
    )
    r = s.post(f"{base}/compilationResults", json=cuerpo, timeout=120)
    r.raise_for_status()
    compilacion = r.json()
    if compilacion.get("compilationErrors"):
        raise RuntimeError(f"Errores de compilación Dataform: {compilacion['compilationErrors']}")
    inv = {
        "compilationResult": compilacion["name"],
        "invocationConfig": {"transitiveDependenciesIncluded": True, "includedTags": etiquetas or []},
    }
    r = s.post(f"{base}/workflowInvocations", json=inv, timeout=120)
    r.raise_for_status()
    return r.json()["name"]
