"""Categorización del gasto por reglas (línea base, fase 1 sin ML).

Las reglas viven en ``config/categorias.yaml`` y se evalúan en orden: gana la primera
que coincide. Cada línea guarda qué regla la clasificó. Lo que ninguna regla reconoce
queda en "Sin categoría" y pasa a la lista de pendientes por etiquetar, que es la
semilla del aprendizaje activo de la fase 2.

``evaluar`` calcula precisión, recall y F1 por categoría contra etiquetas puestas por
el autor; sin etiquetas no se reporta ninguna métrica (no se inventa).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SIN_CATEGORIA = "Sin categoría"


def _plano(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


@dataclass
class Regla:
    ident: str
    categoria: str
    descripcion: re.Pattern | None
    emisor_nombre: re.Pattern | None
    emisor_nit: tuple[str, ...]
    ciiu: tuple[str, ...]

    def coincide(self, descripcion: str, emisor_nombre: str, emisor_nit: str, ciiu: str) -> bool:
        if self.descripcion and not self.descripcion.search(_plano(descripcion)):
            return False
        if self.emisor_nombre and not self.emisor_nombre.search(_plano(emisor_nombre)):
            return False
        if self.emisor_nit and emisor_nit not in self.emisor_nit:
            return False
        if self.ciiu:
            codigos = [c.strip() for c in re.split(r"[;,\s]+", ciiu) if c.strip()]
            if not any(c.startswith(p) for c in codigos for p in self.ciiu):
                return False
        return True


def cargar_reglas(cfg: dict) -> list[Regla]:
    reglas = []
    for cat in cfg.get("categorias", []):
        for n, r in enumerate(cat.get("reglas", []), start=1):
            if not r:
                raise ValueError(f"Regla vacía en la categoría {cat['id']}: clasificaría todo")
            reglas.append(Regla(
                ident=f"{cat['id']}#{n}", categoria=cat["nombre"],
                descripcion=re.compile(_plano(r["descripcion"])) if r.get("descripcion") else None,
                emisor_nombre=re.compile(_plano(r["emisor_nombre"])) if r.get("emisor_nombre") else None,
                emisor_nit=tuple(str(x) for x in r.get("emisor_nit", [])),
                ciiu=tuple(str(x) for x in r.get("ciiu", [])),
            ))
    return reglas


def clasificar(reglas: list[Regla], descripcion: str, emisor_nombre: str, emisor_nit: str, ciiu: str) -> tuple[str, str]:
    """Devuelve (categoría, identificador de la regla que decidió)."""
    for r in reglas:
        if r.coincide(descripcion, emisor_nombre, emisor_nit, ciiu):
            return r.categoria, r.ident
    return SIN_CATEGORIA, ""


def evaluar(reales: list[str], predichas: list[str]) -> dict[str, dict[str, float]]:
    """Precisión, recall, F1 y soporte por categoría."""
    if len(reales) != len(predichas):
        raise ValueError("Las listas deben tener el mismo tamaño")
    salida = {}
    for cat in sorted(set(reales) | set(predichas)):
        vp = sum(1 for r, p in zip(reales, predichas) if r == cat and p == cat)
        fp = sum(1 for r, p in zip(reales, predichas) if r != cat and p == cat)
        fn = sum(1 for r, p in zip(reales, predichas) if r == cat and p != cat)
        prec = vp / (vp + fp) if vp + fp else 0.0
        rec = vp / (vp + fn) if vp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        salida[cat] = {"precision": prec, "recall": rec, "f1": f1, "soporte": vp + fn}
    return salida
