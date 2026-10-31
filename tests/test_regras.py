from datetime import date, datetime

import pytest

from emporio_agent.config import FUSO_LOJA
from emporio_agent.regras import max_parcelas, opcoes_pagamento, prazos_pos_venda, status_loja


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (80.0, 1),  # 2x daria R$ 40 < R$ 50
        (159.9, 3),  # 3x de R$ 53,30
        (349.9, 4),  # 4x de R$ 87,48; 5x ficaria < R$ 80
        (599.9, 6),  # 6x de R$ 99,98 >= R$ 80; 7x de R$ 85,70 < R$ 100
        (1200.0, 12),
        (11499.0, 12),
    ],
)
def test_max_parcelas(valor: float, esperado: int) -> None:
    assert max_parcelas(valor) == esperado


def test_pix_nao_acumula_com_promocao() -> None:
    assert opcoes_pagamento(1000.0, em_promocao=False).preco_pix == 950.0
    sem_desconto = opcoes_pagamento(1000.0, em_promocao=True)
    assert sem_desconto.preco_pix == 1000.0
    assert not sem_desconto.pix_tem_desconto


def test_status_loja() -> None:
    sexta_10h = datetime(2026, 3, 27, 10, 0, tzinfo=FUSO_LOJA)
    assert status_loja(sexta_10h)["aberta_agora"] is True
    sabado_14h = datetime(2026, 3, 28, 14, 0, tzinfo=FUSO_LOJA)
    fechado = status_loja(sabado_14h)
    assert fechado["aberta_agora"] is False
    assert str(fechado["proxima_abertura"]).startswith("segunda, 30/03/2026 09:00")


def test_prazos_pos_venda() -> None:
    prazos = prazos_pos_venda(
        data_compra=date(2026, 3, 1), data_recebimento=date(2026, 3, 10), hoje=date(2026, 3, 15)
    )
    assert prazos["arrependimento_valido"] is True
    assert prazos["troca_por_defeito_na_loja_valida"] is True
    prazos = prazos_pos_venda(
        data_compra=date(2026, 1, 5), data_recebimento=date(2026, 1, 18), hoje=date(2026, 3, 15)
    )
    assert prazos["arrependimento_valido"] is False
    assert prazos["troca_por_defeito_na_loja_valida"] is False
    assert prazos["garantia_legal_valida"] is True
