# Modelo de datos

## Capas

| Dataset | Quién escribe | Contenido |
|---|---|---|
| `raw` | loader Python | Una tabla por `TablaParseada` de cada adaptador, tal cual se parseó y con linaje. Particionada por `_ingestado_en` y clusterizada por `_archivo_id` |
| `ops` | loader Python | `archivos_ingestados` (manifiesto) |
| `staging` | Dataform (vistas) | Limpieza y deduplicación: manda el **último archivo** por periodo, día o llave natural |
| `core` | Dataform (tablas) | Dimensiones y hechos con reglas: `dim_local`, `dim_local_alias`, `fct_pya_linea`, `fct_pya_pedido`, `fct_pya_semana` |
| `marts` | Dataform (tablas) | Salidas para personas y sistemas: Innovaweb, conciliación semanal, excepciones, comparación |
| `assertions` | Dataform | Resultados de las assertions |

## Linaje (todas las tablas raw)

| Columna | Origen |
|---|---|
| `_archivo_id` | sha256 del contenido; es la llave del manifiesto |
| `_archivo_nombre` | nombre original en SharePoint |
| `_hoja` | hoja Excel (`pdf` para el PDF, NULL para CSV) |
| `_fila` | fila 1-based en el archivo original |
| `_ingestado_en` | timestamp de ingesta |
| `_schema_version` | versión del contrato del adaptador |

La carga es idempotente: antes de insertar se borran las filas del mismo `_archivo_id`.

## Manifiesto `ops.archivos_ingestados`

`archivo_id, sha256, source_id, nombre, ruta_origen, gcs_uri, tamano_bytes, estado, origen,
origen_item_id, origen_etag, modificado_en, ingestado_en, cargado_en, schema_version,
filas_cargadas (JSON), mensaje`.

`estado` ∈ `INGESTADO` (en GCS, pendiente de carga) · `CARGADO` · `ERROR` (`mensaje` explica la
causa) · `SIN_ADAPTADOR` (ningún adaptador reconoce el nombre; queda en `raw/sin_clasificar/`).

## Reglas de "archivo vigente" (staging)

| Vista | Llave de vigencia |
|---|---|
| `stg_pya_liquidacion` | último archivo por `periodo_inicio`; marca `ocurrencia`/`n_ocurrencias` |
| `stg_pya_estado_cuenta` | último PDF por `periodo_inicio`; partidas pivoteadas por concepto canónico |
| `stg_idte_boletas` | (`tipo_dte`, `folio`) |
| `stg_aloha_ordenes` | último archivo por (`marca`, `date_of_business`) |
| `stg_bci_movimientos` | `movimiento_id` (hash estable: cartolas con rangos solapados) |
| `stg_sii_rcv_compras` | (`rut_proveedor`, `tipo_doc`, `folio`) |
| `stg_innovaweb_caja` / `_reembolsos` | último archivo por (`cc`, fecha) |
| `dim_local*` | última maestra cargada |

## Maestra de locales

* `dim_local(cc, marca, nombre_plataforma)`
* `dim_local_alias(cc, marca, sistema, alias_original, alias_normalizado)`. `sistema` sale del
  encabezado de la maestra: `plataforma`, `idte`, `uber`, `pya`, `rpi`, `rappi_id`,
  `olo_reaction`, `goodmeal`, `aloha`, `getnet`, `mercado_pago`, `brinks`, `uber_direct`,
  `plataforma_ticket`, `codigo_brinks`, `sap`, `justo`, `redelcom`, `olo_nuevo`, `cheaf`,
  `edenred`, `klap`.
* El JOIN siempre es (`sistema`, `alias_normalizado`). `alias_normalizado` lo calcula Python con
  `common.texto.normalizar_llave`, que repara mojibake, quita tildes y caracteres de ancho cero,
  pasa a mayúsculas y normaliza espacios y guiones. Las fuentes guardan `*_norm` con la misma
  función, así que el SQL sólo compara igualdad.
* Si un alias apunta a dos CC en el mismo sistema, falla `assert_alias_unico` y en los JOIN ese
  alias queda sin CC (aparece en `SUCURSAL_SIN_CC`).

## Marts

| Mart | Grano |
|---|---|
| `out_innovaweb_caja_pya` | cc × fecha real |
| `out_innovaweb_reembolsos_pya` | pedido × medio de pago |
| `conciliacion_semanal_pya` | semana (`periodo_inicio`) |
| `excepciones_pya` | excepción (`tipo`, `severidad`, `cc`, `fecha`, `pedido`, `detalle`) |
| `comparacion_vs_innovaweb_pya` | cc × fecha × concepto (calculado vs Innovaweb, `estado`) |

Los marts Innovaweb usan **nombres de columna flexibles** de BigQuery (con tildes y espacios)
para respetar el formato exacto de carga. Si se define la variable Dataform `export_bucket`, se
exportan a CSV (`;`) en GCS (`definitions/marts/exportes.js`).

## Tipos de excepción

| Tipo | Severidad | Origen |
|---|---|---|
| `PEDIDO_SIN_BOLETA` | MEDIA | pedido Venta sin boleta IDTE PYA |
| `SUCURSAL_SIN_CC` | ALTA | sucursal PYA o IDTE sin alias en la maestra (o con alias ambiguo) |
| `PEDIDO_DUPLICADO` | MEDIA | mismo pedido y tipo repetido en el periodo |
| `INTEGRACION` | BAJA | \|boleta − neto\| > 1 |
| `SEMANA_NO_CUADRA_PDF` | ALTA | liquidación calculada ≠ PDF |
| `ABONO_NO_ENCONTRADO` | ALTA | no hay abono PYA para el total del PDF |
| `FACTURA_NO_ENCONTRADA` / `FACTURA_NO_CUADRA` | ALTA | RCV |
| `ABONO_PYA_NO_ASIGNADO` | ALTA | abono de PYA sin liquidación (p. ej. 4.717.440 y 42.818.978) |
| `BOLETA_PYA_SIN_PEDIDO` | MEDIA | boleta PYA cuyo id no está en el detalle (dentro del rango cargado) |
