"""Regras de negócio do manual implementadas de forma determinística.

Tudo que envolve número (desconto, parcela, prazo) é calculado aqui e não pelo LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

DESCONTO_PIX = 0.05
PRAZO_ARREPENDIMENTO_DIAS = 7  # §4.1 — corridos, a partir do recebimento
PRAZO_TROCA_PREFERENCIA_DIAS = 7  # §4.3
PRAZO_TROCA_DEFEITO_DIAS = 30  # §4.2 — corridos, a partir da compra
GARANTIA_LEGAL_DIAS = 90  # §8.1 — a partir do recebimento

# §3.1: (parcelas mínimas, parcelas máximas, valor mínimo da parcela).
# Suposição: "até 3x sem valor mínimo (exceto abaixo de R$ 50)" = parcela mínima de R$ 50.
# A tabela de §3 cita parcela mínima de R$ 100 para crédito; as regras detalhadas de §3.1
# são mais específicas e prevalecem.
FAIXAS_PARCELAMENTO: tuple[tuple[int, int, float], ...] = (
    (1, 3, 50.0),
    (4, 6, 80.0),
    (7, 12, 100.0),
)

# §2 — weekday(): 0 = segunda ... 6 = domingo.
HORARIOS: dict[int, tuple[time, time] | None] = {
    **{d: (time(9), time(18)) for d in range(5)},
    5: (time(9), time(13)),
    6: None,
}


@dataclass(frozen=True, slots=True)
class OpcoesPagamento:
    preco_atual: float
    preco_pix: float
    pix_tem_desconto: bool
    max_parcelas_sem_juros: int
    valor_parcela_maxima: float


def opcoes_pagamento(preco_atual: float, *, em_promocao: bool) -> OpcoesPagamento:
    """PIX dá 5% sobre o preço de tabela, mas não acumula com promoção (§6.2)."""
    preco_pix = preco_atual if em_promocao else round(preco_atual * (1 - DESCONTO_PIX), 2)
    parcelas = max_parcelas(preco_atual)
    return OpcoesPagamento(
        preco_atual=preco_atual,
        preco_pix=preco_pix,
        pix_tem_desconto=not em_promocao,
        max_parcelas_sem_juros=parcelas,
        valor_parcela_maxima=round(preco_atual / parcelas, 2),
    )


def max_parcelas(valor: float) -> int:
    """Maior número de parcelas sem juros que respeita a parcela mínima de cada faixa."""
    melhor = 1
    for minimo, maximo, parcela_minima in FAIXAS_PARCELAMENTO:
        for n in range(minimo, maximo + 1):
            if valor / n >= parcela_minima:
                melhor = n
    return melhor


def status_loja(agora: datetime) -> dict[str, object]:
    """Diz se a loja está aberta agora e, se não, quando reabre (§2, sem feriados)."""
    janela = HORARIOS[agora.weekday()]
    aberta = janela is not None and janela[0] <= agora.time() < janela[1]
    resultado: dict[str, object] = {"agora": formatar_data_hora(agora), "aberta_agora": aberta}
    if not aberta:
        resultado["proxima_abertura"] = formatar_data_hora(_proxima_abertura(agora))
    return resultado


DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def formatar_data_hora(momento: datetime) -> str:
    """Formato pt-BR independente do locale do sistema."""
    return f"{DIAS_SEMANA[momento.weekday()]}, {momento:%d/%m/%Y %H:%M}"


def _proxima_abertura(agora: datetime) -> datetime:
    for delta in range(8):
        dia = agora.date() + timedelta(days=delta)
        janela = HORARIOS[dia.weekday()]
        if janela is None:
            continue
        abertura = datetime.combine(dia, janela[0], tzinfo=agora.tzinfo)
        if abertura > agora:
            return abertura
    raise RuntimeError("horário de funcionamento sem nenhum dia aberto")


def prazos_pos_venda(
    *, data_compra: date, data_recebimento: date | None, hoje: date
) -> dict[str, object]:
    """Calcula, para um pedido entregue, quais direitos de troca/devolução ainda valem."""
    prazos: dict[str, object] = {
        "dias_desde_a_compra": (hoje - data_compra).days,
        "troca_por_defeito_na_loja_ate": (
            data_compra + timedelta(days=PRAZO_TROCA_DEFEITO_DIAS)
        ).isoformat(),
        "troca_por_defeito_na_loja_valida": (hoje - data_compra).days <= PRAZO_TROCA_DEFEITO_DIAS,
    }
    if data_recebimento is None:
        return prazos
    dias_recebido = (hoje - data_recebimento).days
    prazos |= {
        "dias_desde_o_recebimento": dias_recebido,
        "arrependimento_ate": (
            data_recebimento + timedelta(days=PRAZO_ARREPENDIMENTO_DIAS)
        ).isoformat(),
        "arrependimento_valido": dias_recebido <= PRAZO_ARREPENDIMENTO_DIAS,
        "troca_por_preferencia_valida": dias_recebido <= PRAZO_TROCA_PREFERENCIA_DIAS,
        "garantia_legal_ate": (data_recebimento + timedelta(days=GARANTIA_LEGAL_DIAS)).isoformat(),
        "garantia_legal_valida": dias_recebido <= GARANTIA_LEGAL_DIAS,
    }
    return prazos
