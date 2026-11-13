"""Lector de facturas DIAN y conciliación interna (datos sintéticos)."""
import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from factura_viva.facturas.conciliacion import Tolerancias, conciliada, conciliar
from factura_viva.facturas.lector import Fuente, FacturaInvalida, leer_carpeta, leer_xml, leer_zip
from factura_viva.sinteticos import cufe_falso, escribir_zip, factura_xml

HOY = dt.date(2026, 10, 2)
F = Fuente("prueba.zip", "0" * 64, "prueba.xml")
BASE = dict(numero="A1", fecha=HOY, emisor="TIENDA DE EJEMPLO S.A.S", nit="900111222",
            adquiriente="PERSONA DE EJEMPLO", adq_id="1000000001")


def fx(**k):
    return factura_xml(**{**BASE, "lineas": [("Arroz 5 kg", "2", "10000", "19")], **k})


def test_lee_contenedor_con_factura_en_cdata():
    f = leer_xml(fx(lineas=[("Arroz 5 kg", "2", "10000", "0"), ("Jabón", "1", "5000", "19")], medio="49"), F)
    # 2 × 10.000 = 20.000 ; 5.000 + IVA 950 ; total 25.950
    assert (f.tipo, f.numero, f.fecha) == ("factura", "A1", HOY)
    assert f.valor_bruto == Decimal("25000.00") and f.total_impuestos == Decimal("950.00")
    assert f.total_pagar == Decimal("25950.00")
    assert [l.descripcion for l in f.lineas] == ["Arroz 5 kg", "Jabón"]
    assert f.lineas[0].cantidad == 2 and f.lineas[0].precio_unitario == Decimal("10000")
    assert f.emisor_nit == "900111222" and f.adquiriente_id == "1000000001" and f.medio_pago == "49"
    assert f.validacion.validada and f.cufe == f.cufe_contenedor and len(f.cufe) == 96
    assert conciliada(conciliar(f))


def test_factura_sin_contenedor_tambien_se_lee_pero_no_tiene_validacion():
    f = leer_xml(fx(contenedor=False), F)
    ch = conciliar(f)
    assert f.total_pagar == Decimal("23800.00")
    assert [c.codigo for c in ch if not c.ok] == ["C9"]          # falta la validación DIAN
    assert conciliada(conciliar(f, Tolerancias(exigir_validacion_dian=False)))


@pytest.mark.parametrize("codigo,esperado", [("02", True), ("002", True), ("04", False), (None, False)])
def test_codigo_de_validacion_dian(codigo, esperado):
    assert leer_xml(fx(validacion=codigo), F).validacion.validada is esperado


def test_descuadre_mayor_a_la_tolerancia_se_detecta():
    f = leer_xml(fx(descuadre="5"), F)
    fallas = [c for c in conciliar(f) if not c.ok]
    assert [c.codigo for c in fallas] == ["C3"] and fallas[0].diferencia == Decimal("-5.00")
    assert conciliada(conciliar(leer_xml(fx(descuadre="1.50"), F)))   # dentro de los 2 pesos


def test_nota_credito_resta_y_referencia_la_factura():
    ref = cufe_falso("original")
    n = leer_xml(fx(tipo="CreditNote", numero="NC1", ref_cufe=ref), F)
    assert n.tipo == "nota_credito" and n.signo == -1 and n.cufe_referencia == ref
    assert len(n.lineas) == 1 and conciliada(conciliar(n))


def test_cufe_distinto_al_del_contenedor_falla():
    xml = fx()
    cufe = leer_xml(xml, F).cufe.encode()
    antes, despues = xml.rsplit(cufe, 1)        # la última aparición es la del contenedor
    malo = antes + cufe_falso("otro").encode() + despues
    assert [c.codigo for c in conciliar(leer_xml(malo, F)) if not c.ok] == ["C8"]


def test_rechaza_doctype_y_xml_ajeno():
    with pytest.raises(FacturaInvalida, match="DOCTYPE"):
        leer_xml(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Invoice/>', F)
    with pytest.raises(FacturaInvalida, match="no es una factura"):
        leer_xml(b"<html><body>hola</body></html>", F)
    with pytest.raises(FacturaInvalida, match="mal formado"):
        leer_xml(b"<Invoice", F)


def test_carpeta_deduplica_por_cufe_y_reporta_ilegibles(tmp_path):
    escribir_zip(tmp_path, "a", fx())
    escribir_zip(tmp_path, "a_copia", fx())                       # mismo CUFE
    escribir_zip(tmp_path, "b", fx(numero="B2", fecha=dt.date(2026, 9, 1)))
    (tmp_path / "roto.zip").write_bytes(b"esto no es un zip")
    r = leer_carpeta(tmp_path, tmp_path)
    assert [f.numero for f in r.facturas] == ["B2", "A1"]         # ordenadas por fecha
    assert r.duplicadas == [("a_copia.zip", "a.zip")]
    assert [e[0] for e in r.errores] == ["roto.zip"]
    f = leer_zip(tmp_path / "a.zip", tmp_path)
    assert f.fuente.archivo == "a.zip" and f.fuente.miembro_xml == "a.xml" and len(f.fuente.sha256) == 64


precios = st.decimals(min_value=Decimal("1"), max_value=Decimal("5000000"), places=2)
lineas = st.lists(st.tuples(st.integers(1, 20), precios, st.sampled_from(["0", "5", "19"])), min_size=1, max_size=8)


@settings(max_examples=80, deadline=None)
@given(ls=lineas)
def test_propiedad_toda_factura_bien_armada_concilia(ls):
    f = leer_xml(fx(lineas=[(f"item {i}", str(c), str(p), pct) for i, (c, p, pct) in enumerate(ls)]), F)
    assert conciliada(conciliar(f))
    assert sum(l.valor for l in f.lineas) == f.valor_bruto


@settings(max_examples=60, deadline=None)
@given(ls=lineas, error=st.decimals(min_value=Decimal("2.01"), max_value=Decimal("100000"), places=2),
       signo=st.sampled_from([1, -1]))
def test_propiedad_un_descuadre_mayor_a_la_tolerancia_nunca_pasa(ls, error, signo):
    f = leer_xml(fx(lineas=[(f"item {i}", str(c), str(p), pct) for i, (c, p, pct) in enumerate(ls)],
                    descuadre=str(signo * error)), F)
    assert not conciliada(conciliar(f))
