"""Aplica el adaptador a cada archivo pendiente del manifiesto y lo carga a ``raw``.

* Linaje agregado: ``_archivo_id``, ``_archivo_nombre``, ``_ingestado_en``, ``_schema_version``
  (el adaptador ya trae ``_hoja`` y ``_fila``).
* Idempotente por archivo: antes de insertar se borran las filas con el mismo ``_archivo_id``.
* Un archivo con error queda en estado ERROR con el mensaje; los demás siguen.
* Escritores: ``EscritorBigQuery`` (producción, en ``conciliacion.gcp``) y ``EscritorLocal``
  (CSV en disco, para correr sin GCP).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from conciliacion.adapters import adaptador_por_id
from conciliacion.adapters.base import (
    COLUMNAS_LINAJE_ADAPTADOR,
    COLUMNAS_LINAJE_LOADER,
    Columna,
    ErrorAdaptador,
    TablaParseada,
)
from conciliacion.ingest.manifiesto import CARGADO, ERROR, Almacen, Manifiesto, RegistroArchivo

log = logging.getLogger(__name__)


class Escritor(Protocol):
    def escribir(self, tabla: str, df: pd.DataFrame, esquema: list[Columna], archivo_id: str) -> int: ...


def esquema_completo(esquema: list[Columna]) -> list[Columna]:
    return [*esquema, *(Columna(n, t) for n, t in COLUMNAS_LINAJE_ADAPTADOR + COLUMNAS_LINAJE_LOADER)]


def agregar_linaje(
    tp: TablaParseada, registro: RegistroArchivo, ahora: datetime | None = None
) -> pd.DataFrame:
    df = tp.df.copy()
    df["_archivo_id"] = registro.archivo_id
    df["_archivo_nombre"] = registro.nombre
    df["_ingestado_en"] = pd.Timestamp(ahora or registro.ingestado_en or datetime.now(UTC))
    df["_schema_version"] = tp.schema_version
    return df


def parsear_registro(registro: RegistroArchivo, contenido: bytes, **params) -> list[TablaParseada]:
    adaptador = adaptador_por_id(registro.source_id)
    return adaptador.parse(contenido, registro.nombre, **params)


def cargar_pendientes(
    almacen: Almacen, manifiesto: Manifiesto, escritor: Escritor, incluir_errores: bool = False
) -> dict:
    resumen = {"cargados": 0, "errores": 0, "detalle": []}
    for reg in manifiesto.pendientes(incluir_errores):
        try:
            contenido = almacen.descargar(reg.gcs_uri)
            adaptador = adaptador_por_id(reg.source_id)
            tablas = adaptador.parse(contenido, reg.nombre)
            filas = {}
            for tp in tablas:
                df = agregar_linaje(tp, reg)
                filas[tp.tabla] = escritor.escribir(
                    tp.tabla, df, esquema_completo(adaptador.esquemas[tp.tabla]), reg.archivo_id
                )
                for adv in tp.advertencias or []:
                    log.warning("[%s] %s: %s", reg.source_id, reg.nombre, adv)
            advertencias = [a for tp in tablas for a in (tp.advertencias or [])]
            manifiesto.actualizar(
                reg.archivo_id,
                estado=CARGADO,
                cargado_en=datetime.now(UTC),
                filas_cargadas=json.dumps(filas),
                schema_version=adaptador.schema_version,
                mensaje="; ".join(advertencias)[:4000] or None,
            )
            resumen["cargados"] += 1
            resumen["detalle"].append({"archivo": reg.nombre, "filas": filas})
        except Exception as e:  # un archivo malo no detiene la carga de los demás
            nivel = log.error if isinstance(e, ErrorAdaptador) else log.exception
            nivel("Error cargando %s (%s): %s", reg.nombre, reg.source_id, e)
            manifiesto.actualizar(reg.archivo_id, estado=ERROR, mensaje=str(e)[:4000])
            resumen["errores"] += 1
            resumen["detalle"].append({"archivo": reg.nombre, "error": str(e)})
    return resumen


class EscritorLocal:
    """Escribe cada tabla como CSV (append) en un directorio; útil para correr sin GCP."""

    def __init__(self, directorio: str | Path):
        self.dir = Path(directorio)
        self.dir.mkdir(parents=True, exist_ok=True)

    def escribir(self, tabla: str, df: pd.DataFrame, esquema: list[Columna], archivo_id: str) -> int:
        ruta = self.dir / f"{tabla}.csv"
        n = len(df)
        if ruta.exists():
            previo = pd.read_csv(ruta, dtype=str)
            previo = previo[previo["_archivo_id"] != archivo_id]
            df = pd.concat([previo, df.astype(object)], ignore_index=True)
        df[[c.nombre for c in esquema]].to_csv(ruta, index=False)
        return n
