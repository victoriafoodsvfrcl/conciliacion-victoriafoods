"""Parseo de montos en formato chileno (CLP).

Ejemplos soportados::

    '42.818.978'  -> 42818978
    '$5.125.528 ' -> 5125528
    '-$1.933'     -> -1933
    '$-3.200'     -> -3200
    ' 23.588 '    -> 23588
    '1275,71'     -> 1275.71
    '(1.933)'     -> -1933
    '-' / ' - '   -> guion_como (None por defecto, o 0)

Regla del punto: si el texto sólo tiene puntos y calzan con grupos de miles
(``^\\d{1,3}(\\.\\d{3})+$``) son separadores de miles; si no, el punto es decimal
(p. ej. '2359.47' de un CSV en formato US). La coma siempre es decimal salvo
``formato='us'``.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal

from conciliacion.common.texto import es_nulo, quitar_invisibles

_RE_MILES_PUNTO = re.compile(r"^\d{1,3}(\.\d{3})+$")
_RE_NUMERO = re.compile(r"^\d+(\.\d+)?$")


class MontoInvalido(ValueError):
    pass


def parse_monto(valor: object, *, guion_como: float | None = None, formato: str = "cl") -> float | None:
    """Convierte un valor (texto o número) a float. Lanza ``MontoInvalido`` si no se puede."""
    if es_nulo(valor):
        return None
    if isinstance(valor, bool):
        return float(valor)
    if isinstance(valor, int | float | Decimal):
        f = float(valor)
        return None if math.isnan(f) else f

    texto = quitar_invisibles(str(valor)).strip()
    original = texto
    negativo = False
    if texto.startswith("(") and texto.endswith(")"):
        negativo, texto = True, texto[1:-1]
    texto = texto.replace("$", "").replace("CLP", "").replace(" ", "")
    if texto.endswith("-") and len(texto) > 1:  # contable '1.933-'
        negativo, texto = not negativo, texto[:-1]
    while texto.startswith(("-", "+")):
        if texto[0] == "-":
            negativo = not negativo
        texto = texto[1:]
    if texto in ("", "-"):
        return guion_como

    if formato == "us":
        texto = texto.replace(",", "")
    elif "," in texto:  # en CL la coma siempre es decimal
        texto = texto.replace(".", "").replace(",", ".")
    elif "." in texto:
        texto = texto.replace(".", "") if _RE_MILES_PUNTO.match(texto) else texto
    if not _RE_NUMERO.match(texto):
        raise MontoInvalido(f"Monto no reconocido: {original!r}")
    numero = float(texto)
    return -numero if negativo else numero


def redondear(valor: float | None) -> int | None:
    """Redondeo comercial (half-up) a entero, como Excel/BigQuery ROUND."""
    if valor is None:
        return None
    return int(Decimal(str(valor)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def a_entero(valor: object, **kw) -> int | None:
    return redondear(parse_monto(valor, **kw))
