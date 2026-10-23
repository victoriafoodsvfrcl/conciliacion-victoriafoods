"""Registro de adaptadores. Para agregar un canal: crear el módulo y sumarlo a ``REGISTRO``
(ver docs/adaptadores.md)."""

from __future__ import annotations

from conciliacion.adapters.aloha_ordenes import AlohaOrdenes
from conciliacion.adapters.base import Adaptador, ColumnasFaltantes, ErrorAdaptador, TablaParseada
from conciliacion.adapters.bci_cartola import BciCartola
from conciliacion.adapters.idte_boletas import IdteBoletas
from conciliacion.adapters.innovaweb_informes import InnovawebInformes
from conciliacion.adapters.maestra_locales import MaestraLocales
from conciliacion.adapters.pya_estado_cuenta_pdf import PyaEstadoCuentaPdf
from conciliacion.adapters.pya_liquidacion_detalle import PyaLiquidacionDetalle
from conciliacion.adapters.pya_order_details import PyaOrderDetails
from conciliacion.adapters.sii_rcv import SiiRcv

# El orden importa: el primero que hace match gana (los más específicos primero).
REGISTRO: list[Adaptador] = [
    MaestraLocales(),
    PyaOrderDetails(),
    PyaEstadoCuentaPdf(),
    PyaLiquidacionDetalle(),
    IdteBoletas(),
    SiiRcv(),
    AlohaOrdenes(),
    BciCartola(),
    InnovawebInformes(),
]
POR_ID: dict[str, Adaptador] = {a.source_id: a for a in REGISTRO}

__all__ = [
    "POR_ID",
    "REGISTRO",
    "Adaptador",
    "ColumnasFaltantes",
    "ErrorAdaptador",
    "TablaParseada",
    "adaptador_por_id",
    "resolver_adaptador",
]


def resolver_adaptador(nombre: str, ruta: str = "") -> Adaptador | None:
    for a in REGISTRO:
        if a.matches(nombre, ruta):
            return a
    return None


def adaptador_por_id(source_id: str) -> Adaptador:
    try:
        return POR_ID[source_id]
    except KeyError as e:
        raise ErrorAdaptador(f"No existe adaptador '{source_id}'. Disponibles: {sorted(POR_ID)}") from e
