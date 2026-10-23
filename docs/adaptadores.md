# Adaptadores: contrato y cómo agregar un canal

## Contrato (`src/conciliacion/adapters/base.py`)

```python
class Adaptador(ABC):
    source_id: str                      # p. ej. "uber_pagos"; también es la carpeta en GCS
    schema_version: str                 # súbalo cuando cambie el contrato de salida
    patrones: tuple[str, ...]           # regex sobre "ruta/nombre" para matches()
    esquemas: dict[str, list[Columna]]  # tabla raw -> columnas (nombre, tipo BQ, obligatoria)

    def matches(self, nombre: str, ruta: str = "") -> bool: ...
    def parse(self, contenido: bytes, nombre: str, **params) -> list[TablaParseada]: ...
```

Reglas del contrato:

1. **Sólo parseo.** El adaptador lee, limpia, tipa y normaliza llaves. No aplica reglas de
   negocio, que van en Dataform y en `reglas/<canal>.py`.
2. **Falla fuerte.** Use `mapear_columnas(..., obligatorias)` para que una columna obligatoria
   faltante lance `ColumnasFaltantes` con la lista de columnas encontradas. Si una hoja, un tipo o
   una marca no se pueden determinar, lance `ErrorAdaptador`. No adivine. Los mapeos ambiguos van
   a `config/default.yaml`.
3. **Alias de encabezados.** Declare `{canonico: [alias1, alias2]}`. La comparación ignora
   espacios al inicio y al final, tildes, mayúsculas y signos.
4. **Linaje.** Cada fila debe traer `_hoja` y `_fila` (fila del archivo original, base 1). El
   loader agrega el resto.
5. **Llaves normalizadas.** Si la fuente trae un nombre de sucursal o local, guarde además
   `<col>_norm = normalizar_llave(col)` para el JOIN con `dim_local_alias`.
6. **Montos y fechas.** Use `adapters.comun.montos()` (CLP) y `common.fechas` (`detectar_orden`
   por archivo si hay fechas `a/b/aaaa`).
7. **Salida.** Devuelva `self.tabla(nombre, df)`. Eso valida y tipa contra `esquemas`: falla si
   falta una columna del esquema o si una obligatoria viene nula.

## Agregar un canal (ejemplo: Uber)

1. **Adaptador:** cree `src/conciliacion/adapters/uber_pagos.py` copiando el patrón de
   `idte_boletas.py` (CSV) o `pya_order_details.py` (xlsx).
   ```python
   class UberPagos(Adaptador):
       source_id = "uber_pagos"
       schema_version = "1"
       patrones = (r"uber.*pagos?[^/]*\.csv$", r"/uber/[^/]*\.csv$")
       esquemas = {"uber_pagos": [Columna("id_pedido", "STRING", True), ...]}
       def parse(self, contenido, nombre, **params): ...
   ```
2. **Registro:** agréguelo a `REGISTRO` en `adapters/__init__.py`. Póngalo antes de otro
   adaptador si sus patrones pueden chocar (gana el primero que hace match).
3. **Configuración:** los mapeos de negocio van en `config/default.yaml`. Ejemplos: el prefijo
   `UE` de IDTE ya existe en `idte_boletas.prefijos`, y el RUT de Uber ya está en
   `bci_cartola.contrapartes` y en `constantes.CONTRAPARTES`.
4. **Raw en Dataform:** sume las tablas nuevas a `TABLAS_RAW` en `dataform/definitions/sources/raw.js`.
5. **Reglas:**
   * `src/conciliacion/reglas/uber.py` es la especificación ejecutable, con tests que usen
     ejemplos reales.
   * `dataform/definitions/staging/stg_uber_*.sqlx`, `core/fct_uber_*.sqlx` y
     `marts/out_innovaweb_caja_uber.sqlx` (columna `Uber`), con tag `uber`.
   * Constantes nuevas en `includes/constantes.js` y en `reglas/uber.py`, más su verificación en
     `tests/test_constantes_sql.py`.
6. **Maestra:** confirme que la columna de alias del canal ya se mapea en
   `maestra_locales.SISTEMAS` (`Sucursal Uber` → `uber`).
7. **Tests:** agregue el fixture sintético, el test del adaptador, el test de reglas y una
   semana del canal en `tests/test_sql_duckdb.py`.
8. **Docs:** actualice `docs/reglas_<canal>.md` y la tabla de fuentes del README.

## Canales previstos

| Canal | Fuente probable | Prefijo IDTE | RUT contraparte | Alias maestra |
|---|---|---|---|---|
| Uber Eats | reporte de pagos | `UE` (5 hex) | 77099356-3 | Sucursal Uber / Uber Direct |
| Rappi | reporte de pagos | `RPI` (9 dígitos) | 76837223-3 | Sucursal RPI / ID Sucursal Rappi |
| Justo | reporte | `JTO` (8 dígitos) | ? | Sucursal Justo |
| Mercado Pago | reporte de liquidaciones | — | por nombre (`MERCADO PAGO`) | Mercado Pago |
| Fintoc | movimientos | — | 77143385-5 | — |
| Klap | liquidaciones | — | ? | Klap |
| Adobe Commerce / Magento (OLO) | export de órdenes | `OLO` / `PC OLO` | — | OLO Reaction / OLO Nuevo |
| Getnet | liquidaciones POS | — | ? | Sucursal Getnet |
| BCI POS (BCI Pagos) | liquidaciones | — | 77190661-3 | — |

## Versionado de esquemas

* Cambio compatible (columna nueva): suba `schema_version`. El loader agrega la columna en
  BigQuery con `ALLOW_FIELD_ADDITION`.
* Cambio incompatible (renombrar o cambiar tipo): cree una tabla nueva (`<tabla>_v2`) o migre
  con un script. `_schema_version` en raw permite que staging trate cada versión por separado.
