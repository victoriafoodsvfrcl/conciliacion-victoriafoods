"""SharePoint sync (Graph simulado) + loader (escritor local / en memoria)."""

import json

import pandas as pd

from conciliacion.config import Settings
from conciliacion.ingest.manifiesto import (
    CARGADO,
    ERROR,
    INGESTADO,
    SIN_ADAPTADOR,
    AlmacenMemoria,
    ManifiestoMemoria,
)
from conciliacion.ingest.sharepoint_sync import GraphClient, listar_delta, ruta_gcs, sincronizar
from conciliacion.load.bigquery_loader import EscritorLocal, cargar_pendientes
from tests.conftest import PDF_SEMANA_0510
from tests.test_adapter_idte_rcv import idte_bytes


class GraphFalso(GraphClient):
    def __init__(self, arbol: dict, contenidos: dict):
        super().__init__("t", "c", "s")
        self.arbol, self.contenidos, self.descargas = arbol, contenidos, 0

    def token(self):
        return "x"

    def json(self, url):
        if url.startswith("/sites/") and url.endswith("/drives"):
            return {"value": [{"id": "drv", "name": "Documentos"}]}
        if url.startswith("/sites/"):
            return {"id": "site1"}
        if "/delta" in url:
            return {"value": self.arbol["delta"], "@odata.deltaLink": "https://graph/delta?token=abc"}
        ruta = url.split("root:/")[1].split(":/children")[0] if "root:/" in url else ""
        return {"value": self.arbol[ruta]}

    def descargar(self, drive_id, item_id):
        self.descargas += 1
        return self.contenidos[item_id]


def item(id_, nombre, carpeta=False):
    base = {
        "id": id_,
        "name": nombre,
        "size": 10,
        "lastModifiedDateTime": "2026-10-20T10:00:00Z",
        "eTag": "e",
    }
    base["folder" if carpeta else "file"] = {}
    return base


ARBOL = {
    "Conciliacion": [item("f1", "PYA", True), item("i3", "notas.docx"), item("i4", "~$lock.xlsx")],
    "Conciliacion/PYA": [
        item("i1", "IDTE_boletas_oct.csv"),
        item("i2", "desconocido.csv"),
        item("i5", "IDTE copia.csv"),
    ],
}
CONTENIDOS = {"i1": idte_bytes(), "i2": b"a;b\n1;2\n", "i5": idte_bytes()}


def settings():
    return Settings(
        project_id="p",
        bucket="b",
        sharepoint_hostname="victoria.sharepoint.com",
        sharepoint_site_path="sites/finanzas",
        sharepoint_folder="Conciliacion",
    )


def test_sync_idempotente_por_sha():
    alm, man = AlmacenMemoria("b"), ManifiestoMemoria()
    g = GraphFalso(ARBOL, CONTENIDOS)
    r = sincronizar(settings(), g, alm, man)
    # i1 nuevo, i5 mismo sha que i1 -> omitido, i2 sin adaptador, docx/lock ignorados
    assert r == {"vistos": 3, "nuevos": 1, "omitidos": 1, "sin_adaptador": 1, "ignorados_ext": 2}
    regs = {x.nombre: x for x in man.registros.values()}
    idte = regs["IDTE_boletas_oct.csv"]
    assert idte.source_id == "idte_boletas" and idte.estado == INGESTADO and idte.ruta_origen == "PYA"
    assert idte.gcs_uri.startswith(
        f"gs://b/raw/idte_boletas/{idte.ingestado_en:%Y}/{idte.ingestado_en:%m}/{idte.sha256}_"
    )
    assert regs["desconocido.csv"].estado == SIN_ADAPTADOR
    r2 = sincronizar(settings(), GraphFalso(ARBOL, CONTENIDOS), alm, man)
    assert r2["nuevos"] == 0 and r2["omitidos"] == 3


def test_listar_delta_filtra_carpeta():
    delta = [
        {**item("a", "x.csv"), "parentReference": {"path": "/drives/drv/root:/Conciliacion/PYA"}},
        {**item("b", "y.csv"), "parentReference": {"path": "/drives/drv/root:/Otra"}},
        {**item("c", "z.csv"), "deleted": {}, "parentReference": {"path": "/drives/drv/root:/Conciliacion"}},
    ]
    g = GraphFalso({"delta": delta}, {})
    archivos = list(listar_delta(g, "drv", "Conciliacion", None))
    assert [(a.nombre, a.ruta) for a in archivos] == [("x.csv", "PYA")]
    assert g.ultimo_delta_link.endswith("token=abc")


def test_ruta_gcs():
    from datetime import datetime

    assert (
        ruta_gcs("pya_liquidacion_detalle", "abc", "28-09 al 04-10.xls", datetime(2026, 10, 5))
        == "raw/pya_liquidacion_detalle/2026/10/abc_28-09_al_04-10.xls"
    )


def test_loader_carga_y_marca_estados(tmp_path):
    alm, man = AlmacenMemoria("b"), ManifiestoMemoria()
    sincronizar(settings(), GraphFalso(ARBOL, CONTENIDOS), alm, man)
    # un archivo PDF "roto" registrado manualmente -> ERROR sin detener los demás
    from conciliacion.ingest.sharepoint_sync import ArchivoRemoto, procesar_archivo

    procesar_archivo(
        b"no soy un pdf", ArchivoRemoto("x", "Estado de cuenta 05-10.pdf", "PYA", 1, None, None), alm, man
    )
    esc = EscritorLocal(tmp_path)
    r = cargar_pendientes(alm, man, esc)
    assert r["cargados"] == 1 and r["errores"] == 1
    estados = {x.nombre: x.estado for x in man.registros.values()}
    assert estados["IDTE_boletas_oct.csv"] == CARGADO and estados["Estado de cuenta 05-10.pdf"] == ERROR
    df = pd.read_csv(tmp_path / "idte_boletas.csv", dtype=str)
    assert len(df) == 3
    assert {"_archivo_id", "_archivo_nombre", "_ingestado_en", "_schema_version", "_hoja", "_fila"} <= set(
        df.columns
    )
    reg = next(x for x in man.registros.values() if x.nombre == "IDTE_boletas_oct.csv")
    assert json.loads(reg.filas_cargadas) == {"idte_boletas": 3}
    # recarga del mismo archivo (reintento) no duplica filas
    man.actualizar(reg.archivo_id, estado=INGESTADO)
    cargar_pendientes(alm, man, esc)
    assert len(pd.read_csv(tmp_path / "idte_boletas.csv", dtype=str)) == 3


def test_cli_parse_local(tmp_path):
    from conciliacion.cli import main

    archivo = tmp_path / "IDTE_boletas.csv"
    archivo.write_bytes(idte_bytes())
    assert main(["parse", str(archivo), "--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "idte_boletas.csv").exists()
    assert main(["fuentes"]) == 0


def test_pdf_por_parametro_texto():
    from conciliacion.adapters import adaptador_por_id

    tablas = adaptador_por_id("pya_estado_cuenta_pdf").parse(b"", "x.pdf", texto=PDF_SEMANA_0510)
    assert tablas[0].df["total_liquidado"].iloc[0] == 437_018_419


def test_manifiesto_igual_en_python_terraform_y_gcp():
    import re
    from pathlib import Path

    from conciliacion.gcp import TIPOS_MANIFIESTO
    from conciliacion.ingest.manifiesto import RegistroArchivo

    tf = (Path(__file__).resolve().parents[1] / "infra" / "terraform" / "bigquery.tf").read_text(
        encoding="utf-8"
    )
    columnas_tf = dict(re.findall(r'\{ name = "(\w+)", type = "(\w+)"', tf))
    assert columnas_tf == TIPOS_MANIFIESTO
    assert list(columnas_tf) == list(RegistroArchivo.__dataclass_fields__)


def test_preparar_para_bigquery():
    from datetime import UTC, datetime
    from decimal import Decimal

    from conciliacion.adapters.base import Columna
    from conciliacion.gcp import _preparar

    df = pd.DataFrame({"m": [2807.21, None], "t": [datetime(2026, 10, 1, tzinfo=UTC)] * 2})
    out = _preparar(df, [Columna("m", "NUMERIC"), Columna("t", "TIMESTAMP")])
    assert out["m"].iloc[0] == Decimal("2807.21") and out["m"].iloc[1] is None
    assert str(out["t"].dt.tz) == "UTC"
