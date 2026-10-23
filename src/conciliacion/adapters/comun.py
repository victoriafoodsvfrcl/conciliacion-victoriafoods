"""Helpers compartidos por varios adaptadores."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime

import pandas as pd

from conciliacion.adapters.base import ErrorAdaptador
from conciliacion.common.montos import MontoInvalido, parse_monto
from conciliacion.common.texto import es_nulo, limpiar, quitar_invisibles


def id_texto(valor: object) -> str | None:
    """IDs que Excel convierte a float (2299350728.0) vuelven a texto sin decimales."""
    if es_nulo(valor):
        return None
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    if isinstance(valor, int):
        return str(valor)
    texto = limpiar(valor)
    if texto and re.fullmatch(r"\d+\.0+", texto):
        texto = texto.split(".")[0]
    return texto


def montos(serie: pd.Series, fuente: str, columna: str, **kw) -> pd.Series:
    def _p(v):
        try:
            return parse_monto(v, **kw)
        except MontoInvalido as e:
            raise ErrorAdaptador(f"[{fuente}] columna {columna}: {e}") from e

    return serie.map(_p).astype("float64")


def textos(serie: pd.Series) -> pd.Series:
    return serie.map(limpiar).astype(object)


def ids(serie: pd.Series) -> pd.Series:
    return serie.map(id_texto).astype(object)


def anio_predominante(valores: list[object], defecto: int | None = None) -> int:
    anios: Counter[int] = Counter()
    for v in valores:
        if isinstance(v, datetime | date | pd.Timestamp):
            anios[v.year] += 1
        elif isinstance(v, str):
            m = re.search(r"(\d{4})", quitar_invisibles(v))
            if m:
                anios[int(m.group(1))] += 1
    if anios:
        return anios.most_common(1)[0][0]
    return defecto or date.today().year


def encabezados_desde_fila(crudo: pd.DataFrame, fila: int) -> tuple[list[str], pd.DataFrame]:
    """Usa la fila ``fila`` como encabezado; devuelve (nombres, datos) y agrega ``_fila`` 1-based."""
    nombres = [limpiar(v) or f"col_{i}" for i, v in enumerate(crudo.iloc[fila].tolist())]
    datos = crudo.iloc[fila + 1 :].copy()
    datos.columns = range(len(nombres))
    datos["_fila"] = datos.index + 1  # índice 0-based de pandas -> fila Excel 1-based
    vacia = datos.drop(columns="_fila").map(es_nulo).all(axis=1)
    return nombres, datos.loc[~vacia]
