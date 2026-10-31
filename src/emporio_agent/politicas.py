"""Base de conhecimento das políticas da loja, com recuperação lexical (BM25).

O manual tem ~3 mil tokens e 10 seções bem delimitadas, então a unidade de recuperação é a
própria seção (chunking semântico "de graça") e um BM25 em memória basta: é determinístico,
explicável e não exige embeddings, banco vetorial nem chamadas extras de API.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from emporio_agent.text import tokens

_TITULO = re.compile(r"^(#{2,3})\s+(\d+(?:\.\d+)?)\.?\s+(.*)$")

# Sinônimos/variações de linguagem do cliente -> vocabulário do manual.
_EXPANSOES: dict[str, tuple[str, ...]] = {
    "devolver": ("devolucao", "arrependimento", "reembolso"),
    "devolucao": ("arrependimento", "reembolso"),
    "arrependi": ("arrependimento", "devolucao"),
    "arrependimento": ("devolucao",),
    "trocar": ("troca", "trocas"),
    "quebrado": ("defeito",),
    "quebrou": ("defeito",),
    "estragou": ("defeito",),
    "horario": ("funcionamento", "sabado", "domingo"),
    "abre": ("horario", "funcionamento"),
    "fecha": ("horario", "funcionamento"),
    "aberta": ("horario", "funcionamento"),
    "endereco": ("rua", "cep", "dados", "empresa"),
    "fica": ("endereco", "rua"),
    "telefone": ("whatsapp", "contato"),
    "contato": ("telefone", "whatsapp", "mail"),
    "parcelar": ("parcelamento", "parcela", "credito"),
    "parcelas": ("parcelamento", "parcela"),
    "vezes": ("parcelamento", "parcela"),
    "cartao": ("credito", "debito"),
    "pagar": ("pagamento",),
    "frete": ("entrega", "entregas"),
    "entregam": ("entrega", "frete"),
    "prazo": ("entrega", "dias"),
    "rastrear": ("rastreamento", "codigo"),
    "rastreio": ("rastreamento", "codigo"),
    "desconto": ("promocao", "promocoes", "pix"),
    "promocao": ("promocoes", "desconto"),
    "garantia": ("defeito", "fabricante"),
    "corda": ("acessorios", "cordas"),
    "cordas": ("acessorios",),
    "palheta": ("acessorios",),
    "amplificador": ("acessorios", "amplificadores"),
    "pedal": ("acessorios", "pedais"),
    "cabo": ("acessorios", "cabos"),
    "case": ("acessorios", "cases"),
    "dados": ("lgpd", "privacidade"),
    "lgpd": ("privacidade", "dados"),
}


@dataclass(frozen=True, slots=True)
class Secao:
    numero: str
    titulo: str
    texto: str

    @property
    def rotulo(self) -> str:
        return f"{self.numero} {self.titulo}"


def _expandir(termos: list[str]) -> list[str]:
    expandidos = list(termos)
    for termo in termos:
        expandidos.extend(_EXPANSOES.get(termo, ()))
    return expandidos


class BasePoliticas:
    def __init__(self, secoes: list[Secao], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.secoes = secoes
        self._k1, self._b = k1, b
        self._docs = [Counter(tokens(f"{s.titulo} {s.titulo} {s.texto}")) for s in secoes]
        self._tamanhos = [sum(d.values()) for d in self._docs]
        self._media = sum(self._tamanhos) / max(len(self._docs), 1)
        df: Counter[str] = Counter()
        for doc in self._docs:
            df.update(doc.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @classmethod
    def carregar(cls, caminho: Path) -> BasePoliticas:
        return cls(dividir_em_secoes(caminho.read_text(encoding="utf-8")))

    def sumario(self) -> str:
        return "\n".join(f"- {s.rotulo}" for s in self.secoes)

    def por_numero(self, numero: str) -> Secao | None:
        numero = numero.strip().rstrip(".")
        return next((s for s in self.secoes if s.numero == numero), None)

    def buscar(self, consulta: str, k: int = 3) -> list[tuple[Secao, float]]:
        termos = _expandir(tokens(consulta))
        pontuados: list[tuple[Secao, float]] = []
        for secao, doc, tamanho in zip(self.secoes, self._docs, self._tamanhos, strict=True):
            pontuacao = 0.0
            for termo in termos:
                freq = doc.get(termo, 0)
                if not freq:
                    continue
                norm = self._k1 * (1 - self._b + self._b * tamanho / self._media)
                pontuacao += self._idf[termo] * freq * (self._k1 + 1) / (freq + norm)
            if pontuacao > 0:
                pontuados.append((secao, round(pontuacao, 3)))
        pontuados.sort(key=lambda par: par[1], reverse=True)
        return pontuados[:k]


def dividir_em_secoes(markdown: str) -> list[Secao]:
    """Divide o manual em seções `## N` e subseções `### N.M`.

    Subseções carregam o título da seção-pai para não perderem contexto ao serem
    recuperadas isoladamente (ex.: "4.1 Direito de Arrependimento" sob "Trocas e Devoluções").
    """
    markdown = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    secoes: list[Secao] = []
    titulo_pai = ""
    numero: str | None = None
    titulo = ""
    corpo: list[str] = []

    def fechar() -> None:
        texto = "\n".join(corpo).strip()
        if numero is not None and texto:
            secoes.append(Secao(numero, titulo, texto))

    for linha in markdown.splitlines():
        m = _TITULO.match(linha)
        if m:
            fechar()
            nivel, numero, titulo_bruto = m.groups()
            if len(nivel) == 2:
                titulo_pai = titulo_bruto
                titulo = titulo_bruto
            else:
                titulo = f"{titulo_bruto} ({titulo_pai})"
            corpo = []
        elif numero is not None:
            corpo.append(linha)
    fechar()
    return secoes
