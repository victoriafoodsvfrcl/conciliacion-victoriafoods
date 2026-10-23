"""Interfaz común de adaptadores.

Un adaptador transforma UN archivo crudo de UNA fuente en una o más tablas normalizadas con
esquema fijo y versionado (``schema_version``). Reglas:

* ``matches(nombre, ruta)`` decide si el adaptador reconoce el archivo (por nombre/ruta).
* ``parse(contenido, nombre, **params)`` devuelve ``list[TablaParseada]``.
* Falla FUERTE (``ColumnasFaltantes`` / ``ErrorAdaptador``) si falta una columna obligatoria,
  si una hoja no se reconoce, etc. Nunca adivina.
* Cada fila incluye linaje ``_hoja`` y ``_fila`` (fila 1-based en el archivo original); el loader
  agrega ``_archivo_id``, ``_archivo_nombre``, ``_ingestado_en`` y ``_schema_version``.
* Sin reglas de negocio: sólo parseo, limpieza y tipado. Las reglas viven en Dataform (SQL) y su
  especificación ejecutable en ``conciliacion.reglas``.
"""

from __future__ import annotations

import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from conciliacion.common.texto import limpiar, normalizar_encabezado

TIPOS_BQ = {"STRING", "INT64", "NUMERIC", "FLOAT64", "DATE", "DATETIME", "TIMESTAMP", "BOOL", "JSON"}
COLUMNAS_LINAJE_ADAPTADOR = [("_hoja", "STRING"), ("_fila", "INT64")]
COLUMNAS_LINAJE_LOADER = [
    ("_archivo_id", "STRING"),
    ("_archivo_nombre", "STRING"),
    ("_ingestado_en", "TIMESTAMP"),
    ("_schema_version", "STRING"),
]


class ErrorAdaptador(Exception):
    """Error de parseo: el archivo no cumple lo esperado por el adaptador."""


class ColumnasFaltantes(ErrorAdaptador):
    def __init__(self, fuente: str, faltantes: list[str], disponibles: list[str], hoja: str | None = None):
        self.faltantes = faltantes
        donde = f" (hoja '{hoja}')" if hoja else ""
        super().__init__(
            f"[{fuente}] Faltan columnas obligatorias{donde}: {faltantes}. "
            f"Columnas encontradas: {disponibles}"
        )


@dataclass(frozen=True)
class Columna:
    nombre: str
    tipo: str
    obligatoria: bool = False  # si True, no puede venir nula en ninguna fila
    descripcion: str = ""

    def __post_init__(self):
        if self.tipo not in TIPOS_BQ:
            raise ValueError(f"Tipo BQ inválido {self.tipo} para {self.nombre}")


@dataclass
class TablaParseada:
    tabla: str
    df: pd.DataFrame
    schema_version: str
    advertencias: list[str] | None = None


class Adaptador(ABC):
    source_id: str = ""
    schema_version: str = "1"
    descripcion: str = ""
    #: regex (case-insensitive) evaluadas sobre "ruta/nombre"
    patrones: tuple[str, ...] = ()
    #: tabla -> lista de columnas (sin linaje). Es el contrato versionado con BigQuery raw.
    esquemas: dict[str, list[Columna]] = {}

    def matches(self, nombre: str, ruta: str = "") -> bool:
        objetivo = f"{ruta}/{nombre}" if ruta else nombre
        return any(re.search(p, objetivo, re.IGNORECASE) for p in self.patrones)

    @abstractmethod
    def parse(self, contenido: bytes, nombre: str, **params) -> list[TablaParseada]: ...

    # ---- utilidades para subclases -------------------------------------------------------
    def tabla(
        self, nombre_tabla: str, df: pd.DataFrame, advertencias: list[str] | None = None
    ) -> TablaParseada:
        return TablaParseada(
            tabla=nombre_tabla,
            df=conformar(df, self.esquemas[nombre_tabla], self.source_id, nombre_tabla),
            schema_version=self.schema_version,
            advertencias=advertencias or [],
        )


# --------------------------------------------------------------------------------------------
# Mapeo de columnas por alias
# --------------------------------------------------------------------------------------------
def mapear_columnas(
    columnas: list[object],
    alias: dict[str, list[str]],
    obligatorias: list[str],
    fuente: str,
    hoja: str | None = None,
) -> dict[object, str]:
    """Devuelve {columna_original: nombre_canonico}.

    ``alias`` = {canonico: [alias1, alias2, ...]}; la comparación usa ``normalizar_encabezado``
    (ignora espacios al inicio/fin, tildes, mayúsculas, signos). Lanza ``ColumnasFaltantes`` si
    alguna obligatoria no aparece.
    """
    indice: dict[str, str] = {}
    for canonico, nombres in alias.items():
        for n in [canonico, *nombres]:
            indice[normalizar_encabezado(n)] = canonico
    mapeo: dict[object, str] = {}
    usados: set[str] = set()
    for col in columnas:
        canon = indice.get(normalizar_encabezado(col))
        if canon and canon not in usados:
            mapeo[col] = canon
            usados.add(canon)
    faltan = [c for c in obligatorias if c not in usados]
    if faltan:
        raise ColumnasFaltantes(fuente, faltan, [str(c) for c in columnas], hoja)
    return mapeo


def buscar_fila_encabezado(
    filas: pd.DataFrame, marcadores: list[str], max_filas: int = 40, fuente: str = "", alguno: bool = False
) -> int:
    """Índice (0-based) de la primera fila que contiene TODOS los marcadores (o ALGUNO si
    ``alguno=True``), comparando encabezados normalizados."""
    objetivos = {normalizar_encabezado(m) for m in marcadores}
    for i in range(min(max_filas, len(filas))):
        celdas = {normalizar_encabezado(v) for v in filas.iloc[i].tolist() if limpiar(v)}
        if (objetivos & celdas) if alguno else (objetivos <= celdas):
            return i
    raise ErrorAdaptador(f"[{fuente}] No se encontró la fila de encabezado con {marcadores}")


def _motor_xls() -> str:
    """.xls binario: calamine si está instalado (pandas>=3 depreca xlrd), si no xlrd."""
    try:
        import python_calamine  # noqa: F401

        return "calamine"
    except ImportError:  # pragma: no cover
        return "xlrd"


def leer_excel_crudo(contenido: bytes, nombre: str, hoja: str | int | None = None) -> dict[str, pd.DataFrame]:
    """Lee todas (o una) hoja(s) sin encabezado, todo como object. Soporta .xls (xlrd) y .xlsx."""
    motor = _motor_xls() if nombre.lower().endswith(".xls") else "openpyxl"
    try:
        hojas = pd.read_excel(
            io.BytesIO(contenido),
            sheet_name=hoja if hoja is not None else None,
            header=None,
            dtype=object,
            engine=motor,
        )
    except ValueError as e:
        raise ErrorAdaptador(f"No se pudo leer '{nombre}' como Excel ({motor}): {e}") from e
    if isinstance(hojas, pd.DataFrame):
        hojas = {str(hoja): hojas}
    return {str(k): v for k, v in hojas.items()}


# --------------------------------------------------------------------------------------------
# Conformación al esquema
# --------------------------------------------------------------------------------------------
def _a_tipo(serie: pd.Series, tipo: str) -> pd.Series:
    if tipo == "STRING" or tipo == "JSON":
        valores = [
            None if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA else str(v)
            for v in serie
        ]
        return pd.Series(valores, index=serie.index, dtype=object)
    if tipo == "INT64":
        return pd.to_numeric(serie, errors="raise").round().astype("Int64")
    if tipo in ("NUMERIC", "FLOAT64"):
        return pd.to_numeric(serie, errors="raise").astype("float64")
    if tipo == "BOOL":
        return serie.astype("boolean")
    if tipo == "DATE":
        return serie.map(
            lambda v: v.date() if isinstance(v, datetime) else v if isinstance(v, date) else None
        ).astype(object)
    if tipo in ("DATETIME", "TIMESTAMP"):
        return pd.to_datetime(serie, errors="raise")
    raise ValueError(tipo)  # pragma: no cover


def conformar(df: pd.DataFrame, esquema: list[Columna], fuente: str, tabla: str) -> pd.DataFrame:
    """Ordena, tipa y valida el DataFrame contra el esquema + linaje del adaptador."""
    columnas = [*esquema, *(Columna(n, t) for n, t in COLUMNAS_LINAJE_ADAPTADOR)]
    faltan = [c.nombre for c in columnas if c.nombre not in df.columns]
    if faltan:
        raise ColumnasFaltantes(f"{fuente}.{tabla}", faltan, list(df.columns))
    out = pd.DataFrame(index=df.index)
    for c in columnas:
        try:
            out[c.nombre] = _a_tipo(df[c.nombre], c.tipo)
        except (ValueError, TypeError) as e:
            raise ErrorAdaptador(
                f"[{fuente}.{tabla}] columna {c.nombre} no convertible a {c.tipo}: {e}"
            ) from e
        if c.obligatoria and out[c.nombre].isna().any():
            filas = df.loc[out[c.nombre].isna(), "_fila"].head(5).tolist()
            raise ErrorAdaptador(f"[{fuente}.{tabla}] columna obligatoria {c.nombre} nula en filas {filas}")
    return out.reset_index(drop=True)
