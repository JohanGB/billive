"""Cruce entre movimientos bancarios y facturas: ¿con qué movimiento se pagó cada una?

Sirve para dos cosas: saber cuánto del gasto real tiene factura, y dejar la prueba de
que la factura se pagó por un medio electrónico (requisito de la deducción del 1 %).

Un movimiento es candidato si es una salida, su valor coincide con el total a pagar
(dentro de la tolerancia) y su fecha cae en la ventana configurada. El puntaje combina
monto (0,5), cercanía de fecha (0,3) y parecido del comercio (0,2). La asignación es
uno a uno; si dos candidatos quedan casi empatados, el cruce se declara ambiguo y no
se asigna ninguno (prefiere no afirmar a afirmar mal).
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from .extractos.base import Movimiento
from .facturas.lector import Factura

VACIAS = {"sas", "s", "a", "sa", "ltda", "colombia", "de", "la", "el", "los", "las", "y", "group",
          "grupo", "compania", "cia", "en", "del"}


@dataclass
class ConfigPagos:
    tolerancia: Decimal = Decimal("1.00")
    dias_antes: int = 3
    dias_despues: int = 10
    puntaje_minimo: float = 0.5
    empate: float = 0.05

    @classmethod
    def de_config(cls, cfg: dict | None) -> "ConfigPagos":
        cfg = cfg or {}
        return cls(Decimal(str(cfg.get("tolerancia_pesos", "1.00"))), int(cfg.get("dias_antes", 3)),
                   int(cfg.get("dias_despues", 10)), float(cfg.get("puntaje_minimo", 0.5)))


@dataclass
class Cruce:
    cufe: str
    estado: str                    # conciliada | ambigua | sin_movimiento
    indice_movimiento: int | None  # posición en la lista de movimientos
    puntaje: float
    candidatos: int
    motivo: str


def _fichas(texto: str) -> set[str]:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {x for x in re.findall(r"[a-z0-9]+", t) if len(x) >= 3 and x not in VACIAS}


def parecido_comercio(emisor: str, descripcion: str) -> float:
    """Fracción de palabras significativas del emisor que aparecen en el movimiento."""
    a, b = _fichas(emisor), _fichas(descripcion)
    if not a:
        return 0.0
    return sum(1 for x in a if x in b or any(y.startswith(x) or x.startswith(y) for y in b)) / len(a)


def _puntaje(f: Factura, m: Movimiento, cfg: ConfigPagos) -> float | None:
    es_reembolso = f.tipo == "nota_credito"
    if (m.valor >= 0) != es_reembolso:
        return None
    if abs(abs(m.valor) - f.total_pagar) > cfg.tolerancia:
        return None
    limite = max(f.fecha, f.fecha_vencimiento or f.fecha)
    desde = f.fecha - dt.timedelta(days=cfg.dias_antes)
    hasta = limite + dt.timedelta(days=cfg.dias_despues)
    if not (desde <= m.fecha <= hasta):
        return None
    ventana = max((hasta - desde).days, 1)
    cercania = 1 - min(abs((m.fecha - f.fecha).days), ventana) / ventana
    return round(0.5 + 0.3 * cercania + 0.2 * parecido_comercio(f.emisor_nombre, m.descripcion), 4)


def cruzar(facturas: list[Factura], movimientos: list[Movimiento], cfg: ConfigPagos | None = None) -> list[Cruce]:
    cfg = cfg or ConfigPagos()
    pares = []
    for i, f in enumerate(facturas):
        for j, m in enumerate(movimientos):
            p = _puntaje(f, m, cfg)
            if p is not None and p >= cfg.puntaje_minimo:
                pares.append((p, i, j))
    pares.sort(key=lambda x: (-x[0], x[1], x[2]))
    por_factura: dict[int, list[tuple[float, int]]] = {}
    for p, i, j in pares:
        por_factura.setdefault(i, []).append((p, j))

    usados: set[int] = set()
    asignado: dict[int, tuple[float, int]] = {}
    ambiguas: set[int] = set()
    for p, i, j in pares:
        if i in asignado or i in ambiguas or j in usados:
            continue
        libres = [(q, k) for q, k in por_factura[i] if k not in usados]
        if len(libres) >= 2 and libres[0][0] - libres[1][0] < cfg.empate:
            ambiguas.add(i)
            continue
        asignado[i] = (p, j)
        usados.add(j)

    salida = []
    for i, f in enumerate(facturas):
        n = len(por_factura.get(i, []))
        if i in asignado:
            p, j = asignado[i]
            m = movimientos[j]
            salida.append(Cruce(f.cufe, "conciliada", j, p, n,
                                f"monto igual; fecha {m.fecha.isoformat()} ({(m.fecha - f.fecha).days:+d} días); "
                                f"parecido de comercio {parecido_comercio(f.emisor_nombre, m.descripcion):.0%}"))
        elif i in ambiguas:
            salida.append(Cruce(f.cufe, "ambigua", None, por_factura[i][0][0], n,
                                "varios movimientos igual de probables: revisar a mano"))
        else:
            salida.append(Cruce(f.cufe, "sin_movimiento", None, 0.0, n,
                                "ningún movimiento con ese valor en la ventana de fechas"))
    return salida
