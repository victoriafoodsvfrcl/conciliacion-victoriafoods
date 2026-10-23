"""Cartola BCI cuenta corriente (xlsx, hoja 'Cuenta-Corriente').

* Cabecera: Empresa, Rut Empresa, Número de cuenta CLP, Rango de fecha (en celdas sueltas).
* Tabla de movimientos: tras 'Lista de movimientos'; se detecta la fila con 'Fecha transacción'.
* 64 columnas; se tipan las conocidas y el resto va a ``extra_json`` para no perder nada.
* Dos columnas 'Código transferencia' -> ``codigo_transferencia`` y ``codigo_transferencia_2``.
* La contraparte se clasifica por RUT de origen (tabla configurable ``bci_cartola.contrapartes``).
  La misma tabla se replica en Dataform (``includes/constantes.js``) para el SQL.
"""

from __future__ import annotations

import hashlib
import json
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
from conciliacion.adapters.comun import encabezados_desde_fila, ids, montos, textos
from conciliacion.common.fechas import FechaInvalida, parse_fecha, parse_fecha_hora
from conciliacion.common.rut import normalizar_rut
from conciliacion.common.texto import deduplicar_nombres, es_nulo, limpiar, normalizar_llave, snake
from conciliacion.config import config_negocio

ALIAS = {
    "fecha_transaccion": ["Fecha transacción"],
    "fecha_contable": ["Fecha contable (*)", "Fecha contable"],
    "oficina": ["Oficina"],
    "movimiento": ["Movimiento"],
    "nro_documento": ["N° documento", "Nº documento"],
    "cargo": ["Cargo (-)"],
    "abono": ["Abono (+)"],
    "glosa_detalle": ["Glosa detalle"],
    "rut_origen": ["RUT de origen"],
    "nombre_origen": ["Nombre de origen"],
    "banco_origen": ["Banco de origen"],
    "descripcion_tipo_pago": ["Descripción tipo de pago"],
    "comentario_transferencia": ["Comentario transferencia"],
    "nro_operacion": ["N° Operación", "Nº Operación"],
    "codigo_transferencia": ["Código transferencia"],
    "codigo_transferencia_2": ["Código transferencia_2"],
}
OBLIGATORIAS = ["fecha_transaccion", "fecha_contable", "cargo", "abono"]

ESQ_MOV = [
    Columna("cuenta", "STRING"),
    Columna("rut_empresa", "STRING"),
    Columna("fecha_transaccion", "DATETIME", True),
    Columna("fecha_contable", "DATE", True),
    Columna("oficina", "STRING"),
    Columna("movimiento", "STRING"),
    Columna("nro_documento", "STRING"),
    Columna("cargo", "NUMERIC"),
    Columna("abono", "NUMERIC"),
    Columna("monto", "NUMERIC", True, "abono - cargo"),
    Columna("glosa_detalle", "STRING"),
    Columna("rut_origen", "STRING"),
    Columna("nombre_origen", "STRING"),
    Columna("banco_origen", "STRING"),
    Columna("descripcion_tipo_pago", "STRING"),
    Columna("comentario_transferencia", "STRING"),
    Columna("nro_operacion", "STRING"),
    Columna("codigo_transferencia", "STRING"),
    Columna("codigo_transferencia_2", "STRING"),
    Columna("contraparte", "STRING", False, "clasificación por RUT/nombre de origen"),
    Columna("movimiento_id", "STRING", True, "hash estable para deduplicar cartolas con rangos solapados"),
    Columna("extra_json", "STRING", False, "JSON con las columnas no mapeadas de la cartola"),
]
ESQ_CAB = [
    Columna("empresa", "STRING"),
    Columna("rut_empresa", "STRING"),
    Columna("cuenta", "STRING"),
    Columna("rango_desde", "DATE"),
    Columna("rango_hasta", "DATE"),
]


def clasificar_contraparte(
    rut: str | None, nombre: str | None, glosa: str | None, reglas: list[dict]
) -> str | None:
    for r in reglas:
        if r.get("rut") and rut and normalizar_rut(r["rut"]) == rut:
            return r["contraparte"]
    texto = " ".join(filter(None, [normalizar_llave(nombre), normalizar_llave(glosa)]))
    for r in reglas:
        if r.get("patron_nombre") and re.search(r["patron_nombre"], texto, re.I):
            return r["contraparte"]
    return None


def _fecha_movimiento(v):
    """None para celdas vacías o texto de pie de página ('Saldo final', 'Total')."""
    if es_nulo(v) or (isinstance(v, str) and not re.match(r"\s*\d", v)):
        return None
    return parse_fecha_hora(v)


def _cabecera(crudo: pd.DataFrame, hasta: int) -> dict:
    texto = "\n".join(
        " ".join(str(v) for v in fila if limpiar(v)) for fila in crudo.iloc[:hasta].values.tolist()
    )
    cab: dict = {
        "empresa": None,
        "rut_empresa": None,
        "cuenta": None,
        "rango_desde": None,
        "rango_hasta": None,
    }
    if m := re.search(r"Empresa\s*:?\s+(?!Rut)(.+)", texto):
        cab["empresa"] = limpiar(m.group(1))
    if m := re.search(r"Rut Empresa\s*:?\s*([\d\.]+-[\dkK])", texto):
        cab["rut_empresa"] = normalizar_rut(m.group(1))
    if m := re.search(r"N[úu]mero de cuenta(?:\s+CLP)?\s*:?\s*([\d-]+)", texto):
        cab["cuenta"] = m.group(1)
    if m := re.search(r"Rango de fecha\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})\D+(\d{1,2}/\d{1,2}/\d{4})", texto):
        cab["rango_desde"], cab["rango_hasta"] = parse_fecha(m.group(1)), parse_fecha(m.group(2))
    return cab


class BciCartola(Adaptador):
    source_id = "bci_cartola"
    schema_version = "1"
    descripcion = "Movimientos de cuenta corriente BCI"
    patrones = (r"cartola[^/]*\.xlsx?$", r"/bci/[^/]*\.xlsx?$", r"cuenta.corriente[^/]*\.xlsx?$")
    esquemas = {"bci_movimientos": ESQ_MOV, "bci_cartola_cabecera": ESQ_CAB}

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        hojas = leer_excel_crudo(contenido, nombre)
        hoja = next((h for h in hojas if normalizar_llave(h) == "CUENTA-CORRIENTE"), None) or next(
            iter(hojas)
        )
        crudo = hojas[hoja]
        i = buscar_fila_encabezado(crudo, ["Fecha transacción"], max_filas=60, fuente=self.source_id)
        cab = _cabecera(crudo, i)
        nombres, datos = encabezados_desde_fila(crudo, i)
        nombres = deduplicar_nombres(nombres)
        mapeo = mapear_columnas(nombres, ALIAS, OBLIGATORIAS, self.source_id, hoja)
        df = pd.DataFrame({canon: datos[nombres.index(orig)] for orig, canon in mapeo.items()})
        extras = [n for n in nombres if n not in mapeo and not n.startswith("col_")]
        df["extra_json"] = [
            json.dumps(
                {
                    snake(n): limpiar(datos.iloc[k][nombres.index(n)])
                    for n in extras
                    if not es_nulo(datos.iloc[k][nombres.index(n)])
                },
                ensure_ascii=False,
            )
            for k in range(len(datos))
        ]
        df["_fila"], df["_hoja"] = datos["_fila"], hoja
        for c in ALIAS:
            if c not in df:
                df[c] = None
        # Fin de la tabla: filas sin fecha de transacción válida (pies de página, saldos)
        try:
            df["fecha_transaccion"] = df["fecha_transaccion"].map(_fecha_movimiento)
            df["fecha_contable"] = df["fecha_contable"].map(lambda v: parse_fecha(v) if limpiar(v) else None)
        except FechaInvalida as e:
            raise ErrorAdaptador(f"[{self.source_id}] {e}") from e
        df = df[df["fecha_transaccion"].notna()].copy()
        df["fecha_contable"] = df["fecha_contable"].fillna(df["fecha_transaccion"].map(lambda d: d.date()))
        for c in ["cargo", "abono"]:
            df[c] = montos(df[c], self.source_id, c, guion_como=0.0).fillna(0.0)
        df["monto"] = df["abono"] - df["cargo"]
        for c in ["nro_documento", "nro_operacion", "codigo_transferencia", "codigo_transferencia_2"]:
            df[c] = ids(df[c])
        for c in [
            "oficina",
            "movimiento",
            "glosa_detalle",
            "nombre_origen",
            "banco_origen",
            "descripcion_tipo_pago",
            "comentario_transferencia",
        ]:
            df[c] = textos(df[c])
        df["rut_origen"] = df["rut_origen"].map(normalizar_rut)
        reglas = params.get("contrapartes") or config_negocio()["bci_cartola"]["contrapartes"]
        df["contraparte"] = [
            clasificar_contraparte(r, n, g, reglas)
            for r, n, g in zip(df["rut_origen"], df["nombre_origen"], df["glosa_detalle"], strict=True)
        ]
        df["cuenta"], df["rut_empresa"] = cab["cuenta"], cab["rut_empresa"]
        llave = [
            "cuenta",
            "fecha_transaccion",
            "fecha_contable",
            "monto",
            "nro_operacion",
            "glosa_detalle",
            "nro_documento",
        ]
        # ocurrencia dentro del archivo: distingue movimientos idénticos legítimos del mismo día
        ocurrencia = (
            df.fillna({"nro_operacion": "", "glosa_detalle": "", "nro_documento": "", "cuenta": ""})
            .groupby(llave, dropna=False)
            .cumcount()
        )
        df["movimiento_id"] = [
            hashlib.sha256("|".join(str(x) for x in t).encode()).hexdigest()[:24]
            for t in zip(*(df[c] for c in llave), ocurrencia, strict=True)
        ]
        dfc = pd.DataFrame([{**cab, "_hoja": hoja, "_fila": 1}])
        return [self.tabla("bci_movimientos", df), self.tabla("bci_cartola_cabecera", dfc)]
