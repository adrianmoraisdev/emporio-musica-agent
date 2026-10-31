"""Writes SIMULATED example transcripts when no API key is available.

The tool calls are executed for real against the dataset (same `Ferramentas` the agent
uses); only the agent's replies are scripted below. Every reply is checked so that each
R$ amount it quotes appears in a tool result or in the customer's own messages — the same
"never quote a number the system didn't return" rule the agent follows.

Each file is labeled as simulated. Regenerate from the real agent with
`python scripts/gerar_exemplos.py` once ANTHROPIC_API_KEY is set.

Usage:  python scripts/simular_exemplos.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Fixed date/time so the transcripts are reproducible (Wednesday, store open).
os.environ["EMPORIO_HOJE"] = "2026-03-25T15:40"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gerar_exemplos import CENARIOS, Turno, renderizar

from emporio_agent.agente import ChamadaFerramenta
from emporio_agent.catalogo import Catalogo
from emporio_agent.config import RAIZ_PROJETO, carregar_settings
from emporio_agent.ferramentas import ErroFerramenta, Ferramentas
from emporio_agent.politicas import BasePoliticas

AVISO = (
    "**SIMULATED transcript.** No API key was available when this file was produced. The "
    "tool calls and their results are real: they come from running the project's tools on "
    "the dataset (`scripts/simular_exemplos.py`). The agent replies were written by the "
    "coding assistant (Claude Code), acting as the model and following the agent's system "
    "prompt (`src/emporio_agent/prompt.py`); they were not produced by running "
    "`src/emporio_agent/agente.py`. Every R$ amount in the replies is checked against the "
    "tool results. Simulated date: 2026-03-25 15:40 (Wednesday). To regenerate from the "
    "real agent: `python scripts/gerar_exemplos.py`."
)

Chamada = tuple[str, dict[str, Any]]

# One entry per customer message: (tool calls the agent makes, agent reply).
ROTEIRO: dict[str, list[tuple[list[Chamada], str]]] = {
    "01-catalogo-violoes-ate-1000": [
        (
            [("buscar_produtos", {"categoria": "violões", "preco_max": 1000, "limite": 20})],
            "Oi! Que legal que você está procurando um violão 🎸 Temos *12 modelos "
            "disponíveis* até R$ 1.000,00:\n\n"
            "*Cordas de nylon (clássicos)*\n"
            "• Tagima Memphis AC-39 — R$ 429,90\n"
            "• Rozini RC-104 Estudante — R$ 459,90\n"
            "• Yamaha C40 — R$ 599,90\n"
            "• Giannini GN-15 (tampo em cedro) — R$ 689,00\n"
            "• Yamaha C70 — R$ 849,00\n\n"
            "*Cordas de aço (folk/dreadnought)*\n"
            "• Shelby SN-100 Folk Preto — R$ 549,00\n"
            "• Giannini GF-1R Folk Sunburst — R$ 579,90\n"
            "• Yamaha F310 — R$ 699,90\n"
            "• Tagima Dallas Tuner — R$ 749,00\n"
            "• Shelby SGD-195E Elétrico Sunburst — R$ 799,00\n"
            "• Tagima Woodstock Dreadnought — R$ 899,00\n"
            "• Tagima TW-7 7 Cordas — R$ 999,00\n\n"
            "Quer ajuda pra escolher? Me conta o que você curte tocar 😉",
        ),
        (
            [
                ("detalhes_produto", {"produto": "Yamaha C40"}),
                ("detalhes_produto", {"produto": "Tagima Memphis AC-39"}),
            ],
            "Pra quem está começando, eu iria de *cordas de nylon*: são mais macias nos "
            "dedos e facilitam muito os primeiros meses 🙌 Minhas duas sugestões:\n\n"
            "• *Yamaha C40* — R$ 599,90 (R$ 569,90 no PIX ou até 6x sem juros de "
            "R$ 99,98). Tampo em spruce e ótima projeção sonora; é indicado justamente "
            "para estudantes e iniciantes. Temos 12 em estoque.\n"
            "• *Tagima Memphis AC-39* — R$ 429,90 (R$ 408,40 no PIX ou até 5x sem juros "
            "de R$ 85,98). Corpo leve e confortável para longas sessões de estudo, a "
            "opção mais em conta. Temos 15 em estoque.\n\n"
            "Se a sua praia for pop/rock com palheta, um de aço como o Yamaha F310 "
            "(R$ 699,90) também é uma ótima porta de entrada. Quer detalhes de algum?",
        ),
        (
            [("detalhes_produto", {"produto": "Giannini GF-3D Dreadnought"})],
            "Esse modelo está *temporariamente esgotado* 😕 (por isso ele não apareceu na "
            "lista). Separei opções parecidas, também com cordas de aço, que temos agora:\n\n"
            "• Tagima Woodstock Dreadnought Natural — R$ 899,00 (mesmo formato "
            "dreadnought, 11 em estoque)\n"
            "• Shelby SGD-195E Elétrico Aço Sunburst — R$ 799,00 (6 em estoque)\n"
            "• Tagima Dallas Tuner Aço Natural — R$ 749,00 (9 em estoque)\n\n"
            "Quer que eu te passe os detalhes de algum deles?",
        ),
    ],
    "02-endereco-e-horario": [
        (
            [("consultar_politicas", {"consulta": "endereço da loja"})],
            "A gente fica na *Rua 14 de Maio, 3200 — Centro, Campo Grande/MS* "
            "(CEP 79202-333) 📍\n\n"
            "Se quiser falar com a equipe: (67) 3341-4444 ou "
            "contato@emporiodamusica.com.br. Posso ajudar em mais alguma coisa?",
        ),
        (
            [
                ("consultar_politicas", {"consulta": "horário de funcionamento sábado"}),
                ("status_loja", {}),
            ],
            "Abrimos sim! Aos sábados o horário é das *9h às 13h*. De segunda a sexta é "
            "das 9h às 18h, e domingos e feriados ficamos fechados.\n\n"
            "E agora (quarta, 15h40) estamos *abertos* — dá tempo de passar aqui até as "
            "18h 😊 Em datas especiais, como Black Friday e Natal, o horário pode ser "
            "estendido; a gente avisa nas redes sociais.",
        ),
    ],
    "03-preco-pix-parcelamento-promocao": [
        (
            [("detalhes_produto", {"produto": "Takamine GD20"})],
            "O *Takamine GD20 Dreadnought Natural* sai por *R$ 2.199,00* 🎸\n\n"
            "• No PIX: R$ 2.089,05 (5% de desconto)\n"
            "• No cartão: até 12x sem juros de R$ 183,25\n\n"
            "Temos 5 unidades em estoque. Quer saber mais sobre ele?",
        ),
        (
            [],
            "Dá pra parcelar em até *12x sem juros* de R$ 183,25 no cartão de crédito 💳 "
            "Se preferir menos parcelas, também é tudo sem juros. E à vista no PIX ele sai "
            "por R$ 2.089,05.",
        ),
        (
            [
                ("detalhes_produto", {"produto": "Crafter HT-100"}),
                (
                    "consultar_politicas",
                    {"consulta": "desconto pix cumulativo com promoção", "secoes": ["6.2"]},
                ),
            ],
            "O *Crafter HT-100 Folk Aço Natural* está em promoção 🎉\n\n"
            "• De R$ 2.399,00 por *R$ 1.967,18* (18% off — Desconto Exclusivo)\n\n"
            "Sobre o PIX: nesse caso, não 😕 Pelas regras da loja as promoções não são "
            "cumulativas, então o desconto de 5% do PIX não vale sobre preço promocional — "
            "no PIX ele sai pelos mesmos R$ 1.967,18. No cartão dá pra fazer em até 12x "
            "sem juros de R$ 163,93. Temos 3 em estoque!",
        ),
    ],
    "04-devolucao-arrependimento": [
        (
            [],
            "Poxa, entendo! Vou verificar pra você 😊 Me passa, por favor, o *número do "
            "pedido* e, pra confirmar que é você, o *telefone, e-mail ou nome completo* do "
            "titular da compra.",
        ),
        (
            [
                (
                    "consultar_pedido",
                    {"numero_pedido": 7, "verificacao": "Letícia Gonçalves Rocha"},
                ),
                (
                    "consultar_politicas",
                    {"consulta": "devolução por arrependimento", "secoes": ["4.1"]},
                ),
            ],
            "Obrigada, Letícia! Encontrei o pedido *#7*: ukulele *Shelby SU-21S Soprano "
            "Sunburst*, R$ 159,90 no cartão em 3x, comprado em 03/02/2026 e com entrega "
            "prevista para 17/02/2026.\n\n"
            "Infelizmente a devolução por arrependimento não é mais possível 😕 Pelo nosso "
            "manual (e pelo Código de Defesa do Consumidor), ela pode ser pedida em até *7 "
            "dias corridos após o recebimento* — no seu caso, o prazo foi até 24/02/2026. O "
            "prazo de troca por preferência (também 7 dias) já passou.\n\n"
            "Se o motivo for algum problema com o instrumento, me conta que eu te explico "
            "as opções!",
        ),
        (
            [
                (
                    "consultar_politicas",
                    {"consulta": "produto com defeito garantia", "secoes": ["4.2", "8.1"]},
                )
            ],
            "Boa pergunta! A troca direta na loja por defeito vale até 30 dias corridos "
            "após a compra — no seu caso, até 05/03/2026, então essa já não se aplica. Mas "
            "você *ainda está na garantia legal de 90 dias*, válida até 18/05/2026 🙌\n\n"
            "Ela cobre defeitos de fabricação (danos por mau uso, queda ou umidade não "
            "entram). Passado o prazo de troca na loja, a garantia é acionada com o "
            "fabricante — e a Empório pode *intermediar* esse processo pra você. Se o "
            "ukulele apresentar algum defeito, é só me chamar descrevendo o problema que eu "
            "encaminho pra nossa equipe 😉",
        ),
    ],
    "05-fora-de-escopo": [
        (
            [("consultar_politicas", {"consulta": "vendem cordas e amplificador"})],
            "Que pena, mas não 😕 A Empório trabalha *exclusivamente com instrumentos "
            "musicais* — não vendemos acessórios como cordas, palhetas, cabos, cases, "
            "pedais ou amplificadores. Pra esses itens, o melhor é procurar uma loja "
            "especializada em acessórios.\n\n"
            "Agora, se estiver pensando em um instrumento novo, estou aqui pra ajudar! 🎸",
        ),
        (
            [],
            "Haha, essa receita eu fico devendo 🍰 Por aqui eu só consigo ajudar com "
            "assuntos da Empório da Música: instrumentos, preços, pedidos e políticas da "
            "loja. Posso te ajudar com algo nessa linha?",
        ),
        (
            [],
            "Não posso fazer isso 🙏 Dados de clientes são protegidos pela LGPD e eu não "
            "compartilho informações de outras pessoas. Se você for o titular do pedido 1 "
            "e quiser consultá-lo, me passe o número do pedido junto com seu telefone, "
            "e-mail ou nome completo que eu verifico pra você.",
        ),
    ],
}

_VALOR = re.compile(r"R\$\s?(\d+(?:\.\d{3})*(?:,\d{2})?)")


def _valores_em_texto(texto: str) -> set[float]:
    return {float(v.replace(".", "").replace(",", ".")) for v in _VALOR.findall(texto)}


def _numeros_em_json(dado: Any) -> set[float]:
    if isinstance(dado, bool):
        return set()
    if isinstance(dado, int | float):
        return {float(dado)}
    if isinstance(dado, str):
        return _valores_em_texto(dado)
    if isinstance(dado, dict):
        return set().union(*(_numeros_em_json(v) for v in dado.values()))
    if isinstance(dado, list):
        return set().union(*(_numeros_em_json(v) for v in dado))
    return set()


def simular(nome: str, ferramentas: Ferramentas) -> str:
    cenario = CENARIOS[nome]
    roteiro = ROTEIRO[nome]
    if len(roteiro) != len(cenario.falas):
        raise ValueError(f"{nome}: {len(roteiro)} respostas para {len(cenario.falas)} falas")
    conhecidos: set[float] = set()
    turnos: list[Turno] = []
    for (fala, traducao), (chamadas, resposta) in zip(cenario.falas, roteiro, strict=True):
        conhecidos |= _valores_em_texto(fala)
        executadas: list[ChamadaFerramenta] = []
        for nome_ferramenta, entrada in chamadas:
            try:
                resultado, erro = ferramentas.executar(nome_ferramenta, entrada), False
            except ErroFerramenta as exc:
                resultado, erro = json.dumps({"erro": str(exc)}, ensure_ascii=False), True
            executadas.append(ChamadaFerramenta(nome_ferramenta, entrada, resultado, erro))
            conhecidos |= _numeros_em_json(json.loads(resultado))
        inventados = _valores_em_texto(resposta) - conhecidos
        if inventados:
            raise ValueError(f"{nome}: valores sem origem nas ferramentas: {inventados}")
        turnos.append(Turno(fala, traducao, tuple(executadas), resposta))
    return renderizar(cenario, turnos, AVISO)


def main() -> int:
    settings = carregar_settings()
    catalogo = Catalogo.carregar(settings.dir_dados)
    politicas = BasePoliticas.carregar(settings.dir_dados / "politicas_da_loja.md")
    destino = RAIZ_PROJETO / "examples"
    destino.mkdir(exist_ok=True)
    for nome in CENARIOS:
        # Ferramentas novas por cenário: cada conversa é uma sessão independente.
        conteudo = simular(nome, Ferramentas(catalogo, politicas, settings))
        (destino / f"{nome}.md").write_text(conteudo, encoding="utf-8")
        print(f"[{nome}] ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
