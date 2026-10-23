"""Reglas de conciliación PedidosYa — implementación de referencia.

Espejo SQL:
  * por pedido  -> dataform/definitions/core/fct_pya_pedido.sqlx
  * semanal     -> dataform/definitions/core/fct_pya_semana.sqlx
  * marts       -> dataform/definitions/marts/*.sqlx
Constantes    -> dataform/includes/constantes.js (test_constantes_sql_python verifica que coincidan)

Convenciones: montos en CLP como float; los descuentos PYA del detalle vienen NETOS de IVA.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from conciliacion.common.montos import redondear

IVA = 0.19
COMISION = 0.16
TOL_REDONDEO = 1
TOL_BANCO = 5
TOL_SEMANA = 10
VENTANA_ABONO_DIAS = (1, 12)  # fecha contable entre fin_periodo+1 y fin_periodo+12
RUT_PYA = "76211425-9"
TIPO_DTE_FACTURA = "33"
AGREGADOR_POR_MARCA = {
    "MELT": "Pedidos YA",
    "UNDER": "Pedidos Ya",
    "EXPRESS": "Pedidos Ya",
    "FEELGOOD": "Pedidos Ya",
}
# Marcas donde los Reintegros PARCIALES se tratan como compensación (hipótesis, ver docs).
MARCAS_REINTEGRO_PARCIAL_COMO_COMPENSACION = {"UNDER", "EXPRESS"}
# Prioridad de fuentes para la fecha real (día de negocio). Hipótesis pendiente de confirmar.
ORDEN_FECHA_REAL = ("boleta", "aloha", "pedido")
HABILITAR_CANCELACIONES = False

VENTA, CARGOS, REINTEGROS = "Venta", "Cargos", "Reintegros"


@dataclass
class LineaLiquidacion:
    numero_pedido: str
    tipo: str = VENTA
    fecha_pedido: date | None = None
    periodo_inicio: date | None = None
    periodo_fin: date | None = None
    sucursal: str | None = None
    cc: str | None = None
    marca: str | None = None
    monto_bruto: float = 0.0
    dcto_comercial_pya_neto: float = 0.0
    dcto_local: float = 0.0
    dcto_usuarios_pya_neto: float = 0.0
    dcto_retiro_pya_neto: float = 0.0
    fugaz: float = 0.0
    neto_venta: float = 0.0
    monto_comisionable: float = 0.0
    comision: float = 0.0
    cargo_plus: float = 0.0


@dataclass
class Boleta:
    folio: str
    monto_total: float
    fecha_dte: date | None = None


@dataclass
class ResultadoPedido:
    numero_pedido: str
    dctos_pya_usuarios_bruto: float
    por_cobrar: int
    com_neta: float
    factura_comision_pedido: float
    monto_banco_pedido: float
    com_provision: float
    compensacion: float
    reembolsos: float
    monto_caja: int
    folio: str
    monto_boleta: float
    integracion: float
    integracion_reportable: bool
    fecha_real: date | None
    excepciones: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------
# Calidad del detalle (relaciones verificadas en archivos reales)
# ------------------------------------------------------------------------------------------
def validar_relaciones_linea(ln: LineaLiquidacion, tol: float = TOL_REDONDEO) -> list[str]:
    """neto = bruto − local − 1,19·(comercial+usuarios+retiro); comisionable = bruto − local − fugaz;
    comision = ROUND(comisionable × 0,16). Devuelve las relaciones que NO se cumplen."""
    fallas = []
    neto = (
        ln.monto_bruto
        - ln.dcto_local
        - (1 + IVA) * (ln.dcto_comercial_pya_neto + ln.dcto_usuarios_pya_neto + ln.dcto_retiro_pya_neto)
    )
    if abs(neto - ln.neto_venta) > tol:
        fallas.append("NETO")
    if abs(ln.monto_bruto - ln.dcto_local - ln.fugaz - ln.monto_comisionable) > tol:
        fallas.append("COMISIONABLE")
    if abs(redondear(ln.monto_comisionable * COMISION) - ln.comision) > tol:
        fallas.append("COMISION")
    return fallas


# ------------------------------------------------------------------------------------------
# Por pedido
# ------------------------------------------------------------------------------------------
def compensacion_pedido(venta: LineaLiquidacion, otras: list[LineaLiquidacion]) -> float:
    """|compensación| del pedido: Σ|K| de filas Cargos con K<0; en marcas configuradas además
    Σ|K| de Reintegros PARCIALES (|K| < neto de la venta)."""
    comp = 0.0
    for o in otras:
        if o.numero_pedido != venta.numero_pedido:
            continue
        if o.tipo == CARGOS and o.neto_venta < 0:
            comp += abs(o.neto_venta)
        elif (
            o.tipo == REINTEGROS
            and (venta.marca or "") in MARCAS_REINTEGRO_PARCIAL_COMO_COMPENSACION
            and 0 < abs(o.neto_venta) < abs(venta.neto_venta)
        ):
            comp += abs(o.neto_venta)
    return comp


def cancelacion_pedido(venta: LineaLiquidacion, otras: list[LineaLiquidacion]) -> float:
    """TODO(regla pendiente): Reintegro TOTAL del pedido (|K| >= neto venta) como cancelación.
    Deshabilitado por defecto (HABILITAR_CANCELACIONES)."""
    if not HABILITAR_CANCELACIONES:
        return 0.0
    return sum(
        abs(o.neto_venta)
        for o in otras
        if o.numero_pedido == venta.numero_pedido
        and o.tipo == REINTEGROS
        and abs(o.neto_venta) >= abs(venta.neto_venta) > 0
    )


def fecha_real(
    fecha_boleta: date | None,
    fecha_aloha: date | None,
    fecha_pedido: date | None,
    orden: tuple[str, ...] = ORDEN_FECHA_REAL,
) -> date | None:
    """Día de negocio del pedido. ÚNICO lugar donde vive la regla (espejo: CTE fecha_real)."""
    fuentes = {"boleta": fecha_boleta, "aloha": fecha_aloha, "pedido": fecha_pedido}
    for f in orden:
        if fuentes[f] is not None:
            return fuentes[f]
    return None


def calcular_pedido(
    venta: LineaLiquidacion,
    compensacion: float = 0.0,
    boleta: Boleta | None = None,
    fecha_aloha: date | None = None,
) -> ResultadoPedido:
    if venta.tipo != VENTA:
        raise ValueError("calcular_pedido espera una línea Tipo=Venta")
    dctos_bruto = venta.dcto_usuarios_pya_neto * (1 + IVA)
    por_cobrar = redondear(venta.neto_venta + dctos_bruto)
    com_neta = (
        venta.comision
        + venta.cargo_plus
        + venta.fugaz
        - venta.dcto_comercial_pya_neto
        - venta.dcto_retiro_pya_neto
    )
    factura = com_neta * (1 + IVA)
    com_provision = (
        venta.comision
        + venta.fugaz
        - venta.dcto_comercial_pya_neto
        - venta.dcto_usuarios_pya_neto
        - venta.dcto_retiro_pya_neto
    )
    comp = abs(compensacion)
    exc = []
    if boleta is not None:
        folio, monto_boleta = boleta.folio, boleta.monto_total
        integracion = monto_boleta - venta.neto_venta
    else:
        folio, monto_boleta, integracion = "-", 0.0, 0.0
        exc.append("PEDIDO_SIN_BOLETA")
    reportable = boleta is not None and abs(integracion) > TOL_REDONDEO
    if reportable:
        exc.append("INTEGRACION")
    return ResultadoPedido(
        numero_pedido=venta.numero_pedido,
        dctos_pya_usuarios_bruto=dctos_bruto,
        por_cobrar=por_cobrar,
        com_neta=com_neta,
        factura_comision_pedido=factura,
        monto_banco_pedido=por_cobrar - factura,
        com_provision=com_provision,
        compensacion=comp,
        reembolsos=-comp,
        monto_caja=por_cobrar - redondear(comp),
        folio=folio,
        monto_boleta=monto_boleta,
        integracion=integracion,
        integracion_reportable=reportable,
        fecha_real=fecha_real(boleta.fecha_dte if boleta else None, fecha_aloha, venta.fecha_pedido),
        excepciones=exc,
    )


def residuo_invariante(r: ResultadoPedido) -> float:
    """monto_caja − monto_boleta + (−dctos_pya_usuarios_bruto + |comp| + integracion). Debe ser 0 (±1)
    para pedidos CON boleta (sin boleta no aplica: monto_boleta = 0)."""
    return r.monto_caja - r.monto_boleta + (-r.dctos_pya_usuarios_bruto + r.compensacion + r.integracion)


def cumple_invariante(r: ResultadoPedido, tol: float = TOL_REDONDEO) -> bool:
    return r.folio == "-" or abs(residuo_invariante(r)) <= tol


# ------------------------------------------------------------------------------------------
# Salidas Innovaweb
# ------------------------------------------------------------------------------------------
def lineas_reembolso(
    r: ResultadoPedido, cc: str, sucursal: str, marca: str, cancelacion: float = 0.0
) -> list[dict]:
    """Filas del archivo de reembolsos Innovaweb para un pedido (espejo: out_innovaweb_reembolsos_pya)."""
    base = {
        "Código centro costo": cc,
        "Sucursal": sucursal,
        "Fecha": r.fecha_real.strftime("%d/%m/%Y") if r.fecha_real else None,
        "Agregador": AGREGADOR_POR_MARCA.get(marca, "Pedidos Ya"),
        "Código de orden": r.numero_pedido,
    }
    boleta = r.folio if r.folio != "-" else "0"
    filas = []
    if r.integracion_reportable:
        filas.append(
            {
                **base,
                "Medio de Pago": "Integración",
                "Número de boleta": r.folio,
                "Valor": redondear(r.integracion),
            }
        )
    if redondear(r.dctos_pya_usuarios_bruto):
        filas.append(
            {
                **base,
                "Medio de Pago": "Descuentos Pedidos Ya a Clientes",
                "Número de boleta": boleta,
                "Valor": -redondear(r.dctos_pya_usuarios_bruto),
            }
        )
    if redondear(r.compensacion):
        filas.append(
            {
                **base,
                "Medio de Pago": "Compensaciones de Agregador",
                "Número de boleta": boleta,
                "Valor": redondear(r.compensacion),
            }
        )
    if redondear(cancelacion):
        filas.append(
            {
                **base,
                "Medio de Pago": "Cancelaciones de Agregador",
                "Número de boleta": boleta,
                "Valor": redondear(cancelacion),
            }
        )
    return filas


def caja_innovaweb(filas: list[tuple[str, str, date, int]]) -> list[dict]:
    """[(cc, sucursal, fecha_real, monto_caja)] -> filas 'Pedidos YA' agrupadas por cc y fecha."""
    acum: dict[tuple, int] = {}
    nombres: dict[str, str] = {}
    for cc, suc, f, monto in filas:
        acum[(cc, f)] = acum.get((cc, f), 0) + int(monto)
        nombres[cc] = suc
    return [
        {
            "Código centro costo": cc,
            "Sucursal": nombres[cc],
            "Fecha de informe": f.strftime("%d/%m/%Y"),
            "Pedidos YA": v,
        }
        for (cc, f), v in sorted(acum.items())
    ]


def pedidos_duplicados(lineas: list[LineaLiquidacion]) -> list[tuple[str, str, date | None]]:
    c = Counter((ln.numero_pedido, ln.tipo, ln.periodo_inicio) for ln in lineas)
    return [k for k, n in c.items() if n > 1]


# ------------------------------------------------------------------------------------------
# Semanal
# ------------------------------------------------------------------------------------------
@dataclass
class Abono:
    fecha_contable: date
    monto: float
    rut_origen: str = RUT_PYA
    nro_operacion: str | None = None


@dataclass
class Factura:
    folio: str
    fecha_docto: date
    monto_neto: float
    monto_iva: float
    rut_proveedor: str = RUT_PYA
    tipo_doc: str = TIPO_DTE_FACTURA


def asignar_abono(periodo_fin: date, total_pdf: float, abonos: list[Abono]) -> Abono | None:
    ini, fin = (periodo_fin + timedelta(days=d) for d in VENTANA_ABONO_DIAS)
    cands = [
        a
        for a in abonos
        if a.rut_origen == RUT_PYA
        and a.monto > 0
        and ini <= a.fecha_contable <= fin
        and abs(a.monto - total_pdf) <= TOL_BANCO
    ]
    return min(cands, key=lambda a: (abs(a.monto - total_pdf), a.fecha_contable), default=None)


def buscar_factura(periodo_fin: date, neto_calc: float, facturas: list[Factura]) -> Factura | None:
    lunes = periodo_fin + timedelta(days=1)
    if lunes.weekday() != 0:  # el periodo PYA siempre termina en domingo
        return None
    cands = [
        f
        for f in facturas
        if f.rut_proveedor == RUT_PYA and f.tipo_doc == TIPO_DTE_FACTURA and f.fecha_docto == lunes
    ]
    return min(cands, key=lambda f: abs(f.monto_neto - neto_calc), default=None)


def calcular_semana(
    lineas: list[LineaLiquidacion],
    pdf: dict[str, float],
    total_pdf: float,
    abonos: list[Abono] | None = None,
    facturas: list[Factura] | None = None,
) -> dict:
    """Conciliación semanal (espejo: fct_pya_semana / conciliacion_semanal_pya).

    ``pdf``: partidas del PDF por concepto canónico (se usan costos_envio y ajustes_liquidacion).
    """

    def s(attr, tipo=None):
        return sum(getattr(ln, attr) for ln in lineas if tipo is None or ln.tipo == tipo)

    periodo_fin = max(ln.periodo_fin for ln in lineas)
    sum_comision, sum_plus, publicidad = s("comision"), s("cargo_plus"), s("fugaz")
    sum_usuarios, sum_comercial, sum_retiro = (
        s("dcto_usuarios_pya_neto"),
        s("dcto_comercial_pya_neto"),
        s("dcto_retiro_pya_neto"),
    )
    costo_envio = abs(pdf.get("costos_envio", 0.0))
    ajustes = pdf.get("ajustes_liquidacion", 0.0)
    neto_factura_calc = (
        sum_comision + sum_plus + publicidad - sum_usuarios - sum_comercial - sum_retiro + costo_envio
    )
    iva_calc = IVA * neto_factura_calc
    k_venta, k_reint, k_cargos = s("neto_venta", VENTA), s("neto_venta", REINTEGROS), s("neto_venta", CARGOS)
    liquidacion_calc = (
        k_venta
        + (sum_usuarios + sum_comercial + sum_retiro)
        + k_reint
        + k_cargos
        - sum_comision
        - sum_plus
        - publicidad
        - redondear(iva_calc)
        + ajustes
    )
    abono = asignar_abono(periodo_fin, total_pdf, abonos or [])
    factura = buscar_factura(periodo_fin, neto_factura_calc, facturas or [])
    dif_pdf = liquidacion_calc - total_pdf
    res = {
        "periodo_fin": periodo_fin,
        "sum_comision": sum_comision,
        "sum_plus": sum_plus,
        "publicidad": publicidad,
        "sum_dcto_usuarios": sum_usuarios,
        "sum_dcto_comercial": sum_comercial,
        "sum_dcto_retiro": sum_retiro,
        "costo_envio": costo_envio,
        "ajustes": ajustes,
        "neto_factura_calc": neto_factura_calc,
        "iva_calc": iva_calc,
        "k_venta": k_venta,
        "k_reintegros": k_reint,
        "k_cargos": k_cargos,
        "liquidacion_calc": liquidacion_calc,
        "total_pdf": total_pdf,
        "dif_liquidacion_pdf": dif_pdf,
        "abono_monto": abono.monto if abono else None,
        "abono_fecha": abono.fecha_contable if abono else None,
        "dif_pdf_banco": (abono.monto - total_pdf) if abono else None,
        "factura_folio": factura.folio if factura else None,
        "dif_neto_factura": (factura.monto_neto - neto_factura_calc) if factura else None,
        "dif_iva_factura": (factura.monto_iva - iva_calc) if factura else None,
    }
    excepciones = []
    if abs(dif_pdf) > TOL_SEMANA:
        excepciones.append("SEMANA_NO_CUADRA_PDF")
    if abono is None:
        excepciones.append("ABONO_NO_ENCONTRADO")
    if factura is None:
        excepciones.append("FACTURA_NO_ENCONTRADA")
    elif abs(res["dif_neto_factura"]) > TOL_SEMANA:
        excepciones.append("FACTURA_NO_CUADRA")
    res["excepciones"] = excepciones
    res["estado"] = "OK" if not excepciones else "REVISAR"
    return res


def ingreso_estimado_order_details(
    total_parcial: float, descuento_usted: float, comision: float, impuesto: float, gastos_operativos: float
) -> float:
    """orderDetails: Ingreso estimado = Total parcial − Dcto financiado por usted − Comisión − Impuesto − Gastos."""
    return total_parcial - descuento_usted - comision - impuesto - gastos_operativos
