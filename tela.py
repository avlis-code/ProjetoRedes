"""Desenho simples da arena no terminal e resumo do resultado.

A aparência não é avaliada; isto só ajuda a acompanhar a partida.
"""

from __future__ import annotations

import hashlib
import sys

LIMPAR = "\x1b[H\x1b[2J"
CORES = {"0": "\x1b[31m", "1": "\x1b[32m", "2": "\x1b[33m", "3": "\x1b[36m"}
RESET = "\x1b[0m"


def desenhar(grid: list[str], bikes: list[dict], titulo: str, cor: bool = True) -> None:
    cabecas = {(b["x"], b["y"]): b for b in bikes}
    largura = len(grid[0]) if grid else 0
    linhas = [titulo, "+" + "-" * largura + "+"]
    for y, linha in enumerate(grid):
        s = []
        for x, c in enumerate(linha):
            b = cabecas.get((x, y))
            ch = c
            if b is not None:
                ch = "@" if b["alive"] else "X"
            elif c == ".":
                ch = " "
            if cor and c in CORES:
                ch = CORES[c] + ch + RESET
            s.append(ch)
        linhas.append("|" + "".join(s) + "|")
    linhas.append("+" + "-" * largura + "+")
    sys.stdout.write(LIMPAR + "\n".join(linhas) + "\n")
    sys.stdout.flush()


def resumo_grid(grid: list[str]) -> str:
    """Hash curto da arena. Se todos imprimirem o mesmo, todos viram a mesma arena."""
    return hashlib.sha1("\n".join(grid).encode()).hexdigest()[:12]


def resumo_resultado(game_over: dict, grid: list[str] | None) -> str:
    """Linha única com o resultado, igual em host e jogadores quando há consistência."""
    w = game_over.get("winner")
    vencedor = w["name"] if w else "-"
    elims = ",".join(f"{e['name']}@{e['cycle']}:{e['reason']}" for e in game_over["eliminations"])
    g = resumo_grid(grid) if grid else "-"
    return (f"RESULTADO {game_over['result']} vencedor={vencedor} ciclo={game_over['cycle']} "
            f"eliminacoes={elims} arena={g}")
