from datetime import date, datetime

import pytest

from conciliacion.adapters import ColumnasFaltantes, ErrorAdaptador, resolver_adaptador
from conciliacion.adapters.idte_boletas import IdteBoletas, derivar_canal
from conciliacion.adapters.sii_rcv import SiiRcv
from conciliacion.config import config_negocio

ENC_IDTE = (
    "Tipo_DTE;nameDTE;Fecha_DTE;Fecha_Hora_Emision;Folio;Pedido;RznRecp;RUTFULL;MntExento;MntNeto;MntIVA;"
    "Recargo_1;Glosa_Recargo1;Recargo_2;Glosa_Recargo2;Descuento1;Glosa_Dscto1;Descuento2;Glosa_Dscto2;"
    "MontoTotal;Sucursal;Caja;Cajero;Estado_SII"
)
FILAS_IDTE = [
    "39;Boleta;01-10-2026 0:00;2026-10-01T10:01:38;33523907;980002;PYA - Nicole Saavedra Arias - 2306915845;"
    "66666666-6;0;20908;3972;0;;0;;4400;DESC. PDO.YA;0;;24880;LOMAS DE SAN ANDRES;3;Francisca Sepulveda;"
    "Aprobado Reparo SII",
    "39;Boleta;01-10-2026 0:00;2026-10-01T09:58:56;35650906;20001;Cliente Generico;66666666-6;0;6454;1226;0;;0;;0;;0;;"
    "7680;MATTA ORIENTE;2;Victor Quintero;Aprobado SII",
    "39;Boleta;02-10-2026 0:00;2026-10-02T12:00:00;35650907;20002;UE - Juan Pérez - 3fA9c;66666666-6;0;8403;1597;0;;0;;"
    "0;;0;;10000;PEÑALOLÉN;1;María Núñez;Aprobado SII",
]


def idte_bytes(filas=FILAS_IDTE, enc=ENC_IDTE) -> bytes:
    return ("\r\n".join([enc, *filas]) + "\r\n").encode("cp1252")


def test_derivar_canal():
    pref = config_negocio()["idte_boletas"]["prefijos"]
    assert derivar_canal("PYA - Nicole Saavedra Arias - 2306915845", pref) == ("PYA", "2306915845")
    assert derivar_canal("PYA - Nombre - 123", pref) == ("PYA", None)  # id no cumple patrón
    assert derivar_canal("UE - Juan - 3fA9c", pref) == ("UE", "3fA9c")
    assert derivar_canal("RPI - Ana - 123456789", pref) == ("RPI", "123456789")
    assert derivar_canal("JTO - Ana - 12345678", pref) == ("JTO", "12345678")
    assert derivar_canal("PC OLO - Ana - 99", pref) == ("PC OLO", None)
    assert derivar_canal("OLO - Ana", pref) == ("OLO", None)
    assert derivar_canal("Goodmeal - Ana", pref) == ("GOODMEAL", None)
    assert derivar_canal("Cliente Generico", pref) == (None, None)
    assert derivar_canal("PYAS Ltda", pref) == (None, None)


def test_parse_idte_cp1252_crlf():
    assert resolver_adaptador("IDTE_boletas_oct.csv").source_id == "idte_boletas"
    [tp] = IdteBoletas().parse(idte_bytes(), "idte.csv")
    df = tp.df
    assert len(df) == 3
    r = df.iloc[0]
    assert r["fecha_dte"] == date(2026, 10, 1)
    assert r["fecha_hora_emision"] == datetime(2026, 10, 1, 10, 1, 38)
    assert (r["canal_prefijo"], r["id_externo"]) == ("PYA", "2306915845")
    assert r["folio"] == "33523907" and r["pedido"] == "980002"
    assert r["monto_total"] == 24880 and r["descuento_1"] == 4400
    assert r["sucursal_norm"] == "LOMAS DE SAN ANDRES"
    assert df.iloc[2]["sucursal"] == "PEÑALOLÉN" and df.iloc[2]["sucursal_norm"] == "PENALOLEN"
    assert df.iloc[2]["cajero"] == "María Núñez"
    assert df.iloc[1]["canal_prefijo"] is None
    assert df.iloc[0]["_fila"] == 2


def test_idte_falta_columna():
    enc = ENC_IDTE.replace("MontoTotal", "Total")
    with pytest.raises(ColumnasFaltantes, match="monto_total"):
        IdteBoletas().parse(idte_bytes(enc=enc), "idte.csv")


ENC_RCV = (
    "Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto;Fecha Recepcion;Fecha Acuse;"
    "Monto Exento;Monto Neto;Monto IVA Recuperable;Monto Iva No Recuperable;Codigo IVA No Rec.;Monto Total;"
    "Monto Neto Activo Fijo;IVA Activo Fijo;IVA uso Comun;Impto. Sin Derecho a Credito;IVA No Retenido;"
    "Tabacos Puros;Tabacos Cigarrillos;Tabacos Elaborados;NCE o NDE sobre Fact. de Compra;"
    "Codigo Otro Impuesto;Valor Otro Impuesto;Tasa Otro Impuesto"
)


def rcv_linea(
    nro,
    folio,
    fecha,
    neto,
    iva,
    total,
    razon="Delivery Hero E-Commerce Chile SPA",
    rut="76211425-9",
    cod="",
    valor="",
    tasa="",
):
    return (
        f"{nro};33;Del Giro;{rut};{razon};{folio};{fecha};{fecha} 10:00:00;;0;{neto};{iva};0;;{total};0;0;0;0;0;"
        f"0;0;0;0;{cod};{valor};{tasa};"
    )


RCV = "\n".join(
    [
        ENC_RCV + ";",
        rcv_linea(1, 3407698, "05/10/2026", 96120343, 18262865, 114383208),
        rcv_linea(2, 3418103, "12/10/2026", 87757576, 16673939, 104431515),
        rcv_linea(
            3,
            3425975,
            "19/10/2026",
            87098223,
            16548662,
            103646885,
            razon="Delivery Hero E-commerce Chile Spa",
        ),
        rcv_linea(
            4,
            555,
            "15/10/2026",
            100000,
            19000,
            139000,
            razon="Distribuidora Ñuble",
            rut="77.777.777-7",
            cod="27",
            valor="10000",
            tasa="10",
        ),
        ";;;;;;;;;;;;;;;;;;;;;;;;28;10000;10;",
        rcv_linea(5, 3325210, "15/10/2026", 16000000, 3040000, 19040000),
    ]
)


def test_parse_rcv_lineas_continuacion():
    assert resolver_adaptador("RCV_COMPRA_REGISTRO_76800177-4_202610.csv").source_id == "sii_rcv"
    docs, otros = SiiRcv().parse(RCV.encode("utf-8-sig"), "rcv.csv")
    assert len(docs.df) == 5  # la continuación NO duplica el documento
    d = docs.df.set_index("folio")
    assert d.loc["3418103", "monto_neto"] == 87757576
    assert d.loc["3418103", "monto_iva_recuperable"] == 16673939
    assert d.loc["3418103", "fecha_docto"] == date(2026, 10, 12)
    assert d.loc["3418103", "rut_proveedor"] == "76211425-9"
    assert d.loc["3425975", "razon_social_norm"] == "DELIVERY HERO E-COMMERCE CHILE SPA"
    assert d.loc["555", "rut_proveedor"] == "77777777-7"
    o = otros.df
    assert list(o["codigo"]) == ["27", "28"]
    assert set(o["folio"]) == {"555"}
    assert o["valor"].sum() == 20000


def test_rcv_continuacion_sin_documento_falla():
    texto = "\n".join([ENC_RCV, ";;;;;;;;;;;;;;;;;;;;;;;;28;10000;10"])
    with pytest.raises(ErrorAdaptador, match="continuación"):
        SiiRcv().parse(texto.encode(), "rcv.csv")
