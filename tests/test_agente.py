"""Testa o loop do agente com um cliente falso (sem chamar a API)."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx
import pytest

from emporio_agent.agente import MSG_FALHA, Agente
from emporio_agent.catalogo import Catalogo
from emporio_agent.config import carregar_settings
from emporio_agent.ferramentas import Ferramentas
from emporio_agent.historico import RepositorioHistorico
from emporio_agent.politicas import BasePoliticas

from .conftest import DADOS


class Bloco(SimpleNamespace):
    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


def texto(t: str) -> Bloco:
    return Bloco(type="text", text=t)


def ferramenta(nome: str, entrada: dict[str, Any], id_: str = "tu_1") -> Bloco:
    return Bloco(type="tool_use", id=id_, name=nome, input=entrada)


class ClienteFalso:
    def __init__(self, respostas: list[SimpleNamespace | Exception]) -> None:
        self.respostas = respostas
        self.pedidos: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.pedidos.append({**kwargs, "messages": list(kwargs["messages"])})
        proxima = self.respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima


@pytest.fixture
def ferramentas(catalogo: Catalogo) -> Ferramentas:
    settings = replace(carregar_settings(), hoje_fixo=date(2026, 3, 25))
    return Ferramentas(catalogo, BasePoliticas.carregar(DADOS / "politicas_da_loja.md"), settings)


def test_loop_executa_ferramenta_e_responde(ferramentas: Ferramentas) -> None:
    cliente = ClienteFalso(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[ferramenta("detalhes_produto", {"produto": "Takamine GD20"})],
            ),
            SimpleNamespace(stop_reason="end_turn", content=[texto("Custa R$ 2.199,00!")]),
        ]
    )
    agente = Agente(ferramentas, ferramentas.settings, cliente=cliente)  # type: ignore[arg-type]
    resposta = agente.responder("Quanto custa o Takamine GD20?")

    assert resposta.texto == "Custa R$ 2.199,00!"
    assert [c.nome for c in resposta.chamadas] == ["detalhes_produto"]
    assert '"preco_atual": 2199.0' in resposta.chamadas[0].resultado
    # 2º request leva o tool_result vinculado ao tool_use
    ultimo = cliente.pedidos[1]["messages"][-1]
    assert ultimo["content"][0]["tool_use_id"] == "tu_1"
    assert cliente.pedidos[0]["fallbacks"] == "default"
    assert len(agente.mensagens) == 4


def test_erro_de_ferramenta_volta_como_is_error(ferramentas: Ferramentas) -> None:
    cliente = ClienteFalso(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[ferramenta("consultar_pedido", {"numero_pedido": 8, "verificacao": "x"})],
            ),
            SimpleNamespace(stop_reason="end_turn", content=[texto("Não encontrei.")]),
        ]
    )
    agente = Agente(ferramentas, ferramentas.settings, cliente=cliente)  # type: ignore[arg-type]
    resposta = agente.responder("status do pedido 8")
    assert resposta.chamadas[0].erro
    assert cliente.pedidos[1]["messages"][-1]["content"][0]["is_error"] is True


def test_falha_de_api_desfaz_turno(ferramentas: Ferramentas) -> None:
    erro = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
    cliente = ClienteFalso([erro])
    agente = Agente(ferramentas, ferramentas.settings, cliente=cliente)  # type: ignore[arg-type]
    resposta = agente.responder("oi")
    assert resposta.texto == MSG_FALHA
    assert agente.mensagens == []


def test_historico_persiste(tmp_path: Any) -> None:
    repo = RepositorioHistorico(tmp_path)
    repo.salvar("abc", [{"role": "user", "content": "oi"}])
    assert repo.carregar("abc") == [{"role": "user", "content": "oi"}]
    with pytest.raises(ValueError):
        repo.carregar("../fora")
