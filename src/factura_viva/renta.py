"""Deducción del 1 % en renta por compras con factura electrónica (art. 336-5 ET).

Para cada factura se revisan las condiciones que **sí se pueden comprobar con los datos**
(validación DIAN, comprador identificado, pago por medio electrónico) y se deja dicho
cuáles no (que la compra no se usó en otro beneficio; que el emisor esté obligado a
facturar). El resultado es una estimación para preparar la declaración: no es asesoría
tributaria.

Estados por factura:
  cumple         las condiciones comprobables se cumplen
  por_probar     falta la prueba del pago electrónico (la factura dice efectivo o no dice)
  no_cumple      falla una condición comprobable (sin validación DIAN o comprador genérico)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .facturas.lector import Factura


@dataclass
class EvaluacionRenta:
    cufe: str
    anio: int
    adquiriente_id: str
    estado: str
    motivo: str
    base_sin_impuestos: Decimal
    base_total: Decimal


@dataclass
class ResumenRenta:
    adquiriente_id: str
    adquiriente_nombre: str
    anio: int
    uvt: Decimal | None
    estado_uvt: str
    tope: Decimal | None
    base_cumple: Decimal = Decimal(0)
    base_por_probar: Decimal = Decimal(0)
    base_cumple_total: Decimal = Decimal(0)
    facturas_cumple: int = 0
    facturas_por_probar: int = 0
    facturas_no_cumple: int = 0
    deduccion: Decimal = Decimal(0)              # 1 % de lo que ya cumple, limitado al tope
    deduccion_potencial: Decimal = Decimal(0)    # si además se prueba el pago de las pendientes
    deduccion_sobre_total: Decimal = Decimal(0)  # misma cuenta con impuestos incluidos
    notas: list[str] = field(default_factory=list)


def _medio(cfg: dict, codigo: str) -> dict:
    return cfg.get("medios_pago", {}).get("codigos", {}).get(str(codigo), {"nombre": f"código {codigo or '(vacío)'}",
                                                                            "electronico": "desconocido"})


def nombre_medio_pago(cfg: dict, codigo: str) -> str:
    return _medio(cfg, codigo)["nombre"]


def evaluar(f: Factura, cfg: dict, pago_probado: bool) -> EvaluacionRenta:
    signo = f.signo
    base = signo * f.valor_bruto
    total = signo * f.total_pagar
    genericos = {str(x) for x in cfg.get("adquirientes_genericos", [])}
    if not f.validacion.validada:
        estado, motivo = "no_cumple", "sin validación previa de la DIAN"
    elif not f.adquiriente_id or not f.adquiriente_nombre or f.adquiriente_id in genericos:
        estado, motivo = "no_cumple", "no identifica al comprador con nombre y documento"
    else:
        medio = _medio(cfg, f.medio_pago)
        if medio["electronico"] is True:
            estado, motivo = "cumple", f"medio de pago en la factura: {medio['nombre']}"
        elif pago_probado:
            estado = "cumple"
            motivo = (f"la factura dice '{medio['nombre']}', pero el pago aparece en un extracto bancario "
                      "(prueba admitida según Concepto DIAN 738 de 2024)")
        else:
            estado = "por_probar"
            motivo = (f"la factura dice '{medio['nombre']}': falta probar que se pagó por medio electrónico "
                      "(extracto o comprobante)")
    return EvaluacionRenta(f.cufe, f.fecha.year, f.adquiriente_id, estado, motivo, base, total)


def _regla(cfg: dict, anio: int) -> dict:
    vigentes = [r for r in cfg["deduccion_factura_electronica"] if r["desde"].year <= anio]
    if not vigentes:
        raise ValueError(f"No hay regla de deducción vigente en {anio}")
    return max(vigentes, key=lambda r: r["desde"])


def resumir(facturas: list[Factura], evaluaciones: list[EvaluacionRenta], cfg: dict) -> list[ResumenRenta]:
    uvts = {u["anio"]: u for u in cfg["uvt"]}
    nombres: dict[str, str] = {}
    for f in facturas:  # el nombre más completo que aparezca para ese documento
        if len(f.adquiriente_nombre) > len(nombres.get(f.adquiriente_id, "")):
            nombres[f.adquiriente_id] = f.adquiriente_nombre
    grupos: dict[tuple[str, int], ResumenRenta] = {}
    for e in evaluaciones:
        clave = (e.adquiriente_id, e.anio)
        if clave not in grupos:
            u = uvts.get(e.anio)
            regla = _regla(cfg, e.anio)
            uvt = Decimal(str(u["valor"])) if u else None
            grupos[clave] = ResumenRenta(
                e.adquiriente_id, nombres.get(e.adquiriente_id, ""), e.anio, uvt,
                u["estado"] if u else "sin dato",
                uvt * Decimal(str(regla["tope_uvt"])) if uvt else None)
        g = grupos[clave]
        if e.estado == "cumple":
            g.base_cumple += e.base_sin_impuestos
            g.base_cumple_total += e.base_total
            g.facturas_cumple += 1
        elif e.estado == "por_probar":
            g.base_por_probar += e.base_sin_impuestos
            g.facturas_por_probar += 1
        else:
            g.facturas_no_cumple += 1
    for (_, anio), g in grupos.items():
        regla = _regla(cfg, anio)
        pct = Decimal(str(regla["porcentaje"]))

        def uno(base: Decimal, g=g, pct=pct) -> Decimal:
            v = (max(base, Decimal(0)) * pct).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            return min(v, g.tope) if g.tope is not None else v

        g.deduccion = uno(g.base_cumple)
        g.deduccion_potencial = uno(g.base_cumple + g.base_por_probar)
        g.deduccion_sobre_total = uno(g.base_cumple_total)
        if g.tope is None:
            g.notas.append(f"no hay UVT registrada para {anio}: no se aplicó el tope")
        if regla["estado"] != "verificado":
            g.notas.append("regla de deducción aún no verificada contra el texto oficial")
    return sorted(grupos.values(), key=lambda g: (g.anio, g.adquiriente_nombre))
