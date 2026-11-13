"""Demostración con datos inventados: no necesita facturas ni extractos reales.

    python ejemplos/demo_sintetico.py

Crea un proyecto de ejemplo en una carpeta temporal (3 facturas, una repetida, y un
extracto PDF con clave "demo1234"), ejecuta el proceso completo y abre el tablero.
"""
import sys
import tempfile
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from factura_viva.pipeline import procesar          # noqa: E402
from factura_viva.sinteticos import proyecto_demo   # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
try:
    import reportlab  # noqa: F401
    con_pdf = True
except ImportError:
    con_pdf = False
    print("(sin reportlab: la demo omite el extracto PDF; instale con  pip install -e .[dev])")

raiz = proyecto_demo(Path(tempfile.mkdtemp(prefix="factura_viva_demo_")), con_pdf)
res = procesar(raiz, pedir_clave=lambda nombre: "demo1234")
tablero = raiz / "data" / "procesado" / "tablero.html"
print(f"\nDemo lista en {raiz}\nTablero: {tablero}")
if "--no-abrir" not in sys.argv:
    webbrowser.open(tablero.as_uri())
