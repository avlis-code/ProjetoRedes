"""Piloto automático usado pelo host (entrada local "bot") e pelo cliente de teste.

Vira para uma direção aleatória de vez em quando e desvia se a próxima célula
estiver ocupada. Não é inteligente: só serve para gerar partidas de teste que
não acabam no primeiro segundo.
"""

from __future__ import annotations

import random
from typing import Callable

from tron import DELTAS, OPOSTA


def decidir(livre: Callable[[int, int], bool], x: int, y: int, direcao: str,
            rng: random.Random, chance: float = 0.1) -> str | None:
    """Devolve a nova direção, ou None para seguir em frente."""
    opcoes = [d for d in DELTAS if d != OPOSTA[direcao]]
    seguras = [d for d in opcoes if livre(x + DELTAS[d][0], y + DELTAS[d][1])]
    frente_ok = direcao in seguras
    if frente_ok and rng.random() >= chance:
        return None
    laterais = [d for d in seguras if d != direcao]
    if not laterais:
        return None  # ou a frente é a única saída, ou não há saída
    return rng.choice(laterais)


def livre_em_grid(grid: list[str]) -> Callable[[int, int], bool]:
    """Adapta a lista de strings do STATE para a função `livre(x, y)`."""
    altura = len(grid)
    largura = len(grid[0]) if grid else 0

    def livre(x: int, y: int) -> bool:
        return 0 <= x < largura and 0 <= y < altura and grid[y][x] == "."
    return livre
