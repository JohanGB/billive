"""Catálogo de lectores de extractos.

Cada archivo en ``extractos/plantillas/`` que defina una variable ``LECTOR`` queda
registrado automáticamente: agregar un banco es agregar un archivo, sin tocar nada más.
Si ninguna plantilla reconoce el PDF, se usa el lector genérico; la validación de
saldos decide después si el resultado es confiable o va a la cola de revisión.
"""
from __future__ import annotations

import importlib
import pkgutil

from . import plantillas
from .base import Lector, Pagina
from .generico import LECTOR as GENERICO


def lectores() -> list[Lector]:
    encontrados: list[Lector] = []
    for info in sorted(pkgutil.iter_modules(plantillas.__path__), key=lambda m: m.name):
        modulo = importlib.import_module(f"{plantillas.__name__}.{info.name}")
        lector = getattr(modulo, "LECTOR", None)
        if lector is not None:
            encontrados.append(lector)
    return encontrados


def elegir(paginas: list[Pagina]) -> Lector:
    for lector in lectores():
        try:
            if lector.reconoce(paginas):
                return lector
        except Exception:  # noqa: BLE001 - una plantilla defectuosa no debe tumbar las demás
            continue
    return GENERICO
