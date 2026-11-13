"""Conciliación interna de una factura: ¿las partes suman el total?

Una factura queda **conciliada** cuando todos los chequeos pasan dentro de la
tolerancia configurada (``config/conciliacion.yaml``). Cada chequeo guarda lo esperado,
lo obtenido y la diferencia, para que el resultado se pueda auditar.

Chequeos:
  C1  suma de líneas = valor bruto del encabezado
  C2  valor bruto + impuestos = total con impuestos
  C3  total con impuestos − descuentos + cargos + redondeo = total a pagar
      (se acepta la variante que además resta anticipos, usada antes del anexo 1.8)
  C4  por línea: cantidad × precio unitario − descuentos + cargos = valor de la línea
  C5  impuestos de las líneas = impuestos del encabezado (si las líneas los traen)
  C6  por impuesto: base × tarifa = valor
  C7  número de líneas = el declarado
  C8  CUFE bien formado (SHA-384) y, si hay contenedor, igual al del contenedor
  C9  validación de la DIAN presente (código 02; algunos emisores lo escriben 002)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .lector import Factura, cufe_bien_formado


@dataclass
class Chequeo:
    codigo: str
    nombre: str
    esperado: str
    obtenido: str
    diferencia: Decimal | None
    ok: bool
    nota: str = ""


@dataclass
class Tolerancias:
    pesos: Decimal = Decimal("2.00")          # diferencia admitida en sumas
    exigir_validacion_dian: bool = True

    @classmethod
    def de_config(cls, cfg: dict | None) -> "Tolerancias":
        cfg = cfg or {}
        return cls(Decimal(str(cfg.get("tolerancia_pesos", "2.00"))),
                   bool(cfg.get("exigir_validacion_dian", True)))


def _monto(codigo, nombre, esperado: Decimal, obtenido: Decimal, tol: Decimal, nota="") -> Chequeo:
    dif = obtenido - esperado
    return Chequeo(codigo, nombre, f"{esperado:f}", f"{obtenido:f}", dif, abs(dif) <= tol, nota)


def conciliar(f: Factura, tol: Tolerancias | None = None) -> list[Chequeo]:
    tol = tol or Tolerancias()
    t = tol.pesos
    ch: list[Chequeo] = []

    suma_lineas = sum((l.valor for l in f.lineas), Decimal(0))
    ch.append(_monto("C1", "Suma de líneas = valor bruto", f.valor_bruto, suma_lineas, t))

    ch.append(_monto("C2", "Valor bruto + impuestos = total con impuestos",
                     f.total_con_impuestos, f.valor_bruto + f.total_impuestos, t))

    calc = f.total_con_impuestos - f.descuentos + f.cargos + f.redondeo
    c3 = _monto("C3", "Total con impuestos − descuentos + cargos = total a pagar",
                f.total_pagar, calc, t)
    if not c3.ok and f.anticipos:
        alt = _monto("C3", c3.nombre, f.total_pagar, calc - f.anticipos, t,
                     "cuadra restando anticipos (regla anterior al anexo técnico 1.8)")
        c3 = alt if alt.ok else c3
    ch.append(c3)

    peor: Chequeo | None = None
    for l in f.lineas:
        if l.gratis:
            continue
        c = _monto("C4", f"Línea {l.numero}: cantidad × precio = valor",
                   l.valor, l.cantidad * l.precio_unitario - l.descuentos + l.cargos, t)
        if peor is None or abs(c.diferencia) > abs(peor.diferencia):
            peor = c
    if peor is not None:
        peor.nombre = "Cantidad × precio = valor de línea (peor caso: " + peor.nombre.split(":")[0] + ")"
        ch.append(peor)

    por_linea: dict[str, Decimal] = defaultdict(Decimal)
    for l in f.lineas:
        for i in l.impuestos:
            por_linea[i.codigo] += i.valor
    if por_linea:
        encabezado: dict[str, Decimal] = defaultdict(Decimal)
        for i in f.impuestos:
            encabezado[i.codigo] += i.valor
        dif = max((abs(por_linea[k] - encabezado.get(k, Decimal(0))) for k in por_linea),
                  default=Decimal(0))
        faltan = set(encabezado) - set(por_linea)
        dif = max([dif] + [abs(encabezado[k]) for k in faltan])
        ch.append(Chequeo("C5", "Impuestos de las líneas = impuestos del encabezado",
                          str(dict(sorted((k, f"{v:f}") for k, v in encabezado.items()))),
                          str(dict(sorted((k, f"{v:f}") for k, v in por_linea.items()))),
                          dif, dif <= t))

    peor_imp: Chequeo | None = None
    for i in f.impuestos:
        if i.base is None or i.porcentaje is None:
            continue
        c = _monto("C6", f"{i.nombre or i.codigo} {i.porcentaje:f} %: base × tarifa = valor",
                   i.valor, i.base * i.porcentaje / Decimal(100), t)
        if peor_imp is None or abs(c.diferencia) > abs(peor_imp.diferencia):
            peor_imp = c
    if peor_imp is not None:
        ch.append(peor_imp)

    if f.lineas_declaradas is not None:
        ok = f.lineas_declaradas == len(f.lineas)
        ch.append(Chequeo("C7", "Número de líneas = el declarado", str(f.lineas_declaradas),
                          str(len(f.lineas)), None, ok))

    ok_cufe = cufe_bien_formado(f.cufe)
    nota = "" if ok_cufe else "no tiene 96 caracteres hexadecimales (SHA-384)"
    if ok_cufe and f.cufe_contenedor and f.cufe_contenedor != f.cufe:
        ok_cufe, nota = False, "el CUFE del contenedor no coincide con el de la factura"
    if ok_cufe and f.numero_contenedor and f.numero_contenedor != f.numero:
        ok_cufe, nota = False, "el número del contenedor no coincide con el de la factura"
    ch.append(Chequeo("C8", "CUFE bien formado y consistente con el contenedor",
                      f.cufe_contenedor or "(sin contenedor)", f.cufe, None, ok_cufe, nota))

    if tol.exigir_validacion_dian:
        v = f.validacion
        ch.append(Chequeo("C9", "Validación previa de la DIAN (código 02)", "02",
                          v.codigo or "(ausente)", None, v.validada,
                          f"{v.fecha or ''} {v.hora or ''}".strip()))
    return ch


def conciliada(chequeos: list[Chequeo]) -> bool:
    return all(c.ok for c in chequeos)
