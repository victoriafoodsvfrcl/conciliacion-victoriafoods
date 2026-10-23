from datetime import date

import pytest

from conciliacion.adapters import ErrorAdaptador, resolver_adaptador
from conciliacion.adapters.innovaweb_informes import InnovawebInformes
from conciliacion.adapters.maestra_locales import MaestraLocales, conflictos_alias
from tests.conftest import xlsx_bytes

ZW = "\u200b"
A = InnovawebInformes()


def test_caja_unpivot_layouts_distintos():
    melt = [
        [
            "Código centro costo",
            "Sucursal",
            "Fecha de informe",
            "Creado por",
            "Estado",
            "Ventas Aloha",
            "Boleta Electrónica",
            "Total Ventas",
            "Efectivo",
            "Pedidos YA",
            "Uber",
            "VTA ONLINE POR COBRAR",
            "Total de Recaudación",
            "Diferencia de Caja",
            "Recaudación - Ventas",
        ],
        [
            "L010",
            "La Florida",
            "01-10-2026",
            "ana",
            "Cerrado",
            "$5.125.528 ",
            "$5.125.528 ",
            "$5.125.528 ",
            "$1.000.000",
            "$2.000.000",
            "$500.000",
            "$0",
            "$5.123.595",
            "-$1.933",
            "-$1.933",
        ],
    ]
    under = [
        [
            "Código centro costo",
            "Sucursal",
            "Fecha de informe",
            "Creado por",
            "Estado",
            "Total Ventas",
            "Pedidos Ya",
            "Total de Recaudación",
        ],
        ["U001", "Under Centro", "01-10-2026", "ana", "Cerrado", "$100", "$100", "$100"],
    ]
    c1 = A.parse(xlsx_bytes({"Worksheet": melt}), "innovaweb caja melt.xlsx")[0]
    c2 = A.parse(xlsx_bytes({"Worksheet": under}), "innovaweb caja under.xlsx")[0]
    assert c1.tabla == c2.tabla == "innovaweb_caja"
    d1 = c1.df.set_index("concepto_original")
    assert d1.loc["Pedidos YA", "monto"] == 2_000_000 and d1.loc["Pedidos YA", "categoria"] == "medio_pago"
    assert (
        d1.loc["Diferencia de Caja", "monto"] == -1933
        and d1.loc["Diferencia de Caja", "categoria"] == "resumen"
    )
    assert d1.loc["Ventas Aloha", "categoria"] == "venta"
    assert d1.loc["Pedidos YA", "fecha"] == date(2026, 10, 1)
    assert d1.loc["Pedidos YA", "concepto_norm"] == "PEDIDOS YA"
    d2 = c2.df.set_index("concepto_original")
    assert d2.loc["Pedidos Ya", "concepto_norm"] == "PEDIDOS YA"
    assert set(c2.df["n_columnas_layout"]) == {8}


def test_reembolsos_quita_zero_width():
    filas = [
        [
            "Código centro costo",
            "Sucursal",
            "Fecha Reporte",
            "Tipo de Recaudación",
            "Agregador",
            "Código de orden",
            "Número de boleta",
            "Valor",
            "Fecha Ingreso Reembolso",
        ],
        [
            "L010",
            "La Florida",
            "01-10-2026",
            "Descuentos Pedidos Ya a Clientes",
            "Pedidos YA",
            "2306915845" + ZW,
            "33523907" + ZW,
            "$-3.200",
            "02-10-2026",
        ],
        [
            "U001",
            "Under Centro",
            "01-10-2026",
            "Compensaciones de Agregador",
            "Pedidos Ya",
            "2306915846" + ZW,
            "0" + ZW,
            "$1.964",
            "02-10-2026",
        ],
    ]
    [tp] = A.parse(xlsx_bytes({"Worksheet": filas}), "innovaweb reembolsos.xlsx")
    assert tp.tabla == "innovaweb_reembolsos"
    r = tp.df.iloc[0]
    assert r["codigo_orden"] == "2306915845" and r["numero_boleta"] == "33523907"
    assert r["valor"] == -3200
    assert set(tp.df["agregador_norm"]) == {"PEDIDOS YA"}


def test_ventas_y_recaudaciones():
    ventas = [
        [
            "Código centro costo",
            "Sucursal",
            "Fecha",
            "Tipo de Documento",
            "Desde",
            "Hasta",
            "Monto",
            "Agregador",
            "¿Boleta Nueva?",
            "Comentario",
        ],
        [
            "l010",
            "La Florida",
            "01-10-2026",
            "Boleta Electrónica",
            33523900,
            33523999,
            "$1.000",
            "Pedidos YA",
            "No",
            None,
        ],
    ]
    [tv] = A.parse(xlsx_bytes({"Worksheet": ventas}), "innovaweb_ventas.xlsx")
    assert (
        tv.tabla == "innovaweb_ventas"
        and tv.df.iloc[0]["cc"] == "L010"
        and tv.df.iloc[0]["desde"] == "33523900"
    )
    enc = [
        "Código centro costo",
        "Sucursal",
        "Fecha",
        "Creado por",
        "Fecha de Creación",
        "Tipo de Recaudación",
        "Número",
        "Efectivo",
        "Perla",
        "Residencial",
        "Cheque",
        "Débito",
        "Crédito",
        "Empresa",
        "POS / Multicaja",
        "Agregador",
        "N° Transacción",
        "Valor",
        "Cantidad",
        "Monto",
        "Comentario",
    ]
    fila = [
        "L010",
        "La Florida",
        "01-10-2026",
        "ana",
        "01-10-2026 22:10",
        "Agregador",
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        "Pedidos YA",
        "T1",
        "$2.000.000",
        1,
        "$2.000.000",
        None,
    ]
    [tr] = A.parse(xlsx_bytes({"Worksheet": [enc, fila]}), "innovaweb_recaudaciones.xlsx")
    assert tr.tabla == "innovaweb_recaudaciones" and tr.df.iloc[0]["monto"] == 2_000_000


def test_informe_desconocido_falla():
    with pytest.raises(ErrorAdaptador, match="tipo de informe"):
        A.parse(xlsx_bytes({"Worksheet": [["Código centro costo", "Otra"], ["L1", 1]]}), "innovaweb x.xlsx")


# ---------------------------------------------------------------------------------------------
ENC_MAESTRA = [
    "Marca",
    "Centro de Costo",
    "Sucursal Plataforma",
    "Nombre IDTE",
    "Sucursal Uber",
    "Sucursal PYA",
    "Sucursal RPI",
    "ID Sucursal Rappi",
    "Sucursal OLO Reaction",
    "Sucursal GoodMeal",
    "Sucursal Aloha",
    "Sucursal Getnet",
    "Mercado Pago",
    "Brinks",
    "Uber Direct",
    "Plataforma Ticket",
    "Código Brinks",
    "Sucursal SAP",
    "Sucursal Justo",
    "Sucursal Redelcom",
    "Uber Direct2",
    "OLO Nuevo",
    "Cheaf",
    "Edenred",
    "Klap",
]


def fila_m(marca, cc, plataforma=None, idte=None, pya=None, aloha=None, rappi=None):
    f = [None] * len(ENC_MAESTRA)
    f[0], f[1], f[2], f[3], f[5], f[10], f[7] = marca, cc, plataforma, idte, pya, aloha, rappi
    return f


MAESTRA = [
    ENC_MAESTRA,
    fila_m("MELT", "L010", "La Florida", "LA FLORIDA", "Melt Pizzas - La Florida", "La Florida", 0),
    fila_m("MELT", "L050", "Angamos", "ANGAMOS", "Melt Pizzas - Angamos", "Angamos", 12345),
    fila_m("MELT", "L046", "Antofagasta 2", "ANTOFAGASTA 2", "Melt Pizzas - Antofagasta2", "Antofagasta2"),
    fila_m("MELT", "L020", "Peñalolén", "PEÑALOLEN", "Melt Pizzas - PeÃ±alolÃ©n", "Penalolen"),
    fila_m(None, None, None, None, "Melt Pizzas - Peñalolén Alto"),  # fila de alias adicional sin CC
    fila_m("EXPRESS", "LUE001", "Under Express Centro", "UNDER EXPRESS CENTRO", "Under Express - Centro"),
    fila_m("EXPRESS", "LF001", "Feelgood Centro", "FEELGOOD CENTRO", "Feelgood - Centro"),
]


def test_maestra_alias_y_trampas():
    assert resolver_adaptador("Locales - CC.xlsx").source_id == "maestra_locales"
    dl, da = MaestraLocales().parse(xlsx_bytes({"Hoja1": MAESTRA}), "Locales - CC.xlsx")
    pya = da.df[da.df["sistema"] == "pya"].set_index("alias_normalizado")["cc"]
    assert pya["MELT PIZZAS - ANGAMOS"] == "L050"
    assert pya["MELT PIZZAS - ANTOFAGASTA2"] == "L046"
    assert pya["MELT PIZZAS - LA FLORIDA"] == "L010"
    assert pya["MELT PIZZAS - PENALOLEN"] == "L020"
    assert pya["MELT PIZZAS - PENALOLEN ALTO"] == "L020"  # heredó CC de la fila anterior
    orig = da.df[(da.df["sistema"] == "pya") & (da.df["cc"] == "L020")]["alias_original"].tolist()
    assert "Melt Pizzas - Peñalolén" in orig  # mojibake reparado
    rappi = da.df[da.df["sistema"] == "rappi_id"]
    assert list(rappi["alias_original"]) == ["12345"]  # el 0 no es alias
    assert dl.df.set_index("cc").loc["L010", "nombre_plataforma"] == "La Florida"
    assert dl.df.set_index("cc").loc["LUE001", "marca"] == "EXPRESS"
    assert tp_sin_conflictos(da)


def tp_sin_conflictos(tp):
    return conflictos_alias(tp.df).empty and tp.advertencias == []


def test_maestra_conflicto_de_alias_se_reporta():
    filas = [*MAESTRA, fila_m("MELT", "L099", "Otra", "OTRA", "Melt Pizzas - Angamos")]
    _, da = MaestraLocales().parse(xlsx_bytes({"Hoja1": filas}), "Locales - CC.xlsx")
    c = conflictos_alias(da.df)
    assert set(c["cc"]) == {"L050", "L099"}
    assert any("MELT PIZZAS - ANGAMOS" in a for a in da.advertencias)


def test_maestra_marca_desconocida_falla():
    with pytest.raises(ErrorAdaptador, match="marcas desconocidas"):
        MaestraLocales().parse(
            xlsx_bytes({"Hoja1": [ENC_MAESTRA, fila_m("OTRA", "X1", "a", "b", "c", "d")]}),
            "Locales - CC.xlsx",
        )
