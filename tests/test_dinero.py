from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from factura_viva.dinero import a_decimal, pesos


@pytest.mark.parametrize("texto,valor", [
    ("$1.234.567,89", "1234567.89"), ("1,234,567.89", "1234567.89"), ("-$300.000,00", "-300000.00"),
    ("45.000-", "-45000"), ("(45.000)", "-45000"), ("+$11.000,00", "11000.00"), ("$0.35", "0.35"),
    ("$-0.35", "-0.35"), ("18.91", "18.91"), ("1.234", "1234"), ("45,000", "45000"), ("5,000.00", "5000.00"),
    ("12,5", "12.5"), ("$ 977,36", "977.36"), ("7000", "7000"),
])
def test_formatos_colombianos(texto, valor):
    assert a_decimal(texto) == Decimal(valor)


@pytest.mark.parametrize("texto", ["", None, "abc", "12a", "1.23.4", "$", "--", "2026-10-01", "3/4"])
def test_lo_que_no_es_dinero_devuelve_none(texto):
    assert a_decimal(texto) is None


@given(st.decimals(min_value=Decimal("-99999999999"), max_value=Decimal("99999999999"), places=2))
def test_propiedad_ida_y_vuelta(v):
    assert a_decimal(pesos(v)) == v


def test_pesos_para_mostrar():
    assert pesos(Decimal("217605.13")) == "$217.605,13" and pesos(Decimal("-5000")) == "-$5.000" and pesos(None) == "—"
