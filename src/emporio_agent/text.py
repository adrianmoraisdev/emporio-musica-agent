"""Utilitários de normalização de texto para busca tolerante (acentos, caixa, pontuação)."""

from __future__ import annotations

import re
import unicodedata

_NAO_ALFANUM = re.compile(r"[^a-z0-9]+")

# Palavras sem valor discriminativo em buscas de catálogo/políticas.
STOPWORDS = frozenset(
    "a o as os de da do das dos e em no na nos nas um uma uns umas para por com sem "
    "que qual quais quanto quanta custa custam tem voces voce loja meu minha eu ate "
    "ou se ao aos pelo pela sobre como".split()
)


def normalizar(texto: str) -> str:
    """Remove acentos, baixa a caixa e troca pontuação por espaço."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return _NAO_ALFANUM.sub(" ", sem_acento.lower()).strip()


def tokens(texto: str, *, remover_stopwords: bool = True) -> list[str]:
    """Quebra o texto normalizado em tokens, opcionalmente sem stopwords."""
    partes = normalizar(texto).split()
    if remover_stopwords:
        return [p for p in partes if p not in STOPWORDS]
    return partes
