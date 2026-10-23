# Reglas de negocio PedidosYa (v1)

Las reglas se implementan dos veces y se prueban contra los mismos datos:

* **SQL (producción):** `dataform/definitions/core/fct_pya_pedido.sqlx`, `fct_pya_semana.sqlx` y los marts.
* **Python (especificación ejecutable):** `src/conciliacion/reglas/pya.py`.

`tests/test_sql_duckdb.py` corre el SQL compilado (transpilado a DuckDB) sobre la salida de los
adaptadores y lo compara con el Python. **Si cambia una regla, hay que cambiar los dos lados.**

Notación del detalle (columnas A–W del estado de cuenta):
E = descuento comercial PYA (neto), F = descuento del local, G = descuento a usuarios PYA (neto),
H = descuento por retiro PYA (neto), I = fugaz/comodín, K = neto de la venta, M = comisionable,
O = comisión, Q = cargo por Plus.

## Relaciones verificadas del detalle (calidad)

Se verificaron en archivos reales y quedan como `assert_detalle_relaciones`:

* `neto = bruto − dcto_local − 1,19·(dcto_comercial_pya + dcto_usuarios_pya + dcto_retiro_pya)`
* `comisionable = bruto − dcto_local − fugaz`
* `comision = ROUND(comisionable × 0,16)`

Ejemplo `2297054261`: 16.390 − 5.051 − 1,19·2.359 = 8.531,79 ≈ 8.532; 16.390 − 5.051 − 3.144 = 8.195;
ROUND(8.195·0,16) = 1.311.

## Por pedido (Tipo = Venta)

| Concepto | Fórmula |
|---|---|
| `dctos_pya_usuarios_bruto` | G × 1,19. Se guarda **sin redondear** y se redondea en los marts |
| `por_cobrar` | ROUND(K + dctos_pya_usuarios_bruto) |
| `com_neta` | O + Q + I − E − H |
| `factura_comision_pedido` | com_neta × 1,19 |
| `monto_banco_pedido` | por_cobrar − factura_comision_pedido |
| `com_provision` | O + I − E − G − H |
| `compensacion` | Σ\|K\| de las filas **Cargos** del pedido con K < 0. En UNDER y EXPRESS se suman además los **Reintegros parciales** (0 < \|K\| < K de la venta). Las marcas son configurables |
| `reembolsos` | −\|compensación\| |
| `monto_caja` ("Monto TD") | por_cobrar − \|compensación\|. Va a Innovaweb, columna *Pedidos YA* |
| boleta | IDTE con `canal_prefijo='PYA'` e `id_externo = Numero de Pedido` → folio, monto_boleta |
| sin boleta | folio `'-'`, monto 0, integración 0 y excepción `PEDIDO_SIN_BOLETA` |
| `integracion` | monto_boleta − K (sólo si hay boleta). Se reporta si \|integración\| > `TOL_REDONDEO` (1) |
| `fecha_real` | ver abajo |

Ejemplos (en `tests/test_reglas_pya.py`):

| Pedido | por_cobrar | com_neta | factura | banco | com_provision | monto_caja |
|---|---|---|---|---|---|---|
| 2299350728 | 19.970 | 3.195 | 3.802,05 | 16.167,95 | 3.195 | 19.970 |
| 2297054261 (+ cargo −1.500) | 11.339 | 5.145 | 6.122,55 | 5.216,45 | 2.096 | 9.839 |

**Invariante** (assertion `assert_invariante_pedido`, sólo para pedidos con boleta):
`monto_caja − monto_boleta + (−dctos_pya_usuarios_bruto + |compensación| + integración) = 0 (±1)`.
Se usa la integración completa, no sólo la reportable, para no acumular errores de redondeo.

### Fecha real (hipótesis aislada)

La regla vive en un solo lugar: `reglas.pya.fecha_real` y `constantes.fechaReal()` (CTE
`fecha_real`). El orden de prioridad (`ORDEN_FECHA_REAL`) es:

1. `boleta`: Fecha_DTE de la boleta IDTE.
2. `aloha`: DateOfBusiness de Aloha. Se busca con IDTE.Pedido = CheckNumber, en el mismo CC, con
   DOB igual a la fecha DTE o al día anterior.
3. `pedido`: fecha del pedido en el detalle PYA.

Está pendiente de confirmar (ver preguntas abiertas). Para cambiar el orden basta editar la
constante en ambos lados.

### Duplicados

El mismo pedido puede venir dos veces con el mismo Tipo en un periodo (se vio con 2318734706 y
2318996350). En ese caso se genera `PEDIDO_DUPLICADO`:

* **por pedido** (caja y reembolsos) se toma **una** ocurrencia;
* **semanal** se suman **todas**, porque así lo hace el PDF.

### Cancelaciones de Agregador (pendiente)

La CTE `cancelacion` está aislada, con un TODO, y queda desactivada mientras
`HABILITAR_CANCELACIONES = false`. Candidato: filas Reintegros que devuelven el total del pedido
(\|K\| ≥ K de la venta), cruzadas con "Reintegros por pedidos cancelados por los usuarios" del PDF.

## Semanal (periodo lunes–domingo)

Las sumas son sobre **todas** las líneas del periodo (Venta, Cargos y Reintegros):

```
neto_factura_calc = ΣO + ΣQ + publicidad(ΣI) − ΣG − ΣE − ΣH + |Costos de envio del PDF|
iva_calc          = 0,19 × neto_factura_calc
liquidacion_calc  = ΣK(Venta) + Σ(G+E+H) + ΣK(Reintegros) + ΣK(Cargos)
                    − ΣO − ΣQ − publicidad − ROUND(iva_calc) + Ajustes de liquidación (PDF)
```

Comparaciones (estado `OK` sólo si todas pasan):

| Chequeo | Regla | Excepción |
|---|---|---|
| detalle vs PDF | \|liquidacion_calc − Total liquidado\| ≤ `TOL_SEMANA` | `SEMANA_NO_CUADRA_PDF` |
| PDF vs banco | abono de RUT 76211425-9, fecha contable entre fin+1 y fin+12, \|dif\| ≤ `TOL_BANCO` (5). Un abono se asigna a una sola semana | `ABONO_NO_ENCONTRADO` |
| factura RCV | tipo 33, proveedor 76211425-9, fecha docto = lunes siguiente al fin de periodo; se compara el neto con neto_factura_calc y el IVA con iva_calc y con *Impuestos* del PDF | `FACTURA_NO_ENCONTRADA` / `FACTURA_NO_CUADRA` |

Casos reales: el PDF 21/09–27/09 liquida 407.539.732 y el abono del 05/10 fue de 407.539.735
(dif 3). El PDF 28/09–04/10 liquida 483.396.361 y el abono del 13/10 fue de 483.396.358.
La factura 3418103 del 12/10 tiene IVA 16.673.939, igual a *Impuestos* del PDF 05/10–11/10.

## Salidas para Innovaweb

* `out_innovaweb_caja_pya`: `Código centro costo | Sucursal | Fecha de informe (dd/mm/aaaa) | Pedidos YA`.
  Es Σ monto_caja por (cc, fecha_real).
* `out_innovaweb_reembolsos_pya`:
  `Código centro costo | Sucursal | Fecha | Medio de Pago | Agregador | Código de orden | Número de boleta | Valor`.

| Medio de Pago | Valor | Número de boleta |
|---|---|---|
| Integración | +integración (si \|i\| > 1) | folio |
| Descuentos Pedidos Ya a Clientes | −ROUND(dctos_pya_usuarios_bruto) | folio o `'0'` |
| Compensaciones de Agregador | +\|compensación\| | folio o `'0'` |
| Cancelaciones de Agregador | + (pendiente) | folio o `'0'` |

El Agregador es `'Pedidos YA'` para MELT y `'Pedidos Ya'` para UNDER/EXPRESS/FEELGOOD
(`AGREGADOR_POR_MARCA`). Con estos signos, en Innovaweb se cumple por pedido
`caja − boleta + Σ reembolsos = 0`, que es el mismo invariante.
