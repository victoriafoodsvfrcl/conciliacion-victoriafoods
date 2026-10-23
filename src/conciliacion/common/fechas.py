"""Parseo de fechas heterogéneas.

Formatos: 'dd-mm-aaaa', 'dd/mm/aaaa', 'dd-mm-aaaa 0:00', 'YYYY-MM-DD HH:MM',
ISO 'YYYY-MM-DDTHH:MM:SS', 'm/d/aaaa' (Excel en inglés), seriales Excel (int/float),
datetime/date/pd.Timestamp.

La ambigüedad d/m vs m/d se resuelve POR ARCHIVO con ``detectar_orden``: si algún valor tiene
el primer componente > 12 es d/m; si el segundo > 12 es m/d; si ambos aparecen el archivo es
inconsistente y se falla. Si no hay evidencia se usa un rango esperado (p. ej. el periodo de la
liquidación) o el valor por defecto.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

from conciliacion.common.texto import es_nulo, quitar_invisibles

DMY = "dmy"
MDY = "mdy"

_EPOCH_EXCEL = datetime(1899, 12, 30)
_RE_ISO = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
_RE_AB = re.compile(
    r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?$"
)


class FechaInvalida(ValueError):
    pass


def _anio(a: str) -> int:
    n = int(a)
    return n + 2000 if n < 100 else n


def _hora(h, m, s, ampm=None) -> time:
    if h is None:
        return time(0, 0)
    hh = int(h)
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and hh < 12:
            hh += 12
        if ampm == "am" and hh == 12:
            hh = 0
    return time(hh, int(m), int(s or 0))


def desde_serial_excel(n: float) -> datetime:
    return _EPOCH_EXCEL + timedelta(days=float(n))


def parse_fecha_hora(valor: object, orden: str = DMY) -> datetime | None:
    """Devuelve datetime (hora 00:00 si no viene). ``orden`` aplica sólo a 'a/b/aaaa'."""
    if es_nulo(valor):
        return None
    try:
        import pandas as pd

        if isinstance(valor, pd.Timestamp):
            return valor.to_pydatetime().replace(tzinfo=None)
    except ImportError:  # pragma: no cover
        pass
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=None)
    if isinstance(valor, date):
        return datetime.combine(valor, time(0, 0))
    if isinstance(valor, int | float) and not isinstance(valor, bool):
        if 1 <= float(valor) < 200000:
            return desde_serial_excel(valor)
        raise FechaInvalida(f"Número no interpretable como fecha: {valor!r}")

    texto = quitar_invisibles(str(valor)).strip()
    if re.fullmatch(r"\d+(\.\d+)?", texto):
        return desde_serial_excel(float(texto))
    m = _RE_ISO.match(texto)
    if m:
        y, mo, d, h, mi, s = m.groups()
        return datetime.combine(date(int(y), int(mo), int(d)), _hora(h, mi, s))
    m = _RE_AB.match(texto)
    if m:
        a, b, y, h, mi, s, ampm = m.groups()
        dia, mes = (int(a), int(b)) if orden == DMY else (int(b), int(a))
        try:
            return datetime.combine(date(_anio(y), mes, dia), _hora(h, mi, s, ampm))
        except ValueError as e:
            raise FechaInvalida(f"Fecha inválida {texto!r} con orden {orden}: {e}") from e
    raise FechaInvalida(f"Formato de fecha no reconocido: {texto!r}")


def parse_fecha(valor: object, orden: str = DMY) -> date | None:
    dt = parse_fecha_hora(valor, orden)
    return dt.date() if dt else None


def _componentes(valores: Iterable[object]) -> list[tuple[int, int, int]]:
    comps = []
    for v in valores:
        if isinstance(v, str):
            m = _RE_AB.match(quitar_invisibles(v).strip())
            if m:
                comps.append((int(m.group(1)), int(m.group(2)), _anio(m.group(3))))
    return comps


def detectar_orden(
    valores: Iterable[object],
    *,
    por_defecto: str = DMY,
    rango: tuple[date, date] | None = None,
) -> str:
    """Detecta si los textos 'a/b/aaaa' de un archivo son d/m o m/d.

    1. Evidencia dura (componente > 12). Si hay evidencia contradictoria -> FechaInvalida.
    2. Si no hay evidencia y se entrega ``rango``, se elige el orden que deja más fechas dentro.
    3. Si no, ``por_defecto``.
    """
    comps = _componentes(valores)
    if not comps:
        return por_defecto
    hay_dmy = any(a > 12 for a, _, _ in comps)
    hay_mdy = any(b > 12 for _, b, _ in comps)
    if hay_dmy and hay_mdy:
        raise FechaInvalida("El archivo mezcla fechas d/m y m/d; no se puede decidir el orden.")
    if hay_dmy:
        return DMY
    if hay_mdy:
        return MDY
    if rango:
        ini, fin = rango

        def dentro(orden: str) -> int:
            n = 0
            for a, b, y in comps:
                d, m = (a, b) if orden == DMY else (b, a)
                with contextlib.suppress(ValueError):  # p. ej. 31/02
                    n += ini <= date(y, m, d) <= fin
            return n

        n_dmy, n_mdy = dentro(DMY), dentro(MDY)
        if n_dmy != n_mdy:
            return DMY if n_dmy > n_mdy else MDY
    return por_defecto


def lunes_de(d: date) -> date:
    return d - timedelta(days=d.weekday())


_RE_PERIODO_NOMBRE = re.compile(
    r"(\d{1,2})[-/](\d{1,2})\s*al\s*(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?", re.I
)


def periodo_desde_nombre(nombre: str, anio_referencia: int) -> tuple[date, date] | None:
    """'28-09 al 04-10.xls' + 2026 -> (2026-09-28, 2026-10-04). Maneja cruce de año."""
    m = _RE_PERIODO_NOMBRE.search(nombre)
    if not m:
        return None
    d1, m1, d2, m2, y = m.groups()
    anio_fin = _anio(y) if y else anio_referencia
    fin = date(anio_fin, int(m2), int(d2))
    anio_ini = anio_fin - 1 if int(m1) > int(m2) else anio_fin
    return date(anio_ini, int(m1), int(d1)), fin


def texto_periodo(inicio: date, fin: date) -> str:
    """(2026-09-28, 2026-10-04) -> '28-09 al 04-10' (formato que usa el equipo)."""
    return f"{inicio:%d-%m} al {fin:%d-%m}"
