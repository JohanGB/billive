"""Nu — extracto mensual de la Cuenta Nu (ahorros).

Formato validado con un extracto real de octubre de 2026:
- Página 1: resumen con dinero al inicio y al final del mes, lo que entró, lo que salió,
  impuesto del 4x1000, costos por retiro y rendimiento total.
- Página 2: ``05 oct Pagaste tu tarjeta -$300.000,00``. El renglón "Impuesto del 4x1000"
  no trae fecha: pertenece al movimiento anterior. "Rendimiento total de tu cuenta" es
  un solo renglón mensual y se fecha al cierre del periodo.

Nu no imprime saldo por renglón; la lectura se prueba con los dos saldos y con cuatro
totales por rubro.
"""
from __future__ import annotations

import datetime as dt
import re

from ..base import Extracto, ExtractoIlegible, Movimiento, Pagina
from ..util import MES_RE, buscar_monto, enmascarar, mes, monto, plano, texto

VALOR = r"([+-]?\$[\d.]+,\d{2})"
RE_PERIODO = re.compile(rf"(\d{{1,2}})\s*-\s*(\d{{1,2}}) ({MES_RE})\w* (\d{{4}})", re.I)
RE_MOV = re.compile(rf"^(\d{{1,2}}) ({MES_RE})\w* (.+?) {VALOR}$", re.I)
RE_4X1000 = re.compile(rf"^Impuesto del 4x1000 {VALOR}$", re.I)
RE_REND = re.compile(rf"^Rendimiento total de tu cuenta {VALOR}$", re.I)
RE_COSTO = re.compile(rf"^Costos? por retiro {VALOR}$", re.I)


class LectorNuCuenta:
    nombre = "nu_cuenta"
    banco = "Nu"
    producto = "cuenta"

    def reconoce(self, paginas: list[Pagina]) -> bool:
        t = plano(texto(paginas))
        return "nu financiera" in t and "numero de cuenta" in t and "tu dinero al inicio del mes" in t

    def interpretar(self, paginas: list[Pagina]) -> Extracto:
        todo = texto(paginas)
        m = RE_PERIODO.search(todo)
        if not m:
            raise ExtractoIlegible("Nu cuenta: no se encontró el periodo")
        anio, mes_n = int(m[4]), mes(m[3])
        desde, hasta = dt.date(anio, mes_n, int(m[1])), dt.date(anio, mes_n, int(m[2]))
        inicio = buscar_monto(rf"Tu dinero al inicio del mes {VALOR}", todo, "dinero al inicio del mes")
        final = buscar_monto(rf"Tu dinero a final del mes {VALOR}", todo, "dinero a final del mes")
        resumen = {
            "total_entrada": buscar_monto(rf"Lo que entró a tu cuenta {VALOR}", todo, "lo que entró"),
            "total_salida": abs(buscar_monto(rf"Lo que salió de tu cuenta {VALOR}", todo, "lo que salió")),
            "total_impuesto_4x1000": abs(buscar_monto(rf"^Impuesto del 4x1000 {VALOR}$", paginas_resumen(paginas), "4x1000")),
            "total_costo_retiro": abs(buscar_monto(rf"Costos? por retiro {VALOR}", todo, "costos por retiro")),
            "total_rendimiento": buscar_monto(rf"^Rendimiento total de tu cuenta {VALOR}$", paginas_resumen(paginas), "rendimiento"),
        }
        titular = plano(next((l for l in paginas[0].lineas[2:3]), ""))
        cuenta = re.search(r"^\S+ (\d{6,}) \d", todo, re.M)

        movimientos: list[Movimiento] = []
        en_tabla, ultima = False, None
        for pag in paginas:
            for n, linea in enumerate(pag.lineas, start=1):
                t = linea.strip()
                if t == "Movimientos":
                    en_tabla = True
                    continue
                if not en_tabla:
                    continue
                if t.startswith("¿Tienes preguntas"):
                    en_tabla = False
                    continue
                base = dict(pagina=pag.numero, renglon=n, texto_original=t)
                if mm := RE_MOV.match(t):
                    valor = monto(mm[4])
                    desc = " ".join(mm[3].split())
                    d = plano(desc)
                    if d.startswith("pagaste tu tarjeta"):
                        clase = "pago_tarjeta_propia"
                    elif titular and (d.endswith(titular) and d.startswith(("recibiste de", "enviaste a"))):
                        clase = "interno"
                    else:
                        clase = "ingreso" if valor > 0 else "transferencia"
                    ultima = dt.date(anio, mes(mm[2]), int(mm[1]))
                    movimientos.append(Movimiento(ultima, desc, valor, None,
                                                  "entrada" if valor > 0 else "salida", clase, **base))
                elif mm := RE_4X1000.match(t):
                    if ultima is None:
                        raise ExtractoIlegible("Nu cuenta: 4x1000 sin movimiento anterior")
                    movimientos.append(Movimiento(ultima, "Impuesto del 4x1000", monto(mm[1]), None,
                                                  "impuesto_4x1000", "impuesto", **base))
                elif mm := RE_COSTO.match(t):
                    movimientos.append(Movimiento(ultima or hasta, "Costo por retiro", monto(mm[1]), None,
                                                  "costo_retiro", "comision", **base))
                elif mm := RE_REND.match(t):
                    movimientos.append(Movimiento(hasta, "Rendimiento total de tu cuenta", monto(mm[1]), None,
                                                  "rendimiento", "rendimiento", **base))
        return Extracto(
            banco=self.banco, producto=self.producto, lector=self.nombre,
            periodo_desde=desde, periodo_hasta=hasta, saldo_inicial=inicio, saldo_final=final,
            movimientos=movimientos, numero_enmascarado=enmascarar(cuenta[1]) if cuenta else "",
            resumen=resumen,
            notas=["el rendimiento se registra como un solo movimiento al cierre del mes (Nu lo paga a diario)"],
        )


def paginas_resumen(paginas: list[Pagina]) -> str:
    """Texto anterior a la tabla de movimientos (el resumen de la primera página)."""
    lineas = []
    for p in paginas:
        for l in p.lineas:
            if l.strip() == "Movimientos":
                return "\n".join(lineas)
            lineas.append(l.strip())
    return "\n".join(lineas)


LECTOR = LectorNuCuenta()
