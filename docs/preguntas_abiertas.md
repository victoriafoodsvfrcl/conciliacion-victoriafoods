# Preguntas abiertas

Cada punto indica qué supuesto usa hoy el código y dónde cambiarlo.

1. **Razón social de cada fuente.** PYA liquida a INVERSIONES VFR I SPA (76800177-4), y la cartola
   BCI es de esa misma empresa. En cambio, IDTE y el RCV parecen ser de otra razón social.
   * ¿Qué razón social emite las boletas PYA en IDTE y cuál recibe la factura de comisión en el
     RCV?
   * ¿Se concilia por razón social o consolidado?
   * *Hoy:* no se filtra por razón social. La cabecera del PDF y la de la cartola guardan
     `razon_social` y `rut_empresa`, que sirven para agregar ese filtro.

2. **`VTA ONLINE POR COBRAR`** (informe de caja Melt). ¿Qué es? ¿Ventas online pendientes de un
   medio de pago, o agregadores no liquidados? *Hoy:* entra como `medio_pago` genérico en
   `innovaweb_caja` y no se usa.

3. **Caja del 28–30/09.** ¿Dónde se cargó la caja de esos días? El corte mensual (30/09) no
   coincide con la semana PYA (28/09–04/10). ¿Se carga por fecha real del pedido o por la semana
   de liquidación? *Hoy:* `out_innovaweb_caja_pya` agrupa por `fecha_real`, sin importar la
   semana.

4. **Regla exacta de `Fecha Real`.** *Hoy* la prioridad es boleta (Fecha_DTE) → Aloha
   (DateOfBusiness) → fecha del pedido (`ORDEN_FECHA_REAL`).
   * ¿Debe ganar Aloha DOB? Es más preciso para pedidos después de medianoche.
   * ¿Qué pasa con los pedidos que cruzan medianoche sin boleta?

5. **Cancelaciones de Agregador.** ¿Cómo se calculan? ¿Viene del detalle (Reintegros totales),
   del PDF ("Reintegros por pedidos cancelados por los usuarios") o del portal (orderDetails
   `Estado del pedido` = cancelado)? *Hoy:* la CTE `cancelacion` está aislada y desactivada
   (`HABILITAR_CANCELACIONES=false`).

6. **Origen de "Responsabilidad Tienda", "Pedido Sin Boleta" y "Ajustes recaudación"** en
   reembolsos Innovaweb. ¿Quién los registra y con qué regla? ¿"Pedido Sin Boleta" debe salir de
   la excepción `PEDIDO_SIN_BOLETA`? *Hoy:* no se generan. Sólo se comparan si existen en
   Innovaweb.

7. **Abonos 4.717.440 (01/10) y 42.818.978 (16/10)** de RUT 76211425-9, sin liquidación semanal
   asociada. ¿Son ajustes, liquidaciones de otra razón social o pagos de campañas? Lo mismo para
   las **facturas 3325210 (16 MM) y 3325217 (6 MM) del 15/10**, de otra serie. *Hoy:* salen como
   `ABONO_PYA_NO_ASIGNADO`, y las facturas nunca se asignan a una semana porque la fecha debe ser
   el lunes siguiente.

8. **Plantilla oficial de carga Innovaweb.** ¿Hay que cargar un archivo xlsx o csv? ¿Con qué
   separador y formato numérico? ¿Las columnas exactas son
   `Código centro costo | Sucursal | Fecha de informe | Pedidos YA` para caja? ¿"Sucursal" debe
   ser el nombre de la plataforma (hoy se usa `Sucursal Plataforma` de la maestra)? *Hoy:* los
   marts usan esas columnas y se exportan como CSV `;` si `export_bucket` está definido.

9. **Pestañas del .xls crudo de PYA.** No se pudo abrir el archivo crudo. ¿Trae hojas separadas
   por tipo? ¿Con qué nombres? *Hoy:* el mapeo `hojas_tipo` es configurable y falla si una hoja no
   se reconoce. También acepta la columna `Tipo` pegada a mano.

10. **`PLUS` vs `Cargo por Pedidos con Plus`.** *Hoy* el monto de Plus (690/490) se toma de
    `Cargo por Pedidos con Plus` (columna Q). `PLUS` (columna T) se guarda como indicador
    (`es_plus`). Falta confirmarlo con un archivo crudo.

11. **Compensación en UNDER/EXPRESS.** *Hoy* los Reintegros **parciales** (0 < \|K\| < K de la
    venta) cuentan como compensación sólo en UNDER y EXPRESS
    (`MARCAS_REINTEGRO_PARCIAL_COMO_COMPENSACION`). ¿Aplica también a MELT y FEELGOOD? ¿Un cargo
    de reclamo que llega en una semana posterior se imputa a la fecha real del pedido original?
    Así está hoy: se cruza por número de pedido sin importar el periodo.

12. **Pedidos duplicados reales** (2318734706, 2318996350). ¿PYA los pagó dos veces? *Hoy:* la
    semana suma ambos, igual que el PDF, y por pedido se cuentan una vez. Si PYA los pagó dos
    veces, la caja quedaría corta.

13. **"Total con pago fuera de la aplicación ya cobrado"** y **"... a cobrar"** del PDF. *Hoy*
    son informativos y no entran en la suma que se valida contra el total. Con "Cobrado por =
    PedidosYa" valen 0. Falta confirmar si se restan cuando hay pago en efectivo.

14. **`Costos de envio` en la liquidación calculada.** Entran en `neto_factura_calc`, pero en
    `liquidacion_calc` sólo aparecen de forma indirecta, vía el IVA (según la especificación).
    Falta confirmarlo con una semana que tenga costos de envío altos (05/10–11/10: −1.262.065).

15. **Tipos DTE** que cuentan como "boleta" del pedido. *Hoy* son 33, 34, 39 y 41; las notas de
    crédito (61) no se consideran. Si un pedido tiene varias boletas, se toma la última.

16. **Región de Dataform.** *Hoy* se usa `southamerica-west1` (configurable). Hay que confirmar
    que Dataform esté disponible en esa región.
