# Conciliación de ventas — Victoria Foods

Pipeline de conciliación de ventas de todos los canales (agregadores, venta presencial y
ecommerce con sus pasarelas de pago) para Victoria Foods (Chile). Esta **v1 cubre
PedidosYa (PYA)** para las marcas MELT, UNDER y EXPRESS (Under Express `LUE00x` + Feelgood `LF00x`).
Corre en Google Cloud: GCS, BigQuery, Cloud Run Jobs, Cloud Scheduler, Secret Manager y Dataform.
El CI/CD es GitHub Actions con Workload Identity Federation.

> Estado: primera versión. Todo está parametrizado y probado con **fixtures sintéticos**
> construidos a partir de ejemplos reales. Todavía no se ejecuta contra GCP ni contra los archivos
> reales. Ver [docs/preguntas_abiertas.md](docs/preguntas_abiertas.md).

## Arquitectura

```
 SharePoint (carpeta Conciliacion)                         Cloud Scheduler
   .xls/.xlsx/.csv/.pdf                                (diario 06:47 · lunes 11:17)
          │  Microsoft Graph (client credentials,                │ POST :run (OAuth SA scheduler)
          │  secreto en Secret Manager)                          ▼
          ▼                                         ┌──────────────────────────────┐
 ┌──────────────────────┐   sha256 nuevo?           │ Cloud Run Job  conciliacion  │
 │ ingest/sharepoint_   │──────────────────────────▶│  run-all = sync → load →     │
 │ sync.py              │   ops.archivos_ingestados │  invocar Dataform            │
 └─────────┬────────────┘   (manifiesto idempotente)└──────────────┬───────────────┘
           │ gs://<bucket>/raw/<source_id>/<yyyy>/<mm>/<sha256>_<nombre>
           ▼
 ┌──────────────────────┐  adaptador por fuente (esquema fijo + versión)
 │ load/bigquery_loader │  + linaje _archivo_id _archivo_nombre _hoja _fila _ingestado_en _schema_version
 └─────────┬────────────┘
           ▼
 BigQuery  raw ──▶ staging ──▶ core ──────────────▶ marts ─────────────────▶ (CSV en GCS opcional)
           (1 tabla   (limpio,    (dim_local,        out_innovaweb_caja_pya
            por        dedup,      fct_pya_linea,    out_innovaweb_reembolsos_pya
            Tabla-     último      fct_pya_pedido,   conciliacion_semanal_pya
            Parseada)  archivo)    fct_pya_semana)   excepciones_pya
                                                     comparacion_vs_innovaweb_pya
                       └──────── Dataform (SQLX + assertions) ────────┘
```

**Principio de diseño:** cada fuente tiene un **adaptador** en Python. El adaptador parsea el
archivo crudo y entrega uno o más DataFrames con un esquema fijo y versionado, sin reglas de
negocio. Las reglas de negocio viven en **SQL (Dataform)**. En `src/conciliacion/reglas/` hay
una **especificación ejecutable en Python** de esas mismas reglas, y los tests verifican que el
SQL y el Python den lo mismo (`tests/test_sql_duckdb.py`).

Fuentes de la v1:

| source_id | Archivo | Tablas raw |
|---|---|---|
| `pya_liquidacion_detalle` | `dd-mm al dd-mm.xls(x)` detalle del estado de cuenta | `pya_liquidacion_detalle` |
| `pya_estado_cuenta_pdf` | PDF "Estado de cuenta semanal" | `pya_estado_cuenta_cabecera`, `pya_estado_cuenta_partidas` |
| `pya_order_details` | `orderDetails*.xlsx` del portal | `pya_order_details` |
| `idte_boletas` | CSV IDTE (`;`, cp1252) | `idte_boletas` |
| `aloha_ordenes` | `Ordenes Melt/Under/Under Express.xlsx` | `aloha_ordenes`, `aloha_order_modes` |
| `bci_cartola` | Cartola BCI `Cuenta-Corriente` | `bci_movimientos`, `bci_cartola_cabecera` |
| `sii_rcv` | RCV compras (CSV `;`) | `sii_rcv_compras`, `sii_rcv_otros_impuestos` |
| `innovaweb` | 4 informes (caja, ventas, recaudaciones, reembolsos) | `innovaweb_caja` (largo), `innovaweb_ventas`, `innovaweb_recaudaciones`, `innovaweb_reembolsos` |
| `maestra_locales` | `Locales - CC.xlsx` | `maestra_dim_local`, `maestra_dim_local_alias` |

## Estructura

```
src/conciliacion/
  common/      texto.py (U+200B, mojibake, llaves) · montos.py (CLP) · fechas.py (d/m vs m/d, seriales) · rut.py
  adapters/    base.py (interfaz) + 9 adaptadores + __init__.py (registro)
  ingest/      sharepoint_sync.py (Graph) · manifiesto.py (interfaces + memoria)
  load/        bigquery_loader.py (linaje, idempotencia por archivo)
  reglas/      pya.py (especificación ejecutable de las reglas PYA)
  gcp.py       GCS / BigQuery / invocación Dataform
  cli.py       fuentes | parse | sync | load | run-all
  config/      default.yaml (mapeos configurables: hojas→Tipo, prefijos IDTE, contrapartes...)
dataform/      workflow_settings.yaml · includes/constantes.js · definitions/{sources,staging,core,marts,assertions}
infra/terraform/  buckets, datasets, SAs, AR, Cloud Run Job, Scheduler, Secret Manager, WIF, Dataform
.github/workflows/ ci.yml · deploy.yml
tests/         pytest con fixtures sintéticos (incluye ejecución del SQL en DuckDB)
docs/          modelo_datos · reglas_pya · adaptadores · runbook · preguntas_abiertas
```

## Desarrollo local

Requisitos: Python 3.11+ y Node 20 (para Dataform). Terraform 1.6+ es opcional.

```bash
make install                 # crea .venv e instala .[dev,gcp] + duckdb + sqlglot
make lint test               # ruff + pytest
make compile                 # dataform compile (usa npx @dataform/cli@3.0.0)
DATAFORM_CLI="npx --yes @dataform/cli@3.0.0" make test   # incluye la prueba SQL en DuckDB
```

> La prueba SQL busca la CLI en `DATAFORM_CLI` (un ejecutable) o `dataform` en el PATH. Lo más
> simple es instalarla así: `npm i -g @dataform/cli@3.0.0`.

Para parsear un archivo real **sin GCP** (deja un CSV por tabla en `salida_local/`):

```bash
.venv/bin/conciliacion fuentes
.venv/bin/conciliacion parse "datos/28-09 al 04-10.xls"
.venv/bin/conciliacion parse "datos/Ordenes Under.xlsx" --param marca=UNDER
.venv/bin/conciliacion parse "datos/hoja rara.xlsx" --fuente pya_liquidacion_detalle
```

Los archivos reales nunca se versionan: `.gitignore` excluye `*.xls*`, `*.csv` y `*.pdf`.

## Configuración

| Variable de entorno | Uso |
|---|---|
| `GCP_PROJECT_ID`, `GCP_REGION`, `BQ_LOCATION` | proyecto/región |
| `GCS_BUCKET` | bucket de crudos |
| `BQ_DATASET_RAW` (`raw`), `BQ_DATASET_OPS` (`ops`) | datasets |
| `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID` | app de Entra ID (no son secretos) |
| `GRAPH_CLIENT_SECRET` | **Secret Manager** → inyectado por Cloud Run |
| `SHAREPOINT_HOSTNAME`, `SHAREPOINT_SITE_PATH`, `SHAREPOINT_DRIVE`, `SHAREPOINT_FOLDER` | origen |
| `DATAFORM_REPOSITORY`, `DATAFORM_RELEASE_CONFIG` | para que `run-all` dispare Dataform |
| `CONCILIACION_CONFIG` | YAML que se fusiona sobre `config/default.yaml` |

Las constantes de negocio para el SQL están en `dataform/includes/constantes.js`: `IVA=0.19`,
`COMISION=0.16`, `TOL_REDONDEO=1`, `TOL_BANCO=5`, `TOL_SEMANA=10`, la ventana del abono, el RUT de
PYA, el agregador por marca y las contrapartes. El test `test_constantes_sql.py` verifica que
coincidan con `reglas/pya.py` y con `default.yaml`.

## Despliegue

1. **Terraform** (`infra/terraform`): copie `terraform.tfvars.example` a `terraform.tfvars` y
   luego ejecute `terraform init -backend-config=bucket=<estado> -backend-config=prefix=conciliacion/dev`
   y `terraform apply`.
2. **Secretos** (los valores no van en el repo ni en el estado de Terraform):
   `gcloud secrets versions add graph-client-secret --data-file=-` y lo mismo para
   `dataform-github-token`.
3. **GitHub** → Variables: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER` y `GCP_DEPLOY_SA`
   (salen de los outputs de Terraform), `GCP_AR_REPO=conciliacion` y
   `GCP_JOB_NAME=conciliacion-run-all`.
4. Al hacer push a `main` o a un tag `v*`, `deploy.yml` construye la imagen, la publica en
   Artifact Registry, actualiza el Cloud Run Job y publica `dataform/` en la rama
   `dataform-release`. Esa es la rama que lee el repositorio Dataform.
5. Cloud Scheduler ejecuta el job todos los días (`--modo diario`, sólo el tag `pya`) y los lunes
   (`--modo semanal --reintentar-errores`, todo el grafo).

Detalle operativo en [docs/runbook.md](docs/runbook.md). Para agregar un canal nuevo (Uber, Rappi,
Mercado Pago, Fintoc, Klap, Adobe Commerce, Getnet, BCI POS), siga
[docs/adaptadores.md](docs/adaptadores.md).
