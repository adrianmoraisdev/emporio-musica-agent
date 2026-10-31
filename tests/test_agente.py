"""Testa o loop do agente e os adaptadores de provedor sem chamar nenhuma API."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from emporio_agent.agente import MSG_FALHA, MSG_RECUSA, Agente
from emporio_agent.catalogo import Catalogo
from emporio_agent.config import carregar_settings
from emporio_agent.ferramentas import Ferramentas
from emporio_agent.historico import RepositorioHistorico
from emporio_agent.politicas import BasePoliticas
from emporio_agent.provedores import (
    ChamadaModelo,
    ProvedorAnthropic,
    ProvedorOpenAICompat,
    RespostaModelo,
)

from .conftest import DADOS


class ErroFalso(Exception):
    pass


class ProvedorFalso:
    nome = "falso"
    modelo = "falso-1"
    erros_api = (ErroFalso,)

    def __init__(self, respostas: list[RespostaModelo | Exception]) -> None:
        self.respostas = respostas
        self.historicos: list[list[dict[str, Any]]] = []

    def gerar(
        self, sistema: str, ferramentas: list[dict[str, Any]], historico: list[dict[str, Any]]
    ) -> RespostaModelo:
        self.historicos.append(list(historico))
        proxima = self.respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima


@pytest.fixture
def ferramentas(catalogo: Catalogo) -> Ferramentas:
    settings = replace(carregar_settings(), hoje_fixo=date(2026, 3, 25))
    return Ferramentas(catalogo, BasePoliticas.carregar(DADOS / "politicas_da_loja.md"), settings)


def _chamada(nome: str, entrada: dict[str, Any], id_: str = "c1") -> RespostaModelo:
    return RespostaModelo("", "ferramentas", [ChamadaModelo(id_, nome, entrada)])


# ------------------------------------------------------------------------ loop
def test_loop_executa_ferramenta_e_responde(ferramentas: Ferramentas) -> None:
    provedor = ProvedorFalso(
        [
            _chamada("detalhes_produto", {"produto": "Takamine GD20"}),
            RespostaModelo("Custa R$ 2.199,00!", "fim"),
        ]
    )
    agente = Agente(ferramentas, ferramentas.settings, provedor=provedor)
    resposta = agente.responder("Quanto custa o Takamine GD20?")

    assert resposta.texto == "Custa R$ 2.199,00!"
    assert [c.nome for c in resposta.chamadas] == ["detalhes_produto"]
    assert '"preco_atual": 2199.0' in resposta.chamadas[0].resultado
    resultado = provedor.historicos[1][-1]
    assert resultado["role"] == "ferramentas"
    assert resultado["resultados"][0]["id"] == "c1"
    assert [m["role"] for m in agente.mensagens] == [
        "user",
        "assistant",
        "ferramentas",
        "assistant",
    ]


def test_erro_de_ferramenta_volta_como_erro(ferramentas: Ferramentas) -> None:
    provedor = ProvedorFalso(
        [
            _chamada("consultar_pedido", {"numero_pedido": 8, "verificacao": "x"}),
            RespostaModelo("Não encontrei.", "fim"),
        ]
    )
    resposta = Agente(ferramentas, ferramentas.settings, provedor=provedor).responder("pedido 8")
    assert resposta.chamadas[0].erro
    assert provedor.historicos[1][-1]["resultados"][0]["erro"] is True


def test_falha_de_api_desfaz_turno(ferramentas: Ferramentas) -> None:
    agente = Agente(ferramentas, ferramentas.settings, provedor=ProvedorFalso([ErroFalso()]))
    assert agente.responder("oi").texto == MSG_FALHA
    assert agente.mensagens == []


def test_recusa_vira_mensagem_educada(ferramentas: Ferramentas) -> None:
    provedor = ProvedorFalso([RespostaModelo("", "recusa")])
    resposta = Agente(ferramentas, ferramentas.settings, provedor=provedor).responder("x")
    assert resposta.texto == MSG_RECUSA


# ------------------------------------------------------------------- Anthropic
HISTORICO_NEUTRO: list[dict[str, Any]] = [
    {"role": "user", "texto": "preço do GD20?"},
    {
        "role": "assistant",
        "provedor": "outro",
        "texto": "",
        "chamadas": [{"id": "c1", "nome": "detalhes_produto", "entrada": {"produto": "GD20"}}],
        "bruto": None,
    },
    {"role": "ferramentas", "resultados": [{"id": "c1", "conteudo": "{}", "erro": False}]},
]


class Bloco(SimpleNamespace):
    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


def _cliente_anthropic(resposta: SimpleNamespace) -> tuple[SimpleNamespace, list[dict[str, Any]]]:
    pedidos: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> SimpleNamespace:
        pedidos.append(kwargs)
        return resposta

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))), pedidos


def test_anthropic_converte_historico_e_resposta() -> None:
    cliente, pedidos = _cliente_anthropic(
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                Bloco(type="thinking", thinking="", signature="sig"),
                Bloco(type="tool_use", id="t9", name="status_loja", input={}),
            ],
        )
    )
    provedor = ProvedorAnthropic("claude-opus-5-5", "low", cliente=cliente)
    resposta = provedor.gerar("sistema", [], HISTORICO_NEUTRO)

    mensagens = pedidos[0]["messages"]
    assert mensagens[1]["content"][0] == {
        "type": "tool_use",
        "id": "c1",
        "name": "detalhes_produto",
        "input": {"produto": "GD20"},
    }
    assert mensagens[2]["content"][0]["tool_use_id"] == "c1"
    assert pedidos[0]["fallbacks"] == "default"
    assert resposta.parada == "ferramentas"
    assert resposta.chamadas == [ChamadaModelo("t9", "status_loja", {})]
    assert resposta.bruto[0]["type"] == "thinking"  # preservado para o próximo turno


def test_anthropic_reusa_bruto_do_mesmo_provedor() -> None:
    bruto = [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "oi"}]
    historico = [
        {"role": "user", "texto": "oi"},
        {
            "role": "assistant",
            "provedor": "anthropic",
            "texto": "oi",
            "chamadas": [],
            "bruto": bruto,
        },
    ]
    assert (
        ProvedorAnthropic("m", "low", cliente=object()).converter(historico)[1]["content"] == bruto
    )


def test_fallback_so_em_modelos_suportados() -> None:
    cliente, pedidos = _cliente_anthropic(
        SimpleNamespace(stop_reason="end_turn", content=[Bloco(type="text", text="oi")])
    )
    ProvedorAnthropic("claude-haiku-5-5", "low", cliente=cliente).gerar("s", [], [])
    assert "fallbacks" not in pedidos[0] and "betas" not in pedidos[0]


# ----------------------------------------------------------- OpenAI-compatível
class ToolCallFalso(SimpleNamespace):
    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
            "extra_content": {"google": {"thought_signature": "assinatura"}},
        }


def test_openai_compat_converte_ferramentas_e_preserva_assinatura() -> None:
    pedidos: list[dict[str, Any]] = []
    tool_call = ToolCallFalso(
        id="call_1",
        function=SimpleNamespace(name="detalhes_produto", arguments='{"produto": "GD20"}'),
    )
    resposta_api = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(content=None, tool_calls=[tool_call]),
            )
        ]
    )

    def create(**kwargs: Any) -> SimpleNamespace:
        pedidos.append(kwargs)
        return resposta_api

    cliente = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provedor = ProvedorOpenAICompat("gemini", "gemini-3.5-flash", cliente)
    definicao = {"name": "detalhes_produto", "description": "d", "input_schema": {"type": "object"}}
    resposta = provedor.gerar("sistema", [definicao], HISTORICO_NEUTRO)

    enviado = pedidos[0]
    assert enviado["messages"][0] == {"role": "system", "content": "sistema"}
    assert enviado["messages"][2]["tool_calls"][0]["function"]["name"] == "detalhes_produto"
    assert enviado["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}
    assert enviado["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert resposta.parada == "ferramentas"
    assert resposta.chamadas == [ChamadaModelo("call_1", "detalhes_produto", {"produto": "GD20"})]

    # No turno seguinte a mensagem crua (com a thought signature) volta intacta.
    historico = [
        {"role": "user", "texto": "x"},
        {
            "role": "assistant",
            "provedor": "gemini",
            "texto": "",
            "chamadas": [],
            "bruto": resposta.bruto,
        },
    ]
    reenviada = provedor.converter(historico)[1]
    assert (
        reenviada["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "assinatura"
    )


# ------------------------------------------------------------------- histórico
def test_historico_persiste(tmp_path: Any) -> None:
    repo = RepositorioHistorico(tmp_path)
    repo.salvar("abc", [{"role": "user", "texto": "oi"}])
    assert repo.carregar("abc") == [{"role": "user", "texto": "oi"}]
    repo.salvar("antigo", [{"role": "user", "content": "oi"}])
    assert repo.carregar("antigo") == []  # formato antigo é descartado
    with pytest.raises(ValueError):
        repo.carregar("../fora")
