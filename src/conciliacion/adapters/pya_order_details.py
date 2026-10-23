"""orderDetails del portal PedidosYa (xlsx, hoja 'Sheet1', encabezado en 2 filas).

Fila 1: grupos combinados (Metadatos del pedido, Operaciones, Problemas operativos, Ingresos,
Deducciones, Pago, Información adicional). Fila 2: nombres de columna. Se usa la fila 2 y se
guarda el grupo de cada columna sólo para validar el layout.
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
from conciliacion.common.fechas import FechaInvalida, parse_fecha_hora
from conciliacion.common.texto import normalizar_llave

# (encabezado original, canónico, tipo)
COLUMNAS: list[tuple[str, str, str]] = [
    ("Nombre del local", "nombre_local", "STRING"),
    ("Nro de pedido", "nro_pedido", "STRING"),
    ("ID de tienda", "id_tienda", "STRING"),
    ("Tipo de entrega", "tipo_entrega", "STRING"),
    ("Forma de pago", "forma_pago", "STRING"),
    ("Es un pedido Pro", "es_pedido_pro", "STRING"),
    ("Dirección del restaurante", "direccion_restaurante", "STRING"),
    ("Estado del pedido", "estado_pedido", "STRING"),
    ("Fecha del pedido", "fecha_pedido", "DATETIME"),
    ("Aceptado en", "aceptado_en", "DATETIME"),
    ("Lista para retiro", "lista_para_retiro", "DATETIME"),
    ("Repartidor cerca del local", "repartidor_cerca", "DATETIME"),
    ("Retirado del local el", "retirado_el", "DATETIME"),
    ("Entregado el", "entregado_el", "DATETIME"),
    ("¿Tiene algún reclamo?", "tiene_reclamo", "STRING"),
    ("Motivo del reclamo", "motivo_reclamo", "STRING"),
    ("Cancelado el", "cancelado_el", "DATETIME"),
    ("Motivo de rechazo", "motivo_rechazo", "STRING"),
    ("Responsable del rechazo", "responsable_rechazo", "STRING"),
    ("Total parcial", "total_parcial", "NUMERIC"),
    ("Tarifa mínima por pedido", "tarifa_minima", "NUMERIC"),
    ("Reembolsos a la tienda", "reembolsos_tienda", "NUMERIC"),
    ("Total de cargos al cliente", "total_cargos_cliente", "NUMERIC"),
    ("Impuesto", "impuesto", "NUMERIC"),
    ("Comisión por pago en línea", "comision_pago_linea", "NUMERIC"),
    ("Descuento financiado por usted", "descuento_financiado_usted", "NUMERIC"),
    ("Vale financiado por usted", "vale_financiado_usted", "NUMERIC"),
    ("Comisión", "comision", "NUMERIC"),
    ("Gastos operativos", "gastos_operativos", "NUMERIC"),
    ("Costes de publicidad", "costes_publicidad", "NUMERIC"),
    ("Tarifa por espera", "tarifa_espera", "NUMERIC"),
    ("Total de costes de marketing", "total_costes_marketing", "NUMERIC"),
    ("Detalles de costes de marketing", "detalles_costes_marketing", "STRING"),
    ("Costes de marketing", "costes_marketing", "NUMERIC"),
    ("Tarifa de cancelación evitable", "tarifa_cancelacion_evitable", "NUMERIC"),
    ("Es pagadero", "es_pagadero", "STRING"),
    ("Ingreso estimado", "ingreso_estimado", "NUMERIC"),
    ("Monto en efectivo ya cobrado por el local", "efectivo_cobrado_local", "NUMERIC"),
    ("Monto adeudado a PedidosYa", "monto_adeudado_pya", "NUMERIC"),
    ("Importe de pago", "importe_pago", "NUMERIC"),
    ("Descuento financiado por PedidosYa", "descuento_financiado_pya", "NUMERIC"),
    ("Cupón financiado por PedidosYa", "cupon_financiado_pya", "NUMERIC"),
    ("Descuento total", "descuento_total", "NUMERIC"),
    ("Total del cupón", "total_cupon", "NUMERIC"),
    ("Importe de impuestos", "importe_impuestos", "NUMERIC"),
    ("Artículos del pedido", "articulos_pedido", "STRING"),
]
ALIAS = {canon: [orig] for orig, canon, _ in COLUMNAS}
OBLIGATORIAS = [
    "nombre_local",
    "nro_pedido",
    "id_tienda",
    "estado_pedido",
    "fecha_pedido",
    "total_parcial",
    "impuesto",
    "descuento_financiado_usted",
    "comision",
    "gastos_operativos",
    "ingreso_estimado",
    "importe_pago",
]
GRUPOS_ESPERADOS = ["METADATOS DEL PEDIDO", "INGRESOS", "DEDUCCIONES", "PAGO"]
IDS = {"nro_pedido", "id_tienda"}

ESQUEMA = [Columna(c, t, c in ("nombre_local", "nro_pedido", "fecha_pedido")) for _, c, t in COLUMNAS]
ESQUEMA.insert(1, Columna("nombre_local_norm", "STRING", True))
ESQUEMA.append(Columna("grupo_encabezado_ok", "BOOL", True))


class PyaOrderDetails(Adaptador):
    source_id = "pya_order_details"
    schema_version = "1"
    descripcion = "Detalle por pedido del portal de partners PedidosYa"
    patrones = (r"order\s*details?[^/]*\.xlsx$",)
    esquemas = {"pya_order_details": ESQUEMA}

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        hojas = leer_excel_crudo(contenido, nombre)
        hoja = "Sheet1" if "Sheet1" in hojas else next(iter(hojas))
        crudo = hojas[hoja]
        i = buscar_fila_encabezado(crudo, ["Nro de pedido", "Nombre del local"], fuente=self.source_id)
        grupos = {normalizar_llave(v) for v in crudo.iloc[i - 1].tolist()} if i > 0 else set()
        grupo_ok = all(g in grupos for g in GRUPOS_ESPERADOS)
        adv = [] if grupo_ok else ["La fila de grupos del encabezado no coincide con el layout conocido"]

        nombres, datos = encabezados_desde_fila(crudo, i)
        mapeo = mapear_columnas(nombres, ALIAS, OBLIGATORIAS, self.source_id, hoja)
        df = pd.DataFrame({canon: datos[nombres.index(orig)] for orig, canon in mapeo.items()})
        df["_fila"], df["_hoja"] = datos["_fila"], hoja
        for _, canon, tipo in COLUMNAS:
            if canon not in df:
                df[canon] = None
            elif canon in IDS:
                df[canon] = ids(df[canon])
            elif tipo == "NUMERIC":
                df[canon] = montos(df[canon], self.source_id, canon)
            elif tipo == "DATETIME":
                try:
                    df[canon] = df[canon].map(parse_fecha_hora)
                except FechaInvalida as e:
                    raise ErrorAdaptador(f"[{self.source_id}] {canon}: {e}") from e
            else:
                df[canon] = textos(df[canon])  # incluye trim de 'Motivo atribuible a Cliente o PedidosYa '
        df = df[df["nro_pedido"].notna()]
        df["nombre_local_norm"] = df["nombre_local"].map(normalizar_llave)
        df["grupo_encabezado_ok"] = grupo_ok
        return [self.tabla("pya_order_details", df, adv)]
