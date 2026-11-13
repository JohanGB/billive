"""Datos sintéticos para pruebas y para la demostración pública.

Nada de lo que se genera aquí corresponde a una persona, factura o cuenta real: los
NIT, nombres y valores son inventados y los CUFE son huellas de un texto cualquiera.
Sirve para que alguien que no es el autor pueda ejecutar el proyecto completo sin
datos personales.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import zipfile
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

NS = ('xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
      'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"')
TIPOS = {"Invoice": ("InvoiceLine", "InvoicedQuantity"), "CreditNote": ("CreditNoteLine", "CreditedQuantity")}
C = Decimal("0.01")


def cufe_falso(semilla: str) -> str:
    return hashlib.sha384(semilla.encode("utf-8")).hexdigest()


def factura_xml(numero: str, fecha: dt.date, emisor: str, nit: str, adquiriente: str, adq_id: str,
                lineas: list[tuple[str, str, str, str]], medio: str = "48", tipo: str = "Invoice",
                ciiu: str = "4711", validacion: str | None = "02", cufe: str | None = None,
                ref_cufe: str | None = None, descuadre: str = "0", contenedor: bool = True) -> bytes:
    """Arma un XML DIAN. ``lineas`` = [(descripción, cantidad, precio unitario, % IVA)].

    ``descuadre`` altera el total a pagar para probar que la conciliación lo detecta.
    """
    et_linea, et_cant = TIPOS[tipo]
    cufe = cufe or cufe_falso(f"{numero}|{fecha}|{nit}|{adq_id}")
    bruto = iva_total = Decimal(0)
    bases: dict[str, list[Decimal]] = {}
    xml_lineas = []
    for n, (desc, cant, precio, pct) in enumerate(lineas, start=1):
        valor = (Decimal(cant) * Decimal(precio)).quantize(C)
        iva = (valor * Decimal(pct) / 100).quantize(C)
        bruto += valor
        iva_total += iva
        b = bases.setdefault(pct, [Decimal(0), Decimal(0)])
        b[0] += valor
        b[1] += iva
        impuesto = "" if Decimal(pct) == 0 else f"""
    <cac:TaxTotal><cbc:TaxAmount currencyID="COP">{iva}</cbc:TaxAmount>
      <cac:TaxSubtotal><cbc:TaxableAmount currencyID="COP">{valor}</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="COP">{iva}</cbc:TaxAmount>
        <cac:TaxCategory><cbc:Percent>{pct}</cbc:Percent><cac:TaxScheme><cbc:ID>01</cbc:ID><cbc:Name>IVA</cbc:Name></cac:TaxScheme></cac:TaxCategory>
      </cac:TaxSubtotal></cac:TaxTotal>"""
        xml_lineas.append(f"""
  <cac:{et_linea}><cbc:ID>{n}</cbc:ID><cbc:{et_cant} unitCode="94">{cant}</cbc:{et_cant}>
    <cbc:LineExtensionAmount currencyID="COP">{valor}</cbc:LineExtensionAmount>{impuesto}
    <cac:Item><cbc:Description>{escape(desc)}</cbc:Description></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="COP">{precio}</cbc:PriceAmount><cbc:BaseQuantity unitCode="94">1</cbc:BaseQuantity></cac:Price>
  </cac:{et_linea}>""")
    xml_imp = "".join(f"""
  <cac:TaxTotal><cbc:TaxAmount currencyID="COP">{iva}</cbc:TaxAmount>
    <cac:TaxSubtotal><cbc:TaxableAmount currencyID="COP">{base}</cbc:TaxableAmount><cbc:TaxAmount currencyID="COP">{iva}</cbc:TaxAmount>
      <cac:TaxCategory><cbc:Percent>{pct}</cbc:Percent><cac:TaxScheme><cbc:ID>01</cbc:ID><cbc:Name>IVA</cbc:Name></cac:TaxScheme></cac:TaxCategory>
    </cac:TaxSubtotal></cac:TaxTotal>""" for pct, (base, iva) in bases.items() if Decimal(pct) != 0)
    total = bruto + iva_total
    referencia = f"""
  <cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>REF</cbc:ID><cbc:UUID>{ref_cufe}</cbc:UUID></cac:InvoiceDocumentReference></cac:BillingReference>""" if ref_cufe else ""

    def parte(etiqueta: str, nombre: str, ident: str, extra: str = "") -> str:
        return f"""
  <cac:{etiqueta}><cac:Party>{extra}<cac:PartyName><cbc:Name>{escape(nombre)}</cbc:Name></cac:PartyName>
    <cac:PartyTaxScheme><cbc:RegistrationName>{escape(nombre)}</cbc:RegistrationName>
      <cbc:CompanyID schemeID="1" schemeName="31">{ident}</cbc:CompanyID>
      <cac:TaxScheme><cbc:ID>01</cbc:ID><cbc:Name>IVA</cbc:Name></cac:TaxScheme></cac:PartyTaxScheme></cac:Party></cac:{etiqueta}>"""

    documento = f"""<?xml version="1.0" encoding="UTF-8"?>
<{tipo} xmlns="urn:oasis:names:specification:ubl:schema:xsd:{tipo}-2" {NS}>
  <cbc:UBLVersionID>UBL 2.1</cbc:UBLVersionID><cbc:ID>{numero}</cbc:ID>
  <cbc:UUID schemeName="CUFE-SHA384">{cufe}</cbc:UUID>
  <cbc:IssueDate>{fecha.isoformat()}</cbc:IssueDate><cbc:IssueTime>10:00:00-05:00</cbc:IssueTime>
  <cbc:DocumentCurrencyCode>COP</cbc:DocumentCurrencyCode><cbc:LineCountNumeric>{len(lineas)}</cbc:LineCountNumeric>{referencia}
  {parte("AccountingSupplierParty", emisor, nit, f"<cbc:IndustryClassificationCode>{ciiu}</cbc:IndustryClassificationCode>")}
  {parte("AccountingCustomerParty", adquiriente, adq_id)}
  <cac:PaymentMeans><cbc:ID>1</cbc:ID><cbc:PaymentMeansCode>{medio}</cbc:PaymentMeansCode><cbc:PaymentDueDate>{fecha.isoformat()}</cbc:PaymentDueDate></cac:PaymentMeans>{xml_imp}
  <cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID="COP">{bruto}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="COP">{bruto}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="COP">{total}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="COP">{total + Decimal(descuadre)}</cbc:PayableAmount></cac:LegalMonetaryTotal>{"".join(xml_lineas)}
</{tipo}>"""
    if not contenedor:
        return documento.encode("utf-8")
    verificacion = "" if validacion is None else f"""
      <cac:ResultOfVerification><cbc:ValidatorID>Unidad Especial Dirección de Impuestos y Aduanas Nacionales</cbc:ValidatorID>
        <cbc:ValidationResultCode>{validacion}</cbc:ValidationResultCode><cbc:ValidationDate>{fecha.isoformat()}</cbc:ValidationDate>
        <cbc:ValidationTime>10:00:05-05:00</cbc:ValidationTime></cac:ResultOfVerification>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<AttachedDocument xmlns="urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2" {NS}>
  <cbc:ID>{cufe}</cbc:ID><cbc:IssueDate>{fecha.isoformat()}</cbc:IssueDate><cbc:ParentDocumentID>{numero}</cbc:ParentDocumentID>
  <cac:Attachment><cac:ExternalReference><cbc:MimeCode>text/xml</cbc:MimeCode>
    <cbc:Description><![CDATA[{documento}]]></cbc:Description></cac:ExternalReference></cac:Attachment>
  <cac:ParentDocumentLineReference><cbc:LineID>1</cbc:LineID>
    <cac:DocumentReference><cbc:ID>{numero}</cbc:ID><cbc:UUID schemeName="CUFE-SHA384">{cufe}</cbc:UUID>{verificacion}
    </cac:DocumentReference></cac:ParentDocumentLineReference>
</AttachedDocument>""".encode("utf-8")


def escribir_zip(carpeta: Path, nombre: str, xml: bytes) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"{nombre}.zip"
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{nombre}.xml", xml)
        zf.writestr(f"{nombre}.pdf", b"%PDF-1.4 representacion grafica de ejemplo")
    return ruta


def extracto_pdf(ruta: Path, renglones: list[str], clave: str | None = None) -> Path:
    """PDF de texto (opcionalmente con clave) para probar la lectura. Requiere reportlab."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.pdfencrypt import StandardEncryption
    from reportlab.pdfgen import canvas

    ruta.parent.mkdir(parents=True, exist_ok=True)
    cifrado = StandardEncryption(clave, canPrint=1) if clave else None
    c = canvas.Canvas(str(ruta), pagesize=letter, encrypt=cifrado)
    c.setFont("Courier", 9)
    y = 750
    for r in renglones:
        if y < 50:
            c.showPage()
            c.setFont("Courier", 9)
            y = 750
        c.drawString(40, y, r)
        y -= 13
    c.save()
    return ruta


EXTRACTO_GENERICO = [
    "BANCO DE EJEMPLO S.A.",
    "Extracto de cuenta de ahorros  Periodo: 01/10/2026 al 31/10/2026",
    "SALDO ANTERIOR $ 1.200.000,00",
    "FECHA DESCRIPCION VALOR SALDO",
    "02/10/2026 COMPRA SUPERMERCADO LA CANASTA 95.200,00 1.104.800,00",
    "05/10/2026 ABONO NOMINA EMPRESA DE EJEMPLO 2.000.000,00 3.104.800,00",
    "12/10/2026 PAGO PSE TELEFONIA ANDINA 59.500,00 3.045.300,00",
    "30/10/2026 IMPUESTO 4X1000 618,80 3.044.681,20",
    "SALDO FINAL $ 3.044.681,20",
]


def proyecto_demo(raiz: Path, con_pdf: bool = True) -> Path:
    """Crea en ``raiz`` un proyecto mínimo con datos inventados (config incluida)."""
    import shutil

    origen = Path(__file__).resolve().parents[2] / "config"
    (raiz / "config").mkdir(parents=True, exist_ok=True)
    for y in origen.glob("*.yaml"):
        shutil.copy(y, raiz / "config" / y.name)
    fact = raiz / "data" / "raw" / "facturas"
    hoy = dt.date(2026, 10, 2)
    escribir_zip(fact, "demo1", factura_xml(
        "SUP1001", hoy, "SUPERMERCADO LA CANASTA S.A.S", "900111222", "PERSONA DE EJEMPLO", "1000000001",
        [("Arroz 5 kg", "1", "20000", "0"), ("Detergente 3 L", "2", "30000", "19"), ("Leche caja x 6", "1", "3800", "0")],
        medio="49"))
    escribir_zip(fact, "demo2", factura_xml(
        "TEL2002", dt.date(2026, 10, 10), "TELEFONIA ANDINA S.A.", "800333444", "PERSONA DE EJEMPLO", "1000000001",
        [("Plan pospago 20 GB", "1", "50000", "19")], medio="1", ciiu="6120"))
    escribir_zip(fact, "demo3", factura_xml(
        "SUP0900", dt.date(2026, 9, 3), "SUPERMERCADO LA CANASTA S.A.S", "900111222", "PERSONA DE EJEMPLO", "1000000001",
        [("Arroz 5 kg", "1", "17000", "0")], medio="10"))
    escribir_zip(fact, "demo3_copia", factura_xml(
        "SUP0900", dt.date(2026, 9, 3), "SUPERMERCADO LA CANASTA S.A.S", "900111222", "PERSONA DE EJEMPLO", "1000000001",
        [("Arroz 5 kg", "1", "17000", "0")], medio="10"))
    if con_pdf:
        extracto_pdf(raiz / "data" / "raw" / "extractos" / "banco_ejemplo_2026-10.pdf", EXTRACTO_GENERICO, clave="demo1234")
    return raiz
