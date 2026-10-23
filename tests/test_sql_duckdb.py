"""Prueba de integración del SQL de Dataform SIN BigQuery.

1. Compila el proyecto Dataform (CLI) a JSON.
2. Transpila cada acción de BigQuery a DuckDB con sqlglot (con un par de "shims" de dialecto).
3. Construye las tablas ``raw`` aplicando los ADAPTADORES reales a archivos sintéticos.
4. Ejecuta staging -> core -> marts en orden de dependencias y todas las assertions.
5. Compara los resultados con la implementación de referencia ``reglas/pya.py``.

Se omite si no hay CLI de Dataform (variable DATAFORM_CLI o ``dataform`` en el PATH), duckdb o sqlglot.
No reemplaza una ejecución real en BigQuery (tipos NUMERIC, nombres flexibles, etc.).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")
sqlglot = pytest.importorskip("sqlglot")

from conciliacion.adapters import POR_ID  # noqa: E402
from conciliacion.ingest.manifiesto import RegistroArchivo  # noqa: E402
from conciliacion.load.bigquery_loader import agregar_linaje, esquema_completo  # noqa: E402
from conciliacion.reglas import pya  # noqa: E402
from tests.conftest import ENC_LIQ_ESTADO, VENTA_2297054261, VENTA_2299350728, fila_liq, xlsx_bytes  # noqa: E402
from tests.test_adapter_aloha_bci import ENC_ALOHA, ENC_BCI  # noqa: E402
from tests.test_adapter_idte_rcv import ENC_IDTE, ENC_RCV, rcv_linea  # noqa: E402
from tests.test_adapter_innovaweb_maestra import ENC_MAESTRA, fila_m  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
DB = "vf-conciliacion-dev"
TIPOS_DUCK = {
    "STRING": "VARCHAR",
    "INT64": "BIGINT",
    "NUMERIC": "DOUBLE",
    "FLOAT64": "DOUBLE",
    "DATE": "DATE",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMP",
    "BOOL": "BOOLEAN",
    "JSON": "VARCHAR",
}


def _cli() -> list[str] | None:
    if os.environ.get("DATAFORM_CLI"):
        return shlex.split(os.environ["DATAFORM_CLI"])
    return [shutil.which("dataform")] if shutil.which("dataform") else None


@pytest.fixture(scope="module")
def compilado() -> dict:
    cli = _cli()
    if not cli:
        pytest.skip("CLI de Dataform no disponible (defina DATAFORM_CLI)")
    r = subprocess.run(
        [*cli, "compile", "--json"], cwd=RAIZ / "dataform", capture_output=True, text=True, timeout=600
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout[r.stdout.index("{") :])


def a_duckdb(sql: str) -> str:
    out = sqlglot.transpile(sql, read="bigquery", write="duckdb")
    out = ";\n".join(out)
    # shims: FORMAT() de BigQuery es printf-like; %t (cualquier tipo) -> %s
    out = re.sub(r"\bFORMAT\(", "printf(", out)
    # DuckDB no soporta LIMIT dentro de ARRAY_AGG; [1] sobre la lista ordenada es equivalente
    out = out.replace(" LIMIT 1)[1]", ")[1]")
    return out.replace("%t", "%s")


# ---------------------------------------------------------------------------------------------
# Datos sintéticos de UNA semana (21/09 - 27/09/2026) coherentes entre fuentes
# ---------------------------------------------------------------------------------------------
DUP = fila_liq(
    2318734706, "Melt Pizzas - Quilpue", "9/22/2026", 10000, neto=10000, comisionable=10000, comision=1600
)
CARGO = fila_liq(2297054261, "Melt Pizzas - Concón", "9/24/2026", 0, neto=-1500)
LIQ = xlsx_bytes(
    {
        "Ventas": [ENC_LIQ_ESTADO, VENTA_2299350728, VENTA_2297054261, DUP, DUP],
        "Cargos": [ENC_LIQ_ESTADO, CARGO],
    }
)
MAESTRA = xlsx_bytes(
    {
        "Hoja1": [
            ENC_MAESTRA,
            fila_m("MELT", "L030", "Quilpué", "QUILPUE", "Melt Pizzas - Quilpue", "Quilpue"),
            fila_m("MELT", "L031", "Concón", "CONCON", "Melt Pizzas - Concón", "Concon"),
        ]
    }
)
IDTE = (
    "\r\n".join(
        [
            ENC_IDTE,
            "39;Boleta;26-09-2026 0:00;2026-09-26T20:00:00;100;980002;PYA - Ana - 2299350728;66666666-6;0;16782;3188;0;;0;;0;;0;;"
            "19970;QUILPUE;1;x;Aprobado SII",
            "39;Boleta;24-09-2026 0:00;2026-09-24T20:00:00;101;980003;PYA - Bea - 2297054261;66666666-6;0;7176;1364;0;;0;;0;;0;;"
            "8540;CONCON;1;x;Aprobado SII",
            "39;Boleta;25-09-2026 0:00;2026-09-25T20:00:00;102;980004;PYA - Caro - 2399999999;66666666-6;0;840;160;0;;0;;0;;0;;"
            "1000;CONCON;1;x;Aprobado SII",
        ]
    )
    + "\r\n"
).encode("cp1252")
ALOHA = xlsx_bytes(
    {
        "BBDD Ventas": [
            ["p"],
            ["p"],
            [None],
            ENC_ALOHA,
            ["t1", "9/25/2026", "9", "25", 7, 9, "Quilpue", 980002, 3188, 19970, 16782, "Ana", "PYA"],
        ]
    }
)


def lineas_python(df_liq: pd.DataFrame) -> list[pya.LineaLiquidacion]:
    cc = {"MELT PIZZAS - QUILPUE": "L030", "MELT PIZZAS - CONCON": "L031"}
    campos = [
        "monto_bruto",
        "dcto_comercial_pya_neto",
        "dcto_local",
        "dcto_usuarios_pya_neto",
        "dcto_retiro_pya_neto",
        "fugaz",
        "neto_venta",
        "monto_comisionable",
        "comision",
        "cargo_plus",
    ]
    return [
        pya.LineaLiquidacion(
            r.numero_pedido,
            r.tipo,
            r.fecha_pedido,
            r.periodo_inicio,
            r.periodo_fin,
            r.sucursal,
            cc[r.sucursal_norm],
            "MELT",
            **{c: float(getattr(r, c) or 0) for c in campos},
        )
        for r in df_liq.itertuples()
    ]


@pytest.fixture(scope="module")
def mundo(compilado):
    # --- referencia Python ---------------------------------------------------------------
    df_liq = POR_ID["pya_liquidacion_detalle"].parse(LIQ, "21-09 al 27-09.xlsx")[0].df
    lineas = lineas_python(df_liq)
    semana_py = pya.calcular_semana(lineas, {}, 0)
    total_pdf = semana_py["liquidacion_calc"]
    neto_fact = pya.redondear(semana_py["neto_factura_calc"])
    pdf = (
        f"INVERSIONES VFR I SPA\nTotal pagado por los usuarios $ {total_pdf:,.0f}\n"
        f"Total liquidado del 21/09/26 al 27/09/26 $ {total_pdf:,.0f}\n"
    ).replace(",", ".")
    bci = xlsx_bytes(
        {
            "Cuenta-Corriente": [
                ["Rut Empresa", "76.800.177-4"],
                ENC_BCI,
                [
                    "29/09/2026 23:10",
                    "29/09/2026",
                    "C",
                    "T",
                    "0",
                    "",
                    f"{total_pdf + 2:,.0f}".replace(",", "."),
                    "Pago recibido de Delivery Hero E-",
                    "76.211.425-9",
                    "DH",
                    "BCI",
                    "TEF",
                    "",
                    "op1",
                    "",
                    "",
                    "",
                ],
                [
                    "01/10/2026 10:00",
                    "01/10/2026",
                    "C",
                    "T",
                    "0",
                    "",
                    "4.717.440",
                    "Pago recibido de Delivery Hero E-",
                    "76.211.425-9",
                    "DH",
                    "BCI",
                    "TEF",
                    "",
                    "op2",
                    "",
                    "",
                    "",
                ],
            ]
        }
    )
    rcv = "\n".join(
        [
            ENC_RCV + ";",
            rcv_linea(
                1,
                999,
                "28/09/2026",
                neto_fact,
                pya.redondear(neto_fact * 0.19),
                neto_fact + pya.redondear(neto_fact * 0.19),
            ),
        ]
    )
    caja = xlsx_bytes(
        {
            "Worksheet": [
                ["Código centro costo", "Sucursal", "Fecha de informe", "Total de Recaudación", "Pedidos YA"],
                ["L030", "Quilpué", "26-09-2026", "$19.970", "$19.970"],
                ["L031", "Concón", "24-09-2026", "$9.000", "$9.000"],
            ]
        }
    )
    reemb = xlsx_bytes(
        {
            "Worksheet": [
                [
                    "Código centro costo",
                    "Sucursal",
                    "Fecha Reporte",
                    "Tipo de Recaudación",
                    "Agregador",
                    "Código de orden",
                    "Número de boleta",
                    "Valor",
                ],
                [
                    "L031",
                    "Concón",
                    "24-09-2026",
                    "Descuentos Pedidos Ya a Clientes",
                    "Pedidos YA",
                    "2297054261\u200b",
                    "101\u200b",
                    "$-2.807",
                ],
            ]
        }
    )
    archivos = [
        ("maestra_locales", "Locales - CC.xlsx", MAESTRA, {}),
        ("pya_liquidacion_detalle", "21-09 al 27-09.xlsx", LIQ, {}),
        ("pya_estado_cuenta_pdf", "estado.pdf", b"", {"texto": pdf}),
        ("idte_boletas", "idte.csv", IDTE, {}),
        ("aloha_ordenes", "Ordenes Melt.xlsx", ALOHA, {}),
        ("bci_cartola", "cartola.xlsx", bci, {}),
        ("sii_rcv", "rcv.csv", rcv.encode(), {}),
        ("innovaweb", "caja.xlsx", caja, {}),
        ("innovaweb", "reembolsos.xlsx", reemb, {}),
    ]

    con = duckdb.connect()
    con.execute(f"ATTACH ':memory:' AS \"{DB}\"")
    for s in ("raw", "staging", "core", "marts", "assertions"):
        con.execute(f'CREATE SCHEMA "{DB}".{s}')
    for a in POR_ID.values():  # todas las tablas raw existen aunque vengan vacías
        for tabla, esq in a.esquemas.items():
            cols = ", ".join(f'"{c.nombre}" {TIPOS_DUCK[c.tipo]}' for c in esquema_completo(esq))
            con.execute(f'CREATE TABLE "{DB}".raw.{tabla} ({cols})')
    for i, (sid, nombre, contenido, params) in enumerate(archivos):
        reg = RegistroArchivo(f"arch{i}", f"sha{i}", sid, nombre, "", "", len(contenido))
        for tp in POR_ID[sid].parse(contenido, nombre, **params):
            df = agregar_linaje(tp, reg)  # noqa: F841 (usado por duckdb)
            cols = [c.nombre for c in esquema_completo(POR_ID[sid].esquemas[tp.tabla])]
            con.execute(
                f'INSERT INTO "{DB}".raw.{tp.tabla} ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                f"SELECT {', '.join(chr(34) + c + chr(34) for c in cols)} FROM df"
            )

    # --- ejecutar acciones en orden topológico ---------------------------------------------
    acciones = {}
    for t in compilado["tables"]:
        acciones[(t["target"]["schema"], t["target"]["name"])] = ("tabla", t)
    for t in compilado.get("assertions", []):
        acciones[(t["target"]["schema"], t["target"]["name"])] = ("assertion", t)
    hechas, resultados_assert = set(), {}

    def correr(k):
        if k in hechas or k not in acciones:
            return
        tipo, t = acciones[k]
        for dep in t.get("dependencyTargets", []):
            correr((dep["schema"], dep["name"]))
        sql = a_duckdb(t["query"])
        destino = f'"{DB}".{k[0]}.{k[1]}'
        if tipo == "tabla":
            ddl = "VIEW" if t.get("type") == "view" else "TABLE"
            con.execute(f"CREATE {ddl} {destino} AS {sql}")
        else:
            resultados_assert[k[1]] = con.execute(sql).df()
        hechas.add(k)

    for k in list(acciones):
        correr(k)
    return {
        "con": con,
        "lineas": lineas,
        "semana_py": semana_py,
        "asserts": resultados_assert,
        "total_pdf": total_pdf,
    }


def q(mundo, sql):
    return mundo["con"].execute(sql.replace("{DB}", f'"{DB}"')).df()


def test_assertions_sin_filas(mundo):
    fallidas = {k: len(v) for k, v in mundo["asserts"].items() if len(v)}
    assert fallidas == {}, fallidas
    assert "assert_invariante_pedido" in mundo["asserts"]


def test_pedidos_sql_igual_a_python(mundo):
    sql = q(mundo, "SELECT * FROM {DB}.core.fct_pya_pedido").set_index("numero_pedido")
    lineas = mundo["lineas"]
    boletas = {
        "2299350728": pya.Boleta("100", 19970, date(2026, 9, 26)),
        "2297054261": pya.Boleta("101", 8540, date(2026, 9, 24)),
    }
    vistos = set()
    for v in lineas:
        if v.tipo != "Venta" or v.numero_pedido in vistos:
            continue
        vistos.add(v.numero_pedido)
        r = pya.calcular_pedido(v, pya.compensacion_pedido(v, lineas), boletas.get(v.numero_pedido))
        s = sql.loc[v.numero_pedido]
        for campo in [
            "por_cobrar",
            "monto_caja",
            "com_neta",
            "com_provision",
            "compensacion",
            "integracion",
            "monto_boleta",
            "dctos_pya_usuarios_bruto",
            "factura_comision_pedido",
            "monto_banco_pedido",
            "residuo_invariante",
        ]:
            assert float(s[campo]) == pytest.approx(
                getattr(r, campo) if campo != "residuo_invariante" else pya.residuo_invariante(r), abs=1e-6
            ), (v.numero_pedido, campo)
        assert s["folio"] == r.folio
        assert bool(s["integracion_reportable"]) == r.integracion_reportable
        assert pd.Timestamp(s["fecha_real"]).date() == r.fecha_real
    assert len(sql) == 3  # el duplicado cuenta una vez


def test_marts_innovaweb_formato_y_valores(mundo):
    caja = q(mundo, "SELECT * FROM {DB}.marts.out_innovaweb_caja_pya ORDER BY 1, 3")
    assert list(caja.columns) == ["Código centro costo", "Sucursal", "Fecha de informe", "Pedidos YA"]
    assert caja.to_dict("records") == [
        {
            "Código centro costo": "L030",
            "Sucursal": "Quilpué",
            "Fecha de informe": "22/09/2026",
            "Pedidos YA": 10000,
        },
        {
            "Código centro costo": "L030",
            "Sucursal": "Quilpué",
            "Fecha de informe": "26/09/2026",
            "Pedidos YA": 19970,
        },
        {
            "Código centro costo": "L031",
            "Sucursal": "Concón",
            "Fecha de informe": "24/09/2026",
            "Pedidos YA": 9839,
        },
    ]
    # mismo resultado que la referencia Python
    pedidos = q(mundo, "SELECT cc, nombre_plataforma, fecha_real, monto_caja FROM {DB}.core.fct_pya_pedido")
    py = pya.caja_innovaweb(
        [
            (r.cc, r.nombre_plataforma, pd.Timestamp(r.fecha_real).date(), int(r.monto_caja))
            for r in pedidos.itertuples()
        ]
    )
    assert sorted(py, key=lambda x: (x["Código centro costo"], x["Fecha de informe"])) == caja.to_dict(
        "records"
    )
    reemb = q(mundo, "SELECT * FROM {DB}.marts.out_innovaweb_reembolsos_pya")
    assert list(reemb.columns) == [
        "Código centro costo",
        "Sucursal",
        "Fecha",
        "Medio de Pago",
        "Agregador",
        "Código de orden",
        "Número de boleta",
        "Valor",
    ]
    filas = {
        (r["Medio de Pago"], r["Código de orden"]): (r["Número de boleta"], int(r["Valor"]), r["Agregador"])
        for r in reemb.to_dict("records")
    }
    assert filas == {
        ("Integración", "2297054261"): ("101", 8, "Pedidos YA"),
        ("Descuentos Pedidos Ya a Clientes", "2297054261"): ("101", -2807, "Pedidos YA"),
        ("Compensaciones de Agregador", "2297054261"): ("101", 1500, "Pedidos YA"),
    }


def test_semana_sql_igual_a_python(mundo):
    s = q(mundo, "SELECT * FROM {DB}.core.fct_pya_semana").iloc[0]
    py = mundo["semana_py"]
    for campo in [
        "neto_factura_calc",
        "liquidacion_calc",
        "sum_comision",
        "sum_plus",
        "publicidad",
        "k_venta",
        "k_cargos",
    ]:
        assert float(s[campo]) == pytest.approx(py[campo]), campo
    assert s["estado"] == "OK", s["excepciones"]
    assert float(s["dif_pdf_banco"]) == 2
    assert s["factura_folio"] == "999"


def test_excepciones(mundo):
    e = q(mundo, "SELECT tipo, pedido, detalle FROM {DB}.marts.excepciones_pya")
    tipos = set(e["tipo"])
    assert {
        "PEDIDO_SIN_BOLETA",
        "PEDIDO_DUPLICADO",
        "INTEGRACION",
        "ABONO_PYA_NO_ASIGNADO",
        "BOLETA_PYA_SIN_PEDIDO",
    } <= tipos
    assert not tipos & {
        "SEMANA_NO_CUADRA_PDF",
        "ABONO_NO_ENCONTRADO",
        "FACTURA_NO_ENCONTRADA",
        "SUCURSAL_SIN_CC",
    }
    assert set(e.loc[e["tipo"] == "PEDIDO_DUPLICADO", "pedido"]) == {"2318734706"}
    assert any("4717440" in d for d in e.loc[e["tipo"] == "ABONO_PYA_NO_ASIGNADO", "detalle"])


def test_comparacion_vs_innovaweb(mundo):
    c = q(
        mundo,
        "SELECT cc, CAST(fecha AS VARCHAR) AS fecha, concepto, calculado, innovaweb, estado "
        "FROM {DB}.marts.comparacion_vs_innovaweb_pya",
    ).set_index(["cc", "fecha", "concepto"])
    assert c.loc[("L030", "2026-09-26", "Pedidos YA (caja)"), "estado"] == "OK"
    fila = c.loc[("L031", "2026-09-24", "Pedidos YA (caja)")]
    assert (
        fila["estado"] == "DIFERENCIA"
        and float(fila["calculado"]) == 9839
        and float(fila["innovaweb"]) == 9000
    )
    assert c.loc[("L031", "2026-09-24", "Descuentos Pedidos Ya a Clientes"), "estado"] == "OK"
    assert c.loc[("L031", "2026-09-24", "Compensaciones de Agregador"), "estado"] == "FALTA_EN_INNOVAWEB"


def test_se_ejecutaron_todas_las_assertions(mundo, compilado):
    esperadas = {a["target"]["name"] for a in compilado["assertions"]}
    assert esperadas == set(mundo["asserts"]) and len(esperadas) >= 15


def test_assertion_alias_detecta_conflicto(mundo, compilado):
    con = mundo["con"]
    con.execute(
        f"""INSERT INTO "{DB}".core.dim_local_alias VALUES ('L999', 'MELT', 'pya', 'x', 'MELT PIZZAS - QUILPUE')"""
    )
    try:
        sql = next(a["query"] for a in compilado["assertions"] if a["target"]["name"] == "assert_alias_unico")
        assert len(con.execute(a_duckdb(sql)).df()) == 1
    finally:
        con.execute(f"""DELETE FROM "{DB}".core.dim_local_alias WHERE cc = 'L999'""")
