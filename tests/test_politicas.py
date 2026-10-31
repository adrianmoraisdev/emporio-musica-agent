import re

import pytest

from emporio_agent.politicas import BasePoliticas
from emporio_agent.text import normalizar

from .conftest import DADOS


@pytest.fixture(scope="module")
def politicas() -> BasePoliticas:
    return BasePoliticas.carregar(DADOS / "politicas_da_loja.md")


def test_secoes_extraidas(politicas: BasePoliticas) -> None:
    numeros = [s.numero for s in politicas.secoes]
    assert numeros[:3] == ["1", "1.1", "1.2"]
    assert "4.1" in numeros and "10" in numeros
    assert politicas.por_numero("4.1") is not None
    assert "Trocas e Devoluções" in politicas.por_numero("4.1").titulo  # type: ignore[union-attr]


def test_transcricao_bate_com_pdf(politicas: BasePoliticas) -> None:
    """Garante que a transcrição em Markdown não diverge da estrutura do PDF oficial."""
    pypdf = pytest.importorskip("pypdf")
    leitor = pypdf.PdfReader(DADOS / "politicas_da_loja.pdf")
    pdf = normalizar(" ".join(p.extract_text() for p in leitor.pages))
    for secao in politicas.secoes:
        titulo = re.sub(r"\s*\(.*\)$", "", secao.titulo)
        assert normalizar(f"{secao.numero} {titulo}") in pdf, secao.rotulo
    for fato in ["7 sete dias corridos", "30 trinta dias corridos", "frete gratis", "3341 4444"]:
        assert fato in pdf


@pytest.mark.parametrize(
    ("pergunta", "secao_esperada"),
    [
        ("Me arrependi da minha compra, posso devolver meu pedido?", "4.1"),
        ("Qual o endereço da loja?", "1.2"),
        ("Que horas vocês abrem no sábado?", "2"),
        ("Posso parcelar em 10 vezes no cartão?", "3.1"),
        ("Meu violão veio com defeito, e agora?", "4.2"),
        ("Vocês vendem cordas e amplificador?", "1"),
        ("Quanto custa o frete para Dourados?", "5.2"),
        ("O desconto do pix vale junto com promoção?", "6.2"),
    ],
)
def test_busca_recupera_secao_certa_no_top3(
    politicas: BasePoliticas, pergunta: str, secao_esperada: str
) -> None:
    encontrados = [s.numero for s, _ in politicas.buscar(pergunta, k=3)]
    assert secao_esperada in encontrados, encontrados
