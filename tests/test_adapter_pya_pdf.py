from datetime import date

import pytest

from conciliacion.adapters import ErrorAdaptador, resolver_adaptador
from conciliacion.adapters.pya_estado_cuenta_pdf import PyaEstadoCuentaPdf, parse_texto
from tests.conftest import PDF_SEMANA_0510, PDF_SEMANA_2109


def test_semana_0510_cuadra_437018419():
    cab, partidas = parse_texto(PDF_SEMANA_0510)
    assert cab["total_liquidado"] == 437_018_419
    assert cab["suma_partidas"] == 437_018_419
    assert cab["cuadra"] is True
    assert cab["advertencias"] == []
    assert cab["periodo_inicio"] == date(2026, 10, 5) and cab["periodo_fin"] == date(2026, 10, 11)
    assert cab["fecha_pago"] == date(2026, 10, 19)
    assert cab["cuenta_terminacion"] == "5421"
    assert cab["banco"] == "BANCO DE CREDITO E INVERSIONES"
    assert cab["razon_social"] == "INVERSIONES VFR I SPA"
    p = {x["concepto_canonico"]: x["monto"] for x in partidas}
    assert p["servicios_pya"] == -88_439_523
    assert p["cargos_operativos"] == -13_466_315
    assert p["costos_envio"] == -1_262_065
    assert p["cargos_rechazo"] == -159_961
    assert p["tarifa_fugaces"] == -271_010
    assert p["reintegros"] == 14_156_402
    assert p["reintegro_cancelados"] == 4_628_861
    assert p["iva_dctos_no_facturados"] == -1
    assert p["iva_comisiones"] == -16_673_939
    assert p["ajustes_liquidacion"] == 0
    assert p["total_pago_fuera_app_a_cobrar"] == 0


def test_semana_2109_ajustes_y_etiqueta_en_linea_separada():
    cab, partidas = parse_texto(PDF_SEMANA_2109)
    p = {x["concepto_canonico"]: x["monto"] for x in partidas}
    assert p["total_pagado_usuarios"] == 500_000_000  # monto en la línea siguiente
    assert p["cargos_reintegros_adicionales"] == 855_310
    assert cab["cuadra"] and cab["total_liquidado"] == 407_539_732
    assert cab["fecha_pago"] == date(2026, 10, 5)


def test_no_cuadra_alerta_o_falla():
    texto = PDF_SEMANA_0510.replace("Impuestos -$ 16.673.939", "Impuestos -$ 16.673.000")
    cab, _ = parse_texto(texto)
    assert cab["cuadra"] is False and cab["diferencia_validacion"] == -939
    assert any("Σ partidas" in a for a in cab["advertencias"])
    with pytest.raises(ErrorAdaptador, match="total liquidado"):
        parse_texto(texto, estricto=True)


def test_seccion_distinta_de_sus_partidas_advierte():
    texto = PDF_SEMANA_0510.replace(
        "Costo por pedidos plus -$ 7.312.520", "Costo por pedidos plus -$ 7.312.000"
    )
    cab, _ = parse_texto(texto)
    assert cab["cuadra"]  # el total cuadra con las secciones
    assert any("Cargos operativos" in a for a in cab["advertencias"])


def test_sin_total_falla():
    with pytest.raises(ErrorAdaptador):
        parse_texto("Servicios PedidosYa -$ 1.000")


def test_adaptador_tablas():
    a = PyaEstadoCuentaPdf()
    assert resolver_adaptador("Estado de cuenta semanal 05-10.pdf").source_id == a.source_id
    cab, part = a.parse(b"", "estado_de_cuenta.pdf", texto=PDF_SEMANA_0510)
    assert cab.tabla == "pya_estado_cuenta_cabecera" and len(cab.df) == 1
    assert bool(cab.df["cuadra"].iloc[0]) is True
    assert part.tabla == "pya_estado_cuenta_partidas"
    assert (part.df["periodo_inicio"] == date(2026, 10, 5)).all()
    assert part.df["concepto_canonico"].is_unique


def test_extraer_texto_pdf_real_generado():
    """Si reportlab está disponible, genera un PDF y valida la extracción con pdfplumber."""
    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    import io

    buf = io.BytesIO()
    c = canvas_mod.Canvas(buf)
    y = 800
    for linea in PDF_SEMANA_0510.splitlines():
        c.drawString(40, y, linea)
        y -= 14
        if y < 40:
            c.showPage()
            y = 800
    c.save()
    cab, part = PyaEstadoCuentaPdf().parse(buf.getvalue(), "estado de cuenta.pdf")
    assert part.df["monto"].notna().all()
    assert cab.df["total_liquidado"].iloc[0] == 437_018_419 and bool(cab.df["cuadra"].iloc[0])
