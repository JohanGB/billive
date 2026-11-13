"""Lectura del texto de un PDF, con o sin clave.

La clave vive solo en memoria durante la llamada: no se escribe en archivos, registros
ni mensajes de error. Si el PDF está protegido y no se entrega clave, se lanza
``ClaveRequerida`` para que quien llama decida (pedirla por teclado o enviar el archivo
a la cola de revisión).
"""
from __future__ import annotations

from pathlib import Path

from .base import Pagina


class ClaveRequerida(Exception):
    """El PDF está protegido y no se entregó clave."""


class ClaveIncorrecta(Exception):
    """La clave entregada no abre el PDF."""


def _es_error_de_clave(e: BaseException) -> bool:
    visto = set()
    while e is not None and id(e) not in visto:
        visto.add(id(e))
        nombre = type(e).__name__.lower()
        if "password" in nombre or "encrypt" in nombre:
            return True
        for a in getattr(e, "args", ()):
            if isinstance(a, BaseException) and _es_error_de_clave(a):
                return True
        e = e.__cause__ or e.__context__
    return False


def leer_paginas(ruta: Path, clave: str | None = None) -> list[Pagina]:
    import pdfplumber  # importación tardía: el resto del paquete funciona sin PDF

    try:
        pdf = pdfplumber.open(str(ruta), password=clave or "")
    except Exception as e:  # noqa: BLE001 - pdfminer no expone una jerarquía estable
        if _es_error_de_clave(e):
            if clave:
                raise ClaveIncorrecta(Path(ruta).name) from None
            raise ClaveRequerida(Path(ruta).name) from None
        raise
    paginas: list[Pagina] = []
    with pdf:
        for i, pag in enumerate(pdf.pages, start=1):
            texto = pag.extract_text() or ""
            palabras = tuple(
                {"texto": w["text"], "x0": round(w["x0"], 1), "x1": round(w["x1"], 1),
                 "top": round(w["top"], 1)}
                for w in pag.extract_words(keep_blank_chars=False)
            )
            paginas.append(Pagina(i, tuple(texto.splitlines()), palabras))
    return paginas
