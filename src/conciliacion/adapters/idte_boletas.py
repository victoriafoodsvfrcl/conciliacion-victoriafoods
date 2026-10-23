"""IDTE: boletas/DTE emitidos (CSV ';', cp1252, CRLF).

Deriva ``canal_prefijo`` e ``id_externo`` desde ``RznRecp``:
'PYA - Nicole Saavedra Arias - 2306915845' -> ('PYA', '2306915845').
``Pedido`` es el CheckNumber de Aloha. ``Sucursal`` es texto: se mapea a CC con la maestra
(sistema='idte') usando ``sucursal_norm``.
"""

from __future__ import annotations

import io
import re

import pandas as pd

from conciliacion.adapters.base import Adaptador, Columna, ErrorAdaptador, mapear_columnas
from conciliacion.adapters.comun import ids, montos, textos
from conciliacion.common.fechas import FechaInvalida, parse_fecha, parse_fecha_hora
from conciliacion.common.texto import limpiar, normalizar_llave
from conciliacion.config import config_negocio

COLUMNAS = [
    "Tipo_DTE",
    "nameDTE",
    "Fecha_DTE",
    "Fecha_Hora_Emision",
    "Folio",
    "Pedido",
    "RznRecp",
    "RUTFULL",
    "MntExento",
    "MntNeto",
    "MntIVA",
    "Recargo_1",
    "Glosa_Recargo1",
    "Recargo_2",
    "Glosa_Recargo2",
    "Descuento1",
    "Glosa_Dscto1",
    "Descuento2",
    "Glosa_Dscto2",
    "MontoTotal",
    "Sucursal",
    "Caja",
    "Cajero",
    "Estado_SII",
]
CANON = {
    "Tipo_DTE": "tipo_dte",
    "nameDTE": "nombre_dte",
    "Fecha_DTE": "fecha_dte",
    "Fecha_Hora_Emision": "fecha_hora_emision",
    "RznRecp": "razon_receptor",
    "RUTFULL": "rut_receptor",
    "MntExento": "monto_exento",
    "MntNeto": "monto_neto",
    "MntIVA": "monto_iva",
    "Recargo_1": "recargo_1",
    "Glosa_Recargo1": "glosa_recargo_1",
    "Recargo_2": "recargo_2",
    "Glosa_Recargo2": "glosa_recargo_2",
    "Descuento1": "descuento_1",
    "Glosa_Dscto1": "glosa_descuento_1",
    "Descuento2": "descuento_2",
    "Glosa_Dscto2": "glosa_descuento_2",
    "MontoTotal": "monto_total",
    "Folio": "folio",
    "Pedido": "pedido",
    "Sucursal": "sucursal",
    "Caja": "caja",
    "Cajero": "cajero",
    "Estado_SII": "estado_sii",
}
ALIAS = {CANON[c]: [c] for c in COLUMNAS}
OBLIGATORIAS = ["tipo_dte", "fecha_dte", "folio", "razon_receptor", "monto_total", "sucursal"]
NUMS = [
    "monto_exento",
    "monto_neto",
    "monto_iva",
    "recargo_1",
    "recargo_2",
    "descuento_1",
    "descuento_2",
    "monto_total",
]

ESQUEMA = [
    Columna("tipo_dte", "STRING", True),
    Columna("nombre_dte", "STRING"),
    Columna("fecha_dte", "DATE", True),
    Columna("fecha_hora_emision", "DATETIME"),
    Columna("folio", "STRING", True),
    Columna("pedido", "STRING"),
    Columna("razon_receptor", "STRING"),
    Columna("rut_receptor", "STRING"),
    Columna("canal_prefijo", "STRING", False, "PYA, UE, RPI, JTO, TOT, OLO, PC OLO, GOODMEAL o NULL"),
    Columna("id_externo", "STRING", False, "id del pedido en el canal (PYA = Numero de Pedido)"),
    *(Columna(n, "NUMERIC") for n in NUMS),
    Columna("glosa_recargo_1", "STRING"),
    Columna("glosa_recargo_2", "STRING"),
    Columna("glosa_descuento_1", "STRING"),
    Columna("glosa_descuento_2", "STRING"),
    Columna("sucursal", "STRING", True),
    Columna("sucursal_norm", "STRING", True),
    Columna("caja", "STRING"),
    Columna("cajero", "STRING"),
    Columna("estado_sii", "STRING"),
]


def derivar_canal(razon: str | None, prefijos: list[dict]) -> tuple[str | None, str | None]:
    """Devuelve (canal_prefijo, id_externo) a partir de RznRecp."""
    if not razon:
        return None, None
    texto = limpiar(razon) or ""
    llave = normalizar_llave(texto) or ""
    for p in prefijos:
        pref = normalizar_llave(p["prefijo"])
        if llave == pref or re.match(rf"^{re.escape(pref)}(\s*-\s*|\s+)", llave):
            tokens = [t.strip() for t in texto.split(" - ")]
            ultimo = tokens[-1] if len(tokens) > 1 else None
            patron = p.get("patron_id")
            id_ext = ultimo if (patron and ultimo and re.fullmatch(patron, ultimo)) else None
            return pref, id_ext
    return None, None


class IdteBoletas(Adaptador):
    source_id = "idte_boletas"
    schema_version = "1"
    descripcion = "Documentos tributarios electrónicos emitidos (boletas) desde IDTE"
    patrones = (r"idte[^/]*\.csv$", r"boletas?[^/]*\.csv$", r"/idte/[^/]*\.csv$")
    esquemas = {"idte_boletas": ESQUEMA}

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        encoding = params.get("encoding", "cp1252")
        try:
            texto = contenido.decode(encoding)
        except UnicodeDecodeError as e:
            raise ErrorAdaptador(f"[{self.source_id}] '{nombre}' no es {encoding}: {e}") from e
        df = pd.read_csv(io.StringIO(texto), sep=";", dtype=str, keep_default_na=False)
        mapeo = mapear_columnas(list(df.columns), ALIAS, OBLIGATORIAS, self.source_id)
        df = df.rename(columns=mapeo)[list(mapeo.values())]
        df["_fila"] = df.index + 2  # +1 encabezado, +1 base 1
        df["_hoja"] = None
        for c in ESQUEMA:
            if c.nombre not in df:
                df[c.nombre] = None
        try:
            df["fecha_dte"] = df["fecha_dte"].map(parse_fecha)
            df["fecha_hora_emision"] = df["fecha_hora_emision"].map(parse_fecha_hora)
        except FechaInvalida as e:
            raise ErrorAdaptador(f"[{self.source_id}] {e}") from e
        for n in NUMS:
            df[n] = montos(df[n], self.source_id, n)
        for c in ["tipo_dte", "folio", "pedido", "caja"]:
            df[c] = ids(df[c])
        for c in [
            "nombre_dte",
            "razon_receptor",
            "rut_receptor",
            "glosa_recargo_1",
            "glosa_recargo_2",
            "glosa_descuento_1",
            "glosa_descuento_2",
            "sucursal",
            "cajero",
            "estado_sii",
        ]:
            df[c] = textos(df[c])
        prefijos = params.get("prefijos") or config_negocio()["idte_boletas"]["prefijos"]
        canales = df["razon_receptor"].map(lambda r: derivar_canal(r, prefijos))
        df["canal_prefijo"] = canales.map(lambda t: t[0])
        df["id_externo"] = canales.map(lambda t: t[1])
        df["sucursal_norm"] = df["sucursal"].map(normalizar_llave)
        return [self.tabla("idte_boletas", df)]
