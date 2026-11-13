# Declaración de uso de IA

- **Claude** escribió el código, las pruebas y la documentación; inspeccionó los archivos
  reales del autor (7 facturas y 4 extractos) para diseñar los lectores, y consultó fuentes
  públicas para las reglas tributarias.
- **El autor** definió el alcance y los principios (trazabilidad, datos crudos inmutables,
  privacidad, honestidad del modelo), entregó los archivos y la clave de los PDF por chat, y
  es responsable de verificar las reglas marcadas `por_verificar` y de las decisiones
  tributarias.
- **Cómo se verificó**: 90 pruebas automáticas (casos resueltos a mano, pruebas por
  propiedades y pruebas de aceptación sobre los datos reales); el proceso completo se ejecutó
  sobre los archivos reales y sobre datos sintéticos; el tablero se revisó en pantalla de
  celular y de escritorio. Las pruebas encontraron un error real en el lector genérico
  (campos desplazados) que se corrigió.
- **Qué no se verificó**: ejecución en Windows 11 (se desarrolló en Linux con Python 3.13);
  el texto oficial del art. 336 ET y del Decreto 2231 de 2023.
- Los datos personales se procesaron en un espacio de trabajo privado y temporal de la
  sesión; ninguna clave ni número completo de producto quedó escrito en el proyecto.
