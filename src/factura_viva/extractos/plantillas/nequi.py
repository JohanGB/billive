"""Nequi — extracto mensual del depósito de bajo monto.

Formato validado con extractos reales de junio a octubre de 2026:
- Resumen: saldo anterior, total abonos, total cargos, saldo actual.
- Movimientos: ``17/10/2026 DESCRIPCIÓN $-100,000.00 $82.42`` (valor con signo y saldo
  después del movimiento), impresos **del más reciente al más antiguo**; aquí se
  devuelven en orden cronológico.
- Los intereses pagados están incluidos en "Total abonos".
"""
from __future__ import annotations

import datetime as dt
import re

from ..base import Extracto, ExtractoIlegible, Movimiento, Pagina
from ..util import buscar_monto, enmascarar, monto, plano, texto

RE_PERIODO = re.compile(r"per[ií]odo de: (\d{4})/(\d{2})/(\d{2}) a (\d{4})/(\d{2})/(\d{2})", re.I)
RE_MOV = re.compile(r"^(\d{2})/(\d{2})/(\d{4}) (.+?) \$(-?[\d,]+\.\d{2}) \$(-?[\d,]+\.\d{2})$")
RE_NUMERO = re.compile(r"N[uú]mero de dep[oó]sito de bajo monto: (\d+)", re.I)
VALOR = r"\$(-?[\d,]+\.\d{2})"


def _clase(descripcion: str, entra: bool) -> str:
    d = plano(descripcion)
    if "intereses" in d:
        return "rendimiento"
    if entra:
        # "De NOMBRE": plata que envió otra persona. "RECIBI POR BRE-B DE:" puede venir de una cuenta propia.
        return "ingreso_tercero" if d.startswith("de ") else "ingreso"
    if d.startswith(("envio", "para ")):
        return "transferencia"
    if "retiro" in d:
        return "retiro"
    return "compra"


class LectorNequi:
    nombre = "nequi"
    banco = "Nequi"
    producto = "cuenta"

    def reconoce(self, paginas: list[Pagina]) -> bool:
        t = plano(texto(paginas[:1]))
        return "nequi" in t and "deposito de bajo monto" in t

    def interpretar(self, paginas: list[Pagina]) -> Extracto:
        todo = texto(paginas)
        m = RE_PERIODO.search(todo)
        if not m:
            raise ExtractoIlegible("Nequi: no se encontró el periodo del extracto")
        desde = dt.date(int(m[1]), int(m[2]), int(m[3]))
        hasta = dt.date(int(m[4]), int(m[5]), int(m[6]))
        anterior = buscar_monto(rf"Saldo anterior {VALOR}", todo, "saldo anterior")
        actual = buscar_monto(rf"Saldo actual {VALOR}", todo, "saldo actual")
        abonos = buscar_monto(rf"Total abonos {VALOR}", todo, "total abonos")
        cargos = buscar_monto(rf"Total cargos {VALOR}", todo, "total cargos")
        numero = RE_NUMERO.search(todo)

        movimientos: list[Movimiento] = []
        for pag in paginas:
            for n, linea in enumerate(pag.lineas, start=1):
                mm = RE_MOV.match(linea.strip())
                if not mm:
                    continue
                valor = monto(mm[5])
                desc = " ".join(mm[4].split())
                movimientos.append(Movimiento(
                    fecha=dt.date(int(mm[3]), int(mm[2]), int(mm[1])), descripcion=desc, valor=valor,
                    saldo=monto(mm[6]), tipo="abono" if valor > 0 else "cargo",
                    clase=_clase(desc, valor > 0), pagina=pag.numero, renglon=n, texto_original=linea.strip()))
        movimientos.reverse()   # el PDF va del más reciente al más antiguo
        return Extracto(
            banco=self.banco, producto=self.producto, lector=self.nombre,
            periodo_desde=desde, periodo_hasta=hasta, saldo_inicial=anterior, saldo_final=actual,
            movimientos=movimientos, numero_enmascarado=enmascarar(numero[1]) if numero else "",
            resumen={"total_abono": abonos, "total_cargo": cargos},
            notas=["movimientos reordenados cronológicamente (Nequi los imprime al revés)"],
        )


LECTOR = LectorNequi()
