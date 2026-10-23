"""Innovaweb (plataforma de recaudación): 4 informes xlsx con hoja 'Worksheet'.

El tipo de informe se detecta por sus encabezados (todos vienen en la hoja 'Worksheet'):

* caja           -> ``innovaweb_caja`` en formato LARGO (cc, fecha, concepto, categoría, monto).
                    El UNPIVOT tolera layouts distintos por marca (Melt 60 cols, Under 47 cols).
* ventas         -> ``innovaweb_ventas``
* recaudaciones  -> ``innovaweb_recaudaciones``
* reembolsos     -> ``innovaweb_reembolsos`` (quita U+200B de Código de orden / Número de boleta)
"""

from __future__ import annotations

import pandas as pd

from conciliacion.adapters.base import (
    Adaptador,
    Columna,
    ErrorAdaptador,
    buscar_fila_encabezado,
    leer_excel_crudo,
    mapear_columnas,
)
from conciliacion.adapters.comun import encabezados_desde_fila, ids, montos, textos
from conciliacion.common.fechas import FechaInvalida, parse_fecha, parse_fecha_hora
from conciliacion.common.texto import normalizar_encabezado, normalizar_llave
from conciliacion.config import config_negocio

S, N, D = "STRING", "NUMERIC", "DATE"

ESQ_CAJA = [
    Columna("cc", S, True),
    Columna("sucursal", S),
    Columna("fecha", D, True),
    Columna("creado_por", S),
    Columna("estado", S),
    Columna("concepto_original", S, True),
    Columna("concepto_norm", S, True),
    Columna("categoria", S, True, "venta | medio_pago | resumen"),
    Columna("monto", N),
    Columna("n_columnas_layout", "INT64", True),
]
ESQ_VENTAS = [
    Columna("cc", S, True),
    Columna("sucursal", S),
    Columna("fecha", D, True),
    Columna("tipo_documento", S),
    Columna("desde", S),
    Columna("hasta", S),
    Columna("monto", N),
    Columna("agregador", S),
    Columna("agregador_norm", S),
    Columna("boleta_nueva", S),
    Columna("comentario", S),
]
REC_COLS = [
    ("Código centro costo", "cc", S),
    ("Sucursal", "sucursal", S),
    ("Fecha", "fecha", D),
    ("Creado por", "creado_por", S),
    ("Fecha de Creación", "fecha_creacion", "DATETIME"),
    ("Tipo de Recaudación", "tipo_recaudacion", S),
    ("Número", "numero", S),
    ("Efectivo", "efectivo", N),
    ("Perla", "perla", N),
    ("Residencial", "residencial", N),
    ("Cheque", "cheque", N),
    ("Débito", "debito", N),
    ("Crédito", "credito", N),
    ("Empresa", "empresa", N),
    ("POS / Multicaja", "pos_multicaja", N),
    ("Agregador", "agregador", S),
    ("N° Transacción", "nro_transaccion", S),
    ("Valor", "valor", N),
    ("Cantidad", "cantidad", N),
    ("Monto", "monto", N),
    ("Comentario", "comentario", S),
]
ESQ_REC = [Columna(c, t, c in ("cc", "fecha")) for _, c, t in REC_COLS]
ESQ_REC.insert(16, Columna("agregador_norm", S))
ESQ_REEMB = [
    Columna("cc", S, True),
    Columna("sucursal", S),
    Columna("fecha_reporte", D, True),
    Columna("tipo_recaudacion", S, True),
    Columna("agregador", S),
    Columna("agregador_norm", S),
    Columna("codigo_orden", S),
    Columna("numero_boleta", S),
    Columna("valor", N),
    Columna("fecha_ingreso_reembolso", D),
]

ALIAS_VENTAS = {
    "cc": ["Código centro costo"],
    "sucursal": ["Sucursal"],
    "fecha": ["Fecha"],
    "tipo_documento": ["Tipo de Documento"],
    "desde": ["Desde"],
    "hasta": ["Hasta"],
    "monto": ["Monto"],
    "agregador": ["Agregador"],
    "boleta_nueva": ["¿Boleta nueva?", "¿Boleta Nueva?"],
    "comentario": ["Comentario"],
}
ALIAS_REC = {c: [o] for o, c, _ in REC_COLS}
ALIAS_REC["nro_transaccion"].append("Nº Transacción")
ALIAS_REEMB = {
    "cc": ["Código centro costo"],
    "sucursal": ["Sucursal"],
    "fecha_reporte": ["Fecha Reporte"],
    "tipo_recaudacion": ["Tipo de Recaudación"],
    "agregador": ["Agregador"],
    "codigo_orden": ["Código de orden"],
    "numero_boleta": ["Número de boleta"],
    "valor": ["Valor"],
    "fecha_ingreso_reembolso": ["Fecha Ingreso Reembolso"],
}


def detectar_informe(encabezados: list[str]) -> str:
    h = {normalizar_encabezado(x) for x in encabezados}
    if {"FECHA DE INFORME", "TOTAL DE RECAUDACION"} <= h:
        return "caja"
    if "CODIGO DE ORDEN" in h:
        return "reembolsos"
    if {"TIPO DE DOCUMENTO", "DESDE"} <= h:
        return "ventas"
    if "TIPO DE RECAUDACION" in h and any(x.endswith("TRANSACCION") for x in h):
        return "recaudaciones"
    raise ErrorAdaptador(f"[innovaweb] No se reconoce el tipo de informe por sus encabezados: {sorted(h)}")


def _fechas(serie: pd.Series, fn=parse_fecha) -> pd.Series:
    try:
        return serie.map(fn)
    except FechaInvalida as e:
        raise ErrorAdaptador(f"[innovaweb] {e}") from e


class InnovawebInformes(Adaptador):
    source_id = "innovaweb"
    schema_version = "1"
    descripcion = "Informes de caja, ventas, recaudaciones y reembolsos de Innovaweb"
    patrones = (
        r"innovaweb[^/]*\.xlsx$",
        r"/innovaweb/[^/]*\.xlsx$",
        r"informe.{0,3}(de.)?(caja|ventas|recaudaci[oó]n(es)?|reembolsos?)[^/]*\.xlsx$",
    )
    esquemas = {
        "innovaweb_caja": ESQ_CAJA,
        "innovaweb_ventas": ESQ_VENTAS,
        "innovaweb_recaudaciones": ESQ_REC,
        "innovaweb_reembolsos": ESQ_REEMB,
    }

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        hojas = leer_excel_crudo(contenido, nombre)
        hoja = "Worksheet" if "Worksheet" in hojas else next(iter(hojas))
        crudo = hojas[hoja]
        i = buscar_fila_encabezado(crudo, ["Código centro costo"], fuente=self.source_id)
        nombres, datos = encabezados_desde_fila(crudo, i)
        informe = params.get("informe") or detectar_informe(nombres)
        return [getattr(self, f"_{informe}")(nombres, datos, hoja)]

    # -- caja: UNPIVOT ---------------------------------------------------------------------
    def _caja(self, nombres, datos, hoja):
        cfg = config_negocio()["innovaweb"]
        ids_ = {normalizar_encabezado(x) for x in cfg["caja_columnas_id"]}
        ventas = {normalizar_encabezado(x) for x in cfg["caja_columnas_venta"]}
        resumen = {normalizar_encabezado(x) for x in cfg["caja_columnas_resumen"]}
        alias = {
            "cc": ["Código centro costo"],
            "sucursal": ["Sucursal"],
            "fecha": ["Fecha de informe"],
            "creado_por": ["Creado por"],
            "estado": ["Estado"],
        }
        mapeo = mapear_columnas(nombres, alias, ["cc", "fecha"], self.source_id, hoja)
        base = pd.DataFrame({canon: datos[nombres.index(orig)] for orig, canon in mapeo.items()})
        for c in alias:
            if c not in base:
                base[c] = None
        base["_fila"] = datos["_fila"]
        base["fecha"] = _fechas(base["fecha"])
        base["cc"] = textos(base["cc"]).str.upper()
        for c in ["sucursal", "creado_por", "estado"]:
            base[c] = textos(base[c])
        partes = []
        for k, col in enumerate(nombres):
            norm = normalizar_encabezado(col)
            if norm in ids_ or col.startswith("col_"):
                continue
            categoria = "venta" if norm in ventas else "resumen" if norm in resumen else "medio_pago"
            p = base.copy()
            p["concepto_original"] = col
            p["concepto_norm"] = normalizar_llave(col)
            p["categoria"] = categoria
            p["monto"] = montos(datos[k], self.source_id, col, guion_como=0.0)
            partes.append(p)
        df = pd.concat(partes, ignore_index=True)
        df["n_columnas_layout"] = len([n for n in nombres if not n.startswith("col_")])
        df["_hoja"] = hoja
        return self.tabla("innovaweb_caja", df)

    def _simple(self, tabla, alias, obligatorias, nombres, datos, hoja):
        mapeo = mapear_columnas(nombres, alias, obligatorias, self.source_id, hoja)
        df = pd.DataFrame({canon: datos[nombres.index(orig)] for orig, canon in mapeo.items()})
        for c in self.esquemas[tabla]:
            if c.nombre not in df:
                df[c.nombre] = None
        df["_fila"], df["_hoja"] = datos["_fila"], hoja
        for c in self.esquemas[tabla]:
            if c.tipo == N:
                df[c.nombre] = montos(df[c.nombre], self.source_id, c.nombre, guion_como=0.0)
            elif c.tipo == D:
                df[c.nombre] = _fechas(df[c.nombre])
            elif c.tipo == "DATETIME":
                df[c.nombre] = _fechas(df[c.nombre], parse_fecha_hora)
            elif c.nombre in ("desde", "hasta", "numero", "nro_transaccion", "codigo_orden", "numero_boleta"):
                df[c.nombre] = ids(df[c.nombre])  # ids() limpia U+200B
            else:
                df[c.nombre] = textos(df[c.nombre])
        df["cc"] = df["cc"].str.upper()
        if "agregador_norm" in df:
            df["agregador_norm"] = df["agregador"].map(normalizar_llave)
        return self.tabla(tabla, df)

    def _ventas(self, nombres, datos, hoja):
        return self._simple(
            "innovaweb_ventas", ALIAS_VENTAS, ["cc", "fecha", "tipo_documento", "monto"], nombres, datos, hoja
        )

    def _recaudaciones(self, nombres, datos, hoja):
        return self._simple(
            "innovaweb_recaudaciones", ALIAS_REC, ["cc", "fecha", "tipo_recaudacion"], nombres, datos, hoja
        )

    def _reembolsos(self, nombres, datos, hoja):
        return self._simple(
            "innovaweb_reembolsos",
            ALIAS_REEMB,
            ["cc", "fecha_reporte", "tipo_recaudacion", "codigo_orden", "valor"],
            nombres,
            datos,
            hoja,
        )
