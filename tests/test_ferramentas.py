import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from emporio_agent.catalogo import Catalogo
from emporio_agent.config import carregar_settings
from emporio_agent.ferramentas import DEFINICOES, ErroFerramenta, Ferramentas
from emporio_agent.politicas import BasePoliticas

from .conftest import DADOS


@pytest.fixture
def ferramentas(catalogo: Catalogo) -> Ferramentas:
    settings = replace(carregar_settings(), hoje_fixo=date(2026, 3, 25))
    return Ferramentas(
        catalogo, BasePoliticas.carregar(Path(DADOS) / "politicas_da_loja.md"), settings
    )


def _rodar(ferramentas: Ferramentas, nome: str, **entrada: object) -> dict[str, object]:
    resultado: dict[str, object] = json.loads(ferramentas.executar(nome, dict(entrada)))
    return resultado


def test_definicoes_consistentes_com_handlers(ferramentas: Ferramentas) -> None:
    for definicao in DEFINICOES:
        assert definicao["input_schema"]["type"] == "object"
        assert hasattr(ferramentas, definicao["name"])


def test_violoes_ate_1000(ferramentas: Ferramentas) -> None:
    r = _rodar(ferramentas, "buscar_produtos", categoria="violão", preco_max=1000, limite=50)
    produtos = r["produtos"]
    assert isinstance(produtos, list) and produtos
    assert all(p["categoria"] == "Violões" and p["preco_atual"] <= 1000 for p in produtos)
    assert all(p["disponivel"] for p in produtos)
    assert 96 not in {p["id"] for p in produtos}  # esgotado


def test_consulta_livre_identifica_categoria(ferramentas: Ferramentas) -> None:
    r = _rodar(ferramentas, "buscar_produtos", consulta="ukulele elétrico")
    nomes = [p["nome"] for p in r["produtos"]]  # type: ignore[union-attr]
    assert nomes and all("Elétrico" in n for n in nomes)


def test_categoria_invalida(ferramentas: Ferramentas) -> None:
    with pytest.raises(ErroFerramenta):
        ferramentas.executar("buscar_produtos", {"categoria": "drones"})


def test_preco_takamine(ferramentas: Ferramentas) -> None:
    r = _rodar(ferramentas, "detalhes_produto", produto="Takamine GD20")
    assert r["id"] == 95
    assert r["preco_atual"] == 2199.0
    assert r["pagamento"]["pix"] == round(2199 * 0.95, 2)  # type: ignore[index]
    assert r["pagamento"]["cartao_credito_max_parcelas_sem_juros"] == 12  # type: ignore[index]


def test_produto_em_promocao_mostra_original_e_sem_pix(ferramentas: Ferramentas) -> None:
    r = _rodar(ferramentas, "detalhes_produto", produto="Crafter HT-100")
    assert r["preco_tabela"] == 2399.0
    assert r["promocao"] == {"nome": "Desconto Exclusivo", "desconto_pct": 18.0}
    assert r["pagamento"]["pix_tem_desconto_5pct"] is False  # type: ignore[index]


def test_esgotado_sugere_alternativas(ferramentas: Ferramentas) -> None:
    r = _rodar(ferramentas, "detalhes_produto", produto="Giannini GF-3D")
    assert r["disponibilidade"] == "temporariamente esgotado"
    assert r["alternativas_disponiveis"]


def test_produto_inexistente(ferramentas: Ferramentas) -> None:
    r = _rodar(ferramentas, "detalhes_produto", produto="Playstation 5")
    assert r["encontrado"] is False


def test_pedido_exige_verificacao(ferramentas: Ferramentas) -> None:
    with pytest.raises(ErroFerramenta):
        ferramentas.executar("consultar_pedido", {"numero_pedido": 8, "verificacao": "fulano"})
    r = _rodar(ferramentas, "consultar_pedido", numero_pedido=8, verificacao="(67) 99654-7821")
    assert r["status"] == "shipped"
    assert r["codigo_rastreio"] == "BRJL5544332BR"
    assert "telefone" not in json.dumps(r)


def test_pedido_inexistente_e_dados_errados_tem_mesma_resposta(ferramentas: Ferramentas) -> None:
    with pytest.raises(ErroFerramenta) as inexistente:
        ferramentas.consultar_pedido(999, "(67) 99654-7821")
    with pytest.raises(ErroFerramenta) as errado:
        ferramentas.consultar_pedido(8, "(67) 90000-0000")
    assert str(inexistente.value) == str(errado.value)


def test_limite_de_tentativas(ferramentas: Ferramentas) -> None:
    for _ in range(3):
        with pytest.raises(ErroFerramenta):
            ferramentas.consultar_pedido(1, "errado")
    with pytest.raises(ErroFerramenta, match="limite"):
        ferramentas.consultar_pedido(1, "pedro.oliveira@jmail.com")


def test_pedido_entregue_traz_prazos(ferramentas: Ferramentas) -> None:
    r = _rodar(
        ferramentas, "consultar_pedido", numero_pedido=7, verificacao="Letícia Gonçalves Rocha"
    )
    prazos = r["prazos_pos_venda"]
    assert isinstance(prazos, dict)
    assert prazos["arrependimento_valido"] is False  # entregue em 17/02, hoje 25/03


def test_politicas_com_secao_explicita(ferramentas: Ferramentas) -> None:
    r = _rodar(ferramentas, "consultar_politicas", consulta="devolução", secoes=["4.1"])
    trechos = r["trechos"]
    assert isinstance(trechos, list)
    assert trechos[0]["secao"].startswith("4.1")


def test_encaminhamento_gera_protocolo(ferramentas: Ferramentas, tmp_path: Path) -> None:
    ferramentas.settings = replace(ferramentas.settings, dir_historico=tmp_path)
    r = _rodar(ferramentas, "encaminhar_para_equipe", motivo="reclamacao", resumo="teste")
    assert str(r["protocolo"]).startswith("EM2026")
    assert (tmp_path / "encaminhamentos.jsonl").read_text(encoding="utf-8").count("\n") == 1
