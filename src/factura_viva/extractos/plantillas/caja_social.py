"""Banco Caja Social — extracto individual de cuenta de ahorros (Cuentamiga).

Formato validado con un extracto real de octubre de 2024:
- Renglón de resumen con seis valores: saldo anterior, créditos, débitos (negativo),
  intereses, saldo bolsillo y nuevo saldo. Desde 2026 (validado con extractos reales de
  junio a septiembre de 2026) trae cinco: ya no imprime el saldo del bolsillo.
- Movimientos: ``OCT 01 DESCRIPCIÓN DOCUMENTO LUGAR 50,000.00 240,112.65``. El texto no
  distingue la columna débito de la crédito, así que el signo se deduce del cambio del
  saldo impreso en cada renglón; si un renglón no cuadra con su saldo, el extracto se
  rechaza.
- Los renglones sin fecha que siguen a un movimiento son su detalle.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from ..base import Extracto, ExtractoIlegible, Movimiento, Pagina
from ..util import MES_RE, enmascarar, mes, monto, plano, texto

RE_PERIODO = re.compile(r"(\d{1,2}) de (\w+) a (\d{1,2}) de (\w+) de (\d{4})", re.I)
RE_MOV = re.compile(rf"^({MES_RE})\w* (\d{{1,2}}) (.+?) (-?[\d,]+\.\d{{2}}) (-?[\d,]+\.\d{{2}})$", re.I)
RE_RESUMEN = re.compile(r"^(-?[\d,]+\.\d{2})(?: (-?[\d,]+\.\d{2})){4,5}$")
RE_CUENTA = re.compile(r"^[\d*]{8,}$")
INTERNOS = ("traslado desde bolsillo", "enviado hacia cuenta", "recibido de cuenta", "traslado hacia bolsillo")
SALIDAS = (("retiro", "retiro"), ("compra", "compra"), ("transferencia", "transferencia"))


def _clase(descripcion: str, entra: bool) -> str:
    d = plano(descripcion)
    if d.startswith(INTERNOS):
        return "interno"
    if entra:
        return "rendimiento" if "intereses" in d else "ingreso"
    for clave, clase in SALIDAS:
        if clave in d:
            return clase
    return "otro"


class LectorCajaSocial:
    nombre = "caja_social"
    banco = "Banco Caja Social"
    producto = "cuenta"

    def reconoce(self, paginas: list[Pagina]) -> bool:
        t = plano(texto(paginas))
        return ("banco caja social" in t or "bancocajasocial" in t) and "extracto individual" in t

    def interpretar(self, paginas: list[Pagina]) -> Extracto:
        todo = texto(paginas)
        m = RE_PERIODO.search(todo)
        if not m:
            raise ExtractoIlegible("Caja Social: no se encontró el periodo del informe")
        anio = int(m[5])
        desde = dt.date(anio, mes(m[2]), int(m[1]))
        hasta = dt.date(anio, mes(m[4]), int(m[3]))
        if desde > hasta:  # periodo que cruza de diciembre a enero
            desde = desde.replace(year=anio - 1)

        resumen_linea = next((l for p in paginas for l in p.lineas if RE_RESUMEN.match(l.strip())), None)
        if resumen_linea is None:
            raise ExtractoIlegible("Caja Social: no se encontró el renglón de resumen de saldos")
        valores = [monto(x) for x in resumen_linea.split()]
        ant, cred, deb, nuevo = valores[0], valores[1], valores[2], valores[-1]
        bolsillo = valores[4] if len(valores) == 6 else None
        cuenta = next((l.strip() for p in paginas for l in p.lineas if RE_CUENTA.match(l.strip())), "")

        movimientos: list[Movimiento] = []
        previo = ant
        for pag in paginas:
            en_tabla = False
            for n, linea in enumerate(pag.lineas, start=1):
                t = linea.strip()
                if t.startswith("Fecha Transacci"):
                    en_tabla = True
                    continue
                if not en_tabla:
                    continue
                if t.startswith(("Continua en", "Información Importante", "Informacion Importante", "Pag.")):
                    en_tabla = False
                    continue
                mm = RE_MOV.match(t)
                if not mm:
                    if movimientos and t:
                        movimientos[-1].descripcion += " · " + t
                        movimientos[-1].texto_original += "\n" + t
                    continue
                valor_abs, saldo = monto(mm[4]), monto(mm[5])
                delta = saldo - previo
                if abs(abs(delta) - valor_abs) > Decimal("0.01"):
                    raise ExtractoIlegible(
                        f"Caja Social: el renglón {n} de la página {pag.numero} no cuadra con su saldo")
                mes_n = mes(mm[1])
                anio_mov = desde.year if (desde.year != hasta.year and mes_n >= desde.month) else hasta.year
                desc = " ".join(mm[3].split())
                movimientos.append(Movimiento(
                    fecha=dt.date(anio_mov, mes_n, int(mm[2])), descripcion=desc, valor=delta, saldo=saldo,
                    tipo="credito" if delta > 0 else "debito", clase=_clase(desc, delta > 0),
                    pagina=pag.numero, renglon=n, texto_original=t))
                previo = saldo
        return Extracto(
            banco=self.banco, producto=self.producto, lector=self.nombre,
            periodo_desde=desde, periodo_hasta=hasta, saldo_inicial=ant, saldo_final=nuevo,
            movimientos=movimientos, numero_enmascarado=enmascarar(cuenta),
            resumen={"total_credito": cred, "total_debito": abs(deb),
                     **({"saldo_bolsillo": bolsillo} if bolsillo is not None else {})},
            notas=["signo de cada movimiento deducido del saldo impreso en el renglón"],
        )


LECTOR = LectorCajaSocial()
