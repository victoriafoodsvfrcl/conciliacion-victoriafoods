"""Reglas PYA con los ejemplos reales (2299350728, 2297054261) y el invariante por pedido."""

from datetime import date

import pytest

from conciliacion.reglas import pya
from conciliacion.reglas.pya import (
    Abono,
    Boleta,
    Factura,
    LineaLiquidacion,
    asignar_abono,
    buscar_factura,
    caja_innovaweb,
    calcular_pedido,
    calcular_semana,
    compensacion_pedido,
    cumple_invariante,
    fecha_real,
    lineas_reembolso,
    pedidos_duplicados,
    residuo_invariante,
    validar_relaciones_linea,
)

INI, FIN = date(2026, 9, 21), date(2026, 9, 27)


def venta_2299350728(**kw):
    return LineaLiquidacion(
        "2299350728",
        "Venta",
        date(2026, 9, 26),
        INI,
        FIN,
        "Melt Pizzas - Quilpue",
        "L030",
        "MELT",
        monto_bruto=26370,
        dcto_local=6400,
        neto_venta=19970,
        monto_comisionable=19970,
        comision=3195,
        **kw,
    )


def venta_2297054261(**kw):
    return LineaLiquidacion(
        "2297054261",
        "Venta",
        date(2026, 9, 24),
        INI,
        FIN,
        "Melt Pizzas - Concón",
        "L031",
        "MELT",
        monto_bruto=16390,
        dcto_local=5051,
        dcto_usuarios_pya_neto=2359,
        fugaz=3144,
        neto_venta=8532,
        monto_comisionable=8195,
        comision=1311,
        cargo_plus=690,
        **kw,
    )


def cargo_2297054261():
    return LineaLiquidacion(
        "2297054261",
        "Cargos",
        date(2026, 9, 24),
        INI,
        FIN,
        "Melt Pizzas - Concón",
        "L031",
        "MELT",
        neto_venta=-1500,
    )


def test_relaciones_verificadas_en_ejemplos_reales():
    assert validar_relaciones_linea(venta_2299350728()) == []
    assert validar_relaciones_linea(venta_2297054261()) == []
    malo = venta_2297054261()
    malo.comision = 1400
    assert validar_relaciones_linea(malo) == ["COMISION"]


def test_pedido_2299350728_sin_descuentos_pya():
    r = calcular_pedido(venta_2299350728(), boleta=Boleta("33523907", 19970, date(2026, 9, 26)))
    assert r.dctos_pya_usuarios_bruto == 0
    assert r.por_cobrar == 19970
    assert r.com_neta == 3195
    assert r.factura_comision_pedido == pytest.approx(3802.05)
    assert r.monto_banco_pedido == pytest.approx(16167.95)
    assert r.com_provision == 3195
    assert r.monto_caja == 19970
    assert r.integracion == 0 and not r.integracion_reportable
    assert r.excepciones == []
    assert cumple_invariante(r)


def test_pedido_2297054261_con_dcto_usuarios_fugaz_plus_y_compensacion():
    v, c = venta_2297054261(), cargo_2297054261()
    comp = compensacion_pedido(v, [v, c])
    assert comp == 1500
    r = calcular_pedido(v, compensacion=comp, boleta=Boleta("F1", 8532, date(2026, 9, 24)))
    assert r.dctos_pya_usuarios_bruto == pytest.approx(2807.21)
    assert r.por_cobrar == 11339
    assert r.com_neta == 5145
    assert r.factura_comision_pedido == pytest.approx(6122.55)
    assert r.monto_banco_pedido == pytest.approx(5216.45)
    assert r.com_provision == 2096
    assert r.reembolsos == -1500
    assert r.monto_caja == 9839
    assert residuo_invariante(r) == pytest.approx(-0.21)
    assert cumple_invariante(r)
    filas = {f["Medio de Pago"]: f for f in lineas_reembolso(r, "L031", "Concón", "MELT")}
    assert filas["Descuentos Pedidos Ya a Clientes"]["Valor"] == -2807
    assert filas["Compensaciones de Agregador"]["Valor"] == 1500
    assert "Integración" not in filas
    assert filas["Compensaciones de Agregador"]["Agregador"] == "Pedidos YA"
    assert filas["Compensaciones de Agregador"]["Fecha"] == "24/09/2026"
    # Innovaweb cuadra: caja − ventas(boleta) + reembolsos = 0
    assert r.monto_caja - 8532 + sum(f["Valor"] for f in filas.values()) == 0


def test_integracion_reportable_y_tolerancia():
    r = calcular_pedido(venta_2297054261(), boleta=Boleta("F1", 8540))
    assert r.integracion == 8 and r.integracion_reportable and "INTEGRACION" in r.excepciones
    assert cumple_invariante(r)
    assert any(
        f["Medio de Pago"] == "Integración" and f["Valor"] == 8
        for f in lineas_reembolso(r, "L031", "Concón", "UNDER")
    )
    r1 = calcular_pedido(venta_2297054261(), boleta=Boleta("F1", 8533))
    assert r1.integracion == 1 and not r1.integracion_reportable


def test_pedido_sin_boleta():
    r = calcular_pedido(venta_2297054261())
    assert r.folio == "-" and r.monto_boleta == 0 and r.integracion == 0
    assert "PEDIDO_SIN_BOLETA" in r.excepciones
    assert r.fecha_real == date(2026, 9, 24)  # cae a la fecha del pedido
    filas = lineas_reembolso(r, "L031", "Concón", "UNDER")
    assert filas[0]["Número de boleta"] == "0" and filas[0]["Agregador"] == "Pedidos Ya"


def test_reintegro_parcial_under_es_compensacion():
    v = venta_2297054261()
    v.marca = "UNDER"
    reint = LineaLiquidacion("2297054261", "Reintegros", neto_venta=2000)
    total = LineaLiquidacion("2297054261", "Reintegros", neto_venta=8532)
    assert compensacion_pedido(v, [reint]) == 2000
    assert compensacion_pedido(v, [total]) == 0  # reintegro total no es compensación
    v.marca = "MELT"
    assert compensacion_pedido(v, [reint]) == 0


def test_fecha_real_prioridad():
    b, a, p = date(2026, 10, 2), date(2026, 10, 1), date(2026, 9, 30)
    assert fecha_real(b, a, p) == b
    assert fecha_real(None, a, p) == a
    assert fecha_real(None, None, p) == p
    assert fecha_real(b, a, p, orden=("aloha", "boleta", "pedido")) == a


def test_caja_innovaweb_agrupa_por_cc_y_fecha():
    filas = caja_innovaweb(
        [
            ("L031", "Concón", date(2026, 9, 24), 9839),
            ("L031", "Concón", date(2026, 9, 24), 100),
            ("L030", "Quilpue", date(2026, 9, 26), 19970),
        ]
    )
    assert filas == [
        {
            "Código centro costo": "L030",
            "Sucursal": "Quilpue",
            "Fecha de informe": "26/09/2026",
            "Pedidos YA": 19970,
        },
        {
            "Código centro costo": "L031",
            "Sucursal": "Concón",
            "Fecha de informe": "24/09/2026",
            "Pedidos YA": 9939,
        },
    ]


def test_duplicados():
    d = LineaLiquidacion("2318734706", "Venta", periodo_inicio=INI)
    assert pedidos_duplicados([d, d, LineaLiquidacion("2318996350", "Venta", periodo_inicio=INI)]) == [
        ("2318734706", "Venta", INI)
    ]


ABONOS = [
    Abono(date(2026, 10, 5), 407_539_735, nro_operacion="031002454507"),
    Abono(date(2026, 10, 1), 4_717_440),
    Abono(date(2026, 10, 13), 483_396_358),
    Abono(date(2026, 10, 16), 42_818_978),
]
FACTURAS = [
    Factura("3407698", date(2026, 10, 5), 96_120_343, 18_262_865),
    Factura("3418103", date(2026, 10, 12), 87_757_576, 16_673_939),
    Factura("3425975", date(2026, 10, 19), 87_098_223, 16_548_662),
    Factura("3325210", date(2026, 10, 15), 16_000_000, 3_040_000),
    Factura("3325217", date(2026, 10, 15), 6_000_000, 1_140_000),
]


def test_abono_real_semana_2109_y_2809():
    a = asignar_abono(date(2026, 9, 27), 407_539_732, ABONOS)
    assert a.nro_operacion == "031002454507" and a.monto - 407_539_732 == 3
    assert asignar_abono(date(2026, 10, 4), 483_396_361, ABONOS).fecha_contable == date(2026, 10, 13)
    assert asignar_abono(date(2026, 10, 4), 483_396_400, ABONOS) is None  # fuera de TOL_BANCO


def test_factura_lunes_siguiente():
    f = buscar_factura(date(2026, 10, 11), 87_757_576, FACTURAS)
    assert f.folio == "3418103" and f.monto_iva == 16_673_939  # = Impuestos del PDF 05/10-11/10
    # las facturas de 16 MM / 6 MM del jueves 15/10 nunca se asignan a una semana
    assert buscar_factura(date(2026, 10, 18), 16_000_000, FACTURAS).folio == "3425975"
    assert (
        buscar_factura(date(2026, 10, 14), 16_000_000, FACTURAS) is None
    )  # periodo que no termina en domingo


def test_semana_sintetica_cuadra():
    v1, v2, c = venta_2299350728(), venta_2297054261(), cargo_2297054261()
    res = calcular_semana(
        [v1, v2, c],
        {"costos_envio": 0, "ajustes_liquidacion": 0},
        19885,
        abonos=[Abono(date(2026, 9, 29), 19887)],
        facturas=[Factura("1", date(2026, 9, 28), 5981, 1136)],
    )
    assert res["neto_factura_calc"] == 5981
    assert res["iva_calc"] == pytest.approx(1136.39)
    assert res["liquidacion_calc"] == 19885
    assert res["dif_liquidacion_pdf"] == 0 and res["dif_pdf_banco"] == 2
    assert res["estado"] == "OK", res["excepciones"]


def test_semana_con_envio_y_ajustes_no_cuadra():
    v1 = venta_2299350728()
    res = calcular_semana([v1], {"costos_envio": -1950, "ajustes_liquidacion": 855_310}, 1)
    assert res["costo_envio"] == 1950
    assert res["neto_factura_calc"] == 3195 + 1950
    assert set(res["excepciones"]) == {"SEMANA_NO_CUADRA_PDF", "ABONO_NO_ENCONTRADO", "FACTURA_NO_ENCONTRADA"}
    assert res["estado"] == "REVISAR"


def test_constantes():
    assert (pya.IVA, pya.COMISION, pya.TOL_REDONDEO, pya.TOL_BANCO) == (0.19, 0.16, 1, 5)
