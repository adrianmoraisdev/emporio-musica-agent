"""Configuração do agente, lida de variáveis de ambiente (com suporte a arquivo .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

RAIZ_PROJETO = Path(__file__).resolve().parents[2]
FUSO_LOJA = ZoneInfo("America/Campo_Grande")

# Modelo usado quando EMPORIO_MODELO não é informado, por provedor.
MODELO_PADRAO = {
    "anthropic": "claude-opus-5-5",
    "gemini": "gemini-3.5-flash",  # disponível no plano gratuito do Google AI Studio
    "openai-compat": "qwen2.5:7b",  # ex.: Ollama local
}


@dataclass(frozen=True, slots=True)
class Settings:
    modelo: str
    esforco: str
    dir_dados: Path
    dir_historico: Path
    hoje_fixo: date | None
    max_iteracoes_ferramentas: int
    hora_fixa: datetime | None = None
    provedor: str = "anthropic"

    def agora(self) -> datetime:
        """Data/hora atual no fuso da loja.

        `EMPORIO_HOJE=AAAA-MM-DD` fixa só a data; `EMPORIO_HOJE=AAAA-MM-DDTHH:MM` fixa data e
        hora (demos e exemplos reprodutíveis).
        """
        if self.hora_fixa is not None:
            return self.hora_fixa
        agora = datetime.now(FUSO_LOJA)
        if self.hoje_fixo is None:
            return agora
        return agora.replace(
            year=self.hoje_fixo.year, month=self.hoje_fixo.month, day=self.hoje_fixo.day
        )

    def hoje(self) -> date:
        return self.agora().date()


def carregar_settings() -> Settings:
    load_dotenv(RAIZ_PROJETO / ".env")
    hoje_env = os.getenv("EMPORIO_HOJE")
    hora_fixa = None
    if hoje_env and "T" in hoje_env:
        hora_fixa = datetime.fromisoformat(hoje_env).replace(tzinfo=FUSO_LOJA)
        hoje_env = hoje_env.split("T")[0]
    provedor = os.getenv("EMPORIO_PROVEDOR", "anthropic")
    if provedor not in MODELO_PADRAO:
        raise ValueError(f"EMPORIO_PROVEDOR inválido: {provedor!r} (use {list(MODELO_PADRAO)})")
    return Settings(
        provedor=provedor,
        hora_fixa=hora_fixa,
        modelo=os.getenv("EMPORIO_MODELO") or MODELO_PADRAO[provedor],
        esforco=os.getenv("EMPORIO_ESFORCO", "low"),
        dir_dados=Path(os.getenv("EMPORIO_DADOS", RAIZ_PROJETO / "data")),
        dir_historico=Path(os.getenv("EMPORIO_HISTORICO", RAIZ_PROJETO / ".cache" / "conversas")),
        hoje_fixo=date.fromisoformat(hoje_env) if hoje_env else None,
        max_iteracoes_ferramentas=int(os.getenv("EMPORIO_MAX_ITERACOES", "8")),
    )
