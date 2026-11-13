"""Lector de facturas electrónicas de venta de la DIAN (UBL 2.1).

Lo que llega por correo es un ZIP con dos archivos:

- un XML ``AttachedDocument`` (el "contenedor"), que trae *dentro de un CDATA* la
  factura (``Invoice``), nota crédito (``CreditNote``) o nota débito (``DebitNote``),
  y además la respuesta de validación de la DIAN (``ApplicationResponse``);
- un PDF con la representación gráfica (no se usa para calcular nada).

Este módulo solo lee: nunca descomprime al disco ni modifica ``data/raw``. Cada objeto
devuelto recuerda de qué archivo salió (ruta, SHA-256, archivo dentro del ZIP) para que
toda cifra pueda rastrearse a su fuente.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS = {"cbc": CBC, "cac": CAC}

TAM_MAX_XML = 20 * 1024 * 1024  # protección ante ZIP malicioso ("bomba")
RE_CUFE = re.compile(r"^[0-9a-f]{96}$")  # SHA-384 en hexadecimal

TIPOS = {
    "Invoice": ("factura", "InvoiceLine", "InvoicedQuantity", "LegalMonetaryTotal"),
    "CreditNote": ("nota_credito", "CreditNoteLine", "CreditedQuantity", "LegalMonetaryTotal"),
    "DebitNote": ("nota_debito", "DebitNoteLine", "DebitedQuantity", "RequestedMonetaryTotal"),
}


class FacturaInvalida(ValueError):
    """El archivo no es una factura electrónica legible; el mensaje dice por qué."""


@dataclass(frozen=True)
class Fuente:
    """De dónde salió un dato: archivo crudo, su huella y el XML dentro del ZIP."""

    archivo: str
    sha256: str
    miembro_xml: str
    miembro_pdf: str | None = None


@dataclass
class Impuesto:
    codigo: str            # 01 IVA, 04 INC, 03 ICA...
    nombre: str
    porcentaje: Decimal | None
    base: Decimal | None
    valor: Decimal


@dataclass
class Linea:
    numero: str
    descripcion: str
    cantidad: Decimal
    unidad: str
    precio_unitario: Decimal      # PriceAmount / BaseQuantity
    valor: Decimal                # LineExtensionAmount (antes de impuestos)
    descuentos: Decimal = Decimal(0)
    cargos: Decimal = Decimal(0)
    impuestos: list[Impuesto] = field(default_factory=list)
    codigo_item: str = ""
    gratis: bool = False


@dataclass
class ValidacionDian:
    codigo: str | None            # "02" = documento validado por la DIAN (algunos emisores escriben "002")
    fecha: str | None
    hora: str | None
    validador: str | None

    @property
    def validada(self) -> bool:
        return bool(self.codigo) and self.codigo.strip().lstrip("0") == "2"


@dataclass
class Factura:
    tipo: str                     # factura | nota_credito | nota_debito
    numero: str
    cufe: str                     # CUFE (facturas) o CUDE (notas)
    esquema_cufe: str
    fecha: dt.date
    hora: str
    moneda: str
    emisor_nit: str
    emisor_dv: str
    emisor_nombre: str
    emisor_ciiu: str
    emisor_ciudad: str
    adquiriente_id: str
    adquiriente_nombre: str
    forma_pago: str               # 1 contado, 2 crédito
    medio_pago: str               # código DIAN: 10 efectivo, 48 tarjeta crédito...
    fecha_vencimiento: dt.date | None
    valor_bruto: Decimal          # suma de líneas, antes de impuestos
    base_gravable: Decimal | None
    total_con_impuestos: Decimal
    descuentos: Decimal
    cargos: Decimal
    anticipos: Decimal
    redondeo: Decimal
    total_pagar: Decimal
    impuestos: list[Impuesto]
    retenciones: list[Impuesto]
    lineas: list[Linea]
    lineas_declaradas: int | None
    cufe_contenedor: str | None   # CUFE que declara el AttachedDocument
    numero_contenedor: str | None
    cufe_referencia: str | None   # factura afectada (solo notas)
    validacion: ValidacionDian
    fuente: Fuente

    @property
    def signo(self) -> int:
        """Las notas crédito restan del gasto."""
        return -1 if self.tipo == "nota_credito" else 1

    @property
    def total_impuestos(self) -> Decimal:
        return sum((i.valor for i in self.impuestos), Decimal(0))


# --------------------------------------------------------------------------- XML

def _parsear_xml(datos: bytes, que: str) -> ET.Element:
    """Parsea XML rechazando DOCTYPE/ENTITY (evita ataques de entidades)."""
    if len(datos) > TAM_MAX_XML:
        raise FacturaInvalida(f"{que}: XML demasiado grande ({len(datos)} bytes)")
    cabeza = datos[:4096].upper()
    if b"<!DOCTYPE" in cabeza or b"<!ENTITY" in datos.upper():
        raise FacturaInvalida(f"{que}: el XML declara DOCTYPE/ENTITY y se rechaza por seguridad")
    try:
        return ET.fromstring(datos)
    except ET.ParseError as e:
        raise FacturaInvalida(f"{que}: XML mal formado ({e})") from e


def _local(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _txt(el: ET.Element | None, ruta: str) -> str:
    if el is None:
        return ""
    x = el.find(ruta, NS)
    return (x.text or "").strip() if x is not None else ""


def _dec(el: ET.Element | None, ruta: str, defecto: Decimal | None = Decimal(0)) -> Decimal | None:
    t = _txt(el, ruta)
    if not t:
        return defecto
    try:
        return Decimal(t)
    except InvalidOperation as e:
        raise FacturaInvalida(f"Valor numérico ilegible en {ruta}: {t!r}") from e


def _fecha(t: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(t[:10]) if t else None
    except ValueError:
        return None


def _impuestos(padre: ET.Element, etiqueta: str = "TaxTotal") -> list[Impuesto]:
    """Aplana TaxTotal/TaxSubtotal en una lista de impuestos por tarifa."""
    salida: list[Impuesto] = []
    for tt in padre.findall(f"cac:{etiqueta}", NS):
        subs = tt.findall("cac:TaxSubtotal", NS)
        if not subs:
            salida.append(Impuesto("", "", None, None, _dec(tt, "cbc:TaxAmount")))
            continue
        for st in subs:
            cat = st.find("cac:TaxCategory", NS)
            salida.append(Impuesto(
                codigo=_txt(cat, "cac:TaxScheme/cbc:ID"),
                nombre=_txt(cat, "cac:TaxScheme/cbc:Name"),
                porcentaje=_dec(cat, "cbc:Percent", None),
                base=_dec(st, "cbc:TaxableAmount", None),
                valor=_dec(st, "cbc:TaxAmount"),
            ))
    return salida


def _descuentos_cargos(padre: ET.Element) -> tuple[Decimal, Decimal]:
    desc = carg = Decimal(0)
    for ac in padre.findall("cac:AllowanceCharge", NS):
        monto = _dec(ac, "cbc:Amount")
        if _txt(ac, "cbc:ChargeIndicator").lower() == "true":
            carg += monto
        else:
            desc += monto
    return desc, carg


def _linea(el: ET.Element, etiqueta_cantidad: str) -> Linea:
    item = el.find("cac:Item", NS)
    precio = el.find("cac:Price", NS)
    monto = _dec(precio, "cbc:PriceAmount")
    base = _dec(precio, "cbc:BaseQuantity", Decimal(1)) or Decimal(1)
    cant_el = el.find(f"cbc:{etiqueta_cantidad}", NS)
    desc, carg = _descuentos_cargos(el)
    codigo = ""
    for ruta in ("cac:StandardItemIdentification/cbc:ID", "cac:SellersItemIdentification/cbc:ID"):
        codigo = codigo or _txt(item, ruta)
    return Linea(
        numero=_txt(el, "cbc:ID"),
        descripcion=" ".join(_txt(item, "cbc:Description").split()),
        cantidad=_dec(el, f"cbc:{etiqueta_cantidad}", Decimal(1)),
        unidad=(cant_el.get("unitCode", "") if cant_el is not None else ""),
        precio_unitario=monto / base,
        valor=_dec(el, "cbc:LineExtensionAmount"),
        descuentos=desc,
        cargos=carg,
        impuestos=_impuestos(el),
        codigo_item=codigo,
        gratis=_txt(el, "cbc:FreeOfChargeIndicator").lower() == "true",
    )


def _parte(doc: ET.Element, cual: str) -> dict[str, str]:
    p = doc.find(f"cac:{cual}/cac:Party", NS)
    esquema = p.find("cac:PartyTaxScheme", NS) if p is not None else None
    cid = esquema.find("cbc:CompanyID", NS) if esquema is not None else None
    ident = (cid.text or "").strip() if cid is not None else ""
    if not ident:
        ident = _txt(p, "cac:PartyIdentification/cbc:ID")
    nombre = _txt(esquema, "cbc:RegistrationName") or _txt(p, "cac:PartyName/cbc:Name")
    return {
        "id": ident,
        "dv": (cid.get("schemeID", "") if cid is not None else ""),
        "nombre": " ".join(nombre.split()),
        "ciiu": _txt(p, "cbc:IndustryClassificationCode"),
        "ciudad": _txt(p, "cac:PhysicalLocation/cac:Address/cbc:CityName")
        or _txt(esquema, "cac:RegistrationAddress/cbc:CityName"),
    }


def _documento(doc: ET.Element, fuente: Fuente, contenedor: dict) -> Factura:
    raiz = _local(doc)
    if raiz not in TIPOS:
        raise FacturaInvalida(f"{fuente.archivo}: documento '{raiz}' no soportado")
    tipo, et_linea, et_cant, et_total = TIPOS[raiz]
    uuid = doc.find("cbc:UUID", NS)
    fecha = _fecha(_txt(doc, "cbc:IssueDate"))
    if uuid is None or not (uuid.text or "").strip() or fecha is None:
        raise FacturaInvalida(f"{fuente.archivo}: falta el CUFE o la fecha de expedición")
    emisor = _parte(doc, "AccountingSupplierParty")
    adq = _parte(doc, "AccountingCustomerParty")
    tot = doc.find(f"cac:{et_total}", NS)
    if tot is None:
        raise FacturaInvalida(f"{fuente.archivo}: falta el bloque de totales ({et_total})")
    pm = doc.find("cac:PaymentMeans", NS)
    lcn = _txt(doc, "cbc:LineCountNumeric")
    ref = _txt(doc, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:UUID")
    return Factura(
        tipo=tipo,
        numero=_txt(doc, "cbc:ID"),
        cufe=(uuid.text or "").strip().lower(),
        esquema_cufe=uuid.get("schemeName", ""),
        fecha=fecha,
        hora=_txt(doc, "cbc:IssueTime"),
        moneda=_txt(doc, "cbc:DocumentCurrencyCode") or "COP",
        emisor_nit=emisor["id"], emisor_dv=emisor["dv"], emisor_nombre=emisor["nombre"],
        emisor_ciiu=emisor["ciiu"], emisor_ciudad=emisor["ciudad"],
        adquiriente_id=adq["id"], adquiriente_nombre=adq["nombre"],
        forma_pago=_txt(pm, "cbc:ID"),
        medio_pago=_txt(pm, "cbc:PaymentMeansCode"),
        fecha_vencimiento=_fecha(_txt(pm, "cbc:PaymentDueDate")) or _fecha(_txt(doc, "cbc:DueDate")),
        valor_bruto=_dec(tot, "cbc:LineExtensionAmount"),
        base_gravable=_dec(tot, "cbc:TaxExclusiveAmount", None),
        total_con_impuestos=_dec(tot, "cbc:TaxInclusiveAmount"),
        descuentos=_dec(tot, "cbc:AllowanceTotalAmount"),
        cargos=_dec(tot, "cbc:ChargeTotalAmount"),
        anticipos=_dec(tot, "cbc:PrepaidAmount"),
        redondeo=_dec(tot, "cbc:PayableRoundingAmount"),
        total_pagar=_dec(tot, "cbc:PayableAmount"),
        impuestos=_impuestos(doc),
        retenciones=_impuestos(doc, "WithholdingTaxTotal"),
        lineas=[_linea(l, et_cant) for l in doc.findall(f"cac:{et_linea}", NS)],
        lineas_declaradas=int(lcn) if lcn.isdigit() else None,
        cufe_contenedor=contenedor.get("cufe"),
        numero_contenedor=contenedor.get("numero"),
        cufe_referencia=ref.lower() or None,
        validacion=contenedor.get("validacion") or ValidacionDian(None, None, None, None),
        fuente=fuente,
    )


def leer_xml(datos: bytes, fuente: Fuente) -> Factura:
    """Lee un XML de la DIAN: contenedor ``AttachedDocument`` o documento directo."""
    raiz = _parsear_xml(datos, fuente.archivo)
    if _local(raiz) in TIPOS:
        return _documento(raiz, fuente, {})
    if _local(raiz) != "AttachedDocument":
        raise FacturaInvalida(f"{fuente.archivo}: raíz '{_local(raiz)}' no es una factura DIAN")

    incrustado = _txt(raiz, "cac:Attachment/cac:ExternalReference/cbc:Description")
    if not incrustado:
        raise FacturaInvalida(f"{fuente.archivo}: el contenedor no trae la factura incrustada")
    doc = _parsear_xml(incrustado.encode("utf-8"), fuente.archivo + " (factura incrustada)")

    ref = raiz.find("cac:ParentDocumentLineReference/cac:DocumentReference", NS)
    res = ref.find("cac:ResultOfVerification", NS) if ref is not None else None
    contenedor = {
        "numero": _txt(raiz, "cbc:ParentDocumentID") or None,
        "cufe": (_txt(ref, "cbc:UUID").lower() or None) if ref is not None else None,
        "validacion": ValidacionDian(
            codigo=_txt(res, "cbc:ValidationResultCode") or None,
            fecha=_txt(res, "cbc:ValidationDate") or None,
            hora=_txt(res, "cbc:ValidationTime") or None,
            validador=_txt(res, "cbc:ValidatorID") or None,
        ),
    }
    return _documento(doc, fuente, contenedor)


# --------------------------------------------------------------------------- ZIP

def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def leer_zip(ruta: Path, raiz: Path | None = None) -> Factura:
    """Lee la factura de un ZIP de correo, en memoria y sin tocar el archivo."""
    ruta = Path(ruta)
    nombre = ruta.relative_to(raiz).as_posix() if raiz else ruta.name
    try:
        zf = zipfile.ZipFile(ruta)
    except zipfile.BadZipFile as e:
        raise FacturaInvalida(f"{nombre}: no es un ZIP válido") from e
    with zf:
        xmls = [i for i in zf.infolist() if i.filename.lower().endswith(".xml")]
        pdfs = [i.filename for i in zf.infolist() if i.filename.lower().endswith(".pdf")]
        if not xmls:
            raise FacturaInvalida(f"{nombre}: el ZIP no contiene ningún XML")
        errores = []
        for info in xmls:
            if info.file_size > TAM_MAX_XML:
                errores.append(f"{info.filename}: demasiado grande")
                continue
            fuente = Fuente(nombre, sha256_archivo(ruta), info.filename, pdfs[0] if pdfs else None)
            try:
                return leer_xml(zf.read(info), fuente)
            except FacturaInvalida as e:
                errores.append(str(e))
        raise FacturaInvalida(f"{nombre}: ningún XML es una factura DIAN ({'; '.join(errores)})")


@dataclass
class ResultadoLectura:
    facturas: list[Factura]                      # únicas por CUFE, ordenadas por fecha
    duplicadas: list[tuple[str, str]]            # (archivo repetido, archivo que se conservó)
    errores: list[tuple[str, str]]               # (archivo, motivo)


def leer_carpeta(carpeta: Path, raiz: Path | None = None) -> ResultadoLectura:
    """Lee todos los ZIP/XML de una carpeta y elimina duplicados por CUFE."""
    carpeta = Path(carpeta)
    vistas: dict[str, Factura] = {}
    duplicadas: list[tuple[str, str]] = []
    errores: list[tuple[str, str]] = []
    archivos = sorted(p for p in carpeta.rglob("*") if p.suffix.lower() in (".zip", ".xml"))
    for p in archivos:
        try:
            if p.suffix.lower() == ".zip":
                f = leer_zip(p, raiz)
            else:
                nombre = p.relative_to(raiz).as_posix() if raiz else p.name
                f = leer_xml(p.read_bytes(), Fuente(nombre, sha256_archivo(p), p.name))
        except FacturaInvalida as e:
            errores.append((p.name, str(e)))
            continue
        if f.cufe in vistas:
            duplicadas.append((f.fuente.archivo, vistas[f.cufe].fuente.archivo))
        else:
            vistas[f.cufe] = f
    orden = sorted(vistas.values(), key=lambda f: (f.fecha, f.hora, f.numero))
    return ResultadoLectura(orden, duplicadas, errores)


def cufe_bien_formado(cufe: str) -> bool:
    return bool(RE_CUFE.match(cufe))


__all__ = [
    "Factura", "Linea", "Impuesto", "Fuente", "ValidacionDian", "FacturaInvalida",
    "ResultadoLectura", "leer_xml", "leer_zip", "leer_carpeta", "sha256_archivo",
    "cufe_bien_formado",
]
