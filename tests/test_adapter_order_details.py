from datetime import datetime

import pytest

from conciliacion.adapters import ColumnasFaltantes, resolver_adaptador
from conciliacion.adapters.pya_order_details import COLUMNAS, PyaOrderDetails
from conciliacion.reglas.pya import ingreso_estimado_order_details
from tests.conftest import xlsx_bytes

NOMBRES = [orig for orig, _, _ in COLUMNAS]
GRUPOS = (
    ["Metadatos del pedido"]
    + [None] * 6
    + ["Operaciones"]
    + [None] * 6
    + ["Problemas operativos"]
    + [None] * 4
    + ["Ingresos"]
    + [None] * 3
    + ["Deducciones"]
    + [None] * 11
    + ["Pago"]
    + [None] * 4
    + ["Información adicional"]
    + [None] * 5
)


def orden(
    local,
    nro,
    tienda,
    total,
    impuesto,
    dcto_usted,
    comision,
    gastos,
    ingreso,
    importe,
    reclamo="No",
    motivo=None,
    adeudado=0,
    dcto_pya=0,
    estado="Entregado",
):
    v = dict.fromkeys(NOMBRES)
    v.update(
        {
            "Nombre del local": local,
            "Nro de pedido": nro,
            "ID de tienda": tienda,
            "Tipo de entrega": "Delivery de PedidosYa",
            "Forma de pago": "Pago online",
            "Estado del pedido": estado,
            "Fecha del pedido": "2026-10-06 20:15",
            "Entregado el": "2026-10-06 20:51",
            "¿Tiene algún reclamo?": reclamo,
            "Motivo del reclamo": motivo,
            "Total parcial": total,
            "Impuesto": impuesto,
            "Descuento financiado por usted": dcto_usted,
            "Comisión": comision,
            "Gastos operativos": gastos,
            "Ingreso estimado": ingreso,
            "Importe de pago": importe,
            "Monto adeudado a PedidosYa": adeudado,
            "Descuento financiado por PedidosYa": dcto_pya,
            "Responsable del rechazo": "Motivo atribuible a Cliente o PedidosYa ",
        }
    )
    return [v[n] for n in NOMBRES]


ORDENES = [
    orden("Melt Pizzas - Viña Del Mar", 2313311999, 102178, 8290, 252, 0, 1326, 0, 6712, 6712),
    # 2313312481: valores SINTÉTICOS (no se tuvo el detalle real), consistentes con la regla
    orden("Melt Pizzas - Viña Del Mar", 2313312481, 102178, 15980, 389, 3196, 2045, 0, 10350, 10350),
    orden(
        "Melt Pizzas - Viña Del Mar",
        2315079224,
        102178,
        10990,
        314,
        5680,
        960,
        6000,
        -1964,
        0,
        reclamo="Sí",
        motivo="Calidad de la comida",
        adeudado=1964,
        dcto_pya=1450,
    ),
]


@pytest.fixture
def od_xlsx():
    assert len(GRUPOS) == len(NOMBRES)
    return xlsx_bytes(
        {"Sheet1": [GRUPOS, NOMBRES, *ORDENES]},
        merges={"Sheet1": ["A1:G1", "H1:N1", "O1:S1", "T1:W1", "X1:AI1", "AJ1:AN1", "AO1:AT1"]},
    )


def test_parse_order_details(od_xlsx):
    assert resolver_adaptador("orderDetails_2026-10-05.xlsx").source_id == "pya_order_details"
    [tp] = PyaOrderDetails().parse(od_xlsx, "orderDetails.xlsx")
    df = tp.df
    assert len(df) == 3 and tp.advertencias == []
    assert bool(df["grupo_encabezado_ok"].iloc[0])
    r = df.set_index("nro_pedido").loc["2315079224"]
    assert r["id_tienda"] == "102178"
    assert r["fecha_pedido"] == datetime(2026, 10, 6, 20, 15)
    assert r["motivo_reclamo"] == "Calidad de la comida"
    assert r["responsable_rechazo"] == "Motivo atribuible a Cliente o PedidosYa"  # trim
    assert (
        r["monto_adeudado_pya"] == 1964 and r["importe_pago"] == 0 and r["descuento_financiado_pya"] == 1450
    )
    assert r["nombre_local_norm"] == "MELT PIZZAS - VINA DEL MAR"


@pytest.mark.parametrize("nro", ["2313311999", "2313312481", "2315079224"])
def test_regla_ingreso_estimado(od_xlsx, nro):
    [tp] = PyaOrderDetails().parse(od_xlsx, "orderDetails.xlsx")
    r = tp.df.set_index("nro_pedido").loc[nro]
    calc = ingreso_estimado_order_details(
        r["total_parcial"],
        r["descuento_financiado_usted"],
        r["comision"],
        r["impuesto"],
        r["gastos_operativos"],
    )
    assert calc == r["ingreso_estimado"]


def test_falta_columna():
    nombres = [n for n in NOMBRES if n != "Ingreso estimado"]
    with pytest.raises(ColumnasFaltantes, match="ingreso_estimado"):
        PyaOrderDetails().parse(xlsx_bytes({"Sheet1": [GRUPOS[:-1], nombres]}), "orderDetails.xlsx")
