"""Lector genérico de extractos de cuenta: mejor esfuerzo para un banco sin plantilla.

Busca renglones que empiezan con una fecha y terminan con uno o más valores. Si el
banco imprime el saldo después de cada movimiento, el signo se deduce de cómo cambia
el saldo (lo más confiable); si no, de signos explícitos y de palabras clave.

Este lector no decide si el resultado es bueno: eso lo hace ``validacion.validar``.
Si los saldos no cuadran, el archivo va a la cola de revisión y se crea su plantilla.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from ..dinero import a_decimal
from .base import Extracto, ExtractoIlegible, Movimiento, Pagina

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
_MES = "(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)[a-zé]*\\.?"
RE_YMD = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?=\s|$)")
RE_DMY = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4}|\d{2})(?=\s|$)")
RE_DMON = re.compile(rf"^(\d{{1,2}})[ \-/]?(?:de\s+)?{_MES}(?:[ \-/](?:de\s+)?(\d{{4}}|\d{{2}}))?(?=\s|$)", re.I)
RE_DM = re.compile(r"^(\d{1,2})/(\d{1,2})(?=\s|$)")
RE_FECHA_SUELTA = re.compile(
    rf"(\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}}|\d{{1,2}}[-/]\d{{1,2}}[-/]\d{{2,4}}|\d{{1,2}}\s+(?:de\s+)?{_MES}\s+(?:de\s+)?\d{{4}})", re.I)
RE_SALDO_INI = re.compile(r"saldo\s+(anterior|inicial)", re.I)
RE_SALDO_FIN = re.compile(r"(saldo\s+final|nuevo\s+saldo|saldo\s+actual|saldo\s+al\s+corte|saldo\s+a\s+la\s+fecha)", re.I)
RE_ENTRADA = re.compile(
    r"\b(abono|consignaci[oó]n|dep[oó]sito|recibid[ao]|rendimiento|intereses?\s+(ganados|pagados|a\s+favor)"
    r"|n[oó]mina|reverso|devoluci[oó]n|reintegro)\b", re.I)


def _anio(a: str) -> int:
    n = int(a)
    return n + 2000 if n < 100 else n


def _fecha_inicio(linea: str, hasta: dt.date | None) -> tuple[dt.date | None, str]:
    """Si el renglón empieza con fecha, la devuelve junto con el resto del texto."""
    try:
        if m := RE_YMD.match(linea):
            return dt.date(int(m[1]), int(m[2]), int(m[3])), linea[m.end():]
        if m := RE_DMY.match(linea):
            return dt.date(_anio(m[3]), int(m[2]), int(m[1])), linea[m.end():]
        if m := RE_DMON.match(linea):
            mes = MESES[m[2].lower()[:3]]
            if m[3]:
                return dt.date(_anio(m[3]), mes, int(m[1])), linea[m.end():]
            return _sin_anio(int(m[1]), mes, hasta), linea[m.end():]
        if m := RE_DM.match(linea):
            return _sin_anio(int(m[1]), int(m[2]), hasta), linea[m.end():]
    except ValueError:
        return None, linea
    return None, linea


def _sin_anio(dia: int, mes: int, hasta: dt.date | None) -> dt.date | None:
    if hasta is None:
        return None
    anio = hasta.year - 1 if mes > hasta.month else hasta.year
    return dt.date(anio, mes, dia)


def _fecha_suelta(texto: str) -> dt.date | None:
    f, _ = _fecha_inicio(texto.strip(), None)
    return f


def _montos_finales(resto: str, maximo: int = 3) -> tuple[list[tuple[Decimal, str]], str]:
    """Separa los valores del final del renglón; devuelve (valores, descripción)."""
    fichas = re.sub(r"\$\s*", "", resto).split()   # "-$ 1.200" -> "-1.200"
    montos: list[tuple[Decimal, str]] = []
    while fichas and len(montos) < maximo:
        v = a_decimal(fichas[-1])
        if v is None:
            break
        montos.append((v, fichas.pop()))
    montos.reverse()
    return montos, " ".join(fichas)


def _ultimo_monto(linea: str) -> Decimal | None:
    montos, _ = _montos_finales(linea, 1)
    return montos[0][0] if montos else None


def _periodo(paginas: list[Pagina]) -> tuple[dt.date | None, dt.date | None]:
    for pag in paginas:
        for linea in pag.lineas:
            if not re.search(r"per[ií]odo|desde|hasta|corte|\bal\b", linea, re.I):
                continue
            fechas = [f for f in (_fecha_suelta(x[0] if isinstance(x, tuple) else x)
                                  for x in RE_FECHA_SUELTA.findall(linea)) if f]
            if len(fechas) >= 2:
                return min(fechas[:2]), max(fechas[:2])
    return None, None


class LectorGenerico:
    nombre = "generico"
    banco = "desconocido"
    producto = "cuenta"

    def reconoce(self, paginas: list[Pagina]) -> bool:  # siempre disponible como último recurso
        return True

    def interpretar(self, paginas: list[Pagina]) -> Extracto:
        desde, hasta = _periodo(paginas)
        saldo_ini = saldo_fin = None
        crudos = []  # (fecha, descripcion, montos, pagina, renglon, texto)
        for pag in paginas:
            for n, linea in enumerate(pag.lineas, start=1):
                texto = linea.strip()
                if RE_SALDO_INI.search(texto) and saldo_ini is None:
                    saldo_ini = _ultimo_monto(texto)
                    continue
                if RE_SALDO_FIN.search(texto):
                    v = _ultimo_monto(texto)
                    if v is not None:
                        saldo_fin = v
                    continue
                fecha, resto = _fecha_inicio(texto, hasta)
                if fecha is None:
                    continue
                montos, desc = _montos_finales(resto)
                if not montos or not desc:
                    continue
                crudos.append((fecha, desc, montos, pag.numero, n, texto))
        if not crudos:
            raise ExtractoIlegible("no se encontraron renglones con fecha y valor")

        con_saldo = sum(1 for c in crudos if len(c[2]) >= 2) >= 0.6 * len(crudos)
        notas = ["lector genérico: " + ("signo deducido del saldo por renglón" if con_saldo
                                        else "signo deducido de marcas y palabras clave")]
        movimientos: list[Movimiento] = []
        previo = saldo_ini
        for fecha, desc, montos, pagina, renglon, texto in crudos:
            saldo = None
            if con_saldo and len(montos) >= 2:
                saldo = montos[-1][0]
                candidatos = [m for m in montos[:-1] if m[0] != 0] or [montos[-2]]
                monto, ficha = candidatos[-1]
                # Un número suelto antes del valor (p. ej. una referencia) vuelve a la descripción.
                sobrantes = [f for v, f in montos[:-1] if (v, f) != (monto, ficha) and v != 0]
                if sobrantes:
                    desc = " ".join([desc] + sobrantes)
            else:
                monto, ficha = montos[-1]
            valor = None
            if saldo is not None and previo is not None and abs(abs(saldo - previo) - abs(monto)) <= Decimal("0.01"):
                valor = saldo - previo
            if valor is None:
                if monto < 0:
                    valor = monto
                elif ficha.strip().startswith("+") or RE_ENTRADA.search(desc):
                    valor = abs(monto)
                else:
                    valor = -abs(monto)
            if saldo is not None:
                if previo is None:
                    saldo_ini = saldo - valor
                    notas.append("saldo inicial no impreso: se dedujo del primer renglón (no sirve como prueba)")
                previo = saldo
            movimientos.append(Movimiento(fecha=fecha, descripcion=desc, valor=valor, saldo=saldo,
                                          clase="ingreso" if valor > 0 else "salida",
                                          pagina=pagina, renglon=renglon, texto_original=texto))
        if saldo_fin is None and con_saldo and movimientos and movimientos[-1].saldo is not None:
            saldo_fin = movimientos[-1].saldo
            notas.append("saldo final no impreso: se tomó el saldo del último renglón")
        return Extracto(self.banco, self.producto, self.nombre, desde, hasta, saldo_ini, saldo_fin,
                        movimientos, notas=notas)


LECTOR = LectorGenerico()
