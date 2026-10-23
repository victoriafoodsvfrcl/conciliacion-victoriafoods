// Exporta los marts Innovaweb a CSV en GCS si la variable export_bucket está definida
// (workflow_settings.yaml o compilation override). Encabezados = nombres exactos de columnas.
if (constantes.EXPORT_BUCKET) {
  [
    ["out_innovaweb_caja_pya", "innovaweb/caja_pya"],
    ["out_innovaweb_reembolsos_pya", "innovaweb/reembolsos_pya"],
    ["conciliacion_semanal_pya", "pya/conciliacion_semanal"],
    ["excepciones_pya", "pya/excepciones"],
  ].forEach(([tabla, ruta]) => {
    operate(`exportar_${tabla}`)
      .tags(["pya", "exportes"])
      .dependencies([tabla])
      .queries((ctx) => `
        EXPORT DATA OPTIONS (
          uri = 'gs://${constantes.EXPORT_BUCKET}/${ruta}/${tabla}_*.csv',
          format = 'CSV', overwrite = true, header = true, field_delimiter = ';'
        ) AS SELECT * FROM ${ctx.ref(tabla)}`);
  });
}
