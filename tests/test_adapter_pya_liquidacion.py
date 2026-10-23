from datetime import date

import pytest

from conciliacion.adapters import ColumnasFaltantes, ErrorAdaptador, resolver_adaptador
from conciliacion.adapters.pya_liquidacion_detalle import PyaLiquidacionDetalle
from tests.conftest import (
    ENC_LIQ_COMISIONES,
    ENC_LIQ_ESTADO,
    VENTA_2297054261,
    VENTA_2299350728,
    xls_bytes,
    xlsx_bytes,
)

A = PyaLiquidacionDetalle()


def test_resuelve_por_nombre():
    assert resolver_adaptador("21-09 al 27-09.xls").source_id == "pya_liquidacion_detalle"
    assert resolver_adaptador("28-09 al 04-10.xlsx").source_id == "pya_liquidacion_detalle"


def test_parse_xlsx_estado_cuenta(liq_xlsx):
    [tp] = A.parse(liq_xlsx, "21-09 al 27-09.xlsx")
    df = tp.df
    assert tp.tabla == "pya_liquidacion_detalle"
    assert set(df["tipo"]) == {"Venta", "Cargos"}
    assert len(df[df["tipo"] == "Venta"]) == 4  # fila "Total" descartada
    assert any("Se descartan 1" in a for a in tp.advertencias)
    v = df[(df["numero_pedido"] == "2297054261") & (df["tipo"] == "Venta")].iloc[0]
    assert v["fecha_pedido"] == date(2026, 9, 24)  # m/d detectado
    assert v["dcto_usuarios_pya_neto"] == 2359
    assert v["fugaz"] == 3144
    assert v["cargo_plus"] == 690
    assert v["pct_comision"] == pytest.approx(0.16)
    assert v["sucursal_norm"] == "MELT PIZZAS - CONCON"
    assert v["periodo"] == "21-09 al 27-09"
    assert v["periodo_inicio"] == date(2026, 9, 21) and v["periodo_fin"] == date(2026, 9, 27)
    assert v["version_encabezado"] == "estado_cuenta"
    assert v["_hoja"] == "Ventas" and v["_fila"] == 3


def test_parse_xls_crudo_version_comisiones():
    contenido = xls_bytes({"Venta": [ENC_LIQ_COMISIONES, VENTA_2299350728]})
    [tp] = A.parse(contenido, "21-09 al 27-09.xls")
    r = tp.df.iloc[0]
    assert r["numero_pedido"] == "2299350728"
    assert r["sucursal"] == "Melt Pizzas - Quilpue"
    assert r["neto_venta"] == 19970 and r["comision"] == 3195
    assert r["version_encabezado"] == "comisiones"
    assert r["tipo"] == "Venta"


def test_columna_tipo_pegada_a_mano():
    enc = [*ENC_LIQ_ESTADO, "Periodo", "Tipo"]
    filas = [
        enc,
        [*VENTA_2299350728, "21-09 al 27-09", "Venta"],
        [*VENTA_2297054261, "21-09 al 27-09", "Reintegros"],
    ]
    [tp] = A.parse(xlsx_bytes({"Hoja pegada": filas}), "liquidacion pya detalle.xlsx")
    assert list(tp.df["tipo"]) == ["Venta", "Reintegros"]
    assert tp.df["periodo_inicio"].iloc[0] == date(2026, 9, 21)


def test_hoja_no_reconocida_falla_fuerte():
    with pytest.raises(ErrorAdaptador, match="No se reconoce la hoja 'Detalle raro'"):
        A.parse(xlsx_bytes({"Detalle raro": [ENC_LIQ_ESTADO, VENTA_2299350728]}), "21-09 al 27-09.xlsx")


def test_hoja_configurable_por_parametro():
    contenido = xlsx_bytes({"Detalle raro": [ENC_LIQ_ESTADO, VENTA_2299350728]})
    [tp] = A.parse(contenido, "21-09 al 27-09.xlsx", hojas_tipo={"Detalle raro": "Venta"})
    assert tp.df["tipo"].iloc[0] == "Venta"


def test_falta_columna_obligatoria():
    enc = [c for c in ENC_LIQ_ESTADO if c != "Servicio Ventas PedidoYa ($)"]
    fila = VENTA_2299350728[:14] + VENTA_2299350728[15:]
    with pytest.raises(ColumnasFaltantes, match="comision"):
        A.parse(xlsx_bytes({"Ventas": [enc, fila]}), "21-09 al 27-09.xlsx")


def test_periodo_desde_fechas_si_nombre_no_lo_indica():
    [tp] = A.parse(xlsx_bytes({"Ventas": [ENC_LIQ_ESTADO, VENTA_2299350728]}), "pya detalle.xlsx")
    assert tp.df["periodo_inicio"].iloc[0] == date(2026, 9, 21)
    assert any("derivado del rango" in a for a in tp.advertencias)
