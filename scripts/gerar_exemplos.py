"""Roda conversas roteirizadas contra o agente real e grava as transcrições em examples/.

Uso:  python scripts/gerar_exemplos.py   (requer ANTHROPIC_API_KEY no ambiente ou no .env)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from emporio_agent.cli import criar_agente
from emporio_agent.config import RAIZ_PROJETO, carregar_settings
from emporio_agent.prompt import PERSONA

CENARIOS: dict[str, tuple[str, list[str]]] = {
    "01-catalogo-violoes-ate-1000": (
        "Busca no catálogo com filtro de categoria e preço",
        [
            "Oi! Quais opções de violões disponíveis custando até R$1000?",
            "Sou iniciante, qual desses você indicaria?",
        ],
    ),
    "02-preco-promocao-parcelamento": (
        "Consulta de preço, PIX, parcelamento e regra de promoção não cumulativa",
        [
            "Quanto custa o Takamine GD20?",
            "E o Crafter HT-100? Se eu pagar no PIX ganho os 5% também?",
        ],
    ),
    "03-devolucao-com-verificacao": (
        "Não trivial: verificação de identidade (LGPD) + aplicação da política de devolução",
        [
            "Me arrependi da minha compra, posso devolver meu pedido?",
            "É o pedido 7, no nome de Letícia Gonçalves Rocha",
            "Entendi. E se o ukulele apresentar defeito, o que eu faço?",
        ],
    ),
    "04-status-pedido-e-esgotado": (
        "Rastreio de pedido e produto esgotado com alternativas",
        [
            "Oi, cadê meu pedido 8? Meu telefone é (67) 99654-7821",
            "Aproveitando: vocês têm o Giannini GF-3D Dreadnought?",
        ],
    ),
    "05-institucional-e-fora-de-escopo": (
        "Endereço/horário via manual, acessórios fora do catálogo e pergunta fora do escopo",
        [
            "Qual o endereço da loja? Vocês abrem sábado?",
            "Vocês vendem cordas e amplificador?",
            "Beleza. Me passa uma receita de bolo de cenoura?",
        ],
    ),
}


def gerar(nome: str, descricao: str, falas: list[str], destino: Path) -> None:
    agente, _ = criar_agente(f"exemplo-{nome}", nova=True)
    settings = carregar_settings()
    linhas = [
        f"# {descricao}",
        "",
        f"> Gerado por `scripts/gerar_exemplos.py` com `{settings.modelo}` "
        f"(esforço `{settings.esforco}`), data simulada {settings.hoje():%d/%m/%Y}.",
        "",
    ]
    for fala in falas:
        resposta = agente.responder(fala)
        linhas += [f"**Cliente:** {fala}", ""]
        if resposta.chamadas:
            linhas += ["<details><summary>Ferramentas consultadas</summary>", ""]
            for c in resposta.chamadas:
                entrada = json.dumps(c.entrada, ensure_ascii=False)
                marca = " ⚠️ erro" if c.erro else ""
                linhas += [f"- `{c.nome}({entrada})`{marca}"]
            linhas += ["", "</details>", ""]
        linhas += [f"**{PERSONA}:** {resposta.texto}", "", "---", ""]
        print(f"[{nome}] {fala[:40]}... -> {len(resposta.chamadas)} ferramenta(s)")
    destino.write_text("\n".join(linhas), encoding="utf-8")


def main() -> int:
    destino = RAIZ_PROJETO / "examples"
    destino.mkdir(exist_ok=True)
    filtro = set(sys.argv[1:])
    for nome, (descricao, falas) in CENARIOS.items():
        if filtro and nome not in filtro:
            continue
        gerar(nome, descricao, falas, destino / f"{nome}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
