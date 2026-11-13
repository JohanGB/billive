# Ficha: categorización de gasto por reglas (línea base)

- **Qué es**: reglas ordenadas en `config/categorias.yaml` (descripción de la línea, nombre o
  NIT del emisor, actividad económica CIIU). No es un modelo entrenado: es la línea base que
  cualquier modelo futuro debe superar fuera de muestra.
- **Datos**: 9 líneas de 7 facturas reales del autor (sep-2025 a oct-2026).
- **Resultado**: 8 de 9 líneas clasificadas por una regla directa; 1 por herencia de su
  factura. **No hay métricas**: no existen etiquetas puestas por el autor, y reportar un F1
  contra etiquetas del mismo autor de las reglas sería circular.
- **Cómo se medirá**: precisión, recall y F1 por categoría (`categorias.evaluar`) contra
  `data/etiquetas/etiquetas.csv`, que el proceso lee si existe.
- **Sesgos y límites**: las reglas se escribieron viendo estas mismas 7 facturas; con
  emisores nuevos caerán más líneas en "Sin categoría". El CIIU describe al emisor, no a la
  compra (un supermercado también vende medicamentos).
- **Cuándo no usarlo**: para decisiones tributarias (la categoría no define si un gasto es
  deducible) ni como verdad sobre el tipo de gasto sin revisar las líneas heredadas.
- **Siguiente paso**: con 20–50 facturas etiquetadas, comparar contra LightGBM sobre TF-IDF
  de caracteres con validación temporal; desplegarlo solo si gana.
