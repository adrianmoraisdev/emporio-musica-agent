"""Ferramentas (function calling) expostas ao LLM.

Cada ferramenta é uma função pura sobre o catálogo/políticas que devolve JSON. O LLM decide
*quando* chamar; os números e regras vêm sempre daqui. Erros de entrada voltam como
`ErroFerramenta`, que o agente repassa ao modelo como `tool_result` com `is_error`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from emporio_agent.catalogo import (
    DESCRICAO_STATUS_PEDIDO,
    Catalogo,
    Produto,
    StatusPedido,
    StatusProduto,
    descrever_pagamento,
    para_dict,
)
from emporio_agent.config import Settings
from emporio_agent.politicas import BasePoliticas
from emporio_agent.regras import opcoes_pagamento, prazos_pos_venda, status_loja

MAX_TENTATIVAS_VERIFICACAO = 3


class ErroFerramenta(Exception):
    """Erro de uso da ferramenta, com mensagem destinada ao modelo."""


DEFINICOES: list[dict[str, Any]] = [
    {
        "name": "buscar_produtos",
        "description": (
            "Busca instrumentos no catálogo da loja com filtros. Use para perguntas do tipo "
            "'quais violões até R$1000?', 'tem ukulele elétrico?', 'opções de guitarra'. "
            "Por padrão retorna só itens disponíveis para compra. Preços já consideram "
            "promoções ativas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "Palavras-chave livres: tipo, marca, característica "
                    "(ex.: 'violão nylon', 'Yamaha', 'elétrico', '7 cordas').",
                },
                "categoria": {
                    "type": "string",
                    "description": "Categoria (ex.: violões, guitarras, baixos, baterias, "
                    "teclados, ukuleles, sopro, cordas orquestrais).",
                },
                "preco_min": {"type": "number", "description": "Preço mínimo em reais."},
                "preco_max": {"type": "number", "description": "Preço máximo em reais."},
                "incluir_indisponiveis": {
                    "type": "boolean",
                    "description": "Inclui esgotados, descontinuados e lançamentos futuros.",
                },
                "limite": {"type": "integer", "description": "Máximo de itens (padrão 8)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "detalhes_produto",
        "description": (
            "Consulta preço, promoção vigente, preço no PIX, parcelamento, estoque, status e "
            "especificações de UM produto pelo nome (aproximado) ou id. Use sempre antes de "
            "informar preço ou disponibilidade de um item específico. Se o produto estiver "
            "indisponível, já retorna alternativas disponíveis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "produto": {
                    "type": "string",
                    "description": "Nome do produto como o cliente escreveu, ou o id numérico.",
                }
            },
            "required": ["produto"],
            "additionalProperties": False,
        },
    },
    {
        "name": "promocoes_ativas",
        "description": "Lista as promoções vigentes hoje, com preço original e com desconto.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "consultar_pedido",
        "description": (
            "Consulta status, itens, rastreio, previsão de entrega e prazos de troca/devolução "
            "de um pedido. Por LGPD exige confirmação de identidade: peça ao cliente o número "
            "do pedido E o telefone, e-mail ou nome completo do titular antes de chamar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "numero_pedido": {"type": "integer", "description": "Número do pedido."},
                "verificacao": {
                    "type": "string",
                    "description": "Telefone, e-mail ou nome completo do titular do pedido, "
                    "exatamente como informado pelo cliente.",
                },
            },
            "required": ["numero_pedido", "verificacao"],
            "additionalProperties": False,
        },
    },
    {
        "name": "consultar_politicas",
        "description": (
            "Busca no manual oficial da loja: endereço e contatos, horários, formas de "
            "pagamento e parcelamento, trocas e devoluções, frete e entrega, rastreamento, "
            "regras de promoções, garantia, privacidade e escopo (o que a loja vende). "
            "Use para qualquer regra ou informação institucional, nunca responda de memória."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "A dúvida do cliente em linguagem natural.",
                },
                "secoes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Opcional: números de seções do sumário (ex.: ['4.1']).",
                },
            },
            "required": ["consulta"],
            "additionalProperties": False,
        },
    },
    {
        "name": "status_loja",
        "description": (
            "Data/hora atual em Campo Grande e se a loja está aberta agora; se fechada, "
            "quando reabre. Use para 'vocês estão abertos?' ou para calcular prazos."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def _descrever_disponibilidade(produto: Produto) -> str:
    if produto.status is StatusProduto.DESCONTINUADO:
        return "descontinuado: não faz mais parte do catálogo"
    if produto.status is StatusProduto.EM_BREVE:
        return "lançamento em breve: ainda não disponível para venda"
    if produto.estoque == 0:
        return "temporariamente esgotado"
    return f"disponível ({produto.estoque} em estoque)"


@dataclass
class Ferramentas:
    catalogo: Catalogo
    politicas: BasePoliticas
    settings: Settings
    _tentativas: dict[int, int] = field(default_factory=dict)

    @property
    def definicoes(self) -> list[dict[str, Any]]:
        return DEFINICOES

    def executar(self, nome: str, entrada: dict[str, Any]) -> str:
        handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "buscar_produtos": self.buscar_produtos,
            "detalhes_produto": self.detalhes_produto,
            "promocoes_ativas": self.promocoes_ativas,
            "consultar_pedido": self.consultar_pedido,
            "consultar_politicas": self.consultar_politicas,
            "status_loja": self.status_loja,
        }
        handler = handlers.get(nome)
        if handler is None:
            raise ErroFerramenta(f"ferramenta desconhecida: {nome}")
        try:
            resultado = handler(**entrada)
        except TypeError as exc:
            raise ErroFerramenta(f"parâmetros inválidos para {nome}: {exc}") from exc
        return json.dumps(resultado, ensure_ascii=False)

    # ------------------------------------------------------------ produtos
    def buscar_produtos(
        self,
        consulta: str = "",
        categoria: str | None = None,
        preco_min: float | None = None,
        preco_max: float | None = None,
        incluir_indisponiveis: bool = False,
        limite: int = 8,
    ) -> dict[str, Any]:
        cat = None
        if categoria:
            cat = self.catalogo.categoria_por_nome(categoria)
            if cat is None:
                nomes = ", ".join(c.nome for c in self.catalogo.categorias.values())
                raise ErroFerramenta(f"categoria '{categoria}' não existe. Categorias: {nomes}")
        produtos = self.catalogo.buscar(
            consulta,
            categoria=cat,
            preco_min=preco_min,
            preco_max=preco_max,
            apenas_disponiveis=not incluir_indisponiveis,
        )
        limite = max(1, min(limite, 20))
        return {
            "total_encontrado": len(produtos),
            "exibindo": min(len(produtos), limite),
            "produtos": [para_dict(p, self.catalogo) for p in produtos[:limite]],
        }

    def detalhes_produto(self, produto: str) -> dict[str, Any]:
        candidatos: list[tuple[Produto, float]]
        termo = produto.strip()
        if termo.isdigit() and int(termo) in self.catalogo.produtos:
            candidatos = [(self.catalogo.produtos[int(termo)], 1.0)]
        else:
            candidatos = self.catalogo.buscar_por_nome(termo)
        if not candidatos:
            return {
                "encontrado": False,
                "mensagem": f"Nenhum produto do catálogo corresponde a '{produto}'.",
            }

        item, confianca = candidatos[0]
        promo = self.catalogo.promocoes_ativas.get(item.id)
        preco = self.catalogo.preco_final(item)
        pagamento = opcoes_pagamento(preco, em_promocao=promo is not None)
        dados = para_dict(item, self.catalogo) | {
            "encontrado": True,
            "confianca_correspondencia": confianca,
            "disponibilidade": _descrever_disponibilidade(item),
            "descricao": item.descricao,
            "especificacoes": item.specs,
            "pagamento": {
                "pix": pagamento.preco_pix,
                "pix_tem_desconto_5pct": pagamento.pix_tem_desconto,
                "cartao_credito_max_parcelas_sem_juros": pagamento.max_parcelas_sem_juros,
                "valor_parcela_no_maximo": pagamento.valor_parcela_maxima,
            },
        }
        if not item.disponivel:
            dados["alternativas_disponiveis"] = [
                para_dict(p, self.catalogo) for p in self.catalogo.alternativas(item)
            ]
        outros = [p.nome for p, nota in candidatos[1:] if nota >= confianca - 0.1]
        if outros:
            dados["outros_nomes_parecidos"] = outros
        return dados

    def promocoes_ativas(self) -> dict[str, Any]:
        return {
            "promocoes": [
                para_dict(self.catalogo.produtos[promo.produto_id], self.catalogo)
                for promo in self.catalogo.promocoes_ativas.values()
            ]
        }

    # ------------------------------------------------------------- pedidos
    def consultar_pedido(self, numero_pedido: int, verificacao: str) -> dict[str, Any]:
        tentativas = self._tentativas.get(numero_pedido, 0)
        if tentativas >= MAX_TENTATIVAS_VERIFICACAO:
            raise ErroFerramenta(
                "limite de tentativas de verificação atingido para este pedido; oriente o "
                "cliente a falar com a equipe pelo WhatsApp ou e-mail da loja"
            )
        pedido = self.catalogo.pedidos.get(numero_pedido)
        cliente = self.catalogo.clientes.get(pedido.cliente_id) if pedido else None
        if (
            pedido is None
            or cliente is None
            or not self.catalogo.cliente_confere(cliente, verificacao)
        ):
            # Mesma resposta para "não existe" e "não confere": não vaza a existência do pedido.
            self._tentativas[numero_pedido] = tentativas + 1
            raise ErroFerramenta(
                "não foi possível localizar um pedido com esse número e esses dados do titular. "
                "Peça para o cliente conferir as informações."
            )
        self._tentativas.pop(numero_pedido, None)

        dados: dict[str, Any] = {
            "numero": pedido.id,
            "titular_primeiro_nome": cliente.nome.split()[0],
            "cidade_entrega": cliente.cidade,
            "data_compra": pedido.data.isoformat(),
            "status": pedido.status.value,
            "status_descricao": DESCRICAO_STATUS_PEDIDO[pedido.status],
            "itens": [
                {
                    "produto": self.catalogo.produtos[i.produto_id].nome,
                    "quantidade": i.quantidade,
                }
                for i in pedido.itens
                if i.produto_id in self.catalogo.produtos
            ],
            "total_pago": pedido.total,
            "forma_pagamento": descrever_pagamento(pedido.pagamento),
            "codigo_rastreio": pedido.rastreio,
            "previsao_entrega": pedido.previsao_entrega.isoformat()
            if pedido.previsao_entrega
            else None,
            "observacoes": pedido.observacoes,
            "hoje": self.settings.hoje().isoformat(),
        }
        if pedido.status is StatusPedido.ENTREGUE:
            # Não há data real de recebimento nos dados: usamos a previsão de entrega.
            dados["prazos_pos_venda"] = prazos_pos_venda(
                data_compra=pedido.data,
                data_recebimento=pedido.previsao_entrega,
                hoje=self.settings.hoje(),
            ) | {"recebimento_estimado_pela_previsao": True}
        return dados

    # ----------------------------------------------------------- políticas
    def consultar_politicas(self, consulta: str, secoes: list[str] | None = None) -> dict[str, Any]:
        escolhidas = [s for n in secoes or [] if (s := self.politicas.por_numero(n))]
        for secao, _ in self.politicas.buscar(consulta, k=3):
            if secao not in escolhidas:
                escolhidas.append(secao)
        if not escolhidas:
            return {
                "trechos": [],
                "mensagem": "Nada no manual sobre isso. Não invente: ofereça encaminhar "
                "para a equipe.",
            }
        return {"trechos": [{"secao": s.rotulo, "texto": s.texto} for s in escolhidas[:4]]}

    def status_loja(self) -> dict[str, Any]:
        return status_loja(self.settings.agora())
