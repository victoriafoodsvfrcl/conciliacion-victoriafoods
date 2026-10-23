"""Detalle del estado de cuenta semanal PedidosYa (archivo 'dd-mm al dd-mm.xls').

* Acepta .xls (xlrd) y .xlsx (openpyxl).
* Mapea los DOS conjuntos de encabezados conocidos (estado de cuenta / archivo de comisiones) a
  nombres canónicos; falla si falta una obligatoria.
* ``Tipo`` (Venta/Cargos/Reintegros) sale del nombre de hoja vía mapeo configurable
  (``pya_liquidacion_detalle.hojas_tipo``) o de una columna ``Tipo`` pegada por el equipo.
  Si no se puede determinar, FALLA.
* ``Periodo`` se deriva del nombre de archivo ('28-09 al 04-10') o, en su defecto, de la columna
  ``Periodo`` o del rango de fechas (lunes-domingo).
* Los descuentos PYA vienen NETOS de IVA y pueden traer decimales: se guardan tal cual.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from conciliacion.adapters.base import (
    Adaptador,
    Columna,
    ErrorAdaptador,
    buscar_fila_encabezado,
    leer_excel_crudo,
    mapear_columnas,
)
from conciliacion.adapters.comun import anio_predominante, encabezados_desde_fila, ids, montos, textos
from conciliacion.common.fechas import (
    FechaInvalida,
    detectar_orden,
    lunes_de,
    parse_fecha,
    periodo_desde_nombre,
    texto_periodo,
)
from conciliacion.common.texto import normalizar_encabezado, normalizar_llave
from conciliacion.config import config_negocio

TIPOS_VALIDOS = {
    "VENTA": "Venta",
    "VENTAS": "Venta",
    "CARGOS": "Cargos",
    "CARGO": "Cargos",
    "REINTEGROS": "Reintegros",
    "REINTEGRO": "Reintegros",
}

ALIAS: dict[str, list[str]] = {
    "numero_pedido": ["Numero de Pedido", "Número del pedido"],
    "sucursal": ["Sucursal", "Local"],
    "fecha_pedido": ["Fecha de Pedido", "Fecha del pedido"],
    "monto_bruto": ["Monto bruto de la venta", "Total del pedido"],
    "dcto_comercial_pya_neto": [
        "Descuento comercial neto otorgado x PeYa",
        "Descuento en productos subsidiado por PedidosYa",
    ],
    "dcto_local": ["Descuento otorgado por el local", "Descuento en productos subsidiado por el local"],
    "dcto_usuarios_pya_neto": [
        "Desc. neto otorgado por Peya a usuarios",
        "Descuento al usuario pagado por PedidosYa",
    ],
    "dcto_retiro_pya_neto": [
        "Descuentos neto por Retiro en el local",
        "Descuento por retiro en el local pagado por PedidosYa",
    ],
    "fugaz": ["Descuento por Comodín pago por el local", "Tarifa de descuentos fugaces"],
    "envio_local": ["Envío a cargo del local"],
    "neto_venta": ["Monto neto de la Venta ($)", "Total pagado por el usuario"],
    "dctos_pya_a_cobrar": ["Descuentos PedidosYa a cobrar"],
    "monto_comisionable": ["Monto comisionable Servicios PedidosYa", "Monto comisionable"],
    "pct_comision": ["Servicio Ventas PedidosYA (%)", "Porcentaje de comisión"],
    "comision": ["Servicio Ventas PedidoYa ($)", "Comisión por pedido"],
    "venta_envio_cobrado_local": [
        "Monto venta + envió cobrado por el local",
        "Monto venta + envío cobrado por el local",
    ],
    "cargo_plus": ["Cargo por Pedidos con Plus"],
    "dcto_tarifa_servicio": ["Descuento tarifa de servicio al cliente"],
    "pedido_con_comodin": ["Pedido con Comodin"],
    "es_plus": ["PLUS"],
    "metodo_pago": ["Método de pago"],
    "cobrado_por": ["Cobrado por"],
    "metodo_entrega": ["Método de entrega"],
    # agregadas a mano por el equipo (opcionales)
    "periodo_texto": ["Periodo", "Período"],
    "tipo_columna": ["Tipo"],
}

OBLIGATORIAS = [
    "numero_pedido",
    "sucursal",
    "fecha_pedido",
    "monto_bruto",
    "dcto_comercial_pya_neto",
    "dcto_local",
    "dcto_usuarios_pya_neto",
    "dcto_retiro_pya_neto",
    "fugaz",
    "neto_venta",
    "monto_comisionable",
    "comision",
    "cargo_plus",
    "metodo_pago",
    "metodo_entrega",
]
MONTOS = [
    "monto_bruto",
    "dcto_comercial_pya_neto",
    "dcto_local",
    "dcto_usuarios_pya_neto",
    "dcto_retiro_pya_neto",
    "fugaz",
    "envio_local",
    "neto_venta",
    "dctos_pya_a_cobrar",
    "monto_comisionable",
    "comision",
    "venta_envio_cobrado_local",
    "cargo_plus",
    "dcto_tarifa_servicio",
]
TEXTOS = ["sucursal", "metodo_pago", "cobrado_por", "metodo_entrega", "pedido_con_comodin", "es_plus"]

N = "NUMERIC"
ESQUEMA = [
    Columna("periodo", "STRING", True, "Texto 'dd-mm al dd-mm'"),
    Columna("periodo_inicio", "DATE", True),
    Columna("periodo_fin", "DATE", True),
    Columna("tipo", "STRING", True, "Venta | Cargos | Reintegros"),
    Columna("numero_pedido", "STRING", True),
    Columna("sucursal", "STRING", True),
    Columna(
        "sucursal_norm", "STRING", True, "normalizar_llave(sucursal) para JOIN con maestra (sistema=pya)"
    ),
    Columna("fecha_pedido", "DATE", True),
    *(Columna(m, N) for m in MONTOS),
    Columna("pct_comision", N, False, "fracción (0.16)"),
    *(Columna(t, "STRING") for t in TEXTOS if t != "sucursal"),
    Columna("version_encabezado", "STRING", True, "estado_cuenta | comisiones"),
]


def _pct(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str):
        v = v.replace("%", "")
    from conciliacion.common.montos import parse_monto

    x = parse_monto(v)
    if x is None:
        return None
    return x / 100 if x > 1 else x


class PyaLiquidacionDetalle(Adaptador):
    source_id = "pya_liquidacion_detalle"
    schema_version = "1"
    descripcion = "Detalle semanal del estado de cuenta PedidosYa (pedido x tipo)"
    patrones = (
        r"\d{1,2}-\d{1,2}\s*al\s*\d{1,2}-\d{1,2}[^/]*\.xlsx?$",
        r"pya.*(detalle|comision)[^/]*\.xlsx?$",
    )
    esquemas = {"pya_liquidacion_detalle": ESQUEMA}

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        cfg = {**config_negocio()["pya_liquidacion_detalle"], **params}
        hojas_tipo = {normalizar_llave(k): v for k, v in cfg["hojas_tipo"].items()}
        ignorar = {normalizar_llave(h) for h in cfg.get("hojas_ignorar", [])}
        partes, advertencias = [], []

        for hoja, crudo in leer_excel_crudo(contenido, nombre).items():
            if crudo.dropna(how="all").empty or normalizar_llave(hoja) in ignorar:
                continue
            i = buscar_fila_encabezado(crudo, ALIAS["numero_pedido"], fuente=self.source_id, alguno=True)
            nombres, datos = encabezados_desde_fila(crudo, i)
            mapeo = mapear_columnas(nombres, ALIAS, OBLIGATORIAS, self.source_id, hoja)
            df = pd.DataFrame({canon: datos[nombres.index(orig)] for orig, canon in mapeo.items()})
            df["_fila"] = datos["_fila"]
            df["_hoja"] = hoja
            df["version_encabezado"] = (
                "comisiones"
                if normalizar_encabezado("Local") in {normalizar_encabezado(n) for n in nombres}
                else "estado_cuenta"
            )
            # Tipo: columna pegada a mano > nombre de hoja configurado > error
            if "tipo_columna" in df:
                tipos = df["tipo_columna"].map(lambda v: TIPOS_VALIDOS.get(normalizar_llave(v) or ""))
                if tipos.isna().any():
                    malos = df.loc[tipos.isna(), "tipo_columna"].unique().tolist()
                    raise ErrorAdaptador(
                        f"[{self.source_id}] valores de Tipo no reconocidos en hoja '{hoja}': {malos}"
                    )
                df["tipo"] = tipos
            elif normalizar_llave(hoja) in hojas_tipo:
                df["tipo"] = hojas_tipo[normalizar_llave(hoja)]
            else:
                raise ErrorAdaptador(
                    f"[{self.source_id}] No se reconoce la hoja '{hoja}' de '{nombre}' como Venta/Cargos/Reintegros. "
                    "Agregue el nombre a pya_liquidacion_detalle.hojas_tipo o a hojas_ignorar en la configuración."
                )
            partes.append(df)

        if not partes:
            raise ErrorAdaptador(f"[{self.source_id}] '{nombre}' no tiene hojas con datos")
        df = pd.concat(partes, ignore_index=True)

        # Filas de totales o basura: numero_pedido debe ser numérico
        df["numero_pedido"] = ids(df["numero_pedido"])
        validos = df["numero_pedido"].fillna("").str.fullmatch(r"\d+")
        if (~validos).any():
            advertencias.append(f"Se descartan {int((~validos).sum())} filas sin número de pedido numérico")
        df = df.loc[validos].copy()

        # Periodo
        anio = anio_predominante(df["fecha_pedido"].tolist())
        periodo = periodo_desde_nombre(nombre, anio)
        if periodo is None and "periodo_texto" in df and df["periodo_texto"].notna().any():
            periodo = periodo_desde_nombre(str(df["periodo_texto"].dropna().iloc[0]), anio)
        orden = detectar_orden(
            df["fecha_pedido"].tolist(), por_defecto=cfg["orden_fecha_defecto"], rango=periodo
        )
        try:
            df["fecha_pedido"] = df["fecha_pedido"].map(lambda v: parse_fecha(v, orden))
        except FechaInvalida as e:
            raise ErrorAdaptador(f"[{self.source_id}] {e}") from e
        if periodo is None:
            ini = lunes_de(min(df["fecha_pedido"]))
            periodo = (ini, ini + timedelta(days=6))
            advertencias.append("Periodo derivado del rango de fechas (el nombre de archivo no lo indica)")
        fuera = df["fecha_pedido"].map(
            lambda d: not (periodo[0] - timedelta(days=1) <= d <= periodo[1] + timedelta(days=1))
        )
        if fuera.any() and (df.loc[fuera, "tipo"] == "Venta").any():
            advertencias.append(
                f"{int(fuera.sum())} filas con fecha fuera del periodo {texto_periodo(*periodo)}"
            )
        df["periodo_inicio"], df["periodo_fin"] = periodo
        df["periodo"] = texto_periodo(*periodo)

        for m in MONTOS:
            df[m] = montos(df[m], self.source_id, m) if m in df else None
        df["pct_comision"] = df["pct_comision"].map(_pct) if "pct_comision" in df else None
        for t in TEXTOS:
            df[t] = (
                (ids(df[t]) if t in ("es_plus", "pedido_con_comodin") else textos(df[t])) if t in df else None
            )
        df["sucursal_norm"] = df["sucursal"].map(normalizar_llave)
        return [self.tabla("pya_liquidacion_detalle", df, advertencias)]
