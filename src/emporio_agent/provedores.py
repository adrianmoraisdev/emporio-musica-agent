"""Adaptadores de provedor de LLM.

O agente guarda o histórico num formato neutro e cada provedor converte para a sua API:

    {"role": "user", "texto": "..."}
    {"role": "assistant", "provedor": "anthropic", "texto": "...",
     "chamadas": [{"id": "...", "nome": "...", "entrada": {...}}], "bruto": <resposta crua>}
    {"role": "ferramentas", "resultados": [{"id": "...", "conteudo": "...", "erro": false}]}

`bruto` guarda a mensagem exatamente como o provedor devolveu: o Claude exige que blocos
de thinking voltem intactos e o Gemini exige o mesmo para as "thought signatures" das
chamadas de ferramenta. Se a conversa trocar de provedor, o histórico é reconstruído a
partir dos campos neutros.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
import openai

from emporio_agent.config import Settings

log = logging.getLogger(__name__)

Parada = Literal["fim", "ferramentas", "recusa", "limite"]
MAX_TOKENS = 16_000

# Em uma recusa dos classificadores de segurança, a API da Anthropic refaz o pedido em
# outro modelo automaticamente. O modo "default" só existe para estes modelos.
BETAS_FALLBACK = ["server-side-fallback-2026-07-01"]
MODELOS_COM_FALLBACK = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass(frozen=True, slots=True)
class ChamadaModelo:
    id: str
    nome: str
    entrada: dict[str, Any]


@dataclass
class RespostaModelo:
    texto: str
    parada: Parada
    chamadas: list[ChamadaModelo] = field(default_factory=list)
    bruto: Any = None


class Provedor(Protocol):
    nome: str
    modelo: str
    erros_api: tuple[type[Exception], ...]

    def gerar(
        self, sistema: str, ferramentas: list[dict[str, Any]], historico: list[dict[str, Any]]
    ) -> RespostaModelo: ...


def mensagem_assistente(provedor: str, resposta: RespostaModelo) -> dict[str, Any]:
    return {
        "role": "assistant",
        "provedor": provedor,
        "texto": resposta.texto,
        "chamadas": [{"id": c.id, "nome": c.nome, "entrada": c.entrada} for c in resposta.chamadas],
        "bruto": resposta.bruto,
    }


# --------------------------------------------------------------------------- Anthropic
class ProvedorAnthropic:
    nome = "anthropic"
    erros_api: tuple[type[Exception], ...] = (anthropic.APIError,)

    def __init__(self, modelo: str, esforco: str, cliente: Any = None) -> None:
        self.modelo = modelo
        self.esforco = esforco
        # Any: mensagens e ferramentas são dicts JSON neutros, não os TypedDicts do SDK.
        self.cliente: Any = cliente or anthropic.Anthropic()

    def gerar(
        self, sistema: str, ferramentas: list[dict[str, Any]], historico: list[dict[str, Any]]
    ) -> RespostaModelo:
        extras: dict[str, Any] = {}
        if self.modelo in MODELOS_COM_FALLBACK:
            extras = {"betas": BETAS_FALLBACK, "fallbacks": "default"}
        resposta = self.cliente.beta.messages.create(
            model=self.modelo,
            max_tokens=MAX_TOKENS,
            system=sistema,
            tools=ferramentas,
            messages=self.converter(historico),
            output_config={"effort": self.esforco},
            cache_control={"type": "ephemeral"},
            **extras,
        )
        parada: Parada = {
            "tool_use": "ferramentas",
            "refusal": "recusa",
            "max_tokens": "limite",
        }.get(resposta.stop_reason, "fim")  # type: ignore[assignment]
        return RespostaModelo(
            texto="\n".join(b.text for b in resposta.content if b.type == "text").strip(),
            parada=parada,
            chamadas=[
                ChamadaModelo(b.id, b.name, dict(b.input) if isinstance(b.input, dict) else {})
                for b in resposta.content
                if b.type == "tool_use"
            ],
            bruto=[b.to_dict() for b in resposta.content],
        )

    def converter(self, historico: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mensagens: list[dict[str, Any]] = []
        for msg in historico:
            if msg["role"] == "user":
                mensagens.append({"role": "user", "content": msg["texto"]})
            elif msg["role"] == "assistant":
                if msg.get("provedor") == self.nome and msg.get("bruto"):
                    conteudo = msg["bruto"]
                else:
                    conteudo = [{"type": "text", "text": msg["texto"]}] if msg["texto"] else []
                    conteudo += [
                        {
                            "type": "tool_use",
                            "id": c["id"],
                            "name": c["nome"],
                            "input": c["entrada"],
                        }
                        for c in msg["chamadas"]
                    ]
                mensagens.append({"role": "assistant", "content": conteudo})
            else:  # resultados de ferramentas: todos juntos em uma única mensagem
                mensagens.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r["id"],
                                "content": r["conteudo"],
                                "is_error": r["erro"],
                            }
                            for r in msg["resultados"]
                        ],
                    }
                )
        return mensagens


# ------------------------------------------------- OpenAI-compatível (Gemini, Ollama...)
class ProvedorOpenAICompat:
    """Qualquer API no formato Chat Completions: Gemini, Ollama, Groq, OpenAI etc."""

    erros_api: tuple[type[Exception], ...] = (openai.APIError,)

    def __init__(self, nome: str, modelo: str, cliente: Any) -> None:
        self.nome = nome
        self.modelo = modelo
        self.cliente = cliente

    def gerar(
        self, sistema: str, ferramentas: list[dict[str, Any]], historico: list[dict[str, Any]]
    ) -> RespostaModelo:
        resposta = self.cliente.chat.completions.create(
            model=self.modelo,
            messages=[{"role": "system", "content": sistema}, *self.converter(historico)],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": f["name"],
                        "description": f["description"],
                        "parameters": f["input_schema"],
                    },
                }
                for f in ferramentas
            ],
        )
        escolha = resposta.choices[0]
        mensagem = escolha.message
        chamadas: list[ChamadaModelo] = []
        tool_calls_brutas: list[dict[str, Any]] = []
        for tc in mensagem.tool_calls or []:
            id_ = tc.id or f"call_{uuid.uuid4().hex[:12]}"
            try:
                entrada = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                log.warning("argumentos inválidos na chamada %s", tc.function.name)
                entrada = {}
            chamadas.append(ChamadaModelo(id_, tc.function.name, entrada))
            # model_dump preserva campos extras (ex.: extra_content.google.thought_signature).
            tool_calls_brutas.append({**tc.model_dump(exclude_none=True), "id": id_})
        bruto: dict[str, Any] = {"role": "assistant", "content": mensagem.content}
        if tool_calls_brutas:
            bruto["tool_calls"] = tool_calls_brutas
        parada: Parada
        if chamadas:
            parada = "ferramentas"
        elif escolha.finish_reason == "length":
            parada = "limite"
        elif escolha.finish_reason == "content_filter":
            parada = "recusa"
        else:
            parada = "fim"
        return RespostaModelo((mensagem.content or "").strip(), parada, chamadas, bruto)

    def converter(self, historico: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mensagens: list[dict[str, Any]] = []
        for msg in historico:
            if msg["role"] == "user":
                mensagens.append({"role": "user", "content": msg["texto"]})
            elif msg["role"] == "assistant":
                if msg.get("provedor") == self.nome and msg.get("bruto"):
                    mensagens.append(msg["bruto"])
                    continue
                convertida: dict[str, Any] = {"role": "assistant", "content": msg["texto"] or None}
                if msg["chamadas"]:
                    convertida["tool_calls"] = [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {
                                "name": c["nome"],
                                "arguments": json.dumps(c["entrada"], ensure_ascii=False),
                            },
                        }
                        for c in msg["chamadas"]
                    ]
                mensagens.append(convertida)
            else:
                mensagens.extend(
                    {"role": "tool", "tool_call_id": r["id"], "content": r["conteudo"]}
                    for r in msg["resultados"]
                )
        return mensagens


def criar_provedor(settings: Settings) -> Provedor:
    if settings.provedor == "anthropic":
        return ProvedorAnthropic(settings.modelo, settings.esforco)
    if settings.provedor == "gemini":
        cliente = openai.OpenAI(
            api_key=os.environ["GEMINI_API_KEY"], base_url=GEMINI_BASE_URL, max_retries=4
        )
        return ProvedorOpenAICompat("gemini", settings.modelo, cliente)
    if settings.provedor == "openai-compat":
        cliente = openai.OpenAI(
            api_key=os.getenv("EMPORIO_API_KEY", "nao-usada"),
            base_url=os.getenv("EMPORIO_BASE_URL", "http://localhost:11434/v1"),
        )
        return ProvedorOpenAICompat("openai-compat", settings.modelo, cliente)
    raise ValueError(f"provedor desconhecido: {settings.provedor!r}")
