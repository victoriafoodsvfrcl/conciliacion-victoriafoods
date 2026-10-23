import json
from datetime import date, datetime

import pytest

from conciliacion.adapters import ErrorAdaptador, resolver_adaptador
from conciliacion.adapters.aloha_ordenes import AlohaOrdenes
from conciliacion.adapters.bci_cartola import BciCartola
from tests.conftest import xlsx_bytes

ENC_ALOHA = [
    "Ticket ID",
    "DateOfBusiness",
    "Mes",
    "Dia",
    "FKStoreId",
    "OrderMode2",
    "Tienda",
    "CheckNumber",
    "Impuesto",
    "Bruto",
    "Neto",
    "Cliente",
    "Canal",
]
PARAMS = [["Parámetro", "Desde", "10/1/2026"], ["Parámetro", "Hasta", "10/11/2026"], [None]]
ORDER_MODE = [["Código", "Descripción"], [1, "LLEVAR"], [3, "DELIVERY"], [9, "PEDIDOS YA"], [200, "KIOSKO"]]


def aloha_xlsx(filas, nombre_hoja="BBDD Ventas"):
    return xlsx_bytes(
        {
            nombre_hoja: [*PARAMS, ENC_ALOHA, *filas],
            "Order Mode": ORDER_MODE,
            "BBDD Locales": [["StoreId", "Tienda"], [5, "Quilpue"]],
        }
    )


def test_aloha_melt_mdy_y_montos_texto():
    filas = [
        [
            "46296/5/980002",
            "10/1/2026",
            "10",
            "1",
            5,
            9,
            "Quilpue",
            980002,
            " 3.972 ",
            " 24.880 ",
            " 20.908 ",
            "Nicole Saavedra",
            "PEDIDOS YA",
        ],
        [
            "46297/5/980010",
            "10/2/2026",
            "10",
            "2",
            5,
            0,
            "Quilpue",
            980010,
            " - ",
            "-$5.000",
            "-4201,68",
            "Anula",
            "LOCAL",
        ],
        [
            "46303/5/980020",
            "10/13/2026",
            "10",
            "13",
            5,
            3,
            "Quilpue",
            980020,
            100,
            626.5,
            526.5,
            "x",
            "DELIVERY",
        ],
    ]
    assert resolver_adaptador("Ordenes Melt Octubre.xlsx").source_id == "aloha_ordenes"
    ords, modes = AlohaOrdenes().parse(aloha_xlsx(filas), "Ordenes Melt Octubre.xlsx")
    df = ords.df
    assert set(df["marca"]) == {"MELT"}
    assert list(df["date_of_business"]) == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 13)]
    assert df.iloc[0]["bruto"] == 24880 and df.iloc[0]["neto"] == 20908 and df.iloc[0]["impuesto"] == 3972
    assert df.iloc[1]["impuesto"] == 0 and df.iloc[1]["neto"] == pytest.approx(-4201.68)
    assert bool(df.iloc[1]["es_anulacion"]) and not bool(df.iloc[0]["es_anulacion"])
    assert df.iloc[0]["store_id"] == "5" and df.iloc[0]["check_number"] == "980002"
    assert df.iloc[0]["_fila"] == 5  # 3 filas de parámetros + encabezado
    assert dict(zip(modes.df["codigo"], modes.df["descripcion"], strict=True))[9] == "PEDIDOS YA"


def test_aloha_under_dmy_por_defecto():
    filas = [
        ["x", "01/10/2026", "10", "1", 5, 9, "Quilpue", 1, 0, 100, 84, "a", "PEDIDOS YA"],
        ["y", "05/10/2026", "10", "5", 5, 9, "Quilpue", 2, 0, 100, 84, "a", "PEDIDOS YA"],
    ]
    [ords, _] = AlohaOrdenes().parse(aloha_xlsx(filas), "Ordenes Under.xlsx")
    assert list(ords.df["date_of_business"]) == [date(2026, 10, 1), date(2026, 10, 5)]
    assert set(ords.df["marca"]) == {"UNDER"}
    [ords, _] = AlohaOrdenes().parse(aloha_xlsx(filas), "Ordenes Under Express.xlsx")
    assert set(ords.df["marca"]) == {"EXPRESS"}
    assert list(ords.df["date_of_business"]) == [date(2026, 1, 10), date(2026, 5, 10)]  # defecto m/d Express


def test_aloha_sin_marca_o_sin_hoja_falla():
    with pytest.raises(ErrorAdaptador, match="marca"):
        AlohaOrdenes().parse(aloha_xlsx([]), "ventas.xlsx")
    with pytest.raises(ErrorAdaptador, match="BBDD Ventas"):
        AlohaOrdenes().parse(aloha_xlsx([], nombre_hoja="Otra"), "Ordenes Melt.xlsx")


# ---------------------------------------------------------------------------------------------
ENC_BCI = [
    "Fecha transacción",
    "Fecha contable (*)",
    "Oficina",
    "Movimiento",
    "N° documento",
    "Cargo (-)",
    "Abono (+)",
    "Glosa detalle",
    "RUT de origen",
    "Nombre de origen",
    "Banco de origen",
    "Descripción tipo de pago",
    "Comentario transferencia",
    "N° Operación",
    "Código transferencia",
    "Saldo",
    "Código transferencia",
]


def bci_xlsx():
    cab = (
        [
            ["Cartola de Cuenta Corriente"],
            [None],
            ["Empresa", "INVERSIONES VFR I SPA"],
            ["Rut Empresa", "76.800.177-4"],
            ["Número de cuenta CLP", "10685421"],
            ["Rango de fecha", "01/10/2026 - 16/10/2026"],
        ]
        + [[None]] * 9
        + [["Lista de movimientos"]]
    )
    movs = [
        [
            "02/10/2026 23:10",
            "05/10/2026",
            "Central",
            "Transferencia",
            "0",
            "",
            "407.539.735",
            "Pago recibido de Delivery Hero E-",
            "76.211.425-9",
            "Delivery Hero E-Commerce",
            "BCI",
            "TEF",
            "",
            "031002454507",
            "A1",
            "1.000",
            "B1",
        ],
        [
            "09/10/2026 22:00",
            "13/10/2026",
            "Central",
            "Transferencia",
            "0",
            "",
            "483.396.358",
            "Pago recibido de Delivery Hero E-",
            "762114259",
            "Delivery Hero",
            "BCI",
            "TEF",
            "",
            "031002999999",
            "A2",
            "",
            "B2",
        ],
        [
            "01/10/2026 10:00",
            "01/10/2026",
            "Central",
            "Transferencia",
            "0",
            "",
            "4.717.440",
            "Pago recibido de Delivery Hero E-",
            "76211425-9",
            "Delivery Hero",
            "BCI",
            "TEF",
            "",
            "1",
            "",
            "",
            "",
        ],
        [
            "03/10/2026 10:00",
            "03/10/2026",
            "Central",
            "Transferencia",
            "0",
            "",
            "1.000.000",
            "Transferencia de MERCADOPAGO",
            "",
            "MercadoPago SpA",
            "",
            "TEF",
            "",
            "2",
            "",
            "",
            "",
        ],
        [
            "04/10/2026 10:00",
            "04/10/2026",
            "Central",
            "Cargo",
            "0",
            "1.933",
            "",
            "Comisión",
            "",
            "",
            "",
            "",
            "",
            "3",
            "",
            "",
            "",
        ],
        [
            "Saldo final",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ],
    ]
    return xlsx_bytes({"Cuenta-Corriente": [*cab, ENC_BCI, *movs]})


def test_bci_cartola():
    assert resolver_adaptador("Cartola BCI octubre.xlsx").source_id == "bci_cartola"
    movs, cab = BciCartola().parse(bci_xlsx(), "Cartola BCI octubre.xlsx")
    df = movs.df
    assert len(df) == 5  # "Saldo final" descartado
    r = df.iloc[0]
    assert r["fecha_transaccion"] == datetime(2026, 10, 2, 23, 10)
    assert r["fecha_contable"] == date(2026, 10, 5)
    assert r["abono"] == 407_539_735 and r["monto"] == 407_539_735
    assert r["rut_origen"] == "76211425-9" and r["contraparte"] == "PEDIDOSYA"
    assert r["nro_operacion"] == "031002454507"
    assert (r["codigo_transferencia"], r["codigo_transferencia_2"]) == ("A1", "B1")
    assert json.loads(r["extra_json"]) == {"saldo": "1.000"}
    assert df.iloc[1]["contraparte"] == "PEDIDOSYA"  # RUT sin formato
    assert df.iloc[3]["contraparte"] == "MERCADO_PAGO"  # por nombre
    assert df.iloc[4]["monto"] == -1933 and df.iloc[4]["contraparte"] is None
    assert df["movimiento_id"].is_unique
    c = cab.df.iloc[0]
    assert (
        c["empresa"] == "INVERSIONES VFR I SPA"
        and c["rut_empresa"] == "76800177-4"
        and c["cuenta"] == "10685421"
    )
    assert c["rango_desde"] == date(2026, 10, 1) and c["rango_hasta"] == date(2026, 10, 16)


def test_bci_movimiento_id_estable_entre_cartolas_solapadas():
    a, _ = BciCartola().parse(bci_xlsx(), "Cartola 1.xlsx")
    b, _ = BciCartola().parse(bci_xlsx(), "Cartola 2.xlsx")
    assert list(a.df["movimiento_id"]) == list(b.df["movimiento_id"])
