"""Nu — extracto de tarjeta de crédito.

Formato validado con un extracto real (corte del 15 de octubre de 2026):
- Página 1: resumen con deuda a pagar este mes, intereses, comisiones, cuota de manejo,
  abonos, devoluciones, deuda restante y deuda total.
- Página 2: tabla de movimientos donde cada fila ocupa varios renglones (el año y parte
  de la descripción bajan al renglón siguiente; los pagos traen su desglose "↪ A capital
  / A intereses / A cargos y comisiones").

Qué se prueba (ver ``validacion.py``):
- la ecuación del resumen impreso;
- compras del periodo = "deuda a pagar este mes" (+ deuda restante);
- cuota de manejo y abonos = sus totales impresos;
- cada pago = capital + intereses + cargos.

Nu no imprime la deuda del corte anterior. Si los pagos del periodo superan los
"abonos" impresos, la diferencia debe coincidir exactamente con uno o varios pagos: esos
se marcan como pago de la deuda anterior y la deuda anterior queda *deducida* (no
impresa). Si no coincide, el extracto no pasa la validación.

Límites declarados (no hay datos reales para validarlos; el extracto va a revisión):
compras a varias cuotas de periodos anteriores, avances e intereses cobrados.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from itertools import combinations

from ...dinero import pesos
from ..base import Extracto, ExtractoIlegible, Movimiento, Pagina
from ..util import MES_RE, buscar_monto, mes, monto, plano, texto

V = r"(-?\$[\d.]+,\d{2})"
RE_CABEZA = re.compile(
    rf"(\d{{2}}) ({MES_RE})\w* (\d{{4}}) (\d{{2}}) ({MES_RE})\w* (\d{{4}}) (\d{{2}}) ({MES_RE})\w* (\d{{4}}) - (\d{{2}}) ({MES_RE})",
    re.I)
RE_FILA = re.compile(rf"^(\d{{2}}) ({MES_RE})\w*(?: (\d{{4}}))? (.+)$", re.I)
RE_ANIO = re.compile(r"^(\d{4})(?: (.+))?$")
RE_DESGLOSE = re.compile(rf"^↪ A (capital|intereses|cargos y comisiones) {V}$", re.I)
RE_VALOR = re.compile(r"-?\$[\d.]+,\d{2}")


class LectorNuTarjeta:
    nombre = "nu_tarjeta"
    banco = "Nu"
    producto = "tarjeta_credito"

    def reconoce(self, paginas: list[Pagina]) -> bool:
        t = plano(texto(paginas))
        return "nu financiera" in t and "fecha de corte" in t and "deuda total" in t

    def interpretar(self, paginas: list[Pagina]) -> Extracto:
        todo = texto(paginas)
        m = RE_CABEZA.search(todo)
        if not m:
            raise ExtractoIlegible("Nu tarjeta: no se encontraron las fechas de corte y periodo")
        desde = dt.date(int(m[9]), mes(m[8]), int(m[7]))
        mes_fin = mes(m[11])
        hasta = dt.date(desde.year + (1 if mes_fin < desde.month else 0), mes_fin, int(m[10]))

        def r(etiqueta: str, que: str) -> Decimal:
            return abs(buscar_monto(rf"{etiqueta}\s*[+-]?\s*{V}", todo, que))

        deuda_mes = r("Deuda a pagar este mes", "deuda a pagar este mes")
        intereses = r(r"^Intereses", "intereses")
        com_avances = r("Comisiones de avances", "comisiones de avances")
        com_servicio = r("Comisiones por servicio", "comisiones por servicio")
        conversion = r("Cargos por conversión", "cargos por conversión")
        cuota = r("Cuota de manejo", "cuota de manejo")
        abonos = r(r"^Abonos", "abonos")
        devoluciones = r("Devoluciones y ajustes a favor", "devoluciones")
        restante = r("Deuda restante", "deuda restante")
        deuda_total = r(r"DEUDA TOTAL HASTA EL [^$]*?", "deuda total")

        filas = self._filas(paginas)
        movimientos: list[Movimiento] = []
        propios: list[tuple[str, Decimal, Decimal]] = [(
            "Resumen impreso: deuda del mes + cargos − abonos − devoluciones + restante = deuda total",
            deuda_total,
            deuda_mes + intereses + com_avances + com_servicio + conversion + cuota - abonos - devoluciones + restante,
        )]
        total_mes_compras = Decimal(0)
        for f in filas:
            if f["anio"] is None:
                raise ExtractoIlegible(f"Nu tarjeta: fila sin año (página {f['pagina']}, renglón {f['renglon']})")
            fecha = dt.date(f["anio"], f["mes"], f["dia"])
            desc = " ".join(f["desc"].split())
            d = plano(desc)
            valores = [monto(x) for x in f["valores"]]
            if not valores:
                raise ExtractoIlegible(f"Nu tarjeta: fila sin valor ({desc})")
            base = dict(pagina=f["pagina"], renglon=f["renglon"], texto_original="\n".join(f["texto"]))
            if d.startswith("pago"):
                movimientos.append(Movimiento(fecha, desc, abs(valores[0]), None, "pago", "pago_recibido", **base))
                if f["desglose"]:
                    propios.append((f"Pago del {fecha.isoformat()}: capital + intereses + cargos = pago",
                                    abs(valores[0]), sum(f["desglose"].values(), Decimal(0))))
            elif "cuota de manejo" in d:
                movimientos.append(Movimiento(fecha, desc, -abs(valores[0]), None, "cuota_manejo", "cuota_manejo", **base))
            elif "avance" in d or d.startswith(("interes", "comision")):
                raise ExtractoIlegible(f"Nu tarjeta: fila de tipo aún no validado con datos reales ({desc})")
            elif d.startswith(("devolucion", "ajuste")):
                movimientos.append(Movimiento(fecha, desc, abs(valores[0]), None, "devolucion", "devolucion", **base))
            else:
                if fecha < desde:
                    raise ExtractoIlegible(
                        f"Nu tarjeta: compra del {fecha.isoformat()} anterior al periodo (cuotas de periodos "
                        "anteriores aún no validadas con datos reales)")
                if len(valores) < 5:
                    raise ExtractoIlegible(f"Nu tarjeta: fila de compra con columnas incompletas ({desc})")
                total_mes_compras += valores[-2]
                movimientos.append(Movimiento(fecha, desc, -abs(valores[0]), None, "compra", "compra", **base))
        propios.append(("Compras: suma de 'total a pagar este mes' = deuda a pagar este mes",
                        deuda_mes, total_mes_compras))

        # Pagos que no cuentan como abono del periodo: pagaron la deuda del corte anterior.
        notas = []
        pagos = sorted((x for x in movimientos if x.tipo == "pago"), key=lambda x: x.fecha)
        exceso = sum((p.valor for p in pagos), Decimal(0)) - abonos
        deuda_anterior = Decimal(0)
        resumen = {"total_compra": deuda_mes + restante, "total_cuota_manejo": cuota, "total_pago": abonos,
                   "total_devolucion": devoluciones, "pago_minimo": deuda_total - restante}
        if exceso > 0:
            elegidos = None
            for k in range(1, len(pagos) + 1):          # primero los pagos más antiguos
                if sum((p.valor for p in pagos[:k]), Decimal(0)) == exceso:
                    elegidos = pagos[:k]
                    break
            if elegidos is None:
                for k in range(1, min(len(pagos), 6) + 1):
                    elegidos = next((list(c) for c in combinations(pagos, k)
                                     if sum((p.valor for p in c), Decimal(0)) == exceso), None)
                    if elegidos:
                        break
            if elegidos:
                for p in elegidos:
                    p.tipo = "pago_deuda_anterior"
                deuda_anterior = exceso
                resumen["deducido_pago_deuda_anterior"] = exceso
                notas.append(
                    f"Nu no imprime la deuda del corte anterior: se dedujo en {pesos(exceso)} porque los pagos del "
                    "periodo superan los abonos impresos exactamente en ese valor. Se confirma con el extracto anterior.")
        return Extracto(
            banco=self.banco, producto=self.producto, lector=self.nombre,
            periodo_desde=desde, periodo_hasta=hasta, saldo_inicial=deuda_anterior, saldo_final=deuda_total,
            movimientos=sorted(movimientos, key=lambda x: (x.fecha, x.renglon)),
            numero_enmascarado="/".join("····" + x for x in re.findall(r"(?:•\s*){4}(\d{4})", todo)),
            resumen=resumen, notas=notas, saldo_inicial_impreso=False, chequeos_propios=propios,
        )

    @staticmethod
    def _filas(paginas: list[Pagina]) -> list[dict]:
        filas: list[dict] = []
        en_tabla = False
        for pag in paginas:
            for n, linea in enumerate(pag.lineas, start=1):
                t = linea.strip()
                if t.startswith("Fecha Descripción Valor"):
                    en_tabla = True
                    continue
                if not en_tabla or t.startswith("del mes Porcentaje"):
                    continue
                if plano(t) == "pago minimo":
                    en_tabla = False
                    continue
                if mm := RE_FILA.match(t):
                    resto = mm[4]
                    corte = resto.find("$")
                    desc = resto if corte < 0 else resto[:corte]
                    desc = desc.rstrip(" -")
                    filas.append({"dia": int(mm[1]), "mes": mes(mm[2]), "anio": int(mm[3]) if mm[3] else None,
                                  "desc": desc, "valores": RE_VALOR.findall(resto), "desglose": {},
                                  "pagina": pag.numero, "renglon": n, "texto": [t]})
                    continue
                if not filas:
                    continue
                fila = filas[-1]
                fila["texto"].append(t)
                if mm := RE_DESGLOSE.match(t):
                    fila["desglose"][plano(mm[1])] = monto(mm[2])
                elif (mm := RE_ANIO.match(t)) and fila["anio"] is None:
                    fila["anio"] = int(mm[1])
                    if mm[2]:
                        fila["desc"] += " " + mm[2]
                elif t and not t.startswith("↪"):
                    fila["desc"] += " " + t
        return filas


LECTOR = LectorNuTarjeta()
