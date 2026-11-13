"""Cruce de pagos, categorías, deducción en renta y alertas de precio (datos sintéticos)."""
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings, strategies as st

from factura_viva import alertas, categorias, pagos, renta
from factura_viva.extractos.base import Movimiento
from factura_viva.facturas.lector import Fuente, leer_xml
from factura_viva.pipeline import gasto_por_linea
from factura_viva.sinteticos import factura_xml

CONFIG = Path(__file__).resolve().parents[1] / "config"
CFG_RENTA = yaml.safe_load((CONFIG / "renta.yaml").read_text(encoding="utf-8"))
REGLAS = categorias.cargar_reglas(yaml.safe_load((CONFIG / "categorias.yaml").read_text(encoding="utf-8")))
D = dt.date


def fac(numero="A1", fecha=D(2026, 10, 2), emisor="SUPERMERCADO LA CANASTA S.A.S", lineas=None, **k):
    xml = factura_xml(numero=numero, fecha=fecha, emisor=emisor, nit=k.pop("nit", "900111222"),
                      adquiriente=k.pop("adquiriente", "PERSONA DE EJEMPLO"), adq_id=k.pop("adq_id", "1000000001"),
                      lineas=lineas or [("Arroz 5 kg", "1", "20000", "0")], **k)
    return leer_xml(xml, Fuente(numero + ".zip", "0" * 64, numero + ".xml"))


def mov(fecha, desc, valor):
    return Movimiento(fecha, desc, Decimal(valor))


# ------------------------------------------------------------------ pagos

def test_cruce_por_monto_fecha_y_comercio():
    f = fac()
    c = pagos.cruzar([f], [mov(D(2026, 10, 1), "RETIRO CAJERO", "-20000"),
                           mov(D(2026, 10, 3), "COMPRA SUPERMERCADO LA CANASTA", "-20000")])[0]
    assert (c.estado, c.indice_movimiento, c.candidatos) == ("conciliada", 1, 2)   # gana el del comercio


def test_dos_candidatos_empatados_es_ambiguo_y_no_se_asigna():
    c = pagos.cruzar([fac()], [mov(D(2026, 10, 3), "COMPRA TARJETA", "-20000"),
                               mov(D(2026, 10, 3), "COMPRA TARJETA", "-20000")])[0]
    assert c.estado == "ambigua" and c.indice_movimiento is None


def test_fuera_de_ventana_o_con_otro_valor_no_cruza():
    f = fac()
    for m in (mov(D(2026, 11, 20), "COMPRA SUPERMERCADO LA CANASTA", "-20000"),   # muy tarde
              mov(D(2026, 10, 2), "COMPRA SUPERMERCADO LA CANASTA", "-20500"),    # otro valor
              mov(D(2026, 10, 2), "ABONO SUPERMERCADO LA CANASTA", "20000")):     # es una entrada
        assert pagos.cruzar([f], [m])[0].estado == "sin_movimiento"


def test_un_movimiento_no_paga_dos_facturas():
    f1, f2 = fac("A1"), fac("A2")
    cs = pagos.cruzar([f1, f2], [mov(D(2026, 10, 2), "COMPRA SUPERMERCADO LA CANASTA", "-20000")])
    assert sorted(c.estado for c in cs) == ["conciliada", "sin_movimiento"]


def test_nota_credito_cruza_con_un_reembolso():
    n = fac("NC1", tipo="CreditNote")
    assert pagos.cruzar([n], [mov(D(2026, 10, 4), "DEVOLUCION SUPERMERCADO LA CANASTA", "20000")])[0].estado == "conciliada"


@settings(max_examples=50, deadline=None)
@given(st.lists(st.tuples(st.integers(1, 28), st.integers(1, 5)), min_size=1, max_size=12),
       st.lists(st.tuples(st.integers(1, 28), st.integers(1, 5)), max_size=12))
def test_propiedad_cruce_uno_a_uno(fs, ms):
    facturas = [fac(f"F{i}", D(2026, 10, d), lineas=[("x", "1", str(v * 1000), "0")]) for i, (d, v) in enumerate(fs)]
    movimientos = [mov(D(2026, 10, d), "COMPRA", str(-v * 1000)) for d, v in ms]
    cs = pagos.cruzar(facturas, movimientos)
    usados = [c.indice_movimiento for c in cs if c.estado == "conciliada"]
    assert len(usados) == len(set(usados)) and len(cs) == len(facturas)
    for c, f in zip(cs, facturas):
        if c.estado == "conciliada":
            assert abs(movimientos[c.indice_movimiento].valor) == f.total_pagar


# ------------------------------------------------------------------ categorías

@pytest.mark.parametrize("desc,emisor,ciiu,esperada", [
    ("Plan pospago 20 GB", "TELEFONIA ANDINA S.A.", "6120", "Telecomunicaciones"),
    ("CUOTA MODERADORA O COPAGO", "DROGUERIA EJEMPLO", "4773", "Salud"),
    ("INTERES DE MORA", "UNIVERSIDAD DE EJEMPLO", "8544", "Intereses y cargos financieros"),   # la línea manda
    ("Matrícula semestre", "UNIVERSIDAD DE EJEMPLO", "8544", "Educación"),
    ("Pasaje aéreo BOG-MDE", "AGENCIA EJEMPLO", "6201", "Viajes"),
    ("Arroz 5 kg", "SUPERMERCADO LA CANASTA", "4711", "Mercado"),
    ("Tornillos", "FERRETERIA EJEMPLO", "4752", categorias.SIN_CATEGORIA),
])
def test_reglas_de_categoria(desc, emisor, ciiu, esperada):
    cat, regla = categorias.clasificar(REGLAS, desc, emisor, "900", ciiu)
    assert cat == esperada and (bool(regla) == (esperada != categorias.SIN_CATEGORIA))


def test_metricas_por_categoria():
    m = categorias.evaluar(["A", "A", "B", "B"], ["A", "B", "B", "B"])
    assert m["A"] == {"precision": 1.0, "recall": 0.5, "f1": pytest.approx(2 / 3), "soporte": 2}
    assert m["B"]["precision"] == pytest.approx(2 / 3) and m["B"]["recall"] == 1.0


def test_regla_vacia_se_rechaza():
    with pytest.raises(ValueError):
        categorias.cargar_reglas({"categorias": [{"id": "x", "nombre": "X", "reglas": [{}]}]})


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.integers(1, 9), st.decimals(min_value=Decimal("0.01"), max_value=Decimal("999999"), places=2),
                          st.sampled_from(["0", "5", "19"])), min_size=1, max_size=8))
def test_propiedad_el_gasto_por_linea_suma_el_total_a_pagar(ls):
    f = fac(lineas=[(f"item {i}", str(c), str(p), pct) for i, (c, p, pct) in enumerate(ls)])
    assert sum(gasto_por_linea(f)) == f.total_pagar


# ------------------------------------------------------------------ renta

def test_estados_segun_medio_de_pago_y_prueba_bancaria():
    assert renta.evaluar(fac(medio="48"), CFG_RENTA, False).estado == "cumple"          # tarjeta crédito
    efectivo = fac(medio="10")
    assert renta.evaluar(efectivo, CFG_RENTA, False).estado == "por_probar"
    probada = renta.evaluar(efectivo, CFG_RENTA, True)
    assert probada.estado == "cumple" and "extracto" in probada.motivo
    assert renta.evaluar(fac(validacion=None), CFG_RENTA, True).estado == "no_cumple"
    assert renta.evaluar(fac(adq_id="222222222222"), CFG_RENTA, True).estado == "no_cumple"


def test_resumen_por_persona_y_anio_con_tope():
    # Persona A, 2026: 1.000.000 (tarjeta) + 500.000 (efectivo sin prueba). 1 % de 1.000.000 = 10.000.
    fs = [fac("A1", lineas=[("x", "1", "1000000", "19")], medio="48"),
          fac("A2", lineas=[("y", "1", "500000", "0")], medio="10"),
          fac("B1", lineas=[("z", "1", "200000", "0")], medio="49", adq_id="2000000002", adquiriente="OTRA PERSONA")]
    ev = [renta.evaluar(f, CFG_RENTA, False) for f in fs]
    r = {x.adquiriente_id: x for x in renta.resumir(fs, ev, CFG_RENTA)}
    a = r["1000000001"]
    assert (a.deduccion, a.deduccion_potencial) == (Decimal("10000"), Decimal("15000"))
    assert a.deduccion_sobre_total == Decimal("11900")              # con IVA: 1.190.000
    assert a.tope == Decimal("52374") * 240 and a.estado_uvt == "verificado"
    assert r["2000000002"].deduccion == Decimal("2000")
    # Tope: una compra enorme no puede dar más de 240 UVT.
    grande = fac("G", lineas=[("casa", "1", "5000000000", "0")], medio="48")
    tope = renta.resumir([grande], [renta.evaluar(grande, CFG_RENTA, False)], CFG_RENTA)[0]
    assert tope.deduccion == Decimal("52374") * 240


def test_nota_credito_reduce_la_base():
    fs = [fac("A1", lineas=[("x", "1", "100000", "0")], medio="48"),
          fac("NC", lineas=[("x", "1", "40000", "0")], medio="48", tipo="CreditNote")]
    r = renta.resumir(fs, [renta.evaluar(f, CFG_RENTA, False) for f in fs], CFG_RENTA)[0]
    assert r.base_cumple == Decimal("60000.00") and r.deduccion == Decimal("600")


def test_reglas_tributarias_declaran_fuente_y_estado():
    for u in CFG_RENTA["uvt"]:
        assert u["fuente"] and u["estado"] in ("verificado", "por_verificar")
        assert u["estado"] == "por_verificar" or (u.get("url") and u.get("consultado"))
    for r in CFG_RENTA["deduccion_factura_electronica"]:
        assert r["fuente"] and r["estado"] in ("verificado", "por_verificar")


# ------------------------------------------------------------------ alertas

def test_alza_de_precio():
    fs = [fac("A1", D(2026, 8, 1), lineas=[("Arroz 5 kg", "1", "17000", "0")]),
          fac("A2", D(2026, 9, 1), lineas=[("ARROZ 5 KG.", "1", "17000", "0")]),      # misma descripción normalizada
          fac("A3", D(2026, 10, 1), lineas=[("Arroz 5 kg", "1", "20000", "0"), ("Sal", "1", "2000", "0")])]
    a = alertas.alzas(fs, 0.10)
    assert len(a) == 1 and a[0].producto == "arroz 5 kg" and a[0].cambio == pytest.approx(3000 / 17000)
    assert alertas.alzas(fs, 0.20) == [] and alertas.alzas(fs[:1], 0.10) == []
    otro_emisor = fs[:2] + [fac("X", D(2026, 10, 1), lineas=[("Arroz 5 kg", "1", "30000", "0")], nit="800999888")]
    assert alertas.alzas(otro_emisor, 0.10) == []                                  # otro emisor no compara


# ---------------------------------------------------------------- revisión cruzada (v0.2)

import datetime as _dt
from decimal import Decimal as _D
from types import SimpleNamespace as _NS

from factura_viva import revision as _rev
from factura_viva.extractos.base import Extracto as _Extracto, Movimiento as _Mov


def _leido(archivo, sha, banco, numero, desde, hasta, ini, fin, movs, producto="cuenta"):
    e = _Extracto(banco, producto, "prueba", desde, hasta, _D(ini), _D(fin), movs, numero_enmascarado=numero)
    return _NS(archivo=archivo, sha256=sha, extracto=e, valido=True, chequeos=[])


def _mov(fecha, desc, valor, saldo=None, clase=""):
    return _Mov(_dt.date.fromisoformat(fecha), desc, _D(valor), _D(saldo) if saldo is not None else None, clase=clase)


def test_archivo_repetido_se_ignora_y_avisa():
    a = _leido("a.pdf", "h1", "Nequi", "····1", _dt.date(2026, 10, 1), _dt.date(2026, 10, 31), "10", "5",
               [_mov("2026-10-05", "COMPRA", "-5", "5")])
    b = _leido("a (1).pdf", "h1", "Nequi", "····1", _dt.date(2026, 10, 1), _dt.date(2026, 10, 31), "10", "5",
               [_mov("2026-10-05", "COMPRA", "-5", "5")])
    unicos, avisos = _rev.quitar_duplicados([a, b])
    assert [u.archivo for u in unicos] == ["a.pdf"]
    assert avisos[0].nivel == "info" and avisos[0].referencia == "a (1).pdf"


def test_mismo_periodo_con_otro_archivo_se_ignora():
    args = ("Nequi", "····1", _dt.date(2026, 10, 1), _dt.date(2026, 10, 31), "10", "5")
    a = _leido("a.pdf", "h1", *args, [_mov("2026-10-05", "COMPRA", "-5", "5")])
    b = _leido("b.pdf", "h2", *args, [_mov("2026-10-05", "COMPRA", "-5", "5")])
    unicos, avisos = _rev.quitar_duplicados([a, b])
    assert len(unicos) == 1 and "dos veces" in avisos[0].asunto


def test_solape_parcial_marca_movimientos_repetidos_y_alerta():
    a = _leido("a.pdf", "h1", "B", "····1", _dt.date(2026, 10, 1), _dt.date(2026, 10, 31), "100", "70",
               [_mov("2026-10-05", "COMPRA X", "-10", "90"), _mov("2026-10-20", "COMPRA Y", "-20", "70")])
    b = _leido("b.pdf", "h2", "B", "····1", _dt.date(2026, 10, 15), _dt.date(2026, 11, 14), "90", "40",
               [_mov("2026-10-20", "COMPRA Y", "-20", "70"), _mov("2026-11-02", "COMPRA Z", "-30", "40")])
    unicos, avisos = _rev.quitar_duplicados([a, b])
    assert len(unicos) == 2 and avisos[0].nivel == "alerta"
    assert [m.clase for m in b.extracto.movimientos] == ["duplicado", ""]
    from factura_viva.extractos.base import gasto_real
    assert gasto_real(a.extracto) + gasto_real(b.extracto) == _D("60")   # 10 + 20 + 30, sin contar dos veces


def test_continuidad_avisa_mes_faltante_y_salto_de_saldo():
    a = _leido("ago.pdf", "h1", "B", "····1", _dt.date(2026, 8, 1), _dt.date(2026, 8, 31), "0", "100", [])
    b = _leido("oct.pdf", "h2", "B", "····1", _dt.date(2026, 10, 1), _dt.date(2026, 10, 31), "100", "50", [])
    c = _leido("nov.pdf", "h3", "B", "····1", _dt.date(2026, 11, 1), _dt.date(2026, 11, 30), "999", "50", [])
    avisos = _rev.continuidad([a, b, c])
    assert [(x.nivel, x.tema) for x in avisos] == [("aviso", "continuidad"), ("alerta", "continuidad")]
    assert "sep 2026" in avisos[0].asunto


def test_plata_entre_cuentas_propias_no_es_gasto_pero_una_compra_si():
    a = _leido("a.pdf", "h1", "A", "····1", _dt.date(2026, 6, 1), _dt.date(2026, 6, 30), "0", "0",
               [_mov("2026-06-10", "ENVIO BRE-B", "-50000", clase="transferencia"),
                _mov("2026-06-10", "COMPRA TIENDA", "-20000", clase="compra")])
    b = _leido("b.pdf", "h2", "B", "····2", _dt.date(2026, 6, 1), _dt.date(2026, 6, 30), "0", "0",
               [_mov("2026-06-10", "RECIBI POR BRE-B", "50000", clase="ingreso"),
                _mov("2026-06-10", "RECIBI POR BRE-B", "20000", clase="ingreso"),
                _mov("2026-06-11", "De OTRA PERSONA", "50000", clase="ingreso_tercero")])
    pares, avisos = _rev.transferencias_propias([a, b])
    assert len(pares) == 1 and pares[0]["valor"] == _D("50000")
    assert [m.clase for m in a.extracto.movimientos] == ["interno", "compra"]
    assert avisos[0].nivel == "info"
