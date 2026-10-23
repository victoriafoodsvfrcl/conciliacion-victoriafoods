# Runbook

## Ejecuciones programadas

| Scheduler | Cron (America/Santiago) | Comando |
|---|---|---|
| `conciliacion-diario` | `47 6 * * *` | `run-all --modo diario`: sync + load + Dataform sólo con tag `pya` |
| `conciliacion-lunes` | `17 11 * * 1` | `run-all --modo semanal --reintentar-errores`: todo el grafo; reintenta los archivos en ERROR |

El job termina con código 1 si algún archivo quedó en `ERROR`. El error se ve en Cloud Run →
Jobs → Ejecuciones y en Cloud Logging, donde los logs son JSON con `severity`.

## Primera instalación

1. `terraform apply` en `infra/terraform`.
2. Cargue los secretos:
   ```bash
   printf '%s' "<client secret Graph>" | gcloud secrets versions add graph-client-secret --data-file=-
   printf '%s' "<PAT GitHub fine-grained, contents:read>" | gcloud secrets versions add dataform-github-token --data-file=-
   ```
3. En Entra ID, la app necesita el permiso de aplicación `Sites.Selected` (recomendado) o
   `Sites.Read.All`. Con `Sites.Selected` hay que conceder acceso de lectura al sitio:
   `POST /sites/{site-id}/permissions`.
4. Configure las variables de GitHub (ver README) y haga push a `main`. Eso publica la imagen y
   la rama `dataform-release`.
5. Ejecute el job a mano una vez: `gcloud run jobs execute conciliacion-run-all --region <r> --wait`.

> **Dataform y subcarpetas:** el repositorio Dataform de GCP lee el proyecto desde la raíz de la
> rama. Por eso `deploy.yml` publica `dataform/` en la rama `dataform-release` con
> `git subtree split`, y esa es la rama que usan el repositorio y el release config.

## Operación

**Ver el estado de la semana:**
```sql
SELECT * FROM marts.conciliacion_semanal_pya ORDER BY periodo_inicio DESC;
SELECT tipo, severidad, COUNT(*) FROM marts.excepciones_pya GROUP BY 1, 2 ORDER BY 2, 3 DESC;
```

**Archivos con error o sin adaptador:**
```sql
SELECT nombre, source_id, estado, mensaje, ingestado_en
FROM ops.archivos_ingestados
WHERE estado IN ('ERROR', 'SIN_ADAPTADOR')
ORDER BY ingestado_en DESC;
```

**Reprocesar un archivo:** corrija la causa (configuración o versión del adaptador) y luego:
```sql
UPDATE ops.archivos_ingestados SET estado = 'INGESTADO' WHERE archivo_id = '<sha256>';
```
Después ejecute `gcloud run jobs execute conciliacion-run-all --args=load,--reintentar-errores`.
La carga borra las filas previas de ese `_archivo_id`, así que no se duplican.

**Archivo corregido en SharePoint:** el contenido nuevo tiene otro sha256 y se ingesta como un
archivo nuevo. Staging toma automáticamente el último archivo por periodo o día.

**Hoja del .xls no reconocida** (`No se reconoce la hoja 'X'`): agregue el nombre normalizado a
`pya_liquidacion_detalle.hojas_tipo` o a `hojas_ignorar` en un YAML de override
(`CONCILIACION_CONFIG`). Luego reprocese el archivo.

**Sucursal sin CC** (`SUCURSAL_SIN_CC`): agregue el alias a la maestra `Locales - CC.xlsx`, en la
columna del sistema correspondiente. Se ingesta en la próxima ejecución.

**Alias en conflicto** (`assert_alias_unico` falla): corrija la maestra. Mientras tanto, ese
alias queda sin CC.

**Semana REVISAR:** revise las columnas `dif_*` de `conciliacion_semanal_pya`:
* `dif_liquidacion_pdf`: falta un tipo (Cargos o Reintegros), hay líneas duplicadas o un concepto
  nuevo en el PDF. Revise también `pdf_cuadra_internamente`.
* `ABONO_NO_ENCONTRADO`: falta cargar la cartola o el abono cayó fuera de la ventana (fin+1 a
  fin+12).
* `FACTURA_NO_ENCONTRADA`: falta cargar el RCV del mes o la factura no es del lunes siguiente.

## Ejecución local sin GCP

```bash
make install
.venv/bin/conciliacion parse "datos/05-10 al 11-10.xls"
```
Deja un CSV por tabla en `salida_local/`. `EscritorLocal` (`load/bigquery_loader.py`) también
sirve para pruebas de punta a punta con `AlmacenMemoria` y `ManifiestoMemoria`.

## Agregar un usuario lector

Dé `roles/bigquery.dataViewer` sobre el dataset `marts` (no sobre `raw`) y
`roles/bigquery.jobUser` sobre el proyecto.
