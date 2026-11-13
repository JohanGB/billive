"""Utilidades compartidas por las plantillas de extractos."""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from ..dinero import a_decimal
from .base import ExtractoIlegible, Pagina

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
MES_RE = "ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic"


def mes(nombre: str) -> int:
    return MESES[plano(nombre)[:3]]


def plano(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def texto(paginas: list[Pagina]) -> str:
    return "\n".join(l for p in paginas for l in p.lineas)


def enmascarar(numero: str) -> str:
    """Deja solo los últimos 4 dígitos: nunca se guarda un número completo de producto."""
    digitos = re.sub(r"\D", "", numero)
    return "····" + digitos[-4:] if len(digitos) >= 4 else ""


def monto(t: str, que: str = "valor") -> Decimal:
    v = a_decimal(t)
    if v is None:
        raise ExtractoIlegible(f"{que} ilegible: {t!r}")
    return v


def buscar_monto(patron: str, donde: str, que: str, ultimo: bool = False) -> Decimal:
    """Valor capturado por el grupo 1 de ``patron``; falla con un mensaje claro si no está."""
    hallados = re.findall(patron, donde, flags=re.I | re.M)
    if not hallados:
        raise ExtractoIlegible(f"no se encontró '{que}' en el extracto")
    return monto(hallados[-1] if ultimo else hallados[0], que)


def enmascarar_texto(t: str) -> str:
    """Oculta números largos (documentos, cuentas, referencias) dentro de un texto libre."""
    return re.sub(r"\d{9,}", lambda m: "····" + m[0][-4:], t)
