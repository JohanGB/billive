"""Proceso completo sobre un proyecto sintético: tablas, trazabilidad, inmutabilidad y tablero."""
import csv
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from factura_viva import manifiesto
from factura_viva.pipeline import procesar
from factura_viva.sinteticos import proyecto_demo

RAIZ = Path(__file__).resolve().parents[1]


def tabla(raiz, nombre):
    with open(raiz / "data" / "procesado" / nombre, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def demo(tmp_path):
    pytest.importorskip("reportlab")
    raiz = proyecto_demo(tmp_path)
    return raiz, procesar(raiz, pedir_clave=lambda nombre: "demo1234", decir=lambda s: None)


def test_demo_de_punta_a_punta(demo):
    raiz, res = demo
    r = res.resumen
    assert r["facturas"] == {**r["facturas"], "unicas": 3, "conciliadas": 3, "duplicadas": 1, "ilegibles": 0,
                             "total": "171700.00"}          # 17.000 + 95.200 + 59.500
    assert r["extractos"]["validos"] == 1 and r["extractos"]["en_revision"] == 0
    assert r["pagos"] == {"conciliada": 2, "declarada_por_autor": 0, "ambigua": 0, "sin_movimiento": 1}
    assert r["alertas_precio"] == 1                          # arroz: 17.000 -> 20.000

    lineas = tabla(raiz, "lineas.csv")
    assert sum(Decimal(l["gasto"]) for l in lineas) == Decimal("171700.00")
    manif = json.loads((raiz / "data" / "manifest.json").read_text(encoding="utf-8"))["archivos"]
    for l in lineas:                                         # toda cifra se rastrea a un archivo con su huella
        assert manif[l["fuente_archivo"]]["sha256"] == l["fuente_sha256"] and l["version_codigo"]
    movs = tabla(raiz, "movimientos.csv")
    assert len(movs) == 4 and all(m["fuente_pagina"] and m["fuente_renglon"] for m in movs)

    # La factura que decía "instrumento no definido" queda probada con el extracto.
    renta = {f["numero"]: f for f in tabla(raiz, "facturas.csv")}
    assert renta["TEL2002"]["renta_estado"] == "cumple" and "extracto" in renta["TEL2002"]["renta_motivo"]
    assert renta["SUP0900"]["renta_estado"] == "por_probar"   # efectivo y sin movimiento

    html = (raiz / "data" / "procesado" / "tablero.html").read_text(encoding="utf-8")
    assert "¿En qué se fue la plata?" in html and '"total": "171700.00"' in html and "http" not in html.split("<script>")[0]


def test_la_clave_no_queda_en_ningun_archivo(demo):
    raiz, _ = demo
    for p in raiz.rglob("*"):
        if p.is_file() and p.suffix.lower() not in (".pdf", ".zip"):
            assert "demo1234" not in p.read_text(encoding="utf-8", errors="ignore"), p


def test_los_documentos_de_identidad_no_quedan_completos_en_lo_procesado(demo):
    raiz, _ = demo
    for p in (raiz / "data" / "procesado").iterdir():
        assert "1000000001" not in p.read_text(encoding="utf-8", errors="ignore"), p.name
    assert {f["adquiriente_id"] for f in tabla(raiz, "facturas.csv")} == {"····0001"}


def test_pdf_protegido_sin_clave_va_a_la_cola_de_revision(tmp_path):
    pytest.importorskip("reportlab")
    raiz = proyecto_demo(tmp_path)
    res = procesar(raiz, pedir_clave=None, decir=lambda s: None)
    assert res.resumen["extractos"] == {**res.resumen["extractos"], "archivos": 1, "validos": 0, "en_revision": 1}
    cola = tabla(raiz, "cola_revision.csv")
    assert len(cola) == 1 and "clave" in cola[0]["motivo"] and tabla(raiz, "movimientos.csv") == []


def test_extracto_que_no_cuadra_va_a_revision_y_no_entra_al_analisis(tmp_path):
    pytest.importorskip("reportlab")
    from factura_viva.sinteticos import EXTRACTO_GENERICO, extracto_pdf
    raiz = proyecto_demo(tmp_path, con_pdf=False)
    extracto_pdf(raiz / "data" / "raw" / "extractos" / "raro.pdf", [r for r in EXTRACTO_GENERICO if "NOMINA" not in r])
    res = procesar(raiz, decir=lambda s: None)
    assert res.resumen["extractos"]["validos"] == 0 and res.resumen["extractos"]["en_revision"] == 1
    assert "no cuadran" in tabla(raiz, "cola_revision.csv")[0]["motivo"]
    assert res.resumen["pagos"]["conciliada"] == 0            # sus movimientos no se usan


def test_data_raw_es_inmutable(tmp_path):
    raiz = proyecto_demo(tmp_path, con_pdf=False)
    procesar(raiz, decir=lambda s: None)
    victima = raiz / "data" / "raw" / "facturas" / "demo1.zip"
    victima.write_bytes(victima.read_bytes() + b"x")
    with pytest.raises(RuntimeError, match="inmutable"):
        procesar(raiz, decir=lambda s: None)
    estado = manifiesto.actualizar(raiz / "data" / "raw", raiz / "data" / "manifest.json")
    assert estado.alterados == ["facturas/demo1.zip"]


def test_repetir_el_proceso_da_las_mismas_tablas(tmp_path):
    raiz = proyecto_demo(tmp_path, con_pdf=False)
    procesar(raiz, decir=lambda s: None)
    antes = {n: (raiz / "data" / "procesado" / n).read_bytes() for n in ("facturas.csv", "lineas.csv", "pagos.csv")}
    procesar(raiz, decir=lambda s: None)
    assert antes == {n: (raiz / "data" / "procesado" / n).read_bytes() for n in antes}


def test_cuentas_propias_declaradas_no_son_gasto(tmp_path):
    pytest.importorskip("reportlab")
    raiz = proyecto_demo(tmp_path)
    cfg = raiz / "config" / "conciliacion.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("cuentas_propias: []", 'cuentas_propias: ["TELEFONIA ANDINA"]'),
                   encoding="utf-8")
    procesar(raiz, pedir_clave=lambda n: "demo1234", decir=lambda s: None)
    m = {x["descripcion"]: x for x in tabla(raiz, "movimientos.csv")}
    assert m["PAGO PSE TELEFONIA ANDINA"]["cuenta_como_gasto"] == "no"
    assert m["COMPRA SUPERMERCADO LA CANASTA"]["cuenta_como_gasto"] == "sí"


def test_gitignore_protege_los_datos_personales():
    reglas = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    for patron in ("data/raw/**", "data/procesado/", "data/manifest.json", "data/etiquetas/"):
        assert patron in reglas


# ------------------------------------------------------------------ datos reales (solo en el equipo del autor)

REALES = RAIZ / "data" / "raw" / "facturas"


@pytest.mark.skipif(not REALES.exists() or not any(REALES.glob("*.zip")), reason="sin facturas reales en data/raw")
def test_aceptacion_todas_las_facturas_reales_concilian():
    from factura_viva.facturas.conciliacion import conciliada, conciliar
    from factura_viva.facturas.lector import leer_carpeta
    r = leer_carpeta(REALES, RAIZ / "data" / "raw")
    assert r.errores == [] and r.facturas
    pendientes = [f.numero for f in r.facturas if not conciliada(conciliar(f))]
    assert pendientes == [], f"facturas sin conciliar: {pendientes}"


@pytest.mark.skipif(not os.environ.get("FACTURA_VIVA_CLAVE") or not (RAIZ / "data" / "raw" / "extractos").exists(),
                    reason="defina FACTURA_VIVA_CLAVE en la terminal para validar los extractos reales")
def test_aceptacion_todo_extracto_real_cuadra_o_va_a_revision():
    """Si una plantilla de banco interpreta un extracto, sus saldos tienen que cuadrar.
    Lo que la plantilla rechaza (ExtractoIlegible) o lo que lee el genérico sin cuadrar va a revisión."""
    from factura_viva.extractos import catalogo, validacion
    from factura_viva.extractos.base import ExtractoIlegible
    from factura_viva.extractos.pdf import leer_paginas
    pdfs = sorted(p for p in (RAIZ / "data" / "raw" / "extractos").iterdir() if p.suffix.lower() == ".pdf")
    assert pdfs
    for pdf in pdfs:
        paginas = leer_paginas(pdf, os.environ["FACTURA_VIVA_CLAVE"])
        lector = catalogo.elegir(paginas)
        try:
            e = lector.interpretar(paginas)
        except ExtractoIlegible:
            continue
        if lector.nombre != "generico":
            fallas = [c.nombre for c in validacion.validar(e) if not c.ok]
            assert fallas == [], f"{pdf.name} ({lector.nombre}): {fallas}"
