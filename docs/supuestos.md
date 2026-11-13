# Supuestos y pendientes (v0.1)

| # | Supuesto | Por qué | Cómo se verifica |
|---|---|---|---|
| S1 | Quien aparece como adquiriente es quien podría usar la deducción | Art. 336-5 ET exige que la factura identifique al comprador | Dos de las 7 facturas están a nombre de otra persona del hogar: se reportan por persona |
| S2 | La deducción del 1 % se calcula antes de impuestos | La norma dice "valor de las adquisiciones" | Leer el Decreto 2231 de 2023 y la doctrina DIAN; cambiar `base` en `config/renta.yaml` |
| S3 | Un pago visible en un extracto prueba el pago electrónico aunque la factura diga efectivo | Concepto DIAN 738 de 2024 (leído en reseña del INCP, no en el texto) | Leer el concepto en dian.gov.co |
| S4 | UVT 2025 = $49.799 | Fuente secundaria | Resolución DIAN 000193 de 2024 |
| S5 | Tabla de medios de pago del XML (10 efectivo, 48 tarjeta crédito, 49 débito…) | Anexo técnico de factura electrónica, citado de memoria | Anexo técnico vigente en dian.gov.co |
| S6 | Los intereses facturados (mora, corrientes) son "Intereses y cargos financieros", no educación | La línea describe mejor el gasto que el emisor | Criterio del autor |
| S7 | Una salida que entra a otro producto propio por el mismo valor (±1 día) es plata propia, no gasto. Las demás transferencias sí cuentan como gasto | El autor confirmó que todas las cuentas cargadas son suyas; si el destino no tiene extracto cargado no se puede saber | `transferencias_propias.csv`; lista `cuentas_propias` en `config/conciliacion.yaml` para destinos sin extracto |
| S8 | En la tarjeta Nu, la deuda del corte anterior es la diferencia entre pagos y abonos impresos | Nu no la imprime | Extracto del mes anterior |
| S9 | Ventana del cruce de pagos: 3 días antes, 10 después de la fecha o del vencimiento | Pagos de servicios y compras que se registran con desfase | Ajustar con más datos |

## Pendientes

1. Verificar S2–S5 contra el texto oficial y pasar las reglas a `verificado`.
2. Extractos que faltan (ver `avisos.csv`): Caja Social de octubre de 2026, Nequi de septiembre de 2026,
   Nu de junio a septiembre de 2026. Con los cargados cruza 1 de 7 pagos.
3. Extracto de AV Villas con movimientos para completar su plantilla.
4. Etiquetas del autor en `data/etiquetas/etiquetas.csv` (columnas `cufe,linea,categoria`;
   la plantilla es `data/procesado/por_etiquetar.csv`) para medir F1 por categoría.
5. Fase 2: LightGBM sobre TF-IDF contra la línea base de reglas, normalización de productos
   y pronóstico de gasto, cuando haya 20–50 facturas.
