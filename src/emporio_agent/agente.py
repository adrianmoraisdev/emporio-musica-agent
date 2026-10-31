"""Loop do agente: Claude decide quais ferramentas chamar; o código executa e devolve.

Usamos um loop manual (e não o tool runner beta do SDK) para registrar cada chamada de
ferramenta — isso alimenta o modo debug da CLI e os exemplos de conversa do repositório.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic

from emporio_agent.config import Settings
from emporio_agent.ferramentas import ErroFerramenta, Ferramentas
from emporio_agent.prompt import montar_prompt_sistema

log = logging.getLogger(__name__)

# Em uma recusa dos classificadores de segurança, a API refaz o pedido em outro modelo
# automaticamente (evita que um falso positivo vire "atendimento fora do ar").
# O modo "default" só existe para estes modelos; o Haiku não tem fallback server-side.
BETAS = ["server-side-fallback-2026-07-01"]
MODELOS_COM_FALLBACK = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")
MAX_TOKENS = 16_000

MSG_FALHA = (
    "Poxa, tive um probleminha técnico agora. Pode tentar de novo em instantes? "
    "Se preferir, fale com a equipe pelo WhatsApp da loja."
)


@dataclass(frozen=True, slots=True)
class ChamadaFerramenta:
    nome: str
    entrada: dict[str, Any]
    resultado: str
    erro: bool


@dataclass
class Resposta:
    texto: str
    chamadas: list[ChamadaFerramenta] = field(default_factory=list)


class Agente:
    def __init__(
        self,
        ferramentas: Ferramentas,
        settings: Settings,
        cliente: anthropic.Anthropic | None = None,
        historico: list[dict[str, Any]] | None = None,
    ) -> None:
        self.ferramentas = ferramentas
        self.settings = settings
        self.cliente = cliente or anthropic.Anthropic()
        self.mensagens: list[dict[str, Any]] = historico or []
        self._sistema = montar_prompt_sistema(ferramentas.politicas.sumario())

    def responder(self, texto_usuario: str) -> Resposta:
        """Processa uma mensagem do cliente e devolve a resposta final do agente."""
        inicio = len(self.mensagens)
        self.mensagens.append({"role": "user", "content": texto_usuario})
        chamadas: list[ChamadaFerramenta] = []
        try:
            for _ in range(self.settings.max_iteracoes_ferramentas):
                resposta = self._chamar_modelo()
                self.mensagens.append(
                    {"role": "assistant", "content": [b.to_dict() for b in resposta.content]}
                )
                if resposta.stop_reason != "tool_use":
                    return Resposta(self._texto_final(resposta), chamadas)
                resultados = [
                    self._executar(bloco, chamadas)
                    for bloco in resposta.content
                    if bloco.type == "tool_use"
                ]
                # Todos os tool_results de um turno vão juntos em uma única mensagem.
                self.mensagens.append({"role": "user", "content": resultados})
            log.warning("limite de iterações de ferramentas atingido")
            return Resposta(
                "Essa ficou complexa para mim. Vou deixar a equipe da loja te ajudar, ok?",
                chamadas,
            )
        except anthropic.APIError:
            log.exception("falha na API do modelo")
            # Desfaz o turno incompleto para o histórico continuar válido.
            del self.mensagens[inicio:]
            return Resposta(MSG_FALHA, chamadas)

    def _chamar_modelo(self) -> Any:
        extras: dict[str, Any] = {}
        if self.settings.modelo in MODELOS_COM_FALLBACK:
            extras = {"betas": BETAS, "fallbacks": "default"}
        # Mensagens/ferramentas são dicts JSON (persistidos em disco), não os TypedDicts do SDK.
        return self.cliente.beta.messages.create(  # type: ignore[call-overload]
            model=self.settings.modelo,
            max_tokens=MAX_TOKENS,
            system=self._sistema,
            tools=self.ferramentas.definicoes,
            messages=self.mensagens,
            output_config={"effort": self.settings.esforco},
            cache_control={"type": "ephemeral"},
            **extras,
        )

    def _executar(self, bloco: Any, chamadas: list[ChamadaFerramenta]) -> dict[str, Any]:
        entrada = dict(bloco.input) if isinstance(bloco.input, dict) else {}
        try:
            resultado, erro = self.ferramentas.executar(bloco.name, entrada), False
        except ErroFerramenta as exc:
            resultado, erro = json.dumps({"erro": str(exc)}, ensure_ascii=False), True
        except Exception:
            log.exception("erro inesperado na ferramenta %s", bloco.name)
            resultado, erro = '{"erro": "falha interna ao consultar o sistema"}', True
        chamadas.append(ChamadaFerramenta(bloco.name, entrada, resultado, erro))
        return {
            "type": "tool_result",
            "tool_use_id": bloco.id,
            "content": resultado,
            "is_error": erro,
        }

    @staticmethod
    def _texto_final(resposta: Any) -> str:
        if resposta.stop_reason == "refusal":
            return (
                "Desculpa, não consigo ajudar com isso por aqui. Posso te ajudar com "
                "instrumentos, pedidos ou dúvidas sobre a loja?"
            )
        texto = "\n".join(b.text for b in resposta.content if b.type == "text").strip()
        if resposta.stop_reason == "max_tokens":
            log.warning("resposta truncada por max_tokens")
        return texto or "Hmm, não consegui formular uma resposta. Pode reformular?"
