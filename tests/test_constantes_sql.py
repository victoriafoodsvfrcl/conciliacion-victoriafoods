"""El SQL (Dataform) debe espejar a reglas/pya.py: mismas constantes y mismas contrapartes."""

import json
import re
from pathlib import Path

from conciliacion.config import config_negocio
from conciliacion.reglas import pya

RAIZ = Path(__file__).resolve().parents[1]
JS = (RAIZ / "dataform" / "includes" / "constantes.js").read_text(encoding="utf-8")


def const(nombre: str) -> str:
    m = re.search(rf"^const {nombre} = (.+?);", JS, re.M | re.S)
    assert m, f"{nombre} no está en constantes.js"
    return m.group(1).strip()


def test_constantes_numericas():
    assert float(const("IVA")) == pya.IVA
    assert float(const("COMISION")) == pya.COMISION
    assert int(const("TOL_REDONDEO")) == pya.TOL_REDONDEO
    assert int(const("TOL_BANCO")) == pya.TOL_BANCO
    assert int(const("TOL_SEMANA")) == pya.TOL_SEMANA
    assert (int(const("VENTANA_ABONO_MIN")), int(const("VENTANA_ABONO_MAX"))) == pya.VENTANA_ABONO_DIAS
    assert json.loads(const("RUT_PYA")) == pya.RUT_PYA
    assert json.loads(const("TIPO_DTE_FACTURA")) == pya.TIPO_DTE_FACTURA


def test_agregador_marcas_y_flags():
    js_agr = dict(re.findall(r"(\w+): \"(Pedidos Y[Aa])\"", const("AGREGADOR_POR_MARCA")))
    assert js_agr == pya.AGREGADOR_POR_MARCA
    assert (
        set(json.loads(const("MARCAS_REINTEGRO_PARCIAL_COMO_COMPENSACION")))
        == pya.MARCAS_REINTEGRO_PARCIAL_COMO_COMPENSACION
    )
    assert tuple(json.loads(const("ORDEN_FECHA_REAL"))) == pya.ORDEN_FECHA_REAL
    assert (const("HABILITAR_CANCELACIONES") == "true") == pya.HABILITAR_CANCELACIONES


def test_contrapartes_iguales_a_config_python():
    js = re.findall(r"\{ rut: \"([\d-]+)\", contraparte: \"(\w+)\" \}", const("CONTRAPARTES"))
    py = [(c["rut"], c["contraparte"]) for c in config_negocio()["bci_cartola"]["contrapartes"] if "rut" in c]
    assert js == py
