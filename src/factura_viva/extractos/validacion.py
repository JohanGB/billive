"""Validación obligatoria de un extracto: ¿los saldos cuadran?

Cuenta:            saldo inicial + suma de movimientos = saldo final
Tarjeta de crédito: deuda anterior − suma de movimientos = deuda nueva
                    (compra = valor negativo, pago = valor positivo), y cada total que
                    el banco imprime (``resumen['total_<tipo>']``) = suma de los
                    movimientos de ese tipo.

Un extracto que no pasa todos los chequeos NO entra al análisis: va a la cola de
revisión con el motivo.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .base import Extracto


@dataclass
class ChequeoExtracto:
    codigo: str
    nombre: str
    esperado: str
    obtenido: str
    diferencia: Decimal | None
    ok: bool
    nota: str = ""


def _monto(cod, nombre, esperado: Decimal, obtenido: Decimal, tol: Decimal, nota="") -> ChequeoExtracto:
    dif = obtenido - esperado
    return ChequeoExtracto(cod, nombre, f"{esperado:f}", f"{obtenido:f}", dif, abs(dif) <= tol, nota)


def validar(e: Extracto, tolerancia: Decimal = Decimal("1.00")) -> list[ChequeoExtracto]:
    ch: list[ChequeoExtracto] = []
    totales = [k for k in e.resumen if k.startswith("total_")]
    saldos_impresos = e.saldo_inicial is not None and e.saldo_final is not None and e.saldo_inicial_impreso
    # Para probar que la lectura está completa hace falta evidencia impresa por el banco:
    # los dos saldos, o (si el banco no imprime el saldo anterior) el saldo final más al
    # menos dos totales por rubro contra los cuales sumar los movimientos.
    hay_prueba = saldos_impresos or (e.saldo_final is not None and len(totales) >= 2)
    ch.append(ChequeoExtracto(
        "E1", "El extracto trae con qué probar la lectura (saldos o totales impresos)", "sí",
        "sí" if hay_prueba else "no", None, hay_prueba,
        "" if saldos_impresos else
        ("saldo anterior no impreso: se valida con los totales por rubro" if hay_prueba
         else "sin saldos ni totales impresos no hay cómo probar que la lectura está completa")))
    total = sum((m.valor for m in e.movimientos), Decimal(0))
    if e.saldo_inicial is not None and e.saldo_final is not None:
        nota = "" if e.saldo_inicial_impreso else "saldo anterior deducido por la plantilla (ver notas)"
        if e.producto == "tarjeta_credito":
            ch.append(_monto("E2", "Deuda anterior − movimientos = deuda nueva",
                             e.saldo_final, e.saldo_inicial - total, tolerancia, nota))
        else:
            ch.append(_monto("E2", "Saldo inicial + movimientos = saldo final",
                             e.saldo_final, e.saldo_inicial + total, tolerancia, nota))

    previo, malos, revisados = e.saldo_inicial, 0, 0
    for m in e.movimientos:
        if m.saldo is not None and previo is not None:
            revisados += 1
            if abs(previo + m.valor - m.saldo) > tolerancia:
                malos += 1
        if m.saldo is not None:
            previo = m.saldo
    if revisados:
        ch.append(ChequeoExtracto("E3", "Saldo impreso en cada renglón consistente", "0 renglones con diferencia",
                                  f"{malos} de {revisados}", None, malos == 0))

    for clave in sorted(totales):
        tipo = clave[len("total_"):]
        suma = sum((abs(m.valor) for m in e.movimientos if m.tipo == tipo), Decimal(0))
        ch.append(_monto("E4", f"Total impreso de '{tipo}' = suma de sus movimientos",
                         abs(e.resumen[clave]), suma, tolerancia))
    sin_rubro = [m for m in e.movimientos if totales and f"total_{m.tipo}" not in e.resumen
                 and f"deducido_{m.tipo}" not in e.resumen]
    if sin_rubro:
        ch.append(ChequeoExtracto("E4", "Todo movimiento pertenece a un rubro totalizado por el banco",
                                  "0 movimientos sin rubro", f"{len(sin_rubro)}", None, False,
                                  "tipos sin total impreso: " + ", ".join(sorted({m.tipo or '(vacío)' for m in sin_rubro}))))
    for nombre, esperado, obtenido in e.chequeos_propios:
        ch.append(_monto("E5", nombre, esperado, obtenido, tolerancia))
    return ch


def valido(chequeos: list[ChequeoExtracto]) -> bool:
    return bool(chequeos) and all(c.ok for c in chequeos)
