"""Normalización de RUT chileno: '76.211.425-9' / '762114259' -> '76211425-9'."""

from __future__ import annotations

import re

from conciliacion.common.texto import limpiar


def normalizar_rut(valor: object) -> str | None:
    texto = limpiar(valor)
    if texto is None:
        return None
    texto = re.sub(r"[^0-9kK]", "", texto).upper()
    if len(texto) < 2:
        return None
    cuerpo, dv = texto[:-1].lstrip("0"), texto[-1]
    return f"{cuerpo}-{dv}" if cuerpo else None


def dv_valido(rut: str) -> bool:
    cuerpo, dv = rut.split("-")
    s, m = 0, 2
    for c in reversed(cuerpo):
        s += int(c) * m
        m = 2 if m == 7 else m + 1
    r = 11 - s % 11
    esperado = {11: "0", 10: "K"}.get(r, str(r))
    return esperado == dv.upper()
