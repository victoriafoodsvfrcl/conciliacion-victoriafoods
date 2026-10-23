"""Interfaces de almacenamiento y manifiesto (con implementaciones en memoria para tests/local).

Las implementaciones GCP viven en ``conciliacion.gcp`` para que el núcleo no dependa de las
librerías de Google.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

INGESTADO, CARGADO, ERROR, SIN_ADAPTADOR = "INGESTADO", "CARGADO", "ERROR", "SIN_ADAPTADOR"


@dataclass
class RegistroArchivo:
    """Fila de ``ops.archivos_ingestados``. ``archivo_id`` = sha256 del contenido."""

    archivo_id: str
    sha256: str
    source_id: str
    nombre: str
    ruta_origen: str
    gcs_uri: str
    tamano_bytes: int
    estado: str = INGESTADO
    origen: str = "sharepoint"
    origen_item_id: str | None = None
    origen_etag: str | None = None
    modificado_en: datetime | None = None
    ingestado_en: datetime = field(default_factory=lambda: datetime.now(UTC))
    cargado_en: datetime | None = None
    schema_version: str | None = None
    filas_cargadas: str | None = None  # JSON {tabla: n}
    mensaje: str | None = None

    def fila(self) -> dict:
        return asdict(self)


class Almacen(Protocol):
    def subir(self, contenido: bytes, ruta: str, metadata: dict | None = None) -> str: ...
    def descargar(self, uri: str) -> bytes: ...


class Manifiesto(Protocol):
    def existe_sha(self, sha256: str) -> bool: ...
    def registrar(self, registro: RegistroArchivo) -> None: ...
    def pendientes(self, incluir_errores: bool = False) -> list[RegistroArchivo]: ...
    def actualizar(self, archivo_id: str, **campos) -> None: ...


class AlmacenMemoria:
    def __init__(self, bucket: str = "bucket-local"):
        self.bucket = bucket
        self.objetos: dict[str, bytes] = {}

    def subir(self, contenido: bytes, ruta: str, metadata: dict | None = None) -> str:
        uri = f"gs://{self.bucket}/{ruta}"
        self.objetos[uri] = contenido
        return uri

    def descargar(self, uri: str) -> bytes:
        return self.objetos[uri]


class ManifiestoMemoria:
    def __init__(self):
        self.registros: dict[str, RegistroArchivo] = {}

    def existe_sha(self, sha256: str) -> bool:
        return sha256 in self.registros

    def registrar(self, registro: RegistroArchivo) -> None:
        self.registros[registro.archivo_id] = registro

    def pendientes(self, incluir_errores: bool = False) -> list[RegistroArchivo]:
        estados = {INGESTADO, ERROR} if incluir_errores else {INGESTADO}
        return sorted(
            (r for r in self.registros.values() if r.estado in estados), key=lambda r: r.ingestado_en
        )

    def actualizar(self, archivo_id: str, **campos) -> None:
        for k, v in campos.items():
            setattr(self.registros[archivo_id], k, v)
