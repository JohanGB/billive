"""Alertas de precio: el mismo producto del mismo emisor subió más de un umbral.

Fase 1 (sin ML): compara el precio unitario de la última compra contra la mediana de las
compras anteriores del mismo producto (mismo emisor y misma descripción normalizada).
Necesita al menos dos compras en fechas distintas; con pocos datos simplemente no hay
alertas, y eso se informa tal cual.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .facturas.lector import Factura


@dataclass
class AlertaPrecio:
    emisor: str
    producto: str
    precio_anterior: Decimal      # mediana de compras previas
    precio_actual: Decimal
    cambio: float                 # 0.15 = subió 15 %
    fecha_actual: str
    compras_previas: int
    cufe_actual: str


def normalizar(descripcion: str) -> str:
    t = unicodedata.normalize("NFKD", descripcion.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())


def alzas(facturas: list[Factura], alza_minima: float = 0.10) -> list[AlertaPrecio]:
    serie: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for f in facturas:
        if f.tipo != "factura":
            continue
        for l in f.lineas:
            if l.gratis or l.precio_unitario <= 0:
                continue
            serie[(f.emisor_nit, normalizar(l.descripcion))].append((f.fecha, l.precio_unitario, f, l))
    salida = []
    for (_, producto), compras in serie.items():
        compras.sort(key=lambda c: c[0])
        ultima_fecha = compras[-1][0]
        previas = [c for c in compras if c[0] < ultima_fecha]
        if not previas:
            continue
        base = Decimal(str(statistics.median([c[1] for c in previas])))
        actual = max(c[1] for c in compras if c[0] == ultima_fecha)
        cambio = float((actual - base) / base)
        if cambio >= alza_minima:
            f = compras[-1][2]
            salida.append(AlertaPrecio(f.emisor_nombre, producto, base, actual, cambio,
                                       ultima_fecha.isoformat(), len(previas), f.cufe))
    return sorted(salida, key=lambda a: -a.cambio)
