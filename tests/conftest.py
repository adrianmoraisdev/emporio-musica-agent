from pathlib import Path

import pytest

from emporio_agent.catalogo import Catalogo

DADOS = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="session")
def catalogo() -> Catalogo:
    return Catalogo.carregar(DADOS)
