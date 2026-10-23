// Constantes y helpers de negocio para el SQL.
// ESPEJO de src/conciliacion/reglas/pya.py: tests/test_constantes_sql.py verifica que coincidan.

const IVA = 0.19;
const COMISION = 0.16;
const TOL_REDONDEO = 1;
const TOL_BANCO = 5;
const TOL_SEMANA = 10;
const VENTANA_ABONO_MIN = 1; // días después del fin de periodo
const VENTANA_ABONO_MAX = 12;
const RUT_PYA = "76211425-9";
const TIPO_DTE_FACTURA = "33";
// Tipos DTE considerados "documento de venta" al buscar la boleta de un pedido
const TIPOS_DTE_VENTA = ["33", "34", "39", "41"];

const AGREGADOR_POR_MARCA = {
  MELT: "Pedidos YA",
  UNDER: "Pedidos Ya",
  EXPRESS: "Pedidos Ya",
  FEELGOOD: "Pedidos Ya",
};
const AGREGADOR_DEFECTO = "Pedidos Ya";

// Hipótesis (ver docs/reglas_pya.md): en estas marcas los Reintegros PARCIALES son compensación.
const MARCAS_REINTEGRO_PARCIAL_COMO_COMPENSACION = ["UNDER", "EXPRESS"];
// Prioridad para la fecha real (día de negocio). Hipótesis pendiente de confirmar.
const ORDEN_FECHA_REAL = ["boleta", "aloha", "pedido"];
// Regla de Cancelaciones de Agregador pendiente: deshabilitada.
const HABILITAR_CANCELACIONES = false;

// Clasificación de contrapartes bancarias (espejo de config/default.yaml -> bci_cartola.contrapartes)
const CONTRAPARTES = [
  { rut: "76211425-9", contraparte: "PEDIDOSYA" },
  { rut: "77099356-3", contraparte: "UBER" },
  { rut: "76837223-3", contraparte: "RAPPI" },
  { rut: "77190661-3", contraparte: "BCI_PAGOS" },
  { rut: "77143385-5", contraparte: "FINTOC" },
  { patron_nombre: "MERCADO\\s*PAGO|MERCADOPAGO", contraparte: "MERCADO_PAGO" },
];

const DATASET_RAW = (dataform.projectConfig.vars && dataform.projectConfig.vars.dataset_raw) || "raw";
const EXPORT_BUCKET = (dataform.projectConfig.vars && dataform.projectConfig.vars.export_bucket) || "";

// ---- literales NUMERIC exactos (evita aritmética FLOAT64 en montos) -------------------------
const N_IVA = `NUMERIC '${IVA}'`;
const N_FACTOR_IVA = `NUMERIC '${(1 + IVA).toFixed(2)}'`;

// ---- helpers ---------------------------------------------------------------------------------
function lista(valores) {
  return valores.map((v) => `'${v}'`).join(", ");
}

function agregadorPorMarca(col) {
  const whens = Object.entries(AGREGADOR_POR_MARCA)
    .map(([m, a]) => `WHEN '${m}' THEN '${a}'`)
    .join(" ");
  return `CASE ${col} ${whens} ELSE '${AGREGADOR_DEFECTO}' END`;
}

// Marca a partir del nombre normalizado de la sucursal, sólo como respaldo si no hay CC.
function marcaDesdeNombre(colNorm) {
  return `CASE
    WHEN STARTS_WITH(${colNorm}, 'MELT') THEN 'MELT'
    WHEN STARTS_WITH(${colNorm}, 'UNDER EXPRESS') THEN 'EXPRESS'
    WHEN STARTS_WITH(${colNorm}, 'FEELGOOD') OR STARTS_WITH(${colNorm}, 'FEEL GOOD') THEN 'FEELGOOD'
    WHEN STARTS_WITH(${colNorm}, 'UNDER') THEN 'UNDER'
  END`;
}

// Única definición SQL de la fecha real (espejo de reglas.pya.fecha_real).
function fechaReal() {
  const columnas = { boleta: "fecha_boleta", aloha: "fecha_aloha", pedido: "fecha_pedido" };
  return `COALESCE(${ORDEN_FECHA_REAL.map((f) => columnas[f]).join(", ")})`;
}

// Normalización de llaves en SQL (para valores que NO vienen normalizados desde Python).
function llave(col) {
  return `UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(NORMALIZE(REGEXP_REPLACE(${col}, r'[\\x{200B}-\\x{200F}\\x{2060}\\x{FEFF}]', ''), NFD), r'\\p{M}', ''), r'\\s+', ' ')))`;
}

function contraparteSql(colRut, colNombre) {
  const porRut = CONTRAPARTES.filter((c) => c.rut)
    .map((c) => `WHEN ${colRut} = '${c.rut}' THEN '${c.contraparte}'`)
    .join("\n    ");
  const porNombre = CONTRAPARTES.filter((c) => c.patron_nombre)
    .map((c) => `WHEN REGEXP_CONTAINS(UPPER(COALESCE(${colNombre}, '')), r'${c.patron_nombre}') THEN '${c.contraparte}'`)
    .join("\n    ");
  return `CASE\n    ${porRut}\n    ${porNombre}\n  END`;
}

module.exports = {
  IVA,
  COMISION,
  TOL_REDONDEO,
  TOL_BANCO,
  TOL_SEMANA,
  VENTANA_ABONO_MIN,
  VENTANA_ABONO_MAX,
  RUT_PYA,
  TIPO_DTE_FACTURA,
  TIPOS_DTE_VENTA,
  AGREGADOR_POR_MARCA,
  AGREGADOR_DEFECTO,
  MARCAS_REINTEGRO_PARCIAL_COMO_COMPENSACION,
  ORDEN_FECHA_REAL,
  HABILITAR_CANCELACIONES,
  CONTRAPARTES,
  DATASET_RAW,
  EXPORT_BUCKET,
  N_IVA,
  N_FACTOR_IVA,
  lista,
  agregadorPorMarca,
  marcaDesdeNombre,
  fechaReal,
  llave,
  contraparteSql,
};
