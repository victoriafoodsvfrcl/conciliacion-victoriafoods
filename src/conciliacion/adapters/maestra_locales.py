"""Maestra de locales ('Locales - CC.xlsx', Hoja1, 25 columnas).

Produce:

* ``maestra_dim_local``       (cc, marca, nombre_plataforma)
* ``maestra_dim_local_alias`` (cc, marca, sistema, alias_original, alias_normalizado)

Varias filas por CC traen alias adicionales (sólo algunas columnas llenas). Se repara mojibake
('Melt Pizzas - PeÃ±alolÃ©n'), se descartan IDs Rappi = 0 y valores nulos configurables.
Los conflictos (un alias normalizado -> 2 CC en el mismo sistema) NO detienen la carga: se
reportan con ``conflictos_alias`` y los bloquea la assertion de Dataform ``assert_alias_unico``.
"""

from __future__ import annotations

import pandas as pd

from conciliacion.adapters.base import (
    Adaptador,
    Columna,
    ErrorAdaptador,
    buscar_fila_encabezado,
    leer_excel_crudo,
    mapear_columnas,
)
from conciliacion.adapters.comun import encabezados_desde_fila, id_texto
from conciliacion.common.texto import limpiar, normalizar_llave
from conciliacion.config import config_negocio

# encabezado -> sistema
SISTEMAS: dict[str, str] = {
    "Sucursal Plataforma": "plataforma",
    "Nombre IDTE": "idte",
    "Sucursal Uber": "uber",
    "Sucursal PYA": "pya",
    "Sucursal RPI": "rpi",
    "ID Sucursal Rappi": "rappi_id",
    "Sucursal OLO Reaction": "olo_reaction",
    "Sucursal GoodMeal": "goodmeal",
    "Sucursal Aloha": "aloha",
    "Sucursal Getnet": "getnet",
    "Mercado Pago": "mercado_pago",
    "Brinks": "brinks",
    "Uber Direct": "uber_direct",
    "Plataforma Ticket": "plataforma_ticket",
    "Código Brinks": "codigo_brinks",
    "Sucursal SAP": "sap",
    "Sucursal Justo": "justo",
    "Sucursal Redelcom": "redelcom",
    "Uber Direct2": "uber_direct",
    "OLO Nuevo": "olo_nuevo",
    "Cheaf": "cheaf",
    "Edenred": "edenred",
    "Klap": "klap",
}
ALIAS = {"marca": ["Marca"], "cc": ["Centro de Costo"], **{s + "|" + h: [h] for h, s in SISTEMAS.items()}}
OBLIGATORIAS = [
    "marca",
    "cc",
    "pya|Sucursal PYA",
    "idte|Nombre IDTE",
    "aloha|Sucursal Aloha",
    "plataforma|Sucursal Plataforma",
]
MARCAS = {"MELT", "UNDER", "FEELGOOD", "EXPRESS"}

ESQ_LOCAL = [
    Columna("cc", "STRING", True),
    Columna("marca", "STRING", True),
    Columna("nombre_plataforma", "STRING"),
]
ESQ_ALIAS = [
    Columna("cc", "STRING", True),
    Columna("marca", "STRING", True),
    Columna("sistema", "STRING", True),
    Columna("alias_original", "STRING", True),
    Columna("alias_normalizado", "STRING", True),
]


def conflictos_alias(df_alias: pd.DataFrame) -> pd.DataFrame:
    """Alias normalizados que apuntan a más de un CC dentro del mismo sistema."""
    g = df_alias.groupby(["sistema", "alias_normalizado"])["cc"].nunique()
    malos = g[g > 1].reset_index()[["sistema", "alias_normalizado"]]
    return df_alias.merge(malos, on=["sistema", "alias_normalizado"])


class MaestraLocales(Adaptador):
    source_id = "maestra_locales"
    schema_version = "1"
    descripcion = "Maestra de locales / centros de costo y sus alias por sistema"
    patrones = (r"locales\s*-\s*cc[^/]*\.xlsx$", r"maestra.{0,3}locales[^/]*\.xlsx$")
    esquemas = {"maestra_dim_local": ESQ_LOCAL, "maestra_dim_local_alias": ESQ_ALIAS}

    def parse(self, contenido: bytes, nombre: str, **params) -> list:
        cfg = config_negocio()["maestra_locales"]
        nulos = {normalizar_llave(v) for v in cfg["valores_nulos"]}
        hojas = leer_excel_crudo(contenido, nombre)
        hoja = "Hoja1" if "Hoja1" in hojas else next(iter(hojas))
        crudo = hojas[hoja]
        i = buscar_fila_encabezado(crudo, ["Marca", "Centro de Costo"], fuente=self.source_id)
        nombres, datos = encabezados_desde_fila(crudo, i)
        mapeo = mapear_columnas(nombres, ALIAS, OBLIGATORIAS, self.source_id, hoja)
        df = pd.DataFrame({canon: datos[nombres.index(orig)] for orig, canon in mapeo.items()})
        df["_fila"] = datos["_fila"]
        df["cc"] = df["cc"].map(lambda v: (limpiar(v) or "").upper() or None)
        df["marca"] = df["marca"].map(lambda v: (normalizar_llave(v) or "") or None)
        if cfg.get("heredar_cc_vacio", True):
            df[["cc", "marca"]] = df[["cc", "marca"]].ffill()
        if df["cc"].isna().any():
            raise ErrorAdaptador(
                f"[{self.source_id}] filas sin Centro de Costo: {df.loc[df['cc'].isna(), '_fila'].tolist()}"
            )
        malas = set(df["marca"].dropna()) - MARCAS
        if malas:
            raise ErrorAdaptador(f"[{self.source_id}] marcas desconocidas: {sorted(malas)}")
        # una marca por CC
        marcas_cc = df.groupby("cc")["marca"].nunique()
        if (marcas_cc > 1).any():
            raise ErrorAdaptador(
                f"[{self.source_id}] CC con más de una marca: {marcas_cc[marcas_cc > 1].index.tolist()}"
            )

        alias_filas = []
        for _, fila in df.iterrows():
            for canon in mapeo.values():
                if "|" not in canon:
                    continue
                sistema = canon.split("|")[0]
                original = limpiar(id_texto(fila[canon]))
                norm = normalizar_llave(original)
                if not norm or norm in nulos:
                    continue
                alias_filas.append(
                    {
                        "cc": fila["cc"],
                        "marca": fila["marca"],
                        "sistema": sistema,
                        "alias_original": original,
                        "alias_normalizado": norm,
                        "_hoja": hoja,
                        "_fila": fila["_fila"],
                    }
                )
        da = pd.DataFrame(alias_filas).drop_duplicates(["cc", "sistema", "alias_normalizado"])
        plataforma = da[da["sistema"] == "plataforma"].groupby("cc")["alias_original"].first()
        dl = df.groupby("cc", as_index=False).agg(marca=("marca", "first"), _fila=("_fila", "min"))
        dl["nombre_plataforma"] = dl["cc"].map(plataforma)
        dl["_hoja"] = hoja
        conflictos = conflictos_alias(da)
        adv = [
            f"Alias en conflicto (sistema, alias -> CCs): {r.sistema}/{r.alias_normalizado} -> {r.cc}"
            for r in conflictos.itertuples()
        ]
        return [self.tabla("maestra_dim_local", dl), self.tabla("maestra_dim_local_alias", da, adv)]
