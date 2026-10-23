from datetime import date, datetime

import pytest

from conciliacion.common.fechas import (
    DMY,
    MDY,
    FechaInvalida,
    detectar_orden,
    parse_fecha,
    parse_fecha_hora,
    periodo_desde_nombre,
    texto_periodo,
)
from conciliacion.common.montos import MontoInvalido, parse_monto, redondear
from conciliacion.common.rut import dv_valido, normalizar_rut
from conciliacion.common.texto import (
    deduplicar_nombres,
    limpiar,
    normalizar_encabezado,
    normalizar_llave,
    quitar_invisibles,
    snake,
)


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("42.818.978", 42818978),
        ("$5.125.528 ", 5125528),
        ("-$1.933", -1933),
        ("$-3.200", -3200),
        (" 23.588 ", 23588),
        ("1275,71", 1275.71),
        ("2.359", 2359),
        ("3.144", 3144),
        ("(1.933)", -1933),
        ("1.933-", -1933),
        ("-$ 88.439.523", -88439523),
        ("$ 0", 0),
        ("2359.47", 2359.47),
        (26370, 26370),
        (2359.47, 2359.47),
        ("1.234.567,89", 1234567.89),
        ("3.200\u200b", 3200),
    ],
)
def test_parse_monto(entrada, esperado):
    assert parse_monto(entrada) == pytest.approx(esperado)


def test_parse_monto_guion_y_vacios():
    assert parse_monto("-") is None
    assert parse_monto(" - ") is None
    assert parse_monto(" - ", guion_como=0.0) == 0
    assert parse_monto("") is None
    assert parse_monto(None) is None
    assert parse_monto(float("nan")) is None


def test_parse_monto_invalido():
    with pytest.raises(MontoInvalido):
        parse_monto("abc")


def test_redondear_half_up_como_bigquery():
    assert redondear(3195.2) == 3195
    assert redondear(2.5) == 3
    assert redondear(-2.5) == -3
    assert redondear(11339.21) == 11339


@pytest.mark.parametrize(
    ("entrada", "orden", "esperado"),
    [
        ("01-10-2026", DMY, date(2026, 10, 1)),
        ("01-10-2026 0:00", DMY, date(2026, 10, 1)),
        ("05/10/2026", DMY, date(2026, 10, 5)),
        ("9/26/2026", MDY, date(2026, 9, 26)),
        ("2026-10-01 10:01", DMY, date(2026, 10, 1)),
        ("2026-10-01T10:01:38", DMY, date(2026, 10, 1)),
        ("05/10/26", DMY, date(2026, 10, 5)),
        (46296, DMY, date(2026, 10, 1)),  # serial Excel
        ("46296", DMY, date(2026, 10, 1)),
        (datetime(2026, 10, 1, 8), DMY, date(2026, 10, 1)),
    ],
)
def test_parse_fecha(entrada, orden, esperado):
    assert parse_fecha(entrada, orden) == esperado


def test_parse_fecha_hora():
    assert parse_fecha_hora("2026-10-01 10:01") == datetime(2026, 10, 1, 10, 1)
    assert parse_fecha_hora("02/10/2026 23:10") == datetime(2026, 10, 2, 23, 10)


def test_detectar_orden():
    assert detectar_orden(["9/26/2026", "9/24/2026"]) == MDY
    assert detectar_orden(["26/09/2026", "01/10/2026"]) == DMY
    # sin evidencia: decide el rango esperado
    assert detectar_orden(["10/1/2026", "10/2/2026"], rango=(date(2026, 10, 1), date(2026, 10, 4))) == MDY
    assert detectar_orden(["1/10/2026"], por_defecto=DMY) == DMY
    with pytest.raises(FechaInvalida):
        detectar_orden(["26/09/2026", "9/26/2026"])


def test_periodo_desde_nombre():
    assert periodo_desde_nombre("28-09 al 04-10.xls", 2026) == (date(2026, 9, 28), date(2026, 10, 4))
    assert periodo_desde_nombre("29-12 al 04-01.xls", 2027) == (date(2026, 12, 29), date(2027, 1, 4))
    assert periodo_desde_nombre("otro.xls", 2026) is None
    assert texto_periodo(date(2026, 9, 28), date(2026, 10, 4)) == "28-09 al 04-10"


def test_texto_invisibles_y_mojibake():
    assert quitar_invisibles("2306915845\u200b") == "2306915845"
    assert limpiar("Melt Pizzas - PeÃ±alolÃ©n") == "Melt Pizzas - Peñalolén"
    assert limpiar("  Motivo atribuible a Cliente o PedidosYa ") == "Motivo atribuible a Cliente o PedidosYa"
    assert limpiar("\u200b") is None


def test_normalizar_llave():
    assert normalizar_llave("Melt Pizzas - PeÃ±alolÃ©n") == "MELT PIZZAS - PENALOLEN"
    assert normalizar_llave("Melt Pizzas – Peñalolén ") == "MELT PIZZAS - PENALOLEN"
    assert normalizar_llave("Melt Pizzas -Concón") == "MELT PIZZAS - CONCON"
    assert normalizar_llave("Delivery Hero E-commerce Chile Spa") == "DELIVERY HERO E-COMMERCE CHILE SPA"
    assert normalizar_llave("  ") is None


def test_normalizar_encabezado_y_snake():
    assert (
        normalizar_encabezado(" Descuento comercial neto otorgado x PeYa ")
        == "DESCUENTO COMERCIAL NETO OTORGADO X PEYA"
    )
    assert normalizar_encabezado("¿Tiene algún reclamo?") == "TIENE ALGUN RECLAMO"
    assert snake("Código centro costo") == "codigo_centro_costo"
    assert deduplicar_nombres(["a", "b", "a"]) == ["a", "b", "a_2"]


def test_rut():
    assert normalizar_rut("76.211.425-9") == "76211425-9"
    assert normalizar_rut("762114259") == "76211425-9"
    assert dv_valido("76211425-9")
    assert dv_valido("76800177-4")
