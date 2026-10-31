"""Loop do agente: o LLM decide quais ferramentas chamar; o código executa e devolve.

Usamos um loop manual (e não o tool runner de um SDK) por dois motivos: ele registra cada
chamada de ferramenta — o que alimenta o modo debug da CLI e os exemplos do repositório — e
funciona igual para qualquer provedor (ver `provedores.py`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from emporio_agent.config import Settings
from emporio_agent.ferramentas import ErroFerramenta, Ferramentas
from emporio_agent.prompt import montar_prompt_sistema
from emporio_agent.provedores import (
    ChamadaModelo,
    Provedor,
    RespostaModelo,
    criar_provedor,
    mensagem_assistente,
)

log = logging.getLogger(__name__)

MSG_FALHA = (
    "Poxa, tive um probleminha técnico agora. Pode tentar de novo em instantes? "
    "Se preferir, fale com a equipe pelo WhatsApp da loja."
)
MSG_RECUSA = (
    "Desculpa, não consigo ajudar com isso por aqui. Posso te ajudar com "
    "instrumentos, pedidos ou dúvidas sobre a loja?"
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
        provedor: Provedor | None = None,
        historico: list[dict[str, Any]] | None = None,
    ) -> None:
        self.ferramentas = ferramentas
        self.settings = settings
        self.provedor = provedor or criar_provedor(settings)
        self.mensagens: list[dict[str, Any]] = historico or []
        self._sistema = montar_prompt_sistema(ferramentas.politicas.sumario())

    def responder(self, texto_usuario: str) -> Resposta:
        """Processa uma mensagem do cliente e devolve a resposta final do agente."""
        inicio = len(self.mensagens)
        self.mensagens.append({"role": "user", "texto": texto_usuario})
        chamadas: list[ChamadaFerramenta] = []
        try:
            for _ in range(self.settings.max_iteracoes_ferramentas):
                resposta = self.provedor.gerar(
                    self._sistema, self.ferramentas.definicoes, self.mensagens
                )
                self.mensagens.append(mensagem_assistente(self.provedor.nome, resposta))
                if resposta.parada != "ferramentas":
                    return Resposta(self._texto_final(resposta), chamadas)
                resultados = [self._executar(c, chamadas) for c in resposta.chamadas]
                self.mensagens.append({"role": "ferramentas", "resultados": resultados})
            log.warning("limite de iterações de ferramentas atingido")
            return Resposta(
                "Essa ficou complexa para mim. Vou deixar a equipe da loja te ajudar, ok?",
                chamadas,
            )
        except self.provedor.erros_api:
            log.exception("falha na API do modelo")
            # Desfaz o turno incompleto para o histórico continuar válido.
            del self.mensagens[inicio:]
            return Resposta(MSG_FALHA, chamadas)

    def _executar(
        self, chamada: ChamadaModelo, chamadas: list[ChamadaFerramenta]
    ) -> dict[str, Any]:
        try:
            resultado, erro = self.ferramentas.executar(chamada.nome, chamada.entrada), False
        except ErroFerramenta as exc:
            resultado, erro = json.dumps({"erro": str(exc)}, ensure_ascii=False), True
        except Exception:
            log.exception("erro inesperado na ferramenta %s", chamada.nome)
            resultado, erro = '{"erro": "falha interna ao consultar o sistema"}', True
        chamadas.append(ChamadaFerramenta(chamada.nome, chamada.entrada, resultado, erro))
        return {"id": chamada.id, "nome": chamada.nome, "conteudo": resultado, "erro": erro}

    @staticmethod
    def _texto_final(resposta: RespostaModelo) -> str:
        if resposta.parada == "recusa":
            return MSG_RECUSA
        if resposta.parada == "limite":
            log.warning("resposta truncada por limite de tokens")
        return resposta.texto or "Hmm, não consegui formular uma resposta. Pode reformular?"
