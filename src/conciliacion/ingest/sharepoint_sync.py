"""Sincronización SharePoint (Microsoft Graph, client credentials) -> GCS + manifiesto.

Flujo:
1. Token OAuth2 client credentials (``https://graph.microsoft.com/.default``). El secreto viene
   de Secret Manager inyectado como variable de entorno ``GRAPH_CLIENT_SECRET`` en Cloud Run.
2. Resuelve sitio y drive; recorre la carpeta configurada:
   * ``modo='listado'`` (defecto): listado recursivo de ``children``.
   * ``modo='delta'``: ``/drives/{id}/root/delta`` (en SharePoint delta sólo se soporta sobre la
     raíz) filtrando por la ruta de la carpeta; el ``deltaLink`` se persiste en GCS
     (``state/sharepoint_delta.json``) para la siguiente ejecución.
3. Para cada archivo: descarga, calcula sha256, si el sha ya existe en el manifiesto lo omite
   (idempotencia); si no, resuelve el adaptador por nombre/ruta, sube a
   ``gs://<bucket>/raw/<source_id>/<yyyy>/<mm>/<sha256>_<nombre>`` y registra el manifiesto.
   Archivos sin adaptador se suben a ``raw/sin_clasificar/`` con estado SIN_ADAPTADOR.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import requests

from conciliacion.adapters import resolver_adaptador
from conciliacion.config import Settings
from conciliacion.ingest.manifiesto import INGESTADO, SIN_ADAPTADOR, Almacen, Manifiesto, RegistroArchivo

log = logging.getLogger(__name__)
GRAPH = "https://graph.microsoft.com/v1.0"
EXTENSIONES = (".xls", ".xlsx", ".csv", ".pdf")


@dataclass
class ArchivoRemoto:
    item_id: str
    nombre: str
    ruta: str  # ruta relativa a la carpeta configurada, sin el nombre
    tamano: int
    modificado_en: datetime | None
    etag: str | None
    url_descarga: str | None = None


class GraphClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        session: requests.Session | None = None,
        timeout: int = 60,
    ):
        self.tenant_id, self.client_id, self._secret = tenant_id, client_id, client_secret
        self.s = session or requests.Session()
        self.timeout = timeout
        self._token: str | None = None
        self._expira = 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expira - 60:
            return self._token
        r = self.s.post(
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self.client_id,
                "client_secret": self._secret,
                "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        datos = r.json()
        self._token, self._expira = datos["access_token"], time.time() + int(datos.get("expires_in", 3600))
        return self._token

    def get(self, url: str, **kw) -> requests.Response:
        url = url if url.startswith("http") else GRAPH + url
        for intento in range(5):
            r = self.s.get(
                url, headers={"Authorization": f"Bearer {self.token()}"}, timeout=self.timeout, **kw
            )
            if r.status_code in (429, 503, 504):  # throttling: respetar Retry-After
                time.sleep(int(r.headers.get("Retry-After", 2**intento)))
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def json(self, url: str) -> dict:
        return self.get(url).json()

    def paginar(self, url: str) -> Iterator[dict]:
        while url:
            datos = self.json(url)
            yield from datos.get("value", [])
            url = datos.get("@odata.nextLink")
            if "@odata.deltaLink" in datos:
                self.ultimo_delta_link = datos["@odata.deltaLink"]

    def resolver_drive(self, hostname: str, site_path: str, drive_nombre: str) -> str:
        sitio = self.json(f"/sites/{hostname}:/{site_path.strip('/')}")
        for d in self.paginar(f"/sites/{sitio['id']}/drives"):
            if d["name"] == drive_nombre:
                return d["id"]
        raise RuntimeError(f"No existe la biblioteca '{drive_nombre}' en {hostname}/{site_path}")

    def descargar(self, drive_id: str, item_id: str) -> bytes:
        return self.get(f"/drives/{drive_id}/items/{item_id}/content").content


def _a_remoto(item: dict, ruta: str) -> ArchivoRemoto:
    mod = item.get("lastModifiedDateTime")
    return ArchivoRemoto(
        item_id=item["id"],
        nombre=item["name"],
        ruta=ruta,
        tamano=int(item.get("size", 0)),
        modificado_en=datetime.fromisoformat(mod.replace("Z", "+00:00")) if mod else None,
        etag=item.get("eTag"),
        url_descarga=item.get("@microsoft.graph.downloadUrl"),
    )


def listar_recursivo(graph: GraphClient, drive_id: str, carpeta: str) -> Iterator[ArchivoRemoto]:
    pila = [carpeta.strip("/")]
    while pila:
        actual = pila.pop()
        url = (
            f"/drives/{drive_id}/root:/{quote(actual)}:/children"
            if actual
            else f"/drives/{drive_id}/root/children"
        )
        for item in graph.paginar(url):
            sub = f"{actual}/{item['name']}" if actual else item["name"]
            if "folder" in item:
                pila.append(sub)
            elif "file" in item:
                yield _a_remoto(item, actual[len(carpeta.strip("/")) :].strip("/"))


def listar_delta(
    graph: GraphClient, drive_id: str, carpeta: str, delta_link: str | None
) -> Iterator[ArchivoRemoto]:
    prefijo = f"/drive/root:/{carpeta.strip('/')}".lower()
    for item in graph.paginar(delta_link or f"/drives/{drive_id}/root/delta"):
        if "file" not in item or "deleted" in item:
            continue
        ruta_padre = item.get("parentReference", {}).get("path") or ""
        ruta_padre = re.sub(r"^/drives/[^/]+/root:", "/drive/root:", ruta_padre)
        if ruta_padre.lower().startswith(prefijo):
            yield _a_remoto(item, ruta_padre[len(prefijo) :].strip("/"))


def ruta_gcs(source_id: str, sha256: str, nombre: str, cuando: datetime) -> str:
    limpio = re.sub(r"[^\w.\- ]", "_", nombre).replace(" ", "_")
    return f"raw/{source_id}/{cuando:%Y}/{cuando:%m}/{sha256}_{limpio}"


def procesar_archivo(
    contenido: bytes,
    archivo: ArchivoRemoto,
    almacen: Almacen,
    manifiesto: Manifiesto,
    ahora: datetime | None = None,
) -> RegistroArchivo | None:
    """Sube y registra un archivo. Devuelve None si ya estaba (mismo sha256)."""
    sha = hashlib.sha256(contenido).hexdigest()
    if manifiesto.existe_sha(sha):
        log.info("omitido (ya ingestado): %s/%s", archivo.ruta, archivo.nombre)
        return None
    ahora = ahora or datetime.now(UTC)
    adaptador = resolver_adaptador(archivo.nombre, archivo.ruta)
    source_id = adaptador.source_id if adaptador else "sin_clasificar"
    uri = almacen.subir(
        contenido,
        ruta_gcs(source_id, sha, archivo.nombre, ahora),
        {"sha256": sha, "origen_item_id": archivo.item_id, "ruta_origen": archivo.ruta},
    )
    reg = RegistroArchivo(
        archivo_id=sha,
        sha256=sha,
        source_id=source_id,
        nombre=archivo.nombre,
        ruta_origen=archivo.ruta,
        gcs_uri=uri,
        tamano_bytes=len(contenido),
        estado=INGESTADO if adaptador else SIN_ADAPTADOR,
        origen_item_id=archivo.item_id,
        origen_etag=archivo.etag,
        modificado_en=archivo.modificado_en,
        ingestado_en=ahora,
        schema_version=adaptador.schema_version if adaptador else None,
        mensaje=None if adaptador else "Ningún adaptador reconoce el nombre/ruta",
    )
    manifiesto.registrar(reg)
    return reg


def sincronizar(
    settings: Settings,
    graph: GraphClient,
    almacen: Almacen,
    manifiesto: Manifiesto,
    modo: str = "listado",
    estado_delta: dict | None = None,
) -> dict:
    """Ejecuta la sincronización. Devuelve un resumen (y el nuevo deltaLink en modo delta)."""
    settings.requerir("sharepoint_hostname", "sharepoint_site_path")
    drive_id = graph.resolver_drive(
        settings.sharepoint_hostname, settings.sharepoint_site_path, settings.sharepoint_drive
    )
    if modo == "delta":
        archivos = listar_delta(
            graph, drive_id, settings.sharepoint_folder, (estado_delta or {}).get("delta_link")
        )
    else:
        archivos = listar_recursivo(graph, drive_id, settings.sharepoint_folder)
    resumen = {"vistos": 0, "nuevos": 0, "omitidos": 0, "sin_adaptador": 0, "ignorados_ext": 0}
    for a in archivos:
        if not a.nombre.lower().endswith(EXTENSIONES) or a.nombre.startswith("~$"):
            resumen["ignorados_ext"] += 1
            continue
        resumen["vistos"] += 1
        contenido = graph.descargar(drive_id, a.item_id)
        reg = procesar_archivo(contenido, a, almacen, manifiesto)
        if reg is None:
            resumen["omitidos"] += 1
        elif reg.estado == SIN_ADAPTADOR:
            resumen["sin_adaptador"] += 1
        else:
            resumen["nuevos"] += 1
    if modo == "delta":
        resumen["delta_link"] = getattr(graph, "ultimo_delta_link", None)
    log.info("sync: %s", json.dumps({k: v for k, v in resumen.items() if k != "delta_link"}))
    return resumen
