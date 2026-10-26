"""CLI: ``conciliacion <comando>``.

Comandos:
  fuentes                 lista los adaptadores registrados
  parse ARCHIVO           parsea un archivo local y escribe CSV por tabla (sin GCP)
  sync                    SharePoint -> GCS + manifiesto
  load                    archivos pendientes del manifiesto -> BigQuery raw
  run-all                 sync + load + (opcional) invocación de Dataform
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from conciliacion.adapters import REGISTRO, adaptador_por_id, resolver_adaptador
from conciliacion.config import Settings

log = logging.getLogger("conciliacion")
ESTADO_DELTA = "state/sharepoint_delta.json"


def _params(pares: list[str]) -> dict:
    out = {}
    for p in pares or []:
        k, _, v = p.partition("=")
        out[k] = v
    return out


def cmd_fuentes(_args) -> int:
    for a in REGISTRO:
        print(f"{a.source_id:28} v{a.schema_version}  tablas={list(a.esquemas)}  patrones={list(a.patrones)}")
    return 0


def cmd_parse(args) -> int:
    ruta = Path(args.archivo)
    adaptador = (
        adaptador_por_id(args.fuente) if args.fuente else resolver_adaptador(ruta.name, str(ruta.parent))
    )
    if adaptador is None:
        print(f"Ningún adaptador reconoce '{ruta.name}'. Use --fuente.", file=sys.stderr)
        return 2
    tablas = adaptador.parse(ruta.read_bytes(), ruta.name, **_params(args.param))
    salida = Path(args.out)
    salida.mkdir(parents=True, exist_ok=True)
    for tp in tablas:
        destino = salida / f"{tp.tabla}.csv"
        tp.df.to_csv(destino, index=False)
        print(f"{tp.tabla}: {len(tp.df)} filas -> {destino}")
        for adv in tp.advertencias or []:
            print(f"  ADVERTENCIA: {adv}")
    return 0


def _sync(settings: Settings, modo: str) -> dict:
    from conciliacion.gcp import BigQueryManifiesto, GcsAlmacen
    from conciliacion.ingest.sharepoint_sync import GraphClient, sincronizar

    settings.requerir("project_id", "bucket", "graph_tenant_id", "graph_client_id", "graph_client_secret")
    almacen = GcsAlmacen(settings)
    graph = GraphClient(settings.graph_tenant_id, settings.graph_client_id, settings.graph_client_secret)
    estado = almacen.leer_json(ESTADO_DELTA) if modo == "delta" else None
    resumen = sincronizar(
        settings, graph, almacen, BigQueryManifiesto(settings), modo=modo, estado_delta=estado
    )
    if modo == "delta" and resumen.get("delta_link"):
        almacen.escribir_json(ESTADO_DELTA, {"delta_link": resumen.pop("delta_link")})
    return resumen


def _load(settings: Settings, reintentar: bool) -> dict:
    from conciliacion.gcp import BigQueryManifiesto, EscritorBigQuery, GcsAlmacen
    from conciliacion.load.bigquery_loader import cargar_pendientes

    settings.requerir("project_id", "bucket")
    return cargar_pendientes(
        GcsAlmacen(settings),
        BigQueryManifiesto(settings),
        EscritorBigQuery(settings),
        incluir_errores=reintentar,
    )


def cmd_sync(args) -> int:
    print(json.dumps(_sync(Settings(), args.modo_sync), default=str))
    return 0


def cmd_load(args) -> int:
    r = _load(Settings(), args.reintentar_errores)
    print(json.dumps(r, default=str, ensure_ascii=False))
    return 1 if r["errores"] else 0


def cmd_run_all(args) -> int:
    from conciliacion.gcp import invocar_dataform

    s = Settings()
    rs = _sync(s, args.modo_sync)
    rl = _load(s, args.reintentar_errores)
    inv = (
        None
        if args.sin_dataform
        else invocar_dataform(s, etiquetas=["pya"] if args.modo == "diario" else None)
    )
    print(
        json.dumps(
            {"sync": rs, "load": rl, "dataform": inv, "modo": args.modo}, default=str, ensure_ascii=False
        )
    )
    return 1 if rl["errores"] else 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="conciliacion", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fuentes").set_defaults(fn=cmd_fuentes)
    pp = sub.add_parser("parse")
    pp.add_argument("archivo")
    pp.add_argument("--fuente", help="source_id (si no, se resuelve por nombre)")
    pp.add_argument("--out", default="salida_local")
    pp.add_argument("--param", action="append", help="parámetro del adaptador k=v (p.ej. marca=UNDER)")
    pp.set_defaults(fn=cmd_parse)
    for nombre, fn in (("sync", cmd_sync), ("load", cmd_load), ("run-all", cmd_run_all)):
        sp = sub.add_parser(nombre)
        sp.add_argument("--modo-sync", choices=["listado", "delta"], default="listado")
        sp.add_argument("--reintentar-errores", action="store_true")
        sp.add_argument("--modo", choices=["diario", "semanal"], default="diario")
        sp.add_argument("--sin-dataform", action="store_true")
        sp.set_defaults(fn=fn)
    return p


class FormatoJson(logging.Formatter):
    """Una línea JSON por evento: Cloud Logging la agrupa como una sola entrada con severidad."""

    def format(self, record: logging.LogRecord) -> str:
        evento = {"severity": record.levelname, "logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            evento["error"] = f"{record.exc_info[0].__name__}: {record.exc_info[1]}"
            evento["traceback"] = self.formatException(record.exc_info)
        return json.dumps(evento, ensure_ascii=False, default=str)


def _configurar_logs(verbose: bool) -> None:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(FormatoJson())
    raiz = logging.getLogger()
    raiz.handlers[:] = [h]
    raiz.setLevel(logging.DEBUG if verbose else logging.INFO)


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    _configurar_logs(args.verbose)
    try:
        return args.fn(args)
    except Exception as e:
        log.exception("Falló el comando '%s': %s", args.cmd, e)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
