"""Interface de linha de comando para conversar com o agente."""

from __future__ import annotations

import argparse
import logging
import sys
import uuid

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from emporio_agent.agente import Agente, Resposta
from emporio_agent.catalogo import Catalogo, diagnosticar
from emporio_agent.config import carregar_settings
from emporio_agent.ferramentas import Ferramentas
from emporio_agent.historico import RepositorioHistorico
from emporio_agent.politicas import BasePoliticas
from emporio_agent.prompt import PERSONA

AJUDA = "Comandos: /nova (recomeça a conversa), /debug (liga/desliga ferramentas), /sair"


def criar_agente(sessao: str, *, nova: bool = False) -> tuple[Agente, RepositorioHistorico]:
    settings = carregar_settings()
    catalogo = Catalogo.carregar(settings.dir_dados)
    politicas = BasePoliticas.carregar(settings.dir_dados / "politicas_da_loja.md")
    repositorio = RepositorioHistorico(settings.dir_historico)
    if nova:
        repositorio.apagar(sessao)
    historico = repositorio.carregar(sessao)
    return Agente(Ferramentas(catalogo, politicas, settings), settings, historico=historico), (
        repositorio
    )


def _mostrar(console: Console, resposta: Resposta, debug: bool) -> None:
    if debug:
        for chamada in resposta.chamadas:
            cor = "red" if chamada.erro else "dim"
            console.print(f"[{cor}]⚙ {chamada.nome}({chamada.entrada})[/{cor}]")
            console.print(f"[{cor}]  ↳ {chamada.resultado[:300]}[/{cor}]")
    console.print(Panel(Markdown(resposta.texto), title=f"🎸 {PERSONA}", border_style="cyan"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atendimento Empório da Música (CLI)")
    parser.add_argument("--sessao", default=None, help="id da sessão para retomar a conversa")
    parser.add_argument("--nova", action="store_true", help="descarta o histórico da sessão")
    parser.add_argument("--debug", action="store_true", help="mostra as chamadas de ferramenta")
    parser.add_argument(
        "--diagnostico", action="store_true", help="lista inconsistências dos dados e sai"
    )
    args = parser.parse_args(argv)

    console = Console()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.diagnostico:
        settings = carregar_settings()
        for aviso in diagnosticar(Catalogo.carregar(settings.dir_dados)):
            console.print(f"• {aviso}")
        return 0

    sessao = args.sessao or uuid.uuid4().hex[:8]
    agente, repositorio = criar_agente(sessao, nova=args.nova)
    debug = args.debug
    console.print(f"[bold]Empório da Música[/bold] — sessão [cyan]{sessao}[/cyan]. {AJUDA}")
    if agente.mensagens:
        console.print(f"[dim](retomando conversa com {len(agente.mensagens)} mensagens)[/dim]")

    while True:
        try:
            texto = console.input("[bold green]Você:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not texto:
            continue
        if texto == "/sair":
            break
        if texto == "/debug":
            debug = not debug
            console.print(f"[dim]debug {'ligado' if debug else 'desligado'}[/dim]")
            continue
        if texto == "/nova":
            agente.mensagens.clear()
            repositorio.apagar(sessao)
            console.print("[dim]conversa reiniciada[/dim]")
            continue
        with console.status(f"{PERSONA} está digitando..."):
            resposta = agente.responder(texto)
        repositorio.salvar(sessao, agente.mensagens)
        _mostrar(console, resposta, debug)

    console.print(f"[dim]Até mais! Para retomar: emporio-agent --sessao {sessao}[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
