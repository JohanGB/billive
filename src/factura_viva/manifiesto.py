"""Manifiesto de datos crudos: qué archivos hay en ``data/raw`` y su huella SHA-256.

``data/raw`` es inmutable. El manifiesto permite comprobarlo: si un archivo ya
registrado cambia de contenido, se reporta como ALTERADO y el proceso se detiene,
porque todas las cifras derivadas dejarían de ser rastreables.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from .facturas.lector import sha256_archivo

IGNORAR = {"LEEME.md", ".gitkeep", "desktop.ini", "Thumbs.db"}


@dataclass
class EstadoManifiesto:
    archivos: dict[str, dict]
    nuevos: list[str] = field(default_factory=list)
    alterados: list[str] = field(default_factory=list)
    faltantes: list[str] = field(default_factory=list)


def _tipo(rel: str) -> str:
    partes = rel.split("/")
    return partes[0] if len(partes) > 1 else "otro"


def actualizar(raiz_raw: Path, ruta_manifiesto: Path, ahora: dt.datetime | None = None) -> EstadoManifiesto:
    """Registra archivos nuevos y verifica que los ya registrados no cambiaron."""
    ahora = ahora or dt.datetime.now().astimezone()
    previo: dict[str, dict] = {}
    if ruta_manifiesto.exists():
        previo = json.loads(ruta_manifiesto.read_text(encoding="utf-8")).get("archivos", {})
    estado = EstadoManifiesto(archivos=dict(previo))
    presentes = set()
    for p in sorted(raiz_raw.rglob("*")):
        if not p.is_file() or p.name in IGNORAR:
            continue
        rel = p.relative_to(raiz_raw).as_posix()
        presentes.add(rel)
        huella = sha256_archivo(p)
        if rel not in previo:
            estado.archivos[rel] = {
                "sha256": huella, "bytes": p.stat().st_size, "tipo": _tipo(rel),
                "registrado": ahora.isoformat(timespec="seconds"),
            }
            estado.nuevos.append(rel)
        elif previo[rel]["sha256"] != huella:
            estado.alterados.append(rel)
    estado.faltantes = sorted(set(previo) - presentes)
    if not estado.alterados:
        ruta_manifiesto.parent.mkdir(parents=True, exist_ok=True)
        cuerpo = {
            "descripcion": "Huellas SHA-256 de data/raw (inmutable). Generado por factura_viva.manifiesto.",
            "actualizado": ahora.isoformat(timespec="seconds"),
            "archivos": dict(sorted(estado.archivos.items())),
        }
        ruta_manifiesto.write_text(json.dumps(cuerpo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return estado
