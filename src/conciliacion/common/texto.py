"""Limpieza y normalización de texto.

- ``quitar_invisibles``: elimina U+200B y demás caracteres de ancho cero (vienen en
  Innovaweb ``Código de orden``/``Número de boleta``) y convierte NBSP en espacio.
- ``arreglar_mojibake``: repara textos UTF-8 leídos como Latin-1 ('PeÃ±alolÃ©n' -> 'Peñalolén').
- ``limpiar``: invisibles + mojibake + espacios colapsados + strip. Es lo que se guarda como
  valor "original" limpio.
- ``normalizar_llave``: forma canónica para JOINs (sin tildes, mayúsculas, guiones normalizados).
  Es la ÚNICA implementación de la llave: los adaptadores guardan columnas ``*_norm`` ya
  calculadas para que el SQL sólo haga igualdad.
"""

from __future__ import annotations

import math
import re
import unicodedata

import ftfy

# Caracteres de ancho cero / de formato que aparecen pegados a IDs.
_INVISIBLES = "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad"
_RE_INVISIBLES = re.compile(f"[{_INVISIBLES}]")
_RE_ESPACIOS = re.compile(r"\s+")
_RE_GUIONES = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
# Sólo guiones con espacio a algún lado ('Melt Pizzas -Concón'); 'E-Commerce' queda igual.
_RE_GUION_ESPACIADO = re.compile(r"\s+-\s*|\s*-\s+")
# Firma típica de mojibake UTF-8 -> Latin-1 / cp1252.
_RE_SOSPECHA_MOJIBAKE = re.compile("[\u00c3\u00c2\u00e2][\x80-\xbf\u0152-\u2122]?")


def es_nulo(valor: object) -> bool:
    """True para None, NaN, pd.NA/NaT y strings vacíos tras limpiar invisibles."""
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    try:
        import pandas as pd

        if valor is pd.NA or valor is pd.NaT:
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(valor, str) and quitar_invisibles(valor).strip() == ""


def quitar_invisibles(texto: str) -> str:
    texto = _RE_INVISIBLES.sub("", texto)
    return texto.replace("\u00a0", " ").replace("\u202f", " ")


def arreglar_mojibake(texto: str) -> str:
    if _RE_SOSPECHA_MOJIBAKE.search(texto):
        return ftfy.fix_text(texto, normalization="NFC")
    return unicodedata.normalize("NFC", texto)


def limpiar(valor: object) -> str | None:
    """Limpia un valor de texto; devuelve None si queda vacío."""
    if es_nulo(valor):
        return None
    texto = str(valor)
    texto = quitar_invisibles(texto)
    texto = arreglar_mojibake(texto)
    texto = _RE_ESPACIOS.sub(" ", texto).strip()
    return texto or None


def quitar_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def normalizar_llave(valor: object) -> str | None:
    """Llave canónica: 'Melt Pizzas – PeÃ±alolÃ©n ' -> 'MELT PIZZAS - PENALOLEN'."""
    texto = limpiar(valor)
    if texto is None:
        return None
    texto = _RE_GUIONES.sub("-", texto)
    texto = quitar_tildes(texto).upper()
    texto = _RE_GUION_ESPACIADO.sub(" - ", texto)
    texto = _RE_ESPACIOS.sub(" ", texto).strip()
    return texto or None


def normalizar_encabezado(valor: object) -> str:
    """Llave para comparar encabezados de columnas (insensible a espacios, tildes, mayúsculas,
    signos ¿?() y puntuación final)."""
    texto = normalizar_llave(valor) or ""
    texto = re.sub(r"[¿?¡!()\[\]:]", " ", texto)
    texto = texto.replace("$", " ").replace("%", " % ")
    return _RE_ESPACIOS.sub(" ", texto).strip()


def snake(valor: object) -> str:
    """'Código centro costo' -> 'codigo_centro_costo'."""
    texto = quitar_tildes(limpiar(valor) or "").lower()
    texto = re.sub(r"[^a-z0-9]+", "_", texto).strip("_")
    return texto or "col"


def deduplicar_nombres(nombres: list[str]) -> list[str]:
    """Agrega sufijo _2, _3... a nombres repetidos (p. ej. dos 'Código transferencia')."""
    vistos: dict[str, int] = {}
    salida = []
    for n in nombres:
        if n in vistos:
            vistos[n] += 1
            salida.append(f"{n}_{vistos[n]}")
        else:
            vistos[n] = 1
            salida.append(n)
    return salida
