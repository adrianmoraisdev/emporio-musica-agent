"""Camada de dados: carrega, valida e indexa os CSVs operacionais da loja.

Os CSVs são a "fonte do sistema" (o manual exige nunca informar preço, estoque ou prazo sem
consultar o sistema). Todo cálculo de preço/estoque é feito aqui, de forma determinística,
para que o LLM só redija a resposta, nunca invente números.
"""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path
from typing import Any

from emporio_agent.text import normalizar, tokens

log = logging.getLogger(__name__)

PREFIXO_CSV = "desafio_tecnico_ai_eng - "

# Formas no singular/variações que o cliente usa para cada categoria (id -> tokens).
PALAVRAS_CATEGORIA: dict[int, set[str]] = {
    1: {"guitarra", "guitarras"},
    2: {"baixo", "baixos", "contrabaixo"},
    3: {"bateria", "baterias", "percussao"},
    4: {"teclado", "teclados", "piano", "pianos", "sintetizador", "synth"},
    5: {"violao", "violoes"},
    6: {"flauta", "clarinete", "saxofone", "sax", "oboe", "sopro"},
    7: {"trompete", "trombone", "tuba", "trompa", "metais"},
    8: {"violino", "violinos", "viola", "violoncelo", "cello"},
    9: {"ukulele", "ukuleles", "uke"},
}


class StatusProduto(StrEnum):
    ATIVO = "active"
    DESCONTINUADO = "discontinued"
    EM_BREVE = "coming_soon"


class StatusPedido(StrEnum):
    PENDENTE = "pending"
    CONFIRMADO = "confirmed"
    ENVIADO = "shipped"
    ENTREGUE = "delivered"
    CANCELADO = "cancelled"


DESCRICAO_STATUS_PEDIDO: dict[StatusPedido, str] = {
    StatusPedido.PENDENTE: "aguardando confirmação do pagamento",
    StatusPedido.CONFIRMADO: "pagamento confirmado, pedido em separação (ainda não despachado)",
    StatusPedido.ENVIADO: "despachado, em transporte",
    StatusPedido.ENTREGUE: "entregue",
    StatusPedido.CANCELADO: "cancelado",
}

FORMAS_PAGAMENTO: dict[str, str] = {
    "pix": "PIX",
    "debit": "cartão de débito",
    "boleto": "boleto bancário",
}


def descrever_pagamento(codigo: str) -> str:
    if codigo.startswith("credit_"):
        return f"cartão de crédito em {codigo.removeprefix('credit_')}"
    return FORMAS_PAGAMENTO.get(codigo, codigo)


@dataclass(frozen=True, slots=True)
class Categoria:
    id: int
    nome: str
    descricao: str


@dataclass(frozen=True, slots=True)
class Produto:
    id: int
    nome: str
    preco: float
    categoria_id: int
    descricao: str
    estoque: int
    status: StatusProduto
    specs: dict[str, str]
    criado_em: date | None
    nome_normalizado: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "nome_normalizado", normalizar(self.nome))

    @property
    def disponivel(self) -> bool:
        return self.status is StatusProduto.ATIVO and self.estoque > 0


@dataclass(frozen=True, slots=True)
class Promocao:
    id: int
    produto_id: int
    desconto_pct: float
    descricao: str
    ativa: bool


@dataclass(frozen=True, slots=True)
class Cliente:
    id: int
    nome: str
    telefone: str
    email: str
    cidade: str


@dataclass(frozen=True, slots=True)
class ItemPedido:
    produto_id: int
    quantidade: int


@dataclass(frozen=True, slots=True)
class Pedido:
    id: int
    cliente_id: int
    data: date
    status: StatusPedido
    total: float
    pagamento: str
    rastreio: str | None
    previsao_entrega: date | None
    observacoes: str | None
    itens: tuple[ItemPedido, ...]


def _ler_csv(caminho: Path) -> list[dict[str, str]]:
    with caminho.open(encoding="utf-8", newline="") as f:
        return [
            {k.strip(): (v or "").strip() for k, v in linha.items()} for linha in csv.DictReader(f)
        ]


def _data_opcional(valor: str) -> date | None:
    return date.fromisoformat(valor) if valor else None


def _specs(valor: str) -> dict[str, str]:
    if not valor:
        return {}
    try:
        bruto = json.loads(valor)
    except json.JSONDecodeError:
        log.warning("specs inválido ignorado: %r", valor)
        return {}
    return {str(k): str(v) for k, v in bruto.items()}


def somente_digitos(texto: str) -> str:
    return "".join(c for c in texto if c.isdigit())


class Catalogo:
    """Visão somente-leitura e indexada dos dados da loja."""

    def __init__(
        self,
        categorias: Iterable[Categoria],
        produtos: Iterable[Produto],
        promocoes: Iterable[Promocao],
        clientes: Iterable[Cliente],
        pedidos: Iterable[Pedido],
    ) -> None:
        self.categorias = {c.id: c for c in categorias}
        self.produtos = {p.id: p for p in produtos}
        self.clientes = {c.id: c for c in clientes}
        self.pedidos = {p.id: p for p in pedidos}
        # Só promoções ativas valem para preço; se houver mais de uma ativa para o mesmo
        # produto, vale a de maior desconto (promoções não são cumulativas — manual §6.2).
        self.promocoes_ativas: dict[int, Promocao] = {}
        for promo in promocoes:
            if not promo.ativa or promo.produto_id not in self.produtos:
                continue
            atual = self.promocoes_ativas.get(promo.produto_id)
            if atual is None or promo.desconto_pct > atual.desconto_pct:
                self.promocoes_ativas[promo.produto_id] = promo

    # ------------------------------------------------------------------ carga
    @classmethod
    def carregar(cls, dir_dados: Path) -> Catalogo:
        def arq(nome: str) -> Path:
            return dir_dados / f"{PREFIXO_CSV}{nome}.csv"

        categorias = [
            Categoria(int(r["category_id"]), r["name"], r["description"])
            for r in _ler_csv(arq("categories"))
        ]
        produtos = [
            Produto(
                id=int(r["product_id"]),
                nome=" ".join(r["name"].split()),
                preco=float(r["price_brl"]),
                categoria_id=int(r["category_id"]),
                descricao=r["description"],
                estoque=max(int(r["stock_quantity"]), 0),
                status=StatusProduto(r["status"]),
                specs=_specs(r["specs"]),
                criado_em=_data_opcional(r["created_at"]),
            )
            for r in _ler_csv(arq("products"))
        ]
        promocoes = [
            Promocao(
                id=int(r["promotion_id"]),
                produto_id=int(r["product_id"]),
                desconto_pct=float(r["discount_percent"]),
                descricao=r["description"],
                ativa=r["is_active"] == "1",
            )
            for r in _ler_csv(arq("promotions"))
        ]
        clientes = [
            Cliente(int(r["customer_id"]), r["name"], r["phone"], r["email"].lower(), r["city"])
            for r in _ler_csv(arq("customers"))
        ]
        itens: dict[int, list[ItemPedido]] = {}
        for r in _ler_csv(arq("order_items")):
            itens.setdefault(int(r["order_id"]), []).append(
                ItemPedido(int(r["product_id"]), int(r["quantity"]))
            )
        pedidos = [
            Pedido(
                id=int(r["order_id"]),
                cliente_id=int(r["customer_id"]),
                data=date.fromisoformat(r["order_date"]),
                status=StatusPedido(r["status"]),
                total=float(r["total_brl"]),
                pagamento=r["payment_method"],
                rastreio=r["tracking_code"] or None,
                previsao_entrega=_data_opcional(r["estimated_delivery"]),
                observacoes=r["notes"] or None,
                itens=tuple(itens.get(int(r["order_id"]), ())),
            )
            for r in _ler_csv(arq("orders"))
        ]
        return cls(categorias, produtos, promocoes, clientes, pedidos)

    # --------------------------------------------------------------- produtos
    def categoria_por_nome(self, termo: str) -> Categoria | None:
        termos = set(tokens(termo))
        if not termos:
            return None
        for cat in self.categorias.values():
            if termos & self._palavras_categoria(cat):
                return cat
        return None

    def _palavras_categoria(self, categoria: Categoria) -> set[str]:
        return set(tokens(categoria.nome)) | PALAVRAS_CATEGORIA.get(categoria.id, set())

    def buscar(
        self,
        consulta: str = "",
        *,
        categoria: Categoria | None = None,
        preco_min: float | None = None,
        preco_max: float | None = None,
        apenas_disponiveis: bool = True,
    ) -> list[Produto]:
        """Filtra o catálogo; `consulta` casa com nome, descrição, specs e categoria.

        Termos da consulta que só nomeiam a categoria (ex.: "violão") viram filtro de
        categoria, para que "violão nylon" não exija a palavra "violão" no nome do produto.
        """
        termos = tokens(consulta)
        if categoria is None and termos:
            categoria = self.categoria_por_nome(consulta)
        if categoria is not None:
            termos = [t for t in termos if t not in self._palavras_categoria(categoria)]

        encontrados: list[tuple[Produto, float]] = []
        for p in self.produtos.values():
            if categoria is not None and p.categoria_id != categoria.id:
                continue
            if apenas_disponiveis and not p.disponivel:
                continue
            preco = self.preco_final(p)
            if preco_min is not None and preco < preco_min:
                continue
            if preco_max is not None and preco > preco_max:
                continue
            cobertura = 1.0
            if termos:
                texto = set(tokens(f"{p.nome} {p.descricao} {' '.join(p.specs.values())}"))
                cobertura = sum(1 for t in termos if t in texto) / len(termos)
                if cobertura < 0.5:
                    continue
            encontrados.append((p, cobertura))
        encontrados.sort(key=lambda par: (-par[1], self.preco_final(par[0])))
        return [p for p, _ in encontrados]

    def buscar_por_nome(self, consulta: str, limite: int = 5) -> list[tuple[Produto, float]]:
        """Ranqueia produtos pela similaridade do nome com a consulta (0..1)."""
        termos = tokens(consulta)
        alvo = " ".join(termos)
        if not alvo:
            return []
        resultados: list[tuple[Produto, float]] = []
        for p in self.produtos.values():
            nome_tokens = set(p.nome_normalizado.split())
            cobertura = sum(1 for t in termos if t in nome_tokens) / len(termos)
            similaridade = SequenceMatcher(None, alvo, p.nome_normalizado).ratio()
            pontuacao = 0.7 * cobertura + 0.3 * similaridade
            if pontuacao >= 0.35:
                resultados.append((p, round(pontuacao, 3)))
        resultados.sort(key=lambda par: par[1], reverse=True)
        return resultados[:limite]

    def preco_final(self, produto: Produto) -> float:
        promo = self.promocoes_ativas.get(produto.id)
        if promo is None:
            return produto.preco
        return round(produto.preco * (1 - promo.desconto_pct / 100), 2)

    def alternativas(self, produto: Produto, limite: int = 3) -> list[Produto]:
        """Produtos disponíveis da mesma categoria com preço mais próximo."""
        candidatos = [
            p
            for p in self.produtos.values()
            if p.id != produto.id and p.categoria_id == produto.categoria_id and p.disponivel
        ]
        candidatos.sort(key=lambda p: abs(self.preco_final(p) - produto.preco))
        return candidatos[:limite]

    # ---------------------------------------------------------------- pedidos
    def cliente_confere(self, cliente: Cliente, verificacao: str) -> bool:
        """Confirma a identidade por telefone (dígitos finais), e-mail ou nome completo (LGPD)."""
        valor = verificacao.strip().lower()
        if "@" in valor:
            return valor == cliente.email
        digitos = somente_digitos(valor)
        if len(digitos) >= 8:
            return somente_digitos(cliente.telefone).endswith(digitos[-8:])
        return bool(valor) and normalizar(valor) == normalizar(cliente.nome)


def diagnosticar(catalogo: Catalogo) -> list[str]:
    """Lista inconsistências encontradas nos dados (documentadas no README)."""
    avisos: list[str] = []
    for pedido in catalogo.pedidos.values():
        if pedido.cliente_id not in catalogo.clientes:
            avisos.append(f"pedido {pedido.id}: cliente {pedido.cliente_id} inexistente")
        soma = 0.0
        for item in pedido.itens:
            produto = catalogo.produtos.get(item.produto_id)
            if produto is None:
                avisos.append(f"pedido {pedido.id}: produto {item.produto_id} inexistente")
                continue
            soma += produto.preco * item.quantidade
            if (
                produto.status is StatusProduto.EM_BREVE
                and pedido.status is not StatusPedido.CANCELADO
            ):
                avisos.append(f"pedido {pedido.id}: contém produto 'em breve' ({produto.nome})")
        if pedido.itens and abs(soma - pedido.total) > 0.01:
            avisos.append(
                f"pedido {pedido.id}: total R$ {pedido.total:.2f} difere da soma de tabela "
                f"R$ {soma:.2f} (preço histórico)"
            )
    for produto in catalogo.produtos.values():
        if produto.status is StatusProduto.ATIVO and produto.estoque == 0:
            avisos.append(f"produto {produto.id} ({produto.nome}): ativo porém sem estoque")
    vazias = set(catalogo.categorias) - {p.categoria_id for p in catalogo.produtos.values()}
    for cat_id in sorted(vazias):
        avisos.append(f"categoria {cat_id} ({catalogo.categorias[cat_id].nome}): sem produtos")
    emails: dict[str, list[int]] = {}
    for cliente in catalogo.clientes.values():
        emails.setdefault(cliente.email, []).append(cliente.id)
    for email, ids in emails.items():
        if len(ids) > 1:
            avisos.append(f"clientes {ids} compartilham o e-mail {email}")
    return avisos


def para_dict(produto: Produto, catalogo: Catalogo) -> dict[str, Any]:
    """Representação enxuta de um produto para o LLM."""
    promo = catalogo.promocoes_ativas.get(produto.id)
    dados: dict[str, Any] = {
        "id": produto.id,
        "nome": produto.nome,
        "categoria": catalogo.categorias[produto.categoria_id].nome,
        "preco_tabela": produto.preco,
        "preco_atual": catalogo.preco_final(produto),
        "estoque": produto.estoque,
        "status": produto.status.value,
        "disponivel": produto.disponivel,
    }
    if promo:
        dados["promocao"] = {"nome": promo.descricao, "desconto_pct": promo.desconto_pct}
    return dados
