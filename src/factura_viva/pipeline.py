"""Proceso completo: de ``data/raw`` a tablas, validaciones y tablero.

Todo lo que se escribe va a ``data/procesado`` (privado, fuera de git). Cada fila de
cada tabla lleva el archivo crudo del que salió, su SHA-256 y la versión del código.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable

import yaml

from . import revision, __version__, alertas, categorias, manifiesto, pagos, renta
from .extractos import catalogo, validacion
from .extractos.base import NO_ES_GASTO, Extracto, ExtractoIlegible, Movimiento, gasto_real
from .extractos.util import enmascarar, enmascarar_texto
from .extractos.pdf import ClaveIncorrecta, ClaveRequerida, leer_paginas
from .facturas import conciliacion
from .facturas.lector import Factura, leer_carpeta, sha256_archivo

PedirClave = Callable[[str], "str | None"]


@dataclass
class ExtractoLeido:
    archivo: str
    sha256: str
    extracto: Extracto
    chequeos: list[validacion.ChequeoExtracto]

    @property
    def valido(self) -> bool:
        return validacion.valido(self.chequeos)


@dataclass
class Resultado:
    facturas: list[Factura] = field(default_factory=list)
    chequeos: dict[str, list[conciliacion.Chequeo]] = field(default_factory=dict)
    duplicadas: list[tuple[str, str]] = field(default_factory=list)
    errores_facturas: list[tuple[str, str]] = field(default_factory=list)
    extractos: list[ExtractoLeido] = field(default_factory=list)
    cola_revision: list[dict] = field(default_factory=list)
    avisos: list[revision.Aviso] = field(default_factory=list)
    transferencias: list[dict] = field(default_factory=list)
    extractos_repetidos: int = 0
    cruces: list[pagos.Cruce] = field(default_factory=list)
    movimientos_cruce: list[tuple[ExtractoLeido, Movimiento]] = field(default_factory=list)
    lineas: list[dict] = field(default_factory=list)
    renta_facturas: list[renta.EvaluacionRenta] = field(default_factory=list)
    renta_resumen: list[renta.ResumenRenta] = field(default_factory=list)
    alertas: list[alertas.AlertaPrecio] = field(default_factory=list)
    metricas_categorias: dict | None = None
    resumen: dict = field(default_factory=dict)
    cfg_renta: dict = field(default_factory=dict)


def version_codigo(raiz: Path) -> str:
    """Versión del paquete y, si el proyecto está en git, el commit."""
    try:
        commit = subprocess.run(["git", "-C", str(raiz), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=5)
        if commit.returncode == 0 and commit.stdout.strip():
            return f"{__version__}+{commit.stdout.strip()}"
    except (OSError, subprocess.SubprocessError):
        pass
    return __version__


def _yaml(ruta: Path) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _csv(ruta: Path, filas: list[dict], columnas: list[str]) -> None:
    # utf-8-sig: Excel en Windows muestra bien las tildes.
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        w.writeheader()
        for fila in filas:
            w.writerow({k: _celda(v) for k, v in fila.items()})


def _celda(v):
    if v is None:
        return ""
    if isinstance(v, Decimal):   # 5000.0000 -> 5000 ; 3529.4118 se conserva
        return format(v.normalize(), "f")
    return v


def _q(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01")))


def gasto_por_linea(f: Factura) -> list[Decimal]:
    """Reparte el total a pagar entre las líneas (valor + impuestos de la línea).

    Si las líneas no traen impuestos pero el encabezado sí, se reparten en proporción al
    valor. El residuo (redondeos, cargos o descuentos globales) va a la línea de mayor
    valor, de modo que la suma de las líneas sea exactamente el total a pagar.
    """
    if not f.lineas:
        return []
    bases = [l.valor for l in f.lineas]
    imp = [sum((i.valor for i in l.impuestos), Decimal(0)) for l in f.lineas]
    if not any(imp) and f.total_impuestos and sum(bases):
        imp = [f.total_impuestos * b / sum(bases) for b in bases]
    gasto = [(b + i).quantize(Decimal("0.01")) for b, i in zip(bases, imp)]
    mayor = max(range(len(gasto)), key=lambda k: gasto[k])
    gasto[mayor] += f.total_pagar - sum(gasto)
    return [f.signo * g for g in gasto]


def _leer_extractos(carpeta: Path, raiz_raw: Path, pedir_clave: PedirClave | None, tol: Decimal,
                    res: Resultado, decir: Callable[[str], None]) -> None:
    if not carpeta.exists():
        return
    recordada: str | None = None   # solo en memoria: facilita "la misma clave para todos"
    for ruta in sorted(p for p in carpeta.iterdir() if p.suffix.lower() == ".pdf"):
        rel = ruta.relative_to(raiz_raw).as_posix()
        huella = sha256_archivo(ruta)

        def a_cola(motivo: str, accion: str) -> None:
            res.cola_revision.append({"archivo": rel, "sha256": huella, "motivo": motivo, "que_hacer": accion})
            decir(f"  [revisión] {ruta.name}: {motivo}")

        paginas = None
        try:
            paginas = leer_paginas(ruta)
        except ClaveRequerida:
            candidatas = [recordada] if recordada else []
            for intento in range(3):
                clave = candidatas.pop(0) if candidatas else (pedir_clave(ruta.name) if pedir_clave else None)
                if not clave:
                    break
                try:
                    paginas = leer_paginas(ruta, clave)
                    recordada = clave
                    break
                except ClaveIncorrecta:
                    decir(f"  clave incorrecta para {ruta.name}")
            if paginas is None:
                a_cola("PDF protegido y no se entregó una clave válida",
                       "ejecutar de nuevo y escribir la clave cuando se pida")
                continue
        except Exception as e:  # noqa: BLE001
            a_cola(f"no se pudo abrir el PDF ({type(e).__name__})", "verificar que el archivo no esté dañado")
            continue

        lector = catalogo.elegir(paginas)
        try:
            extracto = lector.interpretar(paginas)
        except ExtractoIlegible as e:
            a_cola(str(e), "crear o ajustar la plantilla del banco en extractos/plantillas/")
            continue
        chequeos = validacion.validar(extracto, tol)
        leido = ExtractoLeido(rel, huella, extracto, chequeos)
        res.extractos.append(leido)
        if leido.valido:
            decir(f"  [ok] {ruta.name}: {extracto.banco} ({extracto.producto}), "
                  f"{len(extracto.movimientos)} movimientos, saldos cuadran")
        else:
            fallas = "; ".join(c.nombre for c in chequeos if not c.ok)
            a_cola(f"los saldos no cuadran con el lector '{lector.nombre}': {fallas}",
                   "crear la plantilla de este banco (un archivo en extractos/plantillas/)")


def procesar(raiz: Path, pedir_clave: PedirClave | None = None,
             decir: Callable[[str], None] = print, ahora: dt.datetime | None = None) -> Resultado:
    raiz = Path(raiz)
    raw, salida, config = raiz / "data" / "raw", raiz / "data" / "procesado", raiz / "config"
    salida.mkdir(parents=True, exist_ok=True)
    ahora = ahora or dt.datetime.now().astimezone()
    cfg = _yaml(config / "conciliacion.yaml")
    cfg_renta = _yaml(config / "renta.yaml")
    reglas = categorias.cargar_reglas(_yaml(config / "categorias.yaml"))
    version = version_codigo(raiz)
    res = Resultado(cfg_renta=cfg_renta)

    decir("1/6 Manifiesto de datos crudos")
    estado = manifiesto.actualizar(raw, raiz / "data" / "manifest.json", ahora)
    if estado.alterados:
        raise RuntimeError("data/raw es inmutable y estos archivos cambiaron de contenido: "
                           + ", ".join(estado.alterados))
    decir(f"  {len(estado.archivos)} archivos registrados ({len(estado.nuevos)} nuevos)")

    decir("2/6 Facturas electrónicas")
    lectura = leer_carpeta(raw / "facturas", raw) if (raw / "facturas").exists() else None
    if lectura:
        res.facturas, res.duplicadas, res.errores_facturas = lectura.facturas, lectura.duplicadas, lectura.errores
    tol_f = conciliacion.Tolerancias.de_config(cfg.get("facturas"))
    for f in res.facturas:
        res.chequeos[f.cufe] = conciliacion.conciliar(f, tol_f)
    n_ok = sum(1 for f in res.facturas if conciliacion.conciliada(res.chequeos[f.cufe]))
    decir(f"  {len(res.facturas)} facturas únicas, {n_ok} conciliadas, "
          f"{len(res.duplicadas)} duplicadas, {len(res.errores_facturas)} ilegibles")

    decir("3/6 Extractos bancarios")
    tol_e = Decimal(str(cfg.get("extractos", {}).get("tolerancia_pesos", "1.00")))
    _leer_extractos(raw / "extractos", raw, pedir_clave, tol_e, res, decir)

    # Duplicados, continuidad entre meses y plata que solo cambió de cuenta propia.
    antes = len(res.extractos)
    res.extractos, avisos = revision.quitar_duplicados(res.extractos)
    res.extractos_repetidos = antes - len(res.extractos)
    res.avisos += avisos + revision.continuidad(res.extractos, tol_e)
    cfg_tp = cfg.get("transferencias_propias") or {}
    if cfg_tp.get("activo", True):
        res.transferencias, avisos = revision.transferencias_propias(
            res.extractos, int(cfg_tp.get("dias", 1)), Decimal(str(cfg_tp.get("valor_minimo", "1000"))))
        res.avisos += avisos
    for a in res.avisos:
        decir(f"  [{a.nivel}] {a.asunto}")

    decir("4/6 Cruce de pagos, categorías, renta y alertas")
    validos = [x for x in res.extractos if x.valido]
    # Destinos que el autor declara como cuentas propias en otros bancos: no son gasto.
    propios = [re.compile(p, re.I) for p in (cfg.get("cuentas_propias") or [])]
    for x in validos:
        for m in x.extracto.movimientos:
            if m.valor < 0 and m.clase not in NO_ES_GASTO and any(p.search(m.descripcion) for p in propios):
                m.clase = "interno"
    res.movimientos_cruce = [(x, m) for x in validos for m in x.extracto.movimientos
                             if m.clase not in NO_ES_GASTO and m.clase != "pago_recibido"]
    res.cruces = pagos.cruzar(res.facturas, [m for _, m in res.movimientos_cruce],
                              pagos.ConfigPagos.de_config(cfg.get("pagos")))
    cfg_pagos = pagos.ConfigPagos.de_config(cfg.get("pagos"))
    revision.pagos_agrupados(res.facturas, res.cruces, [m for _, m in res.movimientos_cruce], cfg_pagos)
    avisos_pago = revision.diagnosticar_pagos(
        res.facturas, res.cruces, res.movimientos_cruce, res.extractos, cfg_pagos,
        lambda codigo: renta._medio(cfg_renta, codigo)["electronico"], _pagos_declarados(raiz))
    res.avisos += avisos_pago
    por_ref = {a.referencia: a for a in avisos_pago}
    for c in res.cruces:
        if c.cufe in por_ref:
            c.motivo = por_ref[c.cufe].detalle
    cruce_por_cufe = {c.cufe: c for c in res.cruces}

    etiquetas = _leer_etiquetas(raiz)
    for f in res.facturas:
        gastos = gasto_por_linea(f)
        clasif = [categorias.clasificar(reglas, l.descripcion, f.emisor_nombre, f.emisor_nit, f.emisor_ciiu)
                  for l in f.lineas]
        # Una línea que ninguna regla reconoce (p. ej. la tarifa de servicio de un tiquete) hereda la
        # categoría de la línea de mayor gasto de su misma factura. Queda marcada como heredada.
        con_cat = [(abs(g), c[0]) for g, c in zip(gastos, clasif) if c[0] != categorias.SIN_CATEGORIA]
        if con_cat:
            dominante = max(con_cat)[1]
            clasif = [c if c[0] != categorias.SIN_CATEGORIA else (dominante, "heredada#factura") for c in clasif]
        for l, gasto, (cat, regla) in zip(f.lineas, gastos, clasif):
            cat_regla = cat
            if (f.cufe, l.numero) in etiquetas:   # la etiqueta del autor manda; la regla queda para medirla
                cat, regla = etiquetas[(f.cufe, l.numero)], "etiqueta#autor"
            res.lineas.append({
                "cufe": f.cufe, "tipo": f.tipo, "numero": f.numero, "fecha": f.fecha.isoformat(),
                "emisor_nit": f.emisor_nit, "emisor_nombre": f.emisor_nombre,
                "adquiriente_id": enmascarar(f.adquiriente_id), "adquiriente_nombre": f.adquiriente_nombre,
                "linea": l.numero, "descripcion": l.descripcion, "cantidad": l.cantidad, "unidad": l.unidad,
                "precio_unitario": l.precio_unitario, "valor_sin_impuestos": f.signo * l.valor,
                "gasto": gasto, "categoria": cat, "regla_categoria": regla, "categoria_regla": cat_regla,
                "fuente_archivo": f.fuente.archivo, "fuente_sha256": f.fuente.sha256,
                "fuente_xml": f.fuente.miembro_xml, "version_codigo": version,
            })

    res.renta_facturas = [
        renta.evaluar(f, cfg_renta, cruce_por_cufe[f.cufe].estado == "conciliada") for f in res.facturas]
    res.renta_resumen = renta.resumir(res.facturas, res.renta_facturas, cfg_renta)
    res.alertas = alertas.alzas(res.facturas, float(cfg.get("alertas_precio", {}).get("alza_minima", 0.10)))
    res.metricas_categorias = _evaluar_categorias(raiz, res)

    decir("5/6 Tablas en data/procesado")
    _escribir_tablas(res, salida, version, cfg_renta)
    res.resumen = _resumen(res, ahora, version)
    minimo = int((cfg.get("fase2") or {}).get("facturas_minimas", 30))   # decisión del autor, 11-nov-2026
    res.resumen["fase2"] = {"facturas": len(res.facturas), "minimo": minimo, "lista": len(res.facturas) >= minimo}
    (salida / "resumen.json").write_text(json.dumps(res.resumen, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")

    decir("6/6 Tablero")
    from .tablero import escribir_tablero
    escribir_tablero(res, salida / "tablero.html")
    decir(f"  listo: {salida / 'tablero.html'}")
    return res


def _pagos_declarados(raiz: Path) -> dict[str, dict]:
    """Pagos que el autor declara a mano (efectivo, pagó otra persona...): cufe,forma_pago,nota."""
    ruta = raiz / "data" / "etiquetas" / "pagos_declarados.csv"
    if not ruta.exists():
        return {}
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        return {r["cufe"].strip().lower(): r for r in csv.DictReader(f) if r.get("cufe", "").strip()}


def _leer_etiquetas(raiz: Path) -> dict[tuple[str, str], str]:
    """Etiquetas del autor (data/etiquetas/etiquetas.csv, opcionales): mandan sobre las reglas."""
    ruta = raiz / "data" / "etiquetas" / "etiquetas.csv"
    if not ruta.exists():
        return {}
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        return {(r["cufe"], r["linea"]): r["categoria"].strip() for r in csv.DictReader(f)
                if r.get("categoria", "").strip()}


def _evaluar_categorias(raiz: Path, res: Resultado) -> dict | None:
    """F1 por categoría: lo que dijeron las reglas (no la etiqueta) contra la etiqueta del autor."""
    etiquetas = _leer_etiquetas(raiz)
    pares = [(etiquetas[(l["cufe"], l["linea"])], l["categoria_regla"]) for l in res.lineas
             if (l["cufe"], l["linea"]) in etiquetas]
    if not pares:
        return None
    return {"n": len(pares), "por_categoria": categorias.evaluar([a for a, _ in pares], [b for _, b in pares])}


def _escribir_tablas(res: Resultado, salida: Path, version: str, cfg_renta: dict) -> None:
    cruce = {c.cufe: c for c in res.cruces}
    eval_renta = {e.cufe: e for e in res.renta_facturas}
    filas = []
    for f in res.facturas:
        c, e = cruce[f.cufe], eval_renta[f.cufe]
        filas.append({
            "cufe": f.cufe, "tipo": f.tipo, "numero": f.numero, "fecha": f.fecha.isoformat(), "hora": f.hora,
            "emisor_nit": f.emisor_nit, "emisor_nombre": f.emisor_nombre, "emisor_ciiu": f.emisor_ciiu,
            "adquiriente_id": enmascarar(f.adquiriente_id), "adquiriente_nombre": f.adquiriente_nombre,
            "medio_pago_codigo": f.medio_pago, "medio_pago": renta.nombre_medio_pago(cfg_renta, f.medio_pago),
            "fecha_vencimiento": f.fecha_vencimiento.isoformat() if f.fecha_vencimiento else "",
            "valor_bruto": f.valor_bruto, "impuestos": f.total_impuestos, "descuentos": f.descuentos,
            "cargos": f.cargos, "anticipos": f.anticipos, "total_pagar": f.total_pagar, "lineas": len(f.lineas),
            "conciliada": "sí" if conciliacion.conciliada(res.chequeos[f.cufe]) else "no",
            "validacion_dian": f.validacion.codigo, "validacion_fecha": f.validacion.fecha,
            "pago_estado": c.estado, "pago_detalle": c.motivo,
            "renta_estado": e.estado, "renta_motivo": e.motivo, "cufe_referencia": f.cufe_referencia,
            "fuente_archivo": f.fuente.archivo, "fuente_sha256": f.fuente.sha256,
            "fuente_xml": f.fuente.miembro_xml, "version_codigo": version,
        })
    _csv(salida / "facturas.csv", filas, list(filas[0]) if filas else ["cufe"])
    _csv(salida / "lineas.csv", res.lineas, list(res.lineas[0]) if res.lineas else ["cufe"])

    filas = [{"cufe": f.cufe, "numero": f.numero, "codigo": c.codigo, "chequeo": c.nombre,
              "esperado": c.esperado, "obtenido": c.obtenido, "diferencia": c.diferencia,
              "ok": "sí" if c.ok else "no", "nota": c.nota, "fuente_archivo": f.fuente.archivo}
             for f in res.facturas for c in res.chequeos[f.cufe]]
    _csv(salida / "chequeos_facturas.csv", filas,
         ["cufe", "numero", "codigo", "chequeo", "esperado", "obtenido", "diferencia", "ok", "nota", "fuente_archivo"])

    filas, filas_ch, filas_mov = [], [], []
    for x in res.extractos:
        e = x.extracto
        filas.append({
            "archivo": x.archivo, "sha256": x.sha256, "banco": e.banco, "producto": e.producto, "lector": e.lector,
            "numero": e.numero_enmascarado, "periodo_desde": e.periodo_desde, "periodo_hasta": e.periodo_hasta,
            "saldo_inicial": e.saldo_inicial, "saldo_final": e.saldo_final, "movimientos": len(e.movimientos),
            "gasto_real": gasto_real(e), "valido": "sí" if x.valido else "no", "notas": " | ".join(e.notas),
            "version_codigo": version})
        for c in x.chequeos:
            filas_ch.append({"archivo": x.archivo, "codigo": c.codigo, "chequeo": c.nombre, "esperado": c.esperado,
                             "obtenido": c.obtenido, "diferencia": c.diferencia, "ok": "sí" if c.ok else "no",
                             "nota": c.nota})
        if x.valido:
            for m in e.movimientos:
                filas_mov.append({
                    "banco": e.banco, "producto": e.producto, "fecha": m.fecha.isoformat(),
                    "descripcion": enmascarar_texto(m.descripcion), "valor": m.valor, "saldo": m.saldo, "tipo": m.tipo,
                    "clase": m.clase, "cuenta_como_gasto": "sí" if (m.valor < 0 and m.clase not in NO_ES_GASTO) else "no",
                    "fuente_archivo": x.archivo, "fuente_sha256": x.sha256, "fuente_pagina": m.pagina,
                    "fuente_renglon": m.renglon, "version_codigo": version})
    _csv(salida / "extractos.csv", filas, list(filas[0]) if filas else ["archivo"])
    _csv(salida / "chequeos_extractos.csv", filas_ch,
         ["archivo", "codigo", "chequeo", "esperado", "obtenido", "diferencia", "ok", "nota"])
    _csv(salida / "movimientos.csv", filas_mov, list(filas_mov[0]) if filas_mov else ["banco"])
    _csv(salida / "cola_revision.csv", res.cola_revision, ["archivo", "sha256", "motivo", "que_hacer"])
    _csv(salida / "avisos.csv", [vars(a) for a in res.avisos],
         ["nivel", "tema", "asunto", "detalle", "que_hacer", "referencia"])
    _csv(salida / "transferencias_propias.csv",
         [t | {"sale_descripcion": enmascarar_texto(t["sale_descripcion"]),
               "entra_descripcion": enmascarar_texto(t["entra_descripcion"])} for t in res.transferencias],
         ["valor", "sale_de", "sale_fecha", "sale_descripcion", "sale_archivo", "sale_pagina", "sale_renglon",
          "entra_a", "entra_fecha", "entra_descripcion", "entra_archivo", "entra_pagina", "entra_renglon"])

    filas = []
    por_cufe = {f.cufe: f for f in res.facturas}
    avisos_pago = {a.referencia: a for a in res.avisos if a.tema == "pago"}
    for c in res.cruces:
        f = por_cufe[c.cufe]
        fila = {"cufe": c.cufe, "numero": f.numero, "fecha_factura": f.fecha.isoformat(), "emisor": f.emisor_nombre,
                "total_pagar": f.total_pagar, "estado": c.estado, "puntaje": c.puntaje, "candidatos": c.candidatos,
                "detalle": c.motivo, "alerta": avisos_pago.get(c.cufe).nivel if c.cufe in avisos_pago else "",
                "que_hacer": avisos_pago.get(c.cufe).que_hacer if c.cufe in avisos_pago else ""}
        if c.indice_movimiento is not None:
            x, m = res.movimientos_cruce[c.indice_movimiento]
            fila.update({"mov_banco": x.extracto.banco, "mov_fecha": m.fecha.isoformat(),
                         "mov_descripcion": enmascarar_texto(m.descripcion), "mov_valor": m.valor, "mov_archivo": x.archivo,
                         "mov_pagina": m.pagina, "mov_renglon": m.renglon})
        filas.append(fila)
    _csv(salida / "pagos.csv", filas,
         ["cufe", "numero", "fecha_factura", "emisor", "total_pagar", "estado", "puntaje", "candidatos", "detalle",
          "alerta", "que_hacer", "mov_banco", "mov_fecha", "mov_descripcion", "mov_valor", "mov_archivo", "mov_pagina", "mov_renglon"])

    _csv(salida / "renta_resumen.csv",
         [vars(r) | {"notas": " | ".join(r.notas), "adquiriente_id": enmascarar(r.adquiriente_id)}
          for r in res.renta_resumen],
         ["anio", "adquiriente_id", "adquiriente_nombre", "facturas_cumple", "facturas_por_probar",
          "facturas_no_cumple", "base_cumple", "base_por_probar", "deduccion", "deduccion_potencial",
          "deduccion_sobre_total", "uvt", "estado_uvt", "tope", "notas"])
    _csv(salida / "alertas_precio.csv", [vars(a) for a in res.alertas],
         ["emisor", "producto", "precio_anterior", "precio_actual", "cambio", "fecha_actual", "compras_previas",
          "cufe_actual"])
    _csv(salida / "por_etiquetar.csv",
         [l | {"categoria_sugerida": l["categoria"], "categoria": ""} for l in res.lineas],
         ["cufe", "linea", "fecha", "emisor_nombre", "descripcion", "gasto", "categoria_sugerida", "categoria"])


def _resumen(res: Resultado, ahora: dt.datetime, version: str) -> dict:
    por_cat: dict[str, Decimal] = defaultdict(Decimal)
    for l in res.lineas:
        por_cat[l["categoria"]] += l["gasto"]
    n_ok = sum(1 for f in res.facturas if conciliacion.conciliada(res.chequeos[f.cufe]))
    validos = [x for x in res.extractos if x.valido]
    total_archivos_extractos = len(res.extractos) + sum(
        1 for c in res.cola_revision if c["archivo"] not in {x.archivo for x in res.extractos})
    return {
        "generado": ahora.isoformat(timespec="seconds"),
        "version_codigo": version,
        "facturas": {
            "unicas": len(res.facturas), "conciliadas": n_ok, "duplicadas": len(res.duplicadas),
            "ilegibles": len(res.errores_facturas),
            "total": _q(sum((f.signo * f.total_pagar for f in res.facturas), Decimal(0))),
            "lineas": len(res.lineas),
            "sin_categoria": sum(1 for l in res.lineas if l["categoria"] == categorias.SIN_CATEGORIA),
            "por_categoria": {k: _q(v) for k, v in sorted(por_cat.items(), key=lambda kv: -kv[1])},
        },
        "extractos": {
            "archivos": total_archivos_extractos, "validos": len(validos), "en_revision": len(res.cola_revision),
            "repetidos_ignorados": res.extractos_repetidos,
            "transferencias_propias": len(res.transferencias),
            "movimientos": sum(len(x.extracto.movimientos) for x in validos),
            "gasto_real": _q(sum((gasto_real(x.extracto) for x in validos), Decimal(0))),
        },
        "pagos": {estado: sum(1 for c in res.cruces if c.estado == estado)
                  for estado in ("conciliada", "declarada_por_autor", "ambigua", "sin_movimiento")},
        "renta": [{"anio": r.anio, "adquiriente": r.adquiriente_nombre, "cumple": r.facturas_cumple,
                   "por_probar": r.facturas_por_probar, "deduccion": str(r.deduccion),
                   "deduccion_potencial": str(r.deduccion_potencial)} for r in res.renta_resumen],
        "avisos": {n: sum(1 for a in res.avisos if a.nivel == n) for n in ("alerta", "aviso", "info")},
        "alertas_precio": len(res.alertas),
        "metricas_categorias": res.metricas_categorias,
    }
