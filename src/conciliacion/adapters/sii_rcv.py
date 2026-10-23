"""SII Registro de Compras y Ventas — detalle de COMPRAS (CSV ';', UTF-8).

Las líneas con ``Nro`` vacío son continuación: otros impuestos del documento anterior. Se
emiten en ``sii_rcv_otros_impuestos`` (junto con el otro impuesto de la línea principal, si lo
trae) y NO duplican el documento.
"""

from __future__ import annotations

import io

import pandas as pd

from conciliacion.adapters.base import Adaptador, Columna, ErrorAdaptador, mapear_columnas
from conciliacion.adapters.comun import id_texto, montos
from conciliacion.common.fechas import FechaInvalida, parse_fecha, parse_fecha_hora
from conciliacion.common.rut import normalizar_rut
from conciliacion.common.texto import es_nulo, limpiar, normalizar_llave

COLS: list[tuple[str, str, str]] = [
    ("Nro", "nro", "STRING"),
    ("Tipo Doc", "tipo_doc", "STRING"),
    ("Tipo Compra", "tipo_compra", "STRING"),
    ("RUT Proveedor", "rut_proveedor", "STRING"),
    ("Razon Social", "razon_social", "STRING"),
    ("Folio", "folio", "STRING"),
    ("Fecha Docto", "fecha_docto", "DATE"),
    ("Fecha Recepcion", "fecha_recepcion", "DATETIME"),
    ("Fecha Acuse", "fecha_acuse", "DATETIME"),
    ("Monto Exento", "monto_exento", "NUMERIC"),
    ("Monto Neto", "monto_neto", "NUMERIC"),
    ("Monto IVA Recuperable", "monto_iva_recuperable", "NUMERIC"),
    ("Monto Iva No Recuperable", "monto_iva_no_recuperable", "NUMERIC"),
    ("Codigo IVA No Rec.", "codigo_iva_no_rec", "STRING"),
    ("Monto Total", "monto_total", "NUMERIC"),
    ("Monto Neto Activo Fijo", "monto_neto_activo_fijo", "NUMERIC"),
    ("IVA Activo Fijo", "iva_activo_fijo", "NUMERIC"),
    ("IVA uso Comun", "iva_uso_comun", "NUMERIC"),
    ("Impto. Sin Derecho a Credito", "impto_sin_derecho_credito", "NUMERIC"),
    ("IVA No Retenido", "iva_no_retenido", "NUMERIC"),
    ("Tabacos Puros", "tabacos_puros", "NUMERIC"),
    ("Tabacos Cigarrillos", "tabacos_cigarrillos", "NUMERIC"),
    ("Tabacos Elaborados", "tabacos_elaborados", "NUMERIC"),
    ("NCE o NDE sobre Fact. de Compra", "nce_nde_sobre_factura", "STRING"),
    ("Codigo Otro Impuesto", "codigo_otro_impuesto", "STRING"),
    ("Valor Otro Impuesto", "valor_otro_impuesto", "NUMERIC"),
    ("Tasa Otro Impuesto", "tasa_otro_impuesto", "NUMERIC"),
]
ALIAS = {c: [o] for o, c, _ in COLS}
OBLIGATORIAS = ["nro", "tipo_doc", "rut_proveedor", "folio", "fecha_docto", "monto_neto", "monto_total"]
OTROS = ["codigo_otro_impuesto", "valor_otro_impuesto", "tasa_otro_impuesto"]

ESQ_DOC = [
    Columna(c, t, c in ("tipo_doc", "rut_proveedor", "folio", "fecha_docto"))
    for _, c, t in COLS
    if c not in OTROS
]
ESQ_DOC.insert(5, Columna("razon_social_norm", "STRING"))
ESQ_OTROS = [
    Columna("tipo_doc", "STRING", True),
    Columna("rut_proveedor", "STRING", True),
    Columna("folio", "STRING", True),
    Columna("codigo", "STRING"),
    Columna("valor", "NUMERIC"),
    Columna("tasa", "NUMERIC"),
]


class SiiRcv(Adaptador):
    source_id = "sii_rcv"
    schema_version = "1"
    descripcion = "Registro de Compras SII (detalle)"
    patrones = (r"rcv[^/]*\.csv$", r"registro.{0,3}compras?[^/]*\.csv$", r"/sii/[^/]*\.csv$")
    esquemas = {"sii_rcv_compras": ESQ_DOC, "sii_rcv_otros_impuestos": ESQ_OTROS}

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        texto = contenido.decode(params.get("encoding", "utf-8-sig"))
        df = pd.read_csv(io.StringIO(texto), sep=";", dtype=str, keep_default_na=False, index_col=False)
        df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]  # ';' final
        mapeo = mapear_columnas(list(df.columns), ALIAS, OBLIGATORIAS, self.source_id)
        df = df.rename(columns=mapeo)[list(mapeo.values())]
        df["_fila"], df["_hoja"] = df.index + 2, None
        for _, c, _t in COLS:
            if c not in df:
                df[c] = None

        docs, otros, actual = [], [], None
        for _, fila in df.iterrows():
            if es_nulo(fila["nro"]):
                if actual is None:
                    raise ErrorAdaptador(
                        f"[{self.source_id}] línea de continuación sin documento previo (fila {fila['_fila']})"
                    )
                fuente = fila
            else:
                actual = fila
                docs.append(fila)
                fuente = fila
            if not all(es_nulo(fuente[c]) for c in OTROS):
                otros.append(
                    {
                        "tipo_doc": id_texto(actual["tipo_doc"]),
                        "rut_proveedor": normalizar_rut(actual["rut_proveedor"]),
                        "folio": id_texto(actual["folio"]),
                        "codigo": id_texto(fuente["codigo_otro_impuesto"]),
                        "valor": fuente["valor_otro_impuesto"],
                        "tasa": fuente["tasa_otro_impuesto"],
                        "_hoja": None,
                        "_fila": fuente["_fila"],
                    }
                )
        dd = pd.DataFrame(docs).drop(columns=OTROS)
        try:
            dd["fecha_docto"] = dd["fecha_docto"].map(parse_fecha)
            for c in ["fecha_recepcion", "fecha_acuse"]:
                dd[c] = dd[c].map(parse_fecha_hora)
        except FechaInvalida as e:
            raise ErrorAdaptador(f"[{self.source_id}] {e}") from e
        for _, c, t in COLS:
            if c in OTROS:
                continue
            if t == "NUMERIC":
                dd[c] = montos(dd[c], self.source_id, c)
            elif t == "STRING":
                dd[c] = dd[c].map(
                    id_texto
                    if c in ("nro", "tipo_doc", "tipo_compra", "folio", "codigo_iva_no_rec")
                    else limpiar
                )
        dd["rut_proveedor"] = dd["rut_proveedor"].map(normalizar_rut)
        dd["razon_social_norm"] = dd["razon_social"].map(normalizar_llave)
        do = pd.DataFrame(
            otros, columns=["tipo_doc", "rut_proveedor", "folio", "codigo", "valor", "tasa", "_hoja", "_fila"]
        )
        do["valor"] = montos(do["valor"], self.source_id, "valor")
        do["tasa"] = montos(do["tasa"], self.source_id, "tasa")
        return [self.tabla("sii_rcv_compras", dd), self.tabla("sii_rcv_otros_impuestos", do)]
