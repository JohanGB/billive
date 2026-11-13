"""Lectura de valores en pesos tal como aparecen en extractos colombianos.

Los XML de la DIAN usan punto decimal y se leen directo con ``Decimal``. Los PDF de
los bancos mezclan formatos: ``$1.234.567,89``, ``1,234,567.89``, ``-45.000``,
``45.000-``, ``(45.000)``. Esta función los unifica sin adivinar: si el texto no es
un valor reconocible, devuelve ``None``.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_RE_VALOR = re.compile(r"^[\d.,]+$")
CENTAVO = Decimal("0.01")


def a_decimal(texto: str | None) -> Decimal | None:
    """Convierte un texto de dinero a ``Decimal``; ``None`` si no es un valor."""
    if texto is None:
        return None
    t = texto.strip().replace(" ", " ")
    if not t:
        return None
    negativo = False
    if t.startswith("(") and t.endswith(")"):
        negativo, t = True, t[1:-1]
    t = t.replace("$", "").replace("COP", "").replace(" ", "")
    if t.startswith("+"):
        t = t[1:]
    if t.startswith("-") or t.startswith("−"):
        negativo, t = True, t[1:]
    if t.endswith("-") or t.endswith("−"):
        negativo, t = True, t[:-1]
    if not t or not _RE_VALOR.match(t) or not t[0].isdigit():
        return None

    puntos, comas = t.count("."), t.count(",")
    if puntos and comas:
        # El último separador que aparece es el decimal.
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif puntos or comas:
        sep = "." if puntos else ","
        partes = t.split(sep)
        if len(partes) > 2:
            # Varios separadores iguales: son de miles (1.234.567).
            if any(len(p) != 3 for p in partes[1:]):
                return None
            t = "".join(partes)
        elif len(partes[1]) == 3 and len(partes[0]) <= 3:
            # "1.234" o "45,000": en Colombia es separador de miles.
            t = "".join(partes)
        else:
            t = partes[0] + "." + partes[1]
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return -v if negativo else v


def pesos(v: Decimal | float | int | None) -> str:
    """Formato colombiano para mostrar: $1.234.567 (centavos solo si existen)."""
    if v is None:
        return "—"
    d = Decimal(str(v)).quantize(CENTAVO)
    signo = "-" if d < 0 else ""
    entero, _, dec = f"{abs(d):f}".partition(".")
    grupos = f"{int(entero):,}".replace(",", ".")
    return f"{signo}${grupos}" + (f",{dec}" if dec and dec != "00" else "")
