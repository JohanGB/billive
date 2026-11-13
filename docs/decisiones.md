# Decisiones de diseño

| # | Decisión | Por qué | Alternativa descartada |
|---|---|---|---|
| D1 | XML con la biblioteca estándar (`xml.etree`) y rechazo de `DOCTYPE`/`ENTITY` | Cero dependencias y sin riesgo de ataques de entidades con archivos que llegan por correo | `lxml`: más rápido, pero una dependencia compilada más en Windows |
| D2 | Los ZIP se leen en memoria; nunca se descomprimen al disco | `data/raw` es inmutable y no se generan copias de datos personales | Descomprimir a una carpeta temporal |
| D3 | Tolerancia de $2 en las sumas de una factura | Los emisores redondean a centavos línea por línea; con las 7 facturas reales la mayor diferencia fue de $0,01. $2 deja margen sin esconder errores reales | Igualdad exacta: rechazaría facturas válidas (p. ej. Comcel: líneas 37.408,83 vs. encabezado 37.408,84) |
| D4 | El total a pagar **no** resta anticipos (se acepta la variante que sí los resta, anotada) | Así lo traen las facturas reales con `PrepaidPayment` (pago previo igual al total a pagar) | Restar siempre: dejaría esas facturas en $0 |
| D5 | Código de validación DIAN `02` o `002` | Dos emisores reales escriben `002` | Exigir `02` literal: 2 de 7 facturas válidas fallarían |
| D6 | Un lector por banco + lector genérico + validación obligatoria | El formato de cada banco es distinto; la validación de saldos es lo que da confianza, no el lector | Un solo lector "inteligente": imposible de probar |
| D7 | El signo de un movimiento se deduce del saldo impreso cuando existe | El texto del PDF pierde la columna (débito o crédito); el saldo no miente | Adivinar por palabras clave |
| D8 | Extracto de tarjeta Nu: la deuda anterior se **deduce** cuando los pagos superan los abonos impresos exactamente en el valor de uno o varios pagos | Nu no imprime la deuda del corte anterior. Se declara como deducida y se confirma con el extracto anterior | Rechazar todos los extractos de Nu |
| D9 | Plantilla de AV Villas: rechaza extractos con movimientos | Solo hay un extracto real y no tiene movimientos; no hay con qué validar el formato de las filas | Escribir un lector de filas sin haber visto ninguna |
| D10 | "Gasto real" excluye traslados a bolsillos, transferencias a uno mismo y el pago de la tarjeta propia | Contarlos duplicaría el gasto (la compra ya está en la tarjeta) | Sumar todas las salidas |
| D11 | Cruce factura ↔ movimiento: uno a uno, y si hay empate se declara ambiguo | Un cruce equivocado se convertiría en "prueba" de pago para la deducción | Tomar siempre el mejor puntaje |
| D12 | Deducción del 1 % sobre el valor **antes de impuestos**; se muestra también sobre el total | Lectura conservadora mientras la regla siga `por_verificar` | Calcular sobre el total pagado |
| D13 | Una línea sin categoría hereda la de la línea de mayor gasto de su factura (`heredada#factura`) | La tarifa de servicio de un tiquete es gasto de viaje | Dejarla en "Sin categoría" |
| D14 | `data/manifest.json` fuera de git | Los nombres de archivo contienen números de cuenta | Publicarlo (lo pide el estándar general del portafolio) |
| D15 | Tablero en un solo HTML sin recursos externos | Se abre sin internet y no envía datos a ningún servidor | Aplicación web con servidor |
| D16 | La clave se puede pasar por teclado o por la variable `FACTURA_VIVA_CLAVE` de la sesión | Permite correr las pruebas de aceptación sin escribir la clave en ningún archivo | Archivo de configuración con claves (prohibido) |
| D17 | En `data/procesado` y en el tablero los documentos de identidad y los números largos de los movimientos se guardan enmascarados (últimos 4 dígitos) | Los bancos suelen usar el documento como clave del PDF; no debe quedar repetido en archivos derivados | Guardar el documento completo para agrupar por persona |

## Revisión cruzada (v0.2, 11-nov-2026)

- **Extractos repetidos.** El mismo archivo dos veces (misma huella SHA-256) o el mismo periodo descargado
  de nuevo se cuenta una sola vez y deja un aviso `info`. Si dos extractos del mismo producto se pisan
  parcialmente, no se descarta ninguno: los movimientos repetidos (misma fecha, valor, saldo y
  descripción) se marcan `duplicado`, no suman, y queda una `alerta`. `data/raw` nunca se toca: el
  archivo repetido sigue ahí y en el manifiesto.
- **Continuidad.** El saldo final de un mes debe ser el inicial del siguiente (`alerta` si no) y los meses
  sin extracto se anuncian (`aviso`): un pago no hallado en un mes sin extracto no es un pago inexistente.
- **Plata propia.** Una salida de un producto propio que entra a otro producto propio por el mismo valor,
  con máximo un día de diferencia, no es gasto. No se emparejan compras, retiros ni plata recibida de
  terceros. Cada pareja queda en `transferencias_propias.csv` con archivo, página y renglón de ambos
  lados. Se apaga o ajusta en `config/conciliacion.yaml`.
- **Pago agrupado.** Varias facturas del mismo emisor y fecha cuyo total coincide con un único movimiento
  se concilian juntas.
- **Pago no encontrado.** No se inventa un cruce. Cada factura sin pago queda con un diagnóstico y un
  nivel: `info` si no se esperaba movimiento (efectivo, factura de otra persona), `aviso` si faltan
  extractos del periodo, `alerta` si hay un movimiento sospechosamente parecido que el autor debe
  confirmar. Todo está en `avisos.csv`, en `pagos.csv` y en el tablero.
- **Etiquetas del autor.** `data/etiquetas/etiquetas.csv` (columnas `cufe,linea,categoria`) es opcional.
  Si existe, la etiqueta manda sobre la regla en las cifras (`regla_categoria = etiqueta#autor`) y la
  categoría que habría puesto la regla se conserva en `categoria_regla` para medir F1. Las reglas se
  cambian en `config/categorias.yaml` sin tocar código. Mientras no haya etiquetas, todo sigue por reglas.
- **Pagos declarados por el autor.** `data/etiquetas/pagos_declarados.csv` (`cufe,forma_pago,nota`) permite anotar
  lo que los extractos no pueden saber (efectivo, lo pagó otra persona). La factura queda como
  `declarada_por_autor`, con aviso `info` que cita la anotación, y deja de buscarse en los extractos.

## Fase 2 en espera (11-nov-2026)

- **Decisión del autor:** los modelos (normalización de productos, categorización con LightGBM, reglas de
  asociación, alzas anómalas y pronóstico de gasto) se entrenan cuando haya **al menos 30 facturas únicas**.
  Con 7 no hay con qué entrenar ni medir. El umbral está en `config/conciliacion.yaml` (`fase2.facturas_minimas`)
  y cada ejecución informa cuántas faltan. Mientras tanto se usan las reglas (línea base).
