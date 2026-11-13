"""Lectores de extractos y validación de saldos. Todos los textos son inventados:
imitan el formato de cada banco, no los datos de nadie."""
import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from factura_viva.dinero import pesos
from factura_viva.extractos import catalogo, validacion
from factura_viva.extractos.base import Extracto, ExtractoIlegible, Movimiento, Pagina, gasto_real
from factura_viva.extractos.generico import LECTOR as GENERICO
from factura_viva.extractos.pdf import ClaveIncorrecta, ClaveRequerida, leer_paginas
from factura_viva.sinteticos import EXTRACTO_GENERICO, extracto_pdf


def pag(*bloques: str) -> list[Pagina]:
    return [Pagina(i, tuple(b.strip("\n").splitlines())) for i, b in enumerate(bloques, start=1)]


def leer(paginas):
    lector = catalogo.elegir(paginas)
    e = lector.interpretar(paginas)
    return e, validacion.validar(e)


# ------------------------------------------------------------------ genérico y validación

def test_catalogo_descubre_las_cuatro_plantillas():
    assert {l.nombre for l in catalogo.lectores()} >= {"av_villas", "caja_social", "nu_cuenta", "nu_tarjeta"}


def test_generico_con_saldo_por_renglon_cuadra():
    e, ch = leer(pag("\n".join(EXTRACTO_GENERICO)))
    assert e.lector == "generico" and len(e.movimientos) == 4
    assert [m.valor for m in e.movimientos] == [Decimal("-95200.00"), Decimal("2000000.00"),
                                                Decimal("-59500.00"), Decimal("-618.80")]
    assert (e.periodo_desde, e.periodo_hasta) == (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert validacion.valido(ch)
    assert e.movimientos[0].pagina == 1 and e.movimientos[0].renglon == 5     # rastreable al renglón


def test_generico_sin_saldo_por_renglon_usa_signos_y_palabras():
    e, ch = leer(pag("""
Banco Nuevo  Periodo 01/03/2026 - 31/03/2026
Saldo inicial 500.000,00
03/03/2026 COMPRA DROGUERIA -20.000,00
10/03/2026 CONSIGNACION EN OFICINA 100.000,00
15/03/2026 RETIRO CAJERO 50.000,00
Saldo final 530.000,00
"""))
    assert [m.valor for m in e.movimientos] == [Decimal("-20000.00"), Decimal("100000.00"), Decimal("-50000.00")]
    assert validacion.valido(ch)


def test_si_falta_un_renglon_los_saldos_no_cuadran_y_no_es_valido():
    texto = [r for r in EXTRACTO_GENERICO if "TELEFONIA" not in r]
    e, ch = leer(pag("\n".join(texto)))
    assert not validacion.valido(ch)
    assert {c.codigo for c in ch if not c.ok} >= {"E3"}


def test_sin_saldos_impresos_no_hay_prueba():
    e, ch = leer(pag("""
Banco Sin Saldos
03/03/2026 COMPRA UNO 20.000,00
04/03/2026 COMPRA DOS 30.000,00
"""))
    assert not validacion.valido(ch) and not ch[0].ok and ch[0].codigo == "E1"


def test_texto_sin_movimientos_es_ilegible():
    with pytest.raises(ExtractoIlegible):
        GENERICO.interpretar(pag("Hola\nEsto no es un extracto"))


@settings(max_examples=60, deadline=None)
@given(inicial=st.integers(0, 10**8), valores=st.lists(st.integers(-10**6, 10**6).filter(lambda v: v != 0),
                                                         min_size=1, max_size=25))
def test_propiedad_generico_recupera_cualquier_extracto_con_saldo(inicial, valores):
    saldo, renglones = Decimal(inicial), [f"SALDO ANTERIOR {pesos(Decimal(inicial))}"]
    for i, v in enumerate(valores):
        saldo += v
        renglones.append(f"{1 + i % 28:02d}/05/2026 MOVIMIENTO {chr(65 + i % 26)} {pesos(abs(Decimal(v)))} {pesos(saldo)}")
    renglones.append(f"SALDO FINAL {pesos(saldo)}")
    e, ch = leer(pag("\n".join(renglones)))
    assert [m.valor for m in e.movimientos] == [Decimal(v) for v in valores]
    assert validacion.valido(ch)


@settings(max_examples=40, deadline=None)
@given(valores=st.lists(st.integers(-10**6, 10**6), min_size=1, max_size=15), error=st.integers(2, 10**5))
def test_propiedad_un_saldo_final_errado_nunca_es_valido(valores, error):
    movs = [Movimiento(dt.date(2026, 1, 1), "x", Decimal(v)) for v in valores]
    e = Extracto("b", "cuenta", "p", None, None, Decimal(1000), Decimal(1000 + sum(valores) + error), movs)
    assert not validacion.valido(validacion.validar(e))


# ------------------------------------------------------------------ plantillas (formato de cada banco)

CAJA_SOCIAL = ("""
BC Extracto Individual
Cuenta de Ahorros
PERSONA DE EJEMPLO
Periodo del Informe
Cuentamiga 1 de Marzo a 31 de Marzo de 2025
999**9*9*9**9*1234
Saldo Anterior Más Créditos y Depósitos Menos Débitos y Retiros Intereses del Periodo Saldo Bolsillo Nuevo Saldo
100,000.00 270,010.00 -85,000.00 10.00 40,000.00 285,010.00
Fecha Transacción Documento Lugar Débitos Créditos Saldos
MAR 01 RETIRO RED PROPIA 11111111 CENTRO COMERCIAL 50,000.00 50,000.00
MAR 02 ABONO NOMINA Y O PROVEEDO 22222222 ACH 250,000.00 300,000.00
EMPRESA DE EJEMPLO
MAR 03 RECIBIDO DE CUENTA 12345678 APP MOBILE 20,000.00 320,000.00
MAR 03 TRASLADO HACIA BOLSILLO 12345678 APP MOBILE 20,000.00 300,000.00
MAR 05 COMPRA INTERNET 3333333 INTERNET 15,000.00 285,000.00
Continua en la siguiente página
Banco Caja Social NIT. 860.007.335-4 informa...
""", """
BC
Continuación Cuentamiga
Fecha Transacción Documento Lugar Débitos Créditos Saldos
4444444 Pago de servicio por PSE
MAR 31 ABONO DE INTERESES 12345678 OFICINA 10.00 285,010.00
Pag. 2 de 2
""")


def test_caja_social():
    e, ch = leer(pag(*CAJA_SOCIAL))
    assert (e.lector, e.banco, e.producto) == ("caja_social", "Banco Caja Social", "cuenta")
    assert (e.periodo_desde, e.periodo_hasta) == (dt.date(2025, 3, 1), dt.date(2025, 3, 31))
    assert (e.saldo_inicial, e.saldo_final) == (Decimal("100000.00"), Decimal("285010.00"))
    assert e.numero_enmascarado == "····1234"
    assert [m.valor for m in e.movimientos] == [Decimal(x) for x in
                                                ("-50000.00", "250000.00", "20000.00", "-20000.00", "-15000.00", "10.00")]
    assert [m.clase for m in e.movimientos] == ["retiro", "ingreso", "interno", "interno", "compra", "rendimiento"]
    assert "EMPRESA DE EJEMPLO" in e.movimientos[1].descripcion          # detalle del renglón siguiente
    assert "Pago de servicio por PSE" in e.movimientos[4].descripcion    # detalle que cruza de página
    assert validacion.valido(ch) and gasto_real(e) == Decimal("65000.00")  # sin el traslado a bolsillo


def test_caja_social_renglon_que_no_cuadra_con_su_saldo_se_rechaza():
    malo = CAJA_SOCIAL[0].replace("15,000.00 285,000.00", "15,000.00 280,000.00")
    with pytest.raises(ExtractoIlegible, match="no cuadra con su saldo"):
        catalogo.elegir(pag(malo, CAJA_SOCIAL[1])).interpretar(pag(malo, CAJA_SOCIAL[1]))


NU_CUENTA = ("""
Hola, Persona
Llegó tu extracto de Marzo
Persona De Ejemplo
Calle Falsa 123
CC-1000000001
Nu Placa Número de Cuenta Período
ABC123 12345678 01 - 31 MAR 2026
Tu dinero al inicio del mes $500.000,00
Lo que entró a tu cuenta +$120.000,00
Lo que salió de tu cuenta -$220.000,00
Impuesto del 4x1000 -$480,00
Costos por retiro $0,00
Rendimiento total de tu cuenta +$1.500,00
Tu dinero a final del mes $401.020,00
Nu Financiera
1/2
""", """
Movimientos
03 mar Pagaste tu tarjeta -$100.000,00
Impuesto del 4x1000 -$400,00
10 mar Recibiste de Persona De Ejemplo +$20.000,00
12 mar Recibiste de Otra Persona +$100.000,00
15 mar Enviaste a Persona De Ejemplo -$100.000,00
20 mar Enviaste a Tienda La Esquina -$20.000,00
Impuesto del 4x1000 -$80,00
Rendimiento total de tu cuenta +$1.500,00
¿Tienes preguntas sobre tu extracto?
Nu Financiera
2 / 2
""")


def test_nu_cuenta():
    e, ch = leer(pag(*NU_CUENTA))
    assert (e.lector, e.producto, e.numero_enmascarado) == ("nu_cuenta", "cuenta", "····5678")
    assert (e.saldo_inicial, e.saldo_final) == (Decimal("500000.00"), Decimal("401020.00"))
    assert len(e.movimientos) == 8 and validacion.valido(ch)
    por_clase = {m.descripcion: m.clase for m in e.movimientos}
    assert por_clase["Pagaste tu tarjeta"] == "pago_tarjeta_propia"
    assert por_clase["Enviaste a Persona De Ejemplo"] == "interno"        # transferencia a sí mismo
    assert por_clase["Enviaste a Tienda La Esquina"] == "transferencia"
    assert e.movimientos[1].fecha == dt.date(2026, 3, 3)                  # el 4x1000 hereda la fecha
    assert gasto_real(e) == Decimal("20480.00")                           # 20.000 + 4x1000 (400 + 80)


def test_nu_cuenta_si_se_pierde_un_movimiento_no_valida():
    recortado = NU_CUENTA[1].replace("20 mar Enviaste a Tienda La Esquina -$20.000,00\n", "")
    e, ch = leer(pag(NU_CUENTA[0], recortado))
    assert not validacion.valido(ch)


NU_TARJETA = ("""
Hola, Persona
Persona De Ejemplo Tus tarjetas
CC 1000000001 Tarjeta física Tarjeta virtual
Calle Falsa 123 • • • • 1111 • • • • 2222
Fecha límite de pago Fecha de corte Periodo facturado
04 ABR 2026 15 MAR 2026 14 FEB 2026 - 14 MAR
Resumen de tu extracto
Deuda a pagar este mes $150.000,00
Intereses + $0,00
Comisiones de avances + $0,00
$50.000,00 $550.000,00 Comisiones por servicio + $0,00
Cargos por conversión + $0,00
Cuota de manejo + $8.000,00
Abonos - $100.000,00
Devoluciones y ajustes a favor - $0,00
PAGO MÍNIMO $58.000,00
Deuda restante + $0,00
DEUDA TOTAL HASTA EL 14 MARZO $58.000,00
Nu Financiera
1 / 2
""", """
Fecha Descripción Valor Cuotas Valor Interés del mes Total a pagar Restante
del mes Porcentaje y valor este mes por pagar
10 MAR Cobro por cuota $8.000,00 - $8.000,00 - - $8.000,00
2026 de manejo
05 MAR Pago $100.000,00 -$100.000,00 $0,00
↪ A capital $100.000,00
2026
↪ A intereses $0,00
↪ A cargos y comisiones $0,00
01 MAR Tienda La $120.000,00 $120.000,00 2.10% $0,00 $120.000,00 $0,00
2026 Esquina
20 FEB 2026 Cine Central $30.000,00 $30.000,00 2.10% $0,00 $30.000,00 $0,00
15 FEB 2026 Pago $25.000,00 $0,00 $0,00
↪ A capital $25.000,00
↪ A intereses $0,00
↪ A cargos y comisiones $0,00
Pago mínimo
$58.000,00
¿Tienes preguntas sobre tu extracto?
""")


def test_nu_tarjeta():
    e, ch = leer(pag(*NU_TARJETA))
    assert (e.lector, e.producto) == ("nu_tarjeta", "tarjeta_credito")
    assert (e.periodo_desde, e.periodo_hasta) == (dt.date(2026, 2, 14), dt.date(2026, 3, 14))
    assert e.numero_enmascarado == "····1111/····2222"
    assert [(m.fecha.isoformat(), m.descripcion, m.valor, m.tipo) for m in e.movimientos] == [
        ("2026-02-15", "Pago", Decimal("25000.00"), "pago_deuda_anterior"),
        ("2026-02-20", "Cine Central", Decimal("-30000.00"), "compra"),
        ("2026-03-01", "Tienda La Esquina", Decimal("-120000.00"), "compra"),
        ("2026-03-05", "Pago", Decimal("100000.00"), "pago"),
        ("2026-03-10", "Cobro por cuota de manejo", Decimal("-8000.00"), "cuota_manejo"),
    ]
    # Deuda anterior deducida: pagos 125.000 − abonos impresos 100.000 = 25.000 (no impresa por Nu)
    assert e.saldo_inicial == Decimal("25000.00") and not e.saldo_inicial_impreso
    assert e.saldo_final == Decimal("58000.00") and validacion.valido(ch)
    assert gasto_real(e) == Decimal("158000.00")


def test_nu_tarjeta_pagos_que_no_explican_los_abonos_impresos_no_valida():
    # Abonos impresos 110.000 (el resumen sigue cuadrando: 150 + 8 − 110 = 48), pero los pagos leídos
    # son 100.000 y 25.000: ninguna combinación explica el exceso de 15.000.
    otro = (NU_TARJETA[0].replace("Abonos - $100.000,00", "Abonos - $110.000,00")
            .replace("PAGO MÍNIMO $58.000,00", "PAGO MÍNIMO $48.000,00")
            .replace("14 MARZO $58.000,00", "14 MARZO $48.000,00"))
    e, ch = leer(pag(otro, NU_TARJETA[1]))
    assert not validacion.valido(ch)
    assert any(c.codigo == "E4" and "pago" in c.nombre and not c.ok for c in ch)


def test_nu_tarjeta_casos_no_validados_van_a_revision():
    avance = NU_TARJETA[1].replace("20 FEB 2026 Cine Central", "20 FEB 2026 Avance en cajero")
    with pytest.raises(ExtractoIlegible, match="no validado"):
        catalogo.elegir(pag(NU_TARJETA[0], avance)).interpretar(pag(NU_TARJETA[0], avance))


AV_VILLAS = """
PERSONA DE EJEMPLO
EXTRACTO TARJETA DE CRÉDITO N° : 5400000000001234
NUMERO COMPROBANTE DD MM AA DETALLE INTERESES VALOR COMPRA CARGOS Y ABONOS SALDO DIFERIDO
lSI EL VALOR DE SU PAGO ES:
Información sobre el Pago Mínimo Reducido en www.avvillas.com.co.
FECHA FACTURACIÓN D2D6 MM03 A2A0A2A6
CUPO TOTAL SALDO TOTAL
$500,000.00 $00.00
SALDO A SU FAVOR PAGUE HASTA
4 000000 PAGINA 1 / 1 $1,200.00 1D7D 04MM A2A02A6A
RESUMEN PAGO MINIMO RESUMEN SALDO TOTAL
(+) SALDOS MORA $0.00 SALDO ANTERIOR $-1,200.00
(+) CARGOS COMPRAS MES $0.00 (+) TOTAL COMPRAS MES $0.00
(+) CARGOS AVANCES MES $0.00 (+) AVANCES $0.00
(+) INTERESES CORRIENTES $0.00 (+) INTERESES CORRIENTES $0.00
(+) INTERESES DE MORA (0.00% E.A) $0.00 (+) INTERESES MORA (0.00 % E.A) $0.00
(+) HONORARIOS $0.00 (+) HONORARIOS $0.00
(+) CARGOS LEGALES $0.00 (+) GASTOS LEGALES $0.00
(+) CARGOS NO DIFERIDOS $0.00 (+) CARGOS NO DIFERIDOS $0.00
(-) PAGOS Y NOTAS CREDITO $0.00
TOTAL PAGO MINIMO $0.00 SALDO TOTAL $0.00 TOTAL PAGO MINIMO REDUCIDO $0.00
"""


def test_av_villas_sin_movimientos():
    e, ch = leer(pag(AV_VILLAS))
    assert (e.lector, e.producto, e.numero_enmascarado) == ("av_villas", "tarjeta_credito", "····1234")
    assert e.periodo_hasta == dt.date(2026, 3, 26)                    # fecha entrelazada con "DD MM AAAA"
    assert e.saldo_inicial == e.saldo_final == Decimal("-1200.00")    # saldo a favor
    assert e.movimientos == [] and validacion.valido(ch)


def test_av_villas_con_movimientos_va_a_revision_en_vez_de_adivinar():
    con_fila = AV_VILLAS.replace("lSI EL VALOR", "123456 05 03 26 COMPRA TIENDA 50,000.00 50,000.00\nlSI EL VALOR")
    with pytest.raises(ExtractoIlegible, match="aún no"):
        catalogo.elegir(pag(con_fila)).interpretar(pag(con_fila))


def test_ningun_numero_de_producto_queda_completo():
    for paginas in (pag(*CAJA_SOCIAL), pag(*NU_CUENTA), pag(*NU_TARJETA), pag(AV_VILLAS)):
        e, _ = leer(paginas)
        assert all(len(p.replace("·", "")) == 4 for p in e.numero_enmascarado.split("/"))


# ------------------------------------------------------------------ PDF con clave

def test_pdf_con_clave(tmp_path):
    pytest.importorskip("reportlab")
    ruta = extracto_pdf(tmp_path / "e.pdf", EXTRACTO_GENERICO, clave="secreta123")
    with pytest.raises(ClaveRequerida):
        leer_paginas(ruta)
    with pytest.raises(ClaveIncorrecta) as exc:
        leer_paginas(ruta, "otra")
    assert "otra" not in str(exc.value)                              # la clave nunca sale en el error
    paginas = leer_paginas(ruta, "secreta123")
    e, ch = leer(paginas)
    assert len(e.movimientos) == 4 and validacion.valido(ch)
    assert leer_paginas(extracto_pdf(tmp_path / "libre.pdf", EXTRACTO_GENERICO))[0].lineas[0] == "BANCO DE EJEMPLO S.A."
