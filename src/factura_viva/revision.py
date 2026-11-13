"""Revisión cruzada de extractos y pagos: duplicados, huecos, plata propia y pagos sin hallar.

Principio: nada se descarta ni se asume en silencio. Cada decisión automática deja un
**aviso** con su nivel, el motivo y qué puede hacer el autor:

  alerta  algo puede estar mal y cambia las cifras: revisar
  aviso   falta información para concluir (p. ej. un extracto que no se ha descargado)
  info    decisión automática explicable (p. ej. un archivo repetido que se ignoró)

Qué hace:
- ``quitar_duplicados``: el mismo extracto dos veces (archivo idéntico o el mismo periodo
  descargado de nuevo) se cuenta una sola vez; si dos extractos del mismo producto se
  pisan parcialmente, los movimientos repetidos se marcan ``duplicado`` y no suman.
- ``continuidad``: el saldo final de un mes debe ser el inicial del siguiente; avisa de
  meses faltantes.
- ``transferencias_propias``: una salida de un producto propio que entra a otro producto
  propio por el mismo valor (± días) es plata que cambió de bolsillo, no gasto.
- ``pagos_agrupados``: varias facturas del mismo emisor pagadas en un solo movimiento.
- ``diagnosticar_pagos``: para cada factura sin pago hallado, dice por qué puede ser.
"""
from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass
from decimal import Decimal

from .extractos.base import NO_ES_GASTO, Movimiento
from .facturas.lector import Factura
from .pagos import ConfigPagos, Cruce, parecido_comercio

def _pesos(v) -> str:
    return "$" + f"{v:,.0f}".replace(",", ".")


# Para que una salida y una entrada se consideren la misma plata, ninguna puede ser claramente otra cosa.
NO_ES_TRANSFERENCIA_SALIDA = frozenset({"compra", "retiro", "impuesto", "cuota_manejo", "interes"})
NO_ES_TRANSFERENCIA_ENTRADA = frozenset({"rendimiento", "ingreso_tercero"})

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


@dataclass
class Aviso:
    nivel: str          # alerta | aviso | info
    tema: str           # duplicado | continuidad | plata_propia | pago
    asunto: str
    detalle: str
    que_hacer: str = ""
    referencia: str = ""   # archivo o CUFE al que se refiere


def _producto(x) -> tuple[str, str, str]:
    e = x.extracto
    return (e.banco, e.producto, e.numero_enmascarado)


def _nombre(x) -> str:
    e = x.extracto
    tipo = "tarjeta" if e.producto == "tarjeta_credito" else "cuenta"
    return f"{e.banco} {tipo} {e.numero_enmascarado}".strip()


def _huella_mov(m: Movimiento) -> tuple:
    return (m.fecha, m.valor, m.saldo, " ".join(m.descripcion.split())[:60])


# ------------------------------------------------------------------ duplicados

def quitar_duplicados(extractos: list) -> tuple[list, list[Aviso]]:
    """Devuelve los extractos que cuentan (sin repetidos) y los avisos generados."""
    avisos: list[Aviso] = []
    unicos: list = []
    por_hash: dict[str, object] = {}
    for x in extractos:
        if not x.valido:
            unicos.append(x)
            continue
        if x.sha256 in por_hash:
            avisos.append(Aviso("info", "duplicado", "Archivo repetido: se ignoró",
                                f"{x.archivo} es idéntico byte a byte a {por_hash[x.sha256].archivo} (misma huella SHA-256).",
                                "Puede borrar la copia; no afecta las cifras.", x.archivo))
            continue
        e = x.extracto
        gemelo = next((u for u in unicos if u.valido and _producto(u) == _producto(x)
                       and u.extracto.periodo_desde == e.periodo_desde and u.extracto.periodo_hasta == e.periodo_hasta
                       and u.extracto.saldo_final == e.saldo_final
                       and [_huella_mov(m) for m in u.extracto.movimientos] == [_huella_mov(m) for m in e.movimientos]), None)
        if gemelo is not None:
            avisos.append(Aviso("info", "duplicado", "Mismo extracto descargado dos veces: se ignoró",
                                f"{x.archivo} trae el mismo periodo, saldos y movimientos que {gemelo.archivo}, "
                                "aunque el archivo no es idéntico.", "Puede borrar uno de los dos.", x.archivo))
            continue
        por_hash[x.sha256] = x
        unicos.append(x)

    # Solapes parciales del mismo producto: los movimientos repetidos no se suman dos veces.
    validos = [x for x in unicos if x.valido and x.extracto.periodo_desde and x.extracto.periodo_hasta]
    for a, b in itertools.combinations(sorted(validos, key=lambda x: (x.extracto.periodo_desde, x.archivo)), 2):
        if _producto(a) != _producto(b) or b.extracto.periodo_desde > a.extracto.periodo_hasta:
            continue
        vistos = {_huella_mov(m) for m in a.extracto.movimientos if m.clase != "duplicado"}
        repetidos = [m for m in b.extracto.movimientos if _huella_mov(m) in vistos]
        for m in repetidos:
            m.clase = "duplicado"
        avisos.append(Aviso(
            "alerta", "duplicado", f"Dos extractos de {_nombre(a)} cubren fechas en común",
            f"{a.archivo} ({a.extracto.periodo_desde} a {a.extracto.periodo_hasta}) y {b.archivo} "
            f"({b.extracto.periodo_desde} a {b.extracto.periodo_hasta}). {len(repetidos)} movimiento(s) repetidos "
            "se marcaron como 'duplicado' y no se suman.",
            "Revise en movimientos.csv que los marcados sí sean los mismos.", b.archivo))
    return unicos, avisos


# ------------------------------------------------------------------ continuidad

def _meses_entre(desde: dt.date, hasta: dt.date) -> list[str]:
    salida, y, m = [], desde.year, desde.month
    while (y, m) <= (hasta.year, hasta.month):
        salida.append(f"{MESES[m - 1]} {y}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return salida


def continuidad(extractos: list, tolerancia: Decimal = Decimal("1.00")) -> list[Aviso]:
    avisos: list[Aviso] = []
    grupos: dict[tuple, list] = {}
    for x in extractos:
        if x.valido and x.extracto.periodo_desde and x.extracto.periodo_hasta:
            grupos.setdefault(_producto(x), []).append(x)
    for grupo in grupos.values():
        grupo.sort(key=lambda x: x.extracto.periodo_desde)
        for a, b in zip(grupo, grupo[1:]):
            ea, eb = a.extracto, b.extracto
            siguiente = ea.periodo_hasta + dt.timedelta(days=1)
            if eb.periodo_desde == siguiente:
                if (ea.producto == "cuenta" and ea.saldo_final is not None and eb.saldo_inicial is not None
                        and abs(ea.saldo_final - eb.saldo_inicial) > tolerancia):
                    avisos.append(Aviso(
                        "alerta", "continuidad", f"{_nombre(a)}: el saldo no continúa de un mes al siguiente",
                        f"{a.archivo} termina en {ea.saldo_final} y {b.archivo} empieza en {eb.saldo_inicial}.",
                        "Verifique que ambos archivos sean del mismo producto y que no falte ninguno.", b.archivo))
            elif eb.periodo_desde > siguiente:
                faltan = _meses_entre(siguiente, eb.periodo_desde - dt.timedelta(days=1))
                texto = ", ".join(faltan) if len(faltan) <= 4 else f"{faltan[0]} a {faltan[-1]} ({len(faltan)} meses)"
                avisos.append(Aviso(
                    "aviso", "continuidad", f"{_nombre(a)}: faltan extractos ({texto})",
                    f"Hay extracto hasta {ea.periodo_hasta} y luego desde {eb.periodo_desde}. "
                    "Los gastos y pagos de ese intervalo no se ven.",
                    "Descargue esos extractos a data/raw/extractos si quiere cubrir el periodo.", a.archivo))
    return avisos


# ------------------------------------------------------------------ plata propia

def transferencias_propias(extractos: list, dias: int = 1, minimo: Decimal = Decimal("1000"),
                           tolerancia: Decimal = Decimal("0.01")) -> tuple[list[dict], list[Aviso]]:
    """Empareja salidas de un producto con entradas del mismo valor en otro producto propio."""
    excluidas = NO_ES_GASTO | {"duplicado"}
    salidas, entradas = [], []
    for x in extractos:
        if not x.valido:
            continue
        for m in x.extracto.movimientos:
            if m.clase in excluidas or abs(m.valor) < minimo:
                continue
            if m.valor < 0 and m.clase not in NO_ES_TRANSFERENCIA_SALIDA:
                salidas.append((x, m))
            elif m.valor > 0 and m.clase not in NO_ES_TRANSFERENCIA_ENTRADA:
                entradas.append((x, m))
    pares: list[dict] = []
    usadas: set[int] = set()
    for xs, ms in sorted(salidas, key=lambda p: (p[1].fecha, p[0].archivo, p[1].renglon)):
        candidatas = [(abs((me.fecha - ms.fecha).days), i) for i, (xe, me) in enumerate(entradas)
                      if i not in usadas and _producto(xe) != _producto(xs)
                      and abs(me.valor + ms.valor) <= tolerancia and abs((me.fecha - ms.fecha).days) <= dias]
        if not candidatas:
            continue
        _, i = min(candidatas)
        usadas.add(i)
        xe, me = entradas[i]
        ms.clase = me.clase = "interno"
        pares.append({"valor": -ms.valor, "sale_de": _nombre(xs), "sale_fecha": ms.fecha.isoformat(),
                      "sale_descripcion": ms.descripcion, "sale_archivo": xs.archivo, "sale_pagina": ms.pagina,
                      "sale_renglon": ms.renglon, "entra_a": _nombre(xe), "entra_fecha": me.fecha.isoformat(),
                      "entra_descripcion": me.descripcion, "entra_archivo": xe.archivo, "entra_pagina": me.pagina,
                      "entra_renglon": me.renglon})
    avisos = []
    if pares:
        total = sum((p["valor"] for p in pares), Decimal(0))
        avisos.append(Aviso(
            "info", "plata_propia", f"{len(pares)} movimiento(s) entre cuentas propias no se cuentan como gasto",
            f"Suman {_pesos(total)}: salen de un producto propio y entran a otro por el mismo valor "
            f"con máximo {dias} día(s) de diferencia. No se emparejan compras, retiros ni plata recibida de terceros.",
            "Están en transferencias_propias.csv. Si alguno sí fue un pago a un tercero, ajuste "
            "'transferencias_propias' en config/conciliacion.yaml."))
    return pares, avisos


# ------------------------------------------------------------------ pagos

def pagos_agrupados(facturas: list[Factura], cruces: list[Cruce], movimientos: list[Movimiento],
                    cfg: ConfigPagos, max_grupo: int = 4) -> None:
    """Varias facturas del mismo emisor y fechas cercanas pagadas con un solo movimiento."""
    por_cufe = {f.cufe: f for f in facturas}
    usados = {c.indice_movimiento for c in cruces if c.indice_movimiento is not None}
    libres = [c for c in cruces if c.estado == "sin_movimiento" and por_cufe[c.cufe].tipo == "factura"]
    por_emisor: dict[str, list[Cruce]] = {}
    for c in libres:
        por_emisor.setdefault(por_cufe[c.cufe].emisor_nit, []).append(c)
    for grupo in por_emisor.values():
        for n in range(min(len(grupo), max_grupo), 1, -1):
            for combo in itertools.combinations(grupo, n):
                if any(c.estado != "sin_movimiento" for c in combo):
                    continue
                fs = [por_cufe[c.cufe] for c in combo]
                if (max(f.fecha for f in fs) - min(f.fecha for f in fs)).days > cfg.dias_antes:
                    continue
                total = sum((f.total_pagar for f in fs), Decimal(0))
                desde = min(f.fecha for f in fs) - dt.timedelta(days=cfg.dias_antes)
                hasta = max(max(f.fecha, f.fecha_vencimiento or f.fecha) for f in fs) + dt.timedelta(days=cfg.dias_despues)
                hallados = [j for j, m in enumerate(movimientos) if j not in usados and m.valor < 0
                            and abs(-m.valor - total) <= cfg.tolerancia and desde <= m.fecha <= hasta]
                if len(hallados) != 1:
                    continue
                j = hallados[0]
                usados.add(j)
                numeros = ", ".join(f.numero for f in fs)
                for c in combo:
                    c.estado, c.indice_movimiento, c.puntaje = "conciliada", j, 0.75
                    c.motivo = (f"pago agrupado: un solo movimiento del {movimientos[j].fecha.isoformat()} por "
                                f"{total:f} cubre las facturas {numeros}")


def _cubre(x, desde: dt.date, hasta: dt.date) -> bool:
    e = x.extracto
    return bool(e.periodo_desde and e.periodo_hasta and e.periodo_desde <= hasta and e.periodo_hasta >= desde)


def diagnosticar_pagos(facturas: list[Factura], cruces: list[Cruce], movs: list[tuple], extractos: list,
                       cfg: ConfigPagos, medio_electronico, declarados: dict[str, dict] | None = None) -> list[Aviso]:
    """Explica cada factura sin pago hallado. ``movs`` = [(extracto leído, movimiento)]."""
    por_cufe = {f.cufe: f for f in facturas}
    validos = [x for x in extractos if x.valido]
    ids = [f.adquiriente_id for f in facturas]
    titular = max(set(ids), key=ids.count) if ids else ""
    avisos: list[Aviso] = []
    for c in cruces:
        if c.estado == "conciliada":
            continue
        f = por_cufe[c.cufe]
        asunto = f"Factura {f.numero} de {f.emisor_nombre} ({f.fecha.isoformat()}, {_pesos(f.total_pagar)})"
        if declarados and c.cufe in declarados:
            d = declarados[c.cufe]
            c.estado = "declarada_por_autor"
            avisos.append(Aviso("info", "pago", asunto + f": pago declarado por el autor ({d['forma_pago']})",
                                f"El autor anotó en data/etiquetas/pagos_declarados.csv: {d['forma_pago']}"
                                + (f" — {d['nota']}" if d.get("nota") else "") + ". No se busca movimiento bancario"
                                + ("; al ser en efectivo no hay prueba de pago electrónico para la deducción del 1 %."
                                   if d["forma_pago"].lower() == "efectivo" else "."), "", f.cufe))
            continue
        if c.estado == "ambigua":
            avisos.append(Aviso("alerta", "pago", asunto + ": varios movimientos posibles",
                                "Hay más de un movimiento con ese valor y fechas parecidas; no se eligió ninguno.",
                                "Mire pagos.csv y movimientos.csv para decidir cuál es.", f.cufe))
            continue
        desde = f.fecha - dt.timedelta(days=cfg.dias_antes)
        hasta = max(f.fecha, f.fecha_vencimiento or f.fecha) + dt.timedelta(days=cfg.dias_despues)
        con = sorted({_nombre(x) for x in validos if _cubre(x, desde, hasta)})
        sin = sorted({_nombre(x) for x in validos} - set(con))
        pistas: list[str] = []
        nivel, hacer = "aviso", ""

        electronico = medio_electronico(f.medio_pago)
        if electronico is False:
            pistas.append("la factura dice que se pagó en efectivo, así que no se espera un movimiento bancario "
                          "(y sin pago electrónico no aplica la deducción del 1 %)")
            nivel = "info"
        if titular and f.adquiriente_id != titular:
            pistas.append(f"está a nombre de otra persona ({f.adquiriente_nombre.title()}); pudo pagarla desde sus propias cuentas")
            nivel = "info"

        otro_valor = [(x, m) for x, m in movs if m.valor < 0 and desde <= m.fecha <= hasta
                      and parecido_comercio(f.emisor_nombre, m.descripcion) >= 0.5]
        misma_plata = [(x, m) for x, m in movs if m.valor < 0 and abs(-m.valor - f.total_pagar) <= cfg.tolerancia
                       and not (desde <= m.fecha <= hasta) and abs((m.fecha - f.fecha).days) <= 60]
        if otro_valor:
            x, m = otro_valor[0]
            pistas.append(f"hay un movimiento del mismo comercio por otro valor ({m.fecha.isoformat()}, {_pesos(-m.valor)}, "
                          f"{_nombre(x)}): pudo ser un pago acumulado, parcial o con otro cargo")
            nivel, hacer = "alerta", "Confirme si ese movimiento corresponde a esta factura."
        # Un valor muy repetido (5.000, 10.000...) no es pista: coincidiría por azar.
        repeticiones = sum(1 for _, m in movs if abs(-m.valor - f.total_pagar) <= cfg.tolerancia)
        if misma_plata and repeticiones <= 2:
            x, m = min(misma_plata, key=lambda p: abs((p[1].fecha - f.fecha).days))
            pistas.append(f"hay un movimiento por el mismo valor fuera de la ventana de fechas "
                          f"({m.fecha.isoformat()}, {_nombre(x)}: {m.descripcion[:40]})")
            nivel, hacer = "alerta", "Confirme si ese movimiento es el pago; si lo es, amplíe 'dias_despues' en config/conciliacion.yaml."

        if not con:
            pistas.append(f"ningún extracto cargado cubre del {desde.isoformat()} al {hasta.isoformat()}")
            if nivel != "info":
                hacer = hacer or "Descargue los extractos de ese periodo."
        else:
            pistas.append("se buscó en " + ", ".join(con) + " y no hay un movimiento por ese valor")
            if sin:
                pistas.append("sin extracto de ese periodo: " + ", ".join(sin))
                if nivel != "info":
                    hacer = hacer or "Descargue los extractos que faltan de ese periodo; si tampoco aparece, se pagó en efectivo o lo pagó otra persona."
            elif nivel == "aviso" and not otro_valor and not misma_plata:
                hacer = "Todos los productos conocidos tienen extracto de ese periodo: se pagó en efectivo, con un producto no cargado o lo pagó otra persona. Anótelo si lo recuerda."
        avisos.append(Aviso(nivel, "pago", asunto + ": pago no encontrado", "; ".join(pistas) + ".", hacer, f.cufe))
    return avisos
