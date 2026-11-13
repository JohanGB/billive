"""Tablero de gasto: una página HTML que se abre sin internet.

Principios de diseño (ver PROMPT_MAESTRO, sección 6.5): una pregunta principal
(¿en qué se fue la plata?), máximo tres gráficas, primero para celular. Cada cifra
tiene debajo su tabla de detalle con el archivo crudo, el SHA-256 y el CUFE o el
renglón del extracto de donde salió.

El archivo contiene datos personales: vive en ``data/procesado`` y no se publica.
"""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from .extractos.base import NO_ES_GASTO, gasto_real
from .extractos.util import enmascarar, enmascarar_texto
from .facturas import conciliacion

if TYPE_CHECKING:
    from .pipeline import Resultado


def _n(v) -> float:
    return float(v) if v is not None else 0.0


def datos_tablero(res: "Resultado") -> dict:
    """Todo lo que el tablero muestra, ya calculado (el navegador solo filtra y dibuja)."""
    cruce = {c.cufe: c for c in res.cruces}
    renta = {e.cufe: e for e in res.renta_facturas}
    cats: dict[str, set] = defaultdict(set)
    for l in res.lineas:
        cats[l["cufe"]].add(l["categoria"])
    facturas = [{
        "cufe": f.cufe, "numero": f.numero, "tipo": f.tipo, "fecha": f.fecha.isoformat(),
        "emisor": f.emisor_nombre, "persona": enmascarar(f.adquiriente_id), "total": _n(f.signo * f.total_pagar),
        "categorias": sorted(cats[f.cufe]),
        "conciliada": conciliacion.conciliada(res.chequeos[f.cufe]),
        "pago": cruce[f.cufe].estado, "renta": renta[f.cufe].estado, "renta_motivo": renta[f.cufe].motivo,
        "archivo": f.fuente.archivo, "sha256": f.fuente.sha256,
    } for f in res.facturas]
    nombres: dict[str, str] = {}
    for f in res.facturas:
        if len(f.adquiriente_nombre) > len(nombres.get(f.adquiriente_id, "")):
            nombres[f.adquiriente_id] = f.adquiriente_nombre
    lineas = [{"cufe": l["cufe"], "persona": l["adquiriente_id"],  # ya enmascarado en el proceso "fecha": l["fecha"], "emisor": l["emisor_nombre"],
               "descripcion": l["descripcion"], "categoria": l["categoria"], "regla": l["regla_categoria"],
               "gasto": _n(l["gasto"]), "linea": l["linea"]} for l in res.lineas]
    extractos = []
    for x in res.extractos:
        e = x.extracto
        extractos.append({
            "banco": e.banco, "producto": "tarjeta de crédito" if e.producto == "tarjeta_credito" else "cuenta",
            "numero": e.numero_enmascarado,
            "desde": e.periodo_desde.isoformat() if e.periodo_desde else None,
            "hasta": e.periodo_hasta.isoformat() if e.periodo_hasta else None,
            "valido": x.valido, "movimientos": len(e.movimientos), "gasto": _n(gasto_real(e)),
            "interno": _n(sum((-m.valor for m in e.movimientos if m.valor < 0 and m.clase in NO_ES_GASTO), Decimal(0))),
            "chequeos": [{"nombre": c.nombre, "ok": c.ok, "esperado": c.esperado, "obtenido": c.obtenido,
                          "nota": c.nota} for c in x.chequeos],
            "notas": e.notas, "archivo": x.archivo, "sha256": x.sha256,
            "detalle": [{"fecha": m.fecha.isoformat(), "descripcion": enmascarar_texto(m.descripcion), "valor": _n(m.valor),
                         "clase": m.clase, "pagina": m.pagina, "renglon": m.renglon}
                        for m in e.movimientos] if x.valido else [],
        })
    return {
        "resumen": res.resumen,
        "personas": [{"id": enmascarar(k), "nombre": v.title()} for k, v in sorted(nombres.items(), key=lambda kv: kv[1])],
        "facturas": facturas, "lineas": lineas, "extractos": extractos,
        "cola": res.cola_revision,
        "avisos": [vars(a) for a in res.avisos],
        "renta": [{"anio": r.anio, "persona": enmascarar(r.adquiriente_id), "nombre": r.adquiriente_nombre.title(),
                   "cumple": r.facturas_cumple, "por_probar": r.facturas_por_probar,
                   "no_cumple": r.facturas_no_cumple, "base_cumple": _n(r.base_cumple),
                   "base_por_probar": _n(r.base_por_probar), "deduccion": _n(r.deduccion),
                   "potencial": _n(r.deduccion_potencial), "sobre_total": _n(r.deduccion_sobre_total),
                   "tope": _n(r.tope) if r.tope is not None else None, "uvt": _n(r.uvt) if r.uvt else None,
                   "estado_uvt": r.estado_uvt, "notas": r.notas} for r in res.renta_resumen],
        "alertas": [{"emisor": a.emisor, "producto": a.producto, "antes": _n(a.precio_anterior),
                     "ahora": _n(a.precio_actual), "cambio": a.cambio, "fecha": a.fecha_actual}
                    for a in res.alertas],
        "fuentes_renta": {
            "regla": res.cfg_renta.get("deduccion_factura_electronica", [{}])[-1].get("fuente", ""),
            "estado_regla": res.cfg_renta.get("deduccion_factura_electronica", [{}])[-1].get("estado", ""),
            "prueba": res.cfg_renta.get("prueba_de_pago", {}).get("fuente", ""),
        },
    }


def escribir_tablero(res: "Resultado", ruta: Path) -> None:
    carga = json.dumps(datos_tablero(res), ensure_ascii=False).replace("</", "<\\/")
    ruta.write_text(PLANTILLA.replace("/*DATOS*/null", carga), encoding="utf-8")


PLANTILLA = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Factura Viva — ¿en qué se fue la plata?</title>
<style>
:root{color-scheme:light;--fondo:#f4f3ef;--sup:#fcfcfb;--tinta:#0b0b0b;--tinta2:#52514e;--tenue:#898781;
  --linea:#e1e0d9;--serie:#2a78d6;--ok:#006300;--aviso:#8a5a00;--mal:#b02a2a;--chip:#eceae3}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--fondo:#121211;--sup:#1a1a19;--tinta:#f0efec;
  --tinta2:#c3c2b7;--tenue:#898781;--linea:#2c2c2a;--serie:#3987e5;--ok:#0ca30c;--aviso:#fab219;--mal:#e66767;--chip:#2c2c2a}}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--tinta);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px 16px 48px}
header h1{font-size:1.05rem;margin:8px 0 0;letter-spacing:.02em;text-transform:uppercase;color:var(--tinta2);font-weight:600}
header p{margin:2px 0 0;color:var(--tenue);font-size:.8rem}
section{background:var(--sup);border:1px solid var(--linea);border-radius:14px;padding:18px 16px;margin-top:16px}
h2{font-size:1.3rem;line-height:1.25;margin:0 0 4px;text-wrap:balance}
h3{font-size:.95rem;margin:20px 0 8px;color:var(--tinta2);font-weight:600}
.sub{color:var(--tinta2);font-size:.9rem;margin:0 0 12px}
.heroe{font-size:2.3rem;font-weight:700;line-height:1.1;margin:10px 0 2px;font-variant-numeric:tabular-nums}
.filtros{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.filtros button{border:1px solid var(--linea);background:var(--sup);color:var(--tinta);border-radius:999px;
  padding:7px 14px;font:inherit;font-size:.88rem;cursor:pointer;min-height:38px}
.filtros button[aria-pressed=true]{background:var(--tinta);color:var(--sup);border-color:var(--tinta)}
.barras{display:grid;gap:10px;margin:8px 0 4px}
.barra{display:grid;grid-template-columns:1fr;gap:3px;align-items:center;position:relative;
  border-radius:6px;padding:2px 0}
@media (min-width:560px){.barra{grid-template-columns:minmax(150px,36%) 1fr;gap:12px}}
.barra:hover,.barra:focus-within{background:var(--chip)}
.barra .nom{font-size:.88rem;color:var(--tinta);overflow-wrap:anywhere}
.barra .nom small{display:block;color:var(--tenue);font-size:.76rem}
.barra .pista{display:flex;align-items:center;gap:8px;min-width:0}
.barra .marca{height:18px;background:var(--serie);border-radius:0 4px 4px 0;min-width:2px;flex:0 0 auto}
.barra .val{font-size:.86rem;color:var(--tinta2);font-variant-numeric:tabular-nums;white-space:nowrap}
.barra .tip{display:none;position:absolute;left:8px;top:100%;z-index:5;background:var(--tinta);color:var(--sup);
  font-size:.8rem;padding:6px 9px;border-radius:8px;max-width:92%;pointer-events:none}
.barra:hover .tip,.barra:focus-within .tip{display:block}
.fichas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:10px}
.ficha{border:1px solid var(--linea);border-radius:10px;padding:10px 12px}
.ficha b{display:block;font-size:1.25rem;font-variant-numeric:tabular-nums}
.ficha span{color:var(--tinta2);font-size:.82rem}
.estado{font-weight:600;font-size:.85rem;white-space:nowrap}
.estado.ok{color:var(--ok)}.estado.aviso{color:var(--aviso)}.estado.mal{color:var(--mal)}
details{margin-top:12px;border-top:1px solid var(--linea);padding-top:8px}
summary{cursor:pointer;color:var(--tinta2);font-size:.88rem;padding:6px 0;min-height:34px}
.tabla{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.8rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--linea);vertical-align:top}
th{color:var(--tenue);font-weight:600;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
code{font-size:.74rem;color:var(--tinta2);overflow-wrap:anywhere}
.nota{font-size:.82rem;color:var(--tinta2);margin:10px 0 0}
ul.lista{margin:8px 0 0;padding-left:18px;font-size:.88rem}
footer{color:var(--tenue);font-size:.78rem;margin-top:20px;text-align:center}
</style>
</head>
<body>
<main>
<header>
  <h1>Factura Viva</h1>
  <p id="meta"></p>
  <div class="filtros" id="filtros" role="group" aria-label="Filtrar por persona"></div>
</header>

<section aria-labelledby="t1">
  <h2 id="t1">¿En qué se fue la plata?</h2>
  <p class="sub" id="sub1"></p>
  <div class="heroe" id="total"></div>
  <p class="sub" id="total-sub"></p>
  <h3>Gasto facturado por categoría</h3>
  <div class="barras" id="g-cat"></div>
  <h3>Gasto facturado por mes</h3>
  <div class="barras" id="g-mes"></div>
  <details><summary>Ver cada línea de factura y de dónde sale</summary><div class="tabla" id="t-lineas"></div></details>
</section>

<section aria-labelledby="t2">
  <h2 id="t2">¿Cuánto salió de verdad de las cuentas?</h2>
  <p class="sub">Salidas de cada extracto sin contar la plata que solo se movió entre productos propios
    (bolsillos, pago de la tarjeta propia). No se filtra por persona.</p>
  <div class="barras" id="g-ext"></div>
  <div id="ext-estado"></div>
  <details><summary>Ver los movimientos leídos y su renglón en el PDF</summary><div class="tabla" id="t-mov"></div></details>
</section>

<section aria-labelledby="t3">
  <h2 id="t3">Renta: deducción del 1 % por compras con factura electrónica</h2>
  <p class="sub">Estimación para preparar la declaración; no es asesoría tributaria.</p>
  <div id="renta"></div>
</section>

<section aria-labelledby="t4">
  <h2 id="t4">¿Se puede confiar en estas cifras?</h2>
  <div class="fichas" id="calidad"></div>
  <div id="avisos"></div>
  <div id="alertas"></div>
  <details><summary>Ver cada factura con su archivo, huella SHA-256 y CUFE</summary><div class="tabla" id="t-fact"></div></details>
</section>

<footer id="pie"></footer>
</main>
<script>
const D = /*DATOS*/null;
const cop = new Intl.NumberFormat('es-CO',{style:'currency',currency:'COP',maximumFractionDigits:0});
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const corto = s => s ? s.slice(0,10) + '…' : '';
const MESES = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
const mesTxt = ym => MESES[+ym.slice(5,7)-1] + ' ' + ym.slice(0,4);
const fechaTxt = f => f ? (+f.slice(8,10)) + ' ' + MESES[+f.slice(5,7)-1] + ' ' + f.slice(0,4) : '';
let persona = '';

function barras(id, filas, vacio){
  const max = Math.max(...filas.map(f => f.v), 0);
  $(id).innerHTML = filas.length ? filas.map(f => `
    <div class="barra" tabindex="0">
      <div class="nom">${esc(f.n)}${f.s ? `<small>${esc(f.s)}</small>` : ''}</div>
      <div class="pista"><div class="marca" style="width:${max > 0 ? Math.max(f.v,0)/max*62 : 0}%"></div>
        <span class="val">${cop.format(f.v)}</span></div>
      <div class="tip">${esc(f.t)}</div>
    </div>`).join('') : `<p class="nota">${esc(vacio)}</p>`;
}
function tabla(id, cols, filas){
  $(id).innerHTML = `<table><thead><tr>${cols.map(c => `<th class="${c.num?'num':''}">${esc(c.t)}</th>`).join('')}</tr></thead><tbody>${
    filas.map(f => `<tr>${cols.map(c => `<td class="${c.num?'num':''}">${c.f(f)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function estado(ok, si, no){ return `<span class="estado ${ok?'ok':'aviso'}">${ok?'✓':'!'} ${esc(ok?si:no)}</span>`; }

function pintar(){
  const fs = D.facturas.filter(f => !persona || f.persona === persona);
  const ls = D.lineas.filter(l => !persona || l.persona === persona);
  const total = fs.reduce((a,f) => a + f.total, 0);
  const fechas = fs.map(f => f.fecha).sort();
  $('total').textContent = cop.format(total);
  $('total-sub').textContent = fs.length
    ? `en ${fs.length} factura${fs.length===1?'':'s'} electrónica${fs.length===1?'':'s'}, del ${fechaTxt(fechas[0])} al ${fechaTxt(fechas.at(-1))}`
    : 'No hay facturas para este filtro.';
  $('sub1').textContent = 'Solo lo que tiene factura electrónica a nombre de ' +
    (persona ? D.personas.find(p => p.id === persona).nombre : 'las personas del hogar') + '.';

  const cat = {}; ls.forEach(l => { (cat[l.categoria] ??= {v:0,n:0}); cat[l.categoria].v += l.gasto; cat[l.categoria].n++; });
  barras('g-cat', Object.entries(cat).sort((a,b) => b[1].v - a[1].v).map(([n,o]) => ({
    n, v:o.v, t:`${n}: ${cop.format(o.v)} · ${total ? Math.round(o.v/total*100) : 0} % del total · ${o.n} línea${o.n===1?'':'s'}`})),
    'Sin datos.');
  const mes = {}; fs.forEach(f => { const k = f.fecha.slice(0,7); (mes[k] ??= {v:0,n:0}); mes[k].v += f.total; mes[k].n++; });
  barras('g-mes', Object.entries(mes).sort().map(([k,o]) => ({
    n: mesTxt(k), v:o.v, t:`${mesTxt(k)}: ${cop.format(o.v)} en ${o.n} factura${o.n===1?'':'s'}`})), 'Sin datos.');

  tabla('t-lineas', [
    {t:'Fecha', f:l => esc(l.fecha)}, {t:'Emisor', f:l => esc(l.emisor)}, {t:'Descripción', f:l => esc(l.descripcion)},
    {t:'Categoría', f:l => esc(l.categoria) + (l.regla ? `<br><code>regla ${esc(l.regla)}</code>` : '')},
    {t:'Gasto', num:true, f:l => cop.format(l.gasto)},
    {t:'Fuente', f:l => { const f = D.facturas.find(x => x.cufe === l.cufe);
       return `<code>${esc(f.archivo)}</code><br><code>línea ${esc(l.linea)} · CUFE ${corto(l.cufe)}</code>`; }}], ls);

  const rs = D.renta.filter(r => !persona || r.persona === persona);
  $('renta').innerHTML = rs.length ? `<div class="tabla"><table><thead><tr><th>Persona y año</th>
      <th class="num">Hoy</th><th class="num">Si se prueba el pago</th></tr></thead><tbody>${
    rs.map(r => `<tr><td>${esc(r.nombre)}<br><code>año gravable ${r.anio}</code></td>
      <td class="num"><b>${cop.format(r.deduccion)}</b><br><code>${r.cumple} factura${r.cumple===1?'':'s'}</code></td>
      <td class="num">${cop.format(r.potencial)}<br><code>+${r.por_probar} por probar</code></td></tr>`).join('')
    }</tbody></table></div>
    <p class="nota">"Hoy" es el 1 % de las compras cuya factura ya dice que se pagó por medio electrónico.
      "Si se prueba el pago" suma las facturas que dicen efectivo o no dicen el medio: sirven si el pago aparece en un extracto.
      Tope anual: 240 UVT (${[...new Set(rs.map(r => r.tope === null ? `sin UVT para ${r.anio}` : `${cop.format(r.tope)} en ${r.anio}, UVT ${r.estado_uvt.replace('_',' ')}`))].map(esc).join('; ')}).</p>` +
    `<details><summary>Ver por qué cada factura cumple o no</summary><div class="tabla"><table><thead><tr><th>Fecha</th><th>Emisor</th><th class="num">Total</th><th>Estado</th><th>Motivo</th></tr></thead><tbody>${
      fs.map(f => `<tr><td>${esc(f.fecha)}</td><td>${esc(f.emisor)}</td><td class="num">${cop.format(f.total)}</td><td>${
        f.renta === 'cumple' ? '<span class="estado ok">✓ cumple</span>' : f.renta === 'por_probar'
        ? '<span class="estado aviso">! por probar</span>' : '<span class="estado mal">✕ no cumple</span>'}</td><td>${esc(f.renta_motivo)}</td></tr>`).join('')
    }</tbody></table></div></details>
    <p class="nota">Base legal: ${esc(D.fuentes_renta.regla)} (${esc(D.fuentes_renta.estado_regla.replace('_',' '))}). ${esc(D.fuentes_renta.prueba)}.
      La deducción exige además que la compra no se haya usado en otro beneficio tributario: eso solo lo sabe quien declara.
      Se calcula sobre el valor antes de impuestos; sobre el total pagado sería ${rs.map(r => cop.format(r.sobre_total)).join(' / ')}.</p>`
    : '<p class="nota">No hay facturas para este filtro.</p>';
}

function fijo(){
  const r = D.resumen;
  $('meta').textContent = `Generado el ${fechaTxt(r.generado.slice(0,10))} · versión ${r.version_codigo} · datos privados, no compartir`;
  $('filtros').innerHTML = [{id:'',nombre:'Todas las personas'}, ...D.personas].map(p =>
    `<button type="button" data-p="${esc(p.id)}" aria-pressed="${p.id === persona}">${esc(p.nombre)}</button>`).join('');
  $('filtros').onclick = e => { const b = e.target.closest('button'); if (!b) return; persona = b.dataset.p;
    document.querySelectorAll('#filtros button').forEach(x => x.setAttribute('aria-pressed', x === b)); pintar(); };

  const ex = D.extractos.filter(e => e.valido);
  barras('g-ext', ex.map(e => ({
    n: `${e.banco} · ${e.producto}`, s: (e.desde ? fechaTxt(e.desde) + ' – ' : 'corte ') + fechaTxt(e.hasta), v: e.gasto,
    t: `${e.movimientos} movimientos · gasto ${cop.format(e.gasto)} · movido entre productos propios ${cop.format(e.interno)} (no cuenta)`})),
    'Todavía no hay extractos validados.');
  $('ext-estado').innerHTML = `<ul class="lista">${D.extractos.map(e =>
    `<li>${esc(e.banco)} ${esc(e.numero)}: ${estado(e.valido, 'saldos cuadran', 'en revisión')} · ${e.chequeos.filter(c => c.ok).length} de ${e.chequeos.length} chequeos${
      e.notas.length ? `<br><span class="nota">${e.notas.map(esc).join(' · ')}</span>` : ''}</li>`).join('')}${
    D.cola.filter(c => !D.extractos.some(e => e.archivo === c.archivo)).map(c =>
      `<li><code>${esc(c.archivo)}</code>: <span class="estado aviso">! en revisión</span> — ${esc(c.motivo)}. ${esc(c.que_hacer)}.</li>`).join('')}</ul>`;
  tabla('t-mov', [
    {t:'Fecha', f:m => esc(m.fecha)}, {t:'Banco', f:m => esc(m.banco)}, {t:'Descripción', f:m => esc(m.descripcion)},
    {t:'Clase', f:m => esc(m.clase.replaceAll('_',' '))}, {t:'Valor', num:true, f:m => cop.format(m.valor)},
    {t:'Fuente', f:m => `<code>${esc(m.archivo)} · pág. ${m.pagina}, renglón ${m.renglon}</code>`}],
    ex.flatMap(e => e.detalle.map(m => ({...m, banco:e.banco, archivo:e.archivo}))));

  const f = r.facturas, e = r.extractos, p = r.pagos;
  $('calidad').innerHTML = [
    [`${f.conciliadas} de ${f.unicas}`, 'facturas cuyas líneas e impuestos suman el total y tienen validación DIAN'],
    [`${e.validos} de ${e.archivos}`, 'extractos con saldos que cuadran' + (e.en_revision ? ` (${e.en_revision} en revisión)` : '')],
    [`${p.conciliada} de ${f.unicas}`, 'facturas con su pago encontrado en un extracto'],
    [`${f.lineas - f.sin_categoria} de ${f.lineas}`, 'líneas clasificadas por regla' + (r.metricas_categorias ? '' : ' (sin etiquetas del autor: aún no hay F1)')],
  ].map(([b,s]) => `<div class="ficha"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join('');
  const NIV = {alerta:['mal','Revisar'], aviso:['aviso','Falta información'], info:['ok','Resuelto solo']};
  const av = ['alerta','aviso','info'].flatMap(n => D.avisos.filter(a => a.nivel === n));
  $('avisos').innerHTML = `<h3>Avisos de la revisión</h3>` + (av.length
    ? `<p class="nota">${['alerta','aviso','info'].map(n => `${D.avisos.filter(a => a.nivel === n).length} ${NIV[n][1].toLowerCase()}`).join(' · ')}</p>
       <details ${D.avisos.some(a => a.nivel === 'alerta') ? 'open' : ''}><summary>Ver los ${av.length} avisos</summary><ul class="lista">${av.map(a =>
        `<li><span class="estado ${NIV[a.nivel][0]}">${NIV[a.nivel][1]}</span> <b>${esc(a.asunto)}</b><br><span class="nota">${esc(a.detalle)}${a.que_hacer ? ' <b>Qué hacer:</b> ' + esc(a.que_hacer) : ''}</span></li>`).join('')}</ul></details>`
    : `<p class="nota">Ninguno.</p>`);
  $('alertas').innerHTML = `<h3>Alertas de precio</h3>` + (D.alertas.length
    ? `<ul class="lista">${D.alertas.map(a => `<li>${esc(a.producto)} (${esc(a.emisor)}): de ${cop.format(a.antes)} a ${cop.format(a.ahora)}, +${Math.round(a.cambio*100)} % el ${esc(a.fecha)}</li>`).join('')}</ul>`
    : `<p class="nota">Ninguna todavía: hace falta comprar el mismo producto al mismo emisor en dos fechas distintas.</p>`);
  tabla('t-fact', [
    {t:'Fecha', f:x => esc(x.fecha)}, {t:'Emisor', f:x => esc(x.emisor) + `<br><code>${esc(x.numero)}</code>`},
    {t:'Total', num:true, f:x => cop.format(x.total)},
    {t:'Suma', f:x => estado(x.conciliada, 'cuadra', 'no cuadra')},
    {t:'Pago', f:x => estado(x.pago === 'conciliada', 'en extracto', x.pago === 'ambigua' ? 'ambiguo' : x.pago === 'declarada_por_autor' ? 'declarado por el autor' : 'sin extracto')},
    {t:'Fuente', f:x => `<code>${esc(x.archivo)}</code><br><code>SHA-256 ${corto(x.sha256)} · CUFE ${corto(x.cufe)}</code>`}], D.facturas);
  $('pie').textContent = 'Factura Viva no es asesoría tributaria ni financiera. Cada cifra sale de los archivos en data/raw; las huellas completas están en data/manifest.json y las tablas en data/procesado.';
}
fijo(); pintar();
</script>
</body>
</html>
"""
