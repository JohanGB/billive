"""Banco AV Villas — extracto de tarjeta de crédito.

Formato validado con un extracto real de octubre de 2026 **sin movimientos**: se lee el
bloque "RESUMEN SALDO TOTAL" y se prueba su ecuación:

    saldo anterior + compras + avances + intereses + mora + honorarios + gastos legales
    + cargos no diferidos − pagos y notas crédito = saldo total − saldo a su favor

Límite declarado: todavía no hay un extracto real con movimientos para validar el
formato de las filas (el PDF además superpone texto girado sobre el encabezado). Si el
extracto trae filas de movimientos, esta plantilla lo rechaza y el archivo va a la cola
de revisión, en lugar de adivinar.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from ..base import Extracto, ExtractoIlegible, Pagina
from ..util import buscar_monto, enmascarar, plano, texto

V = r"\$\s?(-?[\d,]+\.\d{2})"


def _fecha_entrelazada(todo: str, etiqueta: str) -> dt.date | None:
    """El PDF mezcla la plantilla 'DD MM AAAA' con los dígitos: 'D2D6 MM10 A2A0A2A6'."""
    m = re.search(rf"{etiqueta}\s+(\S+)\s+(\S+)\s+(\S+)", todo)
    if not m:
        return None
    d, mm, a = (re.sub(r"\D", "", x) for x in m.groups())
    try:
        return dt.date(int(a), int(mm), int(d))
    except ValueError:
        return None


class LectorAvVillas:
    nombre = "av_villas"
    banco = "Banco AV Villas"
    producto = "tarjeta_credito"

    def reconoce(self, paginas: list[Pagina]) -> bool:
        t = plano(texto(paginas))
        return "avvillas" in t and "extracto tarjeta de credito" in t

    def interpretar(self, paginas: list[Pagina]) -> Extracto:
        todo = texto(paginas)

        def r(patron: str, que: str, ultimo: bool = False) -> Decimal:
            return buscar_monto(patron + r"\s*" + V, todo, que, ultimo)

        anterior = r(r"SALDO ANTERIOR", "saldo anterior")
        compras = r(r"TOTAL COMPRAS MES", "total compras mes")
        avances = r(r"\(\+\) AVANCES", "avances")
        intereses = r(r"INTERESES CORRIENTES", "intereses corrientes", ultimo=True)
        mora = r(r"INTERESES MORA \([^)]*\)", "intereses de mora")
        honorarios = r(r"HONORARIOS", "honorarios", ultimo=True)
        gastos = r(r"GASTOS LEGALES", "gastos legales")
        cargos = r(r"CARGOS NO DIFERIDOS", "cargos no diferidos", ultimo=True)
        pagos = r(r"PAGOS Y NOTAS CREDITO", "pagos y notas crédito")
        saldo_total = r(r"(?<!CUPO TOTAL )SALDO TOTAL", "saldo total", ultimo=True)
        m = re.search(r"SALDO A SU FAVOR[^\n]*\n[^\n$]*" + V, todo)
        a_favor = Decimal(m[1].replace(",", "")) if m else Decimal(0)

        filas = self._filas_de_movimientos(paginas)
        if filas:
            raise ExtractoIlegible(
                f"AV Villas: el extracto trae {len(filas)} renglón(es) de movimientos y la plantilla aún no "
                "está validada con un extracto real con movimientos")

        corte = _fecha_entrelazada(todo, "FECHA FACTURACIÓN")
        numero = re.search(r"TARJETA DE CR[ÉE]DITO N°\s*:\s*(\d{12,19})", todo)
        neto = saldo_total - a_favor
        return Extracto(
            banco=self.banco, producto=self.producto, lector=self.nombre,
            periodo_desde=None, periodo_hasta=corte, saldo_inicial=anterior, saldo_final=neto,
            movimientos=[], numero_enmascarado=enmascarar(numero[1]) if numero else "",
            resumen={"total_compra": compras, "total_avance": avances, "total_interes": intereses,
                     "total_interes_mora": mora, "total_honorarios": honorarios,
                     "total_gastos_legales": gastos, "total_cargo_no_diferido": cargos, "total_pago": pagos,
                     "saldo_a_favor": a_favor},
            notas=["extracto sin movimientos; un saldo negativo significa saldo a favor del titular"],
            chequeos_propios=[(
                "Resumen impreso: saldo anterior + cargos − pagos = saldo total − saldo a favor",
                neto, anterior + compras + avances + intereses + mora + honorarios + gastos + cargos - pagos)],
        )

    @staticmethod
    def _filas_de_movimientos(paginas: list[Pagina]) -> list[str]:
        filas, dentro = [], False
        for pag in paginas:
            for linea in pag.lineas:
                t = linea.strip()
                if "DETALLE" in t and "VALOR COMPRA" in t:
                    dentro = True
                    continue
                if dentro and "SI EL VALOR DE SU PAGO ES" in t:
                    dentro = False
                    continue
                if dentro and t:
                    filas.append(t)
        return filas


LECTOR = LectorAvVillas()
