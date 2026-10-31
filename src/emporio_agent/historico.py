"""Persistência simples do histórico de conversa (um JSON por sessão).

Guarda as mensagens exatamente como a API devolveu (incluindo blocos de thinking e
tool_use), em modo append-only, para que a conversa possa ser retomada depois.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_ID_VALIDO = re.compile(r"^[\w-]{1,64}$")


class RepositorioHistorico:
    def __init__(self, diretorio: Path) -> None:
        self.diretorio = diretorio

    def _caminho(self, sessao: str) -> Path:
        if not _ID_VALIDO.match(sessao):
            raise ValueError(f"id de sessão inválido: {sessao!r}")
        return self.diretorio / f"{sessao}.json"

    def carregar(self, sessao: str) -> list[dict[str, Any]]:
        caminho = self._caminho(sessao)
        if not caminho.exists():
            return []
        dados: list[dict[str, Any]] = json.loads(caminho.read_text(encoding="utf-8"))
        return dados

    def salvar(self, sessao: str, mensagens: list[dict[str, Any]]) -> None:
        self.diretorio.mkdir(parents=True, exist_ok=True)
        caminho = self._caminho(sessao)
        temporario = caminho.with_suffix(".tmp")
        temporario.write_text(json.dumps(mensagens, ensure_ascii=False, indent=1), "utf-8")
        temporario.replace(caminho)  # escrita atômica

    def apagar(self, sessao: str) -> None:
        self._caminho(sessao).unlink(missing_ok=True)
