"""Aloha POS: 'Ordenes Melt / Under / Under Express' (xlsx por marca).

* Hoja ``BBDD Ventas``: 3 filas de parámetros, encabezado en la fila que contiene 'Ticket ID'.
* Hoja ``Order Mode`` (opcional): catálogo código -> descripción.
* ``FKStoreId`` se repite entre marcas: la llave incluye ``marca`` (parámetro o nombre de archivo).
* Fechas m/d (Melt/Express) o d/m (Under): se detecta por archivo, con defecto por marca.
* Números con coma decimal o texto contable (' 23.588 ', ' - ' = 0).
* ``OrderMode2 = 0`` son anulaciones (montos negativos) -> ``es_anulacion``.
"""

from __future__ import annotations

import re

import pandas as pd

from conciliacion.adapters.base import (
    Adaptador,
    Columna,
    ErrorAdaptador,
    buscar_fila_encabezado,
    leer_excel_crudo,
    mapear_columnas,
)
from conciliacion.adapters.comun import encabezados_desde_fila, id_texto, ids, montos, textos
from conciliacion.common.fechas import FechaInvalida, detectar_orden, parse_fecha
from conciliacion.common.texto import limpiar, normalizar_llave
from conciliacion.config import config_negocio

ALIAS = {
    "ticket_id": ["Ticket ID"],
    "date_of_business": ["DateOfBusiness"],
    "mes": ["Mes"],
    "dia": ["Dia", "Día"],
    "store_id": ["FKStoreId"],
    "order_mode": ["OrderMode2"],
    "tienda": ["Tienda"],
    "check_number": ["CheckNumber"],
    "impuesto": ["Impuesto"],
    "bruto": ["Bruto"],
    "neto": ["Neto"],
    "cliente": ["Cliente"],
    "canal": ["Canal"],
}
OBLIGATORIAS = ["date_of_business", "store_id", "order_mode", "check_number", "bruto", "neto"]

ESQ_ORD = [
    Columna("marca", "STRING", True),
    Columna("ticket_id", "STRING"),
    Columna("date_of_business", "DATE", True),
    Columna("mes", "STRING"),
    Columna("dia", "STRING"),
    Columna("store_id", "STRING", True),
    Columna("order_mode", "INT64", True),
    Columna("tienda", "STRING"),
    Columna("tienda_norm", "STRING"),
    Columna("check_number", "STRING", True),
    Columna("impuesto", "NUMERIC"),
    Columna("bruto", "NUMERIC"),
    Columna("neto", "NUMERIC"),
    Columna("cliente", "STRING", False, "truncado por Aloha (~15 chars)"),
    Columna("canal", "STRING"),
    Columna("es_anulacion", "BOOL", True),
]
ESQ_OM = [
    Columna("marca", "STRING", True),
    Columna("codigo", "INT64", True),
    Columna("descripcion", "STRING", True),
]


def marca_desde_nombre(nombre: str) -> str | None:
    for regla in config_negocio()["aloha_ordenes"]["marcas_por_nombre"]:
        if re.search(regla["patron"], nombre, re.I):
            return regla["marca"]
    return None


class AlohaOrdenes(Adaptador):
    source_id = "aloha_ordenes"
    schema_version = "1"
    descripcion = "Órdenes del POS Aloha por marca"
    patrones = (r"ordenes\s*(melt|under|express|feel)[^/]*\.xlsx$", r"/aloha/[^/]*\.xlsx$")
    esquemas = {"aloha_ordenes": ESQ_ORD, "aloha_order_modes": ESQ_OM}

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        marca = params.get("marca") or marca_desde_nombre(nombre)
        if not marca:
            raise ErrorAdaptador(
                f"[{self.source_id}] No se pudo determinar la marca de '{nombre}'; pase marca=..."
            )
        hojas = leer_excel_crudo(contenido, nombre)
        hoja = next((h for h in hojas if normalizar_llave(h) == "BBDD VENTAS"), None)
        if hoja is None:
            raise ErrorAdaptador(
                f"[{self.source_id}] '{nombre}' no tiene hoja 'BBDD Ventas' (hojas: {list(hojas)})"
            )
        crudo = hojas[hoja]
        i = buscar_fila_encabezado(crudo, ["Ticket ID", "CheckNumber"], fuente=self.source_id)
        nombres, datos = encabezados_desde_fila(crudo, i)
        mapeo = mapear_columnas(nombres, ALIAS, OBLIGATORIAS, self.source_id, hoja)
        df = pd.DataFrame({canon: datos[nombres.index(orig)] for orig, canon in mapeo.items()})
        df["_fila"], df["_hoja"], df["marca"] = datos["_fila"], hoja, marca
        for c in ALIAS:
            if c not in df:
                df[c] = None

        defecto = config_negocio()["aloha_ordenes"]["orden_fecha_por_marca"].get(marca, "dmy")
        orden = params.get("orden_fecha") or detectar_orden(
            df["date_of_business"].tolist(), por_defecto=defecto
        )
        try:
            df["date_of_business"] = df["date_of_business"].map(lambda v: parse_fecha(v, orden))
        except FechaInvalida as e:
            raise ErrorAdaptador(f"[{self.source_id}] {e}") from e
        for c in ["impuesto", "bruto", "neto"]:
            df[c] = montos(df[c], self.source_id, c, guion_como=0.0)
        for c in ["ticket_id", "store_id", "check_number", "mes", "dia"]:
            df[c] = ids(df[c])
        df["order_mode"] = df["order_mode"].map(lambda v: int(float(id_texto(v))) if id_texto(v) else None)
        for c in ["tienda", "cliente", "canal"]:
            df[c] = textos(df[c])
        df["tienda_norm"] = df["tienda"].map(normalizar_llave)
        df["es_anulacion"] = df["order_mode"] == 0
        salida = [self.tabla("aloha_ordenes", df)]

        hoja_om = next((h for h in hojas if normalizar_llave(h) == "ORDER MODE"), None)
        if hoja_om is not None:
            filas = []
            for idx, fila in hojas[hoja_om].iterrows():
                vals = [v for v in fila.tolist() if limpiar(v)]
                if len(vals) >= 2 and re.fullmatch(r"\d+", id_texto(vals[0]) or ""):
                    filas.append(
                        {
                            "marca": marca,
                            "codigo": int(id_texto(vals[0])),
                            "descripcion": limpiar(vals[1]),
                            "_hoja": hoja_om,
                            "_fila": idx + 1,
                        }
                    )
            if filas:
                salida.append(self.tabla("aloha_order_modes", pd.DataFrame(filas)))
        return salida
