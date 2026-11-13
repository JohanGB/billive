import shutil
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def proyecto(tmp_path):
    """Proyecto vacío con la configuración real del repositorio."""
    shutil.copytree(RAIZ / "config", tmp_path / "config")
    (tmp_path / "data" / "raw" / "facturas").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "extractos").mkdir(parents=True)
    return tmp_path
