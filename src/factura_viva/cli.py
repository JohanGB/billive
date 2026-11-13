"""Punto de entrada: ``python -m factura_viva`` (o el comando ``factura-viva``).

Lee ``data/raw``, valida, y escribe tablas y tablero en ``data/procesado``.
Las claves de los PDF se piden por teclado (sin mostrarlas) y no se guardan.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import webbrowser
from decimal import Decimal
from pathlib import Path

from . import __version__
from .dinero import pesos
from .pipeline import procesar


def _raiz(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    aqui = Path.cwd()
    for p in (aqui, *aqui.parents):
        if (p / "config" / "conciliacion.yaml").exists() and (p / "data").exists():
            return p
    return aqui


def _pedir_clave(sin_preguntas: bool):
    de_entorno = os.environ.get("FACTURA_VIVA_CLAVE")   # opcional; vive solo en la sesión de la terminal
    usada = {"entorno": False}

    def pedir(nombre: str) -> str | None:
        if de_entorno and not usada["entorno"]:
            usada["entorno"] = True
            return de_entorno
        if sin_preguntas or not sys.stdin.isatty():
            return None
        try:
            return getpass.getpass(f"  Clave del PDF {nombre} (Enter para omitir; no se guarda): ") or None
        except (EOFError, KeyboardInterrupt):
            return None

    return pedir


def main(argv: list[str] | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):   # consola de Windows (cp1252): no fallar por tildes
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="factura-viva", description=__doc__.splitlines()[0])
    ap.add_argument("--raiz", help="carpeta del proyecto (por defecto, la actual)")
    ap.add_argument("--sin-preguntas", action="store_true",
                    help="no pedir claves: los PDF protegidos van a la cola de revisión")
    ap.add_argument("--abrir", action="store_true", help="abrir el tablero en el navegador al terminar")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    raiz = _raiz(a.raiz)
    if not (raiz / "config" / "conciliacion.yaml").exists():
        print(f"No encuentro la configuración en {raiz / 'config'}. Ejecute desde la carpeta del proyecto.")
        return 2
    try:
        res = procesar(raiz, _pedir_clave(a.sin_preguntas))
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 1
    r = res.resumen
    print()
    print(f"Facturas: {r['facturas']['conciliadas']} de {r['facturas']['unicas']} conciliadas "
          f"(total {pesos(Decimal(r['facturas']['total']))})")
    print(f"Extractos: {r['extractos']['validos']} de {r['extractos']['archivos']} con saldos que cuadran; "
          f"{r['extractos']['en_revision']} en revisión")
    print(f"Pagos encontrados en extractos: {r['pagos']['conciliada']} de {r['facturas']['unicas']}")
    for x in r["renta"]:
        print(f"Renta {x['anio']} - {x['adquiriente']}: deducción estimada {pesos(Decimal(x['deduccion']))} "
              f"(hasta {pesos(Decimal(x['deduccion_potencial']))} si se prueba el pago de {x['por_probar']} factura(s))")
    f2 = r.get("fase2")
    if f2:
        print("Fase 2 (modelos): " + ("lista para empezar" if f2["lista"] else
              f"en espera, {f2['facturas']} de {f2['minimo']} facturas (faltan {f2['minimo'] - f2['facturas']})"))
    tablero = raiz / "data" / "procesado" / "tablero.html"
    print(f"Tablero: {tablero}")
    if a.abrir:
        webbrowser.open(tablero.as_uri())
    pendientes = (r["facturas"]["unicas"] - r["facturas"]["conciliadas"]) + r["facturas"]["ilegibles"]
    return 0 if pendientes == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
