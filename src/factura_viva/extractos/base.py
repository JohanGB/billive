"""Tipos comunes de los extractos bancarios.

Convención de signos: ``valor`` positivo = entra plata al producto; negativo = sale.
En una tarjeta de crédito "el producto" es la deuda vista por el titular: una compra
es negativa (sale plata) y un pago a la tarjeta es positivo.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class Pagina:
    numero: int                       # 1 = primera página
    lineas: tuple[str, ...]           # texto, un renglón por elemento
    palabras: tuple[dict, ...] = ()   # opcional: palabras con posición (x0, x1, top)


@dataclass
class Movimiento:
    fecha: dt.date
    descripcion: str
    valor: Decimal                    # + entra, − sale
    saldo: Decimal | None = None      # saldo después del movimiento, si el banco lo imprime
    tipo: str = ""                    # rubro con el que el banco totaliza (se valida contra el resumen)
    clase: str = ""                   # significado para el análisis: compra, retiro, interno, pago_tarjeta_propia...
    pagina: int = 0
    renglon: int = 0                  # número de renglón dentro de la página (1 = primero)
    texto_original: str = ""


@dataclass
class Extracto:
    banco: str
    producto: str                     # cuenta | tarjeta_credito
    lector: str                       # qué plantilla lo leyó
    periodo_desde: dt.date | None
    periodo_hasta: dt.date | None
    saldo_inicial: Decimal | None
    saldo_final: Decimal | None
    movimientos: list[Movimiento] = field(default_factory=list)
    numero_enmascarado: str = ""      # solo los últimos 4 dígitos
    resumen: dict[str, Decimal] = field(default_factory=dict)  # totales impresos por el banco
    notas: list[str] = field(default_factory=list)
    saldo_inicial_impreso: bool = True   # False si la plantilla tuvo que deducirlo
    # Chequeos adicionales que arma la plantilla: (nombre, esperado, obtenido)
    chequeos_propios: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)


# Clases de movimiento que NO son gasto: plata que se mueve entre productos propios, y
# movimientos que ya se contaron en otro extracto del mismo producto.
NO_ES_GASTO = frozenset({"interno", "pago_tarjeta_propia", "duplicado"})


def gasto_real(e: "Extracto") -> Decimal:
    """Salidas del periodo sin contar movimientos entre productos propios."""
    return sum((-m.valor for m in e.movimientos if m.valor < 0 and m.clase not in NO_ES_GASTO), Decimal(0))


class Lector(Protocol):
    """Contrato de una plantilla de banco. Agregar un banco = agregar un archivo
    en ``extractos/plantillas/`` que defina ``LECTOR`` con esta interfaz."""

    nombre: str
    banco: str
    producto: str

    def reconoce(self, paginas: list[Pagina]) -> bool: ...
    def interpretar(self, paginas: list[Pagina]) -> Extracto: ...


class ExtractoIlegible(ValueError):
    """La plantilla reconoció el banco pero no pudo interpretar el contenido."""
