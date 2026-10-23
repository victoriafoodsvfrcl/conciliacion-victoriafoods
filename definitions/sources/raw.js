// Declaraciones de las tablas crudas que crea el loader Python (dataset raw).
// Cada tabla corresponde a un TablaParseada de un adaptador (ver src/conciliacion/adapters).
const TABLAS_RAW = [
  "pya_liquidacion_detalle",
  "pya_estado_cuenta_cabecera",
  "pya_estado_cuenta_partidas",
  "pya_order_details",
  "idte_boletas",
  "aloha_ordenes",
  "aloha_order_modes",
  "bci_movimientos",
  "bci_cartola_cabecera",
  "sii_rcv_compras",
  "sii_rcv_otros_impuestos",
  "innovaweb_caja",
  "innovaweb_ventas",
  "innovaweb_recaudaciones",
  "innovaweb_reembolsos",
  "maestra_dim_local",
  "maestra_dim_local_alias",
];

TABLAS_RAW.forEach((nombre) =>
  declare({
    database: dataform.projectConfig.defaultDatabase,
    schema: constantes.DATASET_RAW,
    name: nombre,
  })
);
