"""PDF 'Estado de cuenta semanal' de PedidosYa (consolidado por razón social).

Se separa en dos pasos para poder probarlo sin PDFs:

* ``extraer_texto(bytes)``: pdfplumber -> texto plano.
* ``parse_texto(texto)``: texto -> (cabecera, partidas). Parsea POR ETIQUETA (no por posición);
  las etiquetas opcionales que no vienen simplemente no generan partida.

Validación: Σ secciones de primer nivel == "Total liquidado". Si no cuadra se registra
``cuadra = false`` y ``diferencia_validacion`` (y se lanza error si ``estricto=True``).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pandas as pd

from conciliacion.adapters.base import Adaptador, Columna, ErrorAdaptador
from conciliacion.common.fechas import parse_fecha
from conciliacion.common.montos import parse_monto
from conciliacion.common.texto import limpiar, normalizar_llave

SECCION, PARTIDA, DETALLE, TOTAL, INFO = "seccion", "partida", "detalle", "total", "info"


@dataclass(frozen=True)
class Concepto:
    canonico: str
    etiqueta: str
    tipo: str
    padre: str | None = None


CATALOGO: list[Concepto] = [
    Concepto("total_pagado_usuarios", "Total pagado por los usuarios", SECCION),
    Concepto("total_pago_app", "Total con pago en la aplicación", PARTIDA, "total_pagado_usuarios"),
    Concepto(
        "total_pago_fuera_app", "Total con pago fuera de la aplicación", PARTIDA, "total_pagado_usuarios"
    ),
    Concepto(
        "total_pago_fuera_app_a_cobrar",
        "Total con pago fuera de la aplicación a cobrar",
        DETALLE,
        "total_pago_fuera_app",
    ),
    Concepto("servicios_pya", "Servicios PedidosYa", SECCION),
    Concepto("comision_pedidos", "Comisión por pedidos", PARTIDA, "servicios_pya"),
    Concepto("cargos_operativos", "Cargos operativos", SECCION),
    Concepto("cargos_rechazo", "Cargos por rechazar pedidos", PARTIDA, "cargos_operativos"),
    Concepto("cargos_reclamos", "Cargos por reclamos de los usuarios", PARTIDA, "cargos_operativos"),
    Concepto("costo_plus", "Costo por pedidos plus", PARTIDA, "cargos_operativos"),
    Concepto("costos_envio", "Costos de envio", PARTIDA, "cargos_operativos"),
    Concepto("publicidad", "Publicidad dentro de la aplicación", SECCION),
    Concepto("tarifa_fugaces", "Tarifa de descuentos fugaces", PARTIDA, "publicidad"),
    Concepto("reintegros", "Reintegros", SECCION),
    Concepto("dcto_usuarios_pya", "Descuentos a usuarios pagados por PedidosYa", PARTIDA, "reintegros"),
    Concepto(
        "dcto_productos_pya", "Descuentos en productos subsidiados por PedidosYa", PARTIDA, "reintegros"
    ),
    Concepto(
        "dcto_retiro_pya", "Descuentos por retiro en el local pagados por PedidosYa", PARTIDA, "reintegros"
    ),
    Concepto(
        "reintegro_cancelados", "Reintegros por pedidos cancelados por los usuarios", PARTIDA, "reintegros"
    ),
    Concepto("iva_dctos_no_facturados", "IVA de descuentos no facturados", PARTIDA, "reintegros"),
    Concepto("ajustes_liquidacion", "Ajustes de liquidación", SECCION),
    Concepto(
        "saldo_pendiente_anterior", "Saldo pendiente liquidación anterior", PARTIDA, "ajustes_liquidacion"
    ),
    Concepto(
        "cargos_reintegros_adicionales", "Cargos / Reintegros adicionales", PARTIDA, "ajustes_liquidacion"
    ),
    Concepto("impuestos", "Impuestos", SECCION),
    Concepto("iva_comisiones", "Impuesto en comisiones - IVA", PARTIDA, "impuestos"),
    Concepto("total_fuera_app_ya_cobrado", "Total con pago fuera de la aplicación ya cobrado", INFO),
]
_POR_ETIQUETA = {normalizar_llave(c.etiqueta): c for c in CATALOGO}
_POR_CANON = {c.canonico: c for c in CATALOGO}
TOLERANCIA = 1

_RE_MONTO_FINAL = re.compile(
    r"^(?P<etq>.*?)(?:^|\s)(?P<monto>-?\s*\$?\s*-?\s*\d{1,3}(?:\.\d{3})*(?:,\d+)?)\s*$"
)
_RE_SOLO_MONTO = re.compile(r"^\s*-?\s*\$?\s*-?\s*\d{1,3}(?:\.\d{3})*(?:,\d+)?\s*$")
_RE_TOTAL = re.compile(r"TOTAL LIQUIDADO DEL (\d{1,2}/\d{1,2}/\d{2,4}) AL (\d{1,2}/\d{1,2}/\d{2,4})")
_RE_PAGO = re.compile(r"El d[ií]a (\d{1,2}/\d{1,2}/\d{2,4}) procesaremos el pago", re.I)
_RE_CUENTA = re.compile(r"cuenta\s+(?P<banco>[^\n]{3,80}?)\s+terminada en\s+(?P<term>\d+)", re.I)
_RE_RAZON = re.compile(r"Raz[oó]n social\s*:?\s*(?P<rs>[^\n]+)", re.I)
_RE_RAZON_MAYUS = re.compile(
    r"^\s*([A-ZÁÉÍÓÚÑ0-9][A-ZÁÉÍÓÚÑ0-9 .,&-]+\b(?:SPA|S\.?A\.?|LTDA|LIMITADA))\s*$", re.M
)


def _etiqueta_norm(texto: str) -> str:
    return (normalizar_llave(texto.replace("*", " ").replace("−", "-")) or "").rstrip(" :")


def parse_texto(texto: str, estricto: bool = False) -> tuple[dict, list[dict]]:
    """Parsea el texto del PDF. Devuelve (cabecera, partidas)."""
    texto = texto.replace("−", "-").replace("\u00a0", " ")
    lineas = [ln.strip() for ln in texto.splitlines()]
    cab: dict = {
        "razon_social": None,
        "periodo_inicio": None,
        "periodo_fin": None,
        "total_liquidado": None,
        "fecha_pago": None,
        "banco": None,
        "cuenta_terminacion": None,
    }
    partidas: dict[str, dict] = {}
    advertencias: list[str] = []

    for n, linea in enumerate(lineas, start=1):
        if not linea:
            continue
        m = _RE_MONTO_FINAL.match(linea)
        etq, monto_txt = (m.group("etq"), m.group("monto")) if m and m.group("etq").strip() else (linea, None)
        if monto_txt is None and n < len(lineas) and _RE_SOLO_MONTO.match(lineas[n]):
            monto_txt = lineas[n]  # etiqueta y monto en líneas separadas
        norm = _etiqueta_norm(etq)
        mt = _RE_TOTAL.search(norm)
        if mt:
            cab["periodo_inicio"] = parse_fecha(mt.group(1))
            cab["periodo_fin"] = parse_fecha(mt.group(2))
            if monto_txt:
                cab["total_liquidado"] = parse_monto(monto_txt)
                cab["_total_original"] = limpiar(etq)
            continue
        concepto = _POR_ETIQUETA.get(norm)
        if concepto is None or monto_txt is None:
            continue
        monto = parse_monto(monto_txt)
        previo = partidas.get(concepto.canonico)
        if previo is not None:
            if abs(previo["monto"] - monto) > TOLERANCIA:
                advertencias.append(f"Concepto '{concepto.etiqueta}' repetido con montos distintos")
            continue
        partidas[concepto.canonico] = {
            "concepto_canonico": concepto.canonico,
            "concepto_original": limpiar(etq),
            "concepto_padre": concepto.padre,
            "tipo_concepto": concepto.tipo,
            "monto": monto,
            "orden": len(partidas) + 1,
            "_fila": n,
        }

    if m := _RE_PAGO.search(texto):
        cab["fecha_pago"] = parse_fecha(m.group(1))
    if m := _RE_CUENTA.search(texto):
        cab["banco"] = limpiar(m.group("banco"))
        cab["cuenta_terminacion"] = m.group("term")
    if m := _RE_RAZON.search(texto):
        cab["razon_social"] = limpiar(m.group("rs"))
    elif m := _RE_RAZON_MAYUS.search(texto):
        cab["razon_social"] = limpiar(m.group(1))

    if cab["total_liquidado"] is None or cab["periodo_inicio"] is None:
        raise ErrorAdaptador(
            "[pya_estado_cuenta_pdf] No se encontró 'Total liquidado del dd/mm/aa al dd/mm/aa'"
        )

    # Validación padre = Σ hijos (sólo informativa)
    for c in CATALOGO:
        if c.tipo != SECCION or c.canonico not in partidas:
            continue
        hijos = [
            p["monto"]
            for p in partidas.values()
            if p["concepto_padre"] == c.canonico and p["tipo_concepto"] == PARTIDA
        ]
        if hijos and abs(sum(hijos) - partidas[c.canonico]["monto"]) > TOLERANCIA:
            advertencias.append(
                f"Sección '{c.etiqueta}' ({partidas[c.canonico]['monto']:,.0f}) "
                f"≠ Σ partidas ({sum(hijos):,.0f})"
            )

    # Σ secciones (si falta la sección pero vienen sus partidas, se usa la suma de partidas)
    suma = 0.0
    for c in CATALOGO:
        if c.tipo != SECCION:
            continue
        if c.canonico in partidas:
            suma += partidas[c.canonico]["monto"]
        else:
            suma += sum(
                p["monto"]
                for p in partidas.values()
                if p["concepto_padre"] == c.canonico and p["tipo_concepto"] == PARTIDA
            )
    cab["suma_partidas"] = suma
    cab["diferencia_validacion"] = cab["total_liquidado"] - suma
    cab["cuadra"] = abs(cab["diferencia_validacion"]) <= TOLERANCIA
    cab["advertencias"] = advertencias
    if not cab["cuadra"]:
        msg = (
            f"[pya_estado_cuenta_pdf] Σ partidas {suma:,.0f} ≠ total liquidado "
            f"{cab['total_liquidado']:,.0f} (dif {cab['diferencia_validacion']:,.0f})"
        )
        if estricto:
            raise ErrorAdaptador(msg)
        advertencias.append(msg)
    return cab, list(partidas.values())


def extraer_texto(contenido: bytes) -> str:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:  # pdfminer lanza excepciones propias
        raise ErrorAdaptador(f"[pya_estado_cuenta_pdf] no se pudo leer el PDF: {e}") from e


ESQ_CAB = [
    Columna("periodo_inicio", "DATE", True),
    Columna("periodo_fin", "DATE", True),
    Columna("razon_social", "STRING"),
    Columna("total_liquidado", "NUMERIC", True),
    Columna("suma_partidas", "NUMERIC", True),
    Columna("diferencia_validacion", "NUMERIC", True),
    Columna("cuadra", "BOOL", True),
    Columna("fecha_pago", "DATE"),
    Columna("banco", "STRING"),
    Columna("cuenta_terminacion", "STRING"),
    Columna("n_partidas", "INT64", True),
]
ESQ_PART = [
    Columna("periodo_inicio", "DATE", True),
    Columna("periodo_fin", "DATE", True),
    Columna("concepto_canonico", "STRING", True),
    Columna("concepto_original", "STRING", True),
    Columna("concepto_padre", "STRING"),
    Columna("tipo_concepto", "STRING", True),
    Columna("monto", "NUMERIC", True),
    Columna("orden", "INT64", True),
]


class PyaEstadoCuentaPdf(Adaptador):
    source_id = "pya_estado_cuenta_pdf"
    schema_version = "1"
    descripcion = "Resumen semanal de liquidación PedidosYa (PDF)"
    patrones = (r"estado.{0,3}de.{0,3}cuenta[^/]*\.pdf$", r"pedidos\s*ya[^/]*\.pdf$", r"/pya/[^/]*\.pdf$")
    esquemas = {"pya_estado_cuenta_cabecera": ESQ_CAB, "pya_estado_cuenta_partidas": ESQ_PART}

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        texto = params.get("texto") or extraer_texto(contenido)
        cab, partidas = parse_texto(texto, estricto=params.get("estricto", False))
        adv = cab.pop("advertencias")
        cab.pop("_total_original", None)
        cab["n_partidas"] = len(partidas)
        df_cab = pd.DataFrame([{**cab, "_hoja": "pdf", "_fila": 0}])
        df_part = pd.DataFrame(partidas)
        df_part["periodo_inicio"] = cab["periodo_inicio"]
        df_part["periodo_fin"] = cab["periodo_fin"]
        df_part["_hoja"] = "pdf"
        return [
            self.tabla("pya_estado_cuenta_cabecera", df_cab, adv),
            self.tabla("pya_estado_cuenta_partidas", df_part),
        ]
