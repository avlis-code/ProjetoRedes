"""Lógica pura do Tron: arena, motos, filas de direção, colisões e fim de jogo.

Não sabe nada de rede nem de tempo. O host chama:
    partida = Partida(40, 20, [(0, "Ana"), (1, "Beto"), (2, "Caio")])
    partida.enfileirar_virada(1, "UP", ciclo_do_cliente)
    partida.marcar_eliminacao(2, "disconnect")
    resultado = partida.avancar()      # calcula o próximo ciclo
e transforma o estado em mensagens com estado_json() / game_over_json().

A ordem de cálculo de cada ciclo é a da seção 3.4 do protocolo:
  (1) eliminações pendentes (desconexão, timeout, quit);
  (2) aplica no máximo uma direção da fila de cada moto viva;
  (3) calcula a nova célula de cada moto viva;
  (4) elimina quem saiu da arena (wall), entrou em rastro (trail) ou caiu na
      mesma célula que outra moto (head_on);
  (5) marca as novas células como rastro;
  (7) verifica o fim (0 ou 1 moto viva).
O passo (6), enviar o STATE, é do host.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

DELTAS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
OPOSTA = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}
TAMANHO_FILA = 2  # decisão D3
VAZIO = "."


def posicoes_iniciais(largura: int, altura: int) -> dict[int, tuple[int, int, str]]:
    """Posição e direção inicial por id (decisão D10): perto dos cantos, para dentro.

    Para 40x20 dá (4,4) RIGHT, (35,15) LEFT, (35,4) DOWN, (4,15) UP, que é o
    exemplo do GAME_START na seção 3.3.
    """
    m = max(0, min(4, (min(largura, altura) - 1) // 4))
    return {
        0: (m, m, "RIGHT"),
        1: (largura - 1 - m, altura - 1 - m, "LEFT"),
        2: (largura - 1 - m, m, "DOWN"),
        3: (m, altura - 1 - m, "UP"),
    }


@dataclass
class Moto:
    id: int
    nome: str
    x: int
    y: int
    dir: str
    viva: bool = True
    ciclo_eliminacao: int | None = None
    motivo: str | None = None
    # fila de (direção, ciclo informado pelo cliente)
    fila: deque = field(default_factory=lambda: deque(maxlen=TAMANHO_FILA))


@dataclass
class Virada:
    """Direção aplicada num ciclo. `atraso` segue a regra 4 da seção 3.4."""
    id: int
    dir: str
    ciclo_cliente: int
    ciclo_aplicado: int

    @property
    def atraso(self) -> int:
        return self.ciclo_aplicado - 1 - self.ciclo_cliente


@dataclass
class ResultadoCiclo:
    ciclo: int
    eventos: list[dict]
    viradas: list[Virada]
    terminou: bool


class Partida:
    def __init__(self, largura: int, altura: int, jogadores: list[tuple[int, str]]):
        if largura < 3 or altura < 3:
            raise ValueError("arena pequena demais")
        ids = [i for i, _ in jogadores]
        if len(set(ids)) != len(ids):
            raise ValueError("ids repetidos")
        self.largura = largura
        self.altura = altura
        self.ciclo = 0
        self.terminou = False
        # grid[y][x] = id do dono do rastro, ou None
        self.grid: list[list[int | None]] = [[None] * largura for _ in range(altura)]
        self.motos: dict[int, Moto] = {}
        self._pendentes: dict[int, str] = {}
        inicio = posicoes_iniciais(largura, altura)
        for pid, nome in sorted(jogadores):
            x, y, d = inicio[pid]
            self.motos[pid] = Moto(pid, nome, x, y, d)
            self.grid[y][x] = pid  # ciclo 0: a célula inicial já é rastro

    # ------------------------------------------------------------------ entradas
    def enfileirar_virada(self, pid: int, direcao: str, ciclo_cliente: int = 0) -> None:
        """Pedido de TURN. Fila de no máximo 2; se cheia, descarta o mais antigo."""
        moto = self.motos.get(pid)
        if moto is None or not moto.viva or self.terminou:
            return  # TURN de moto eliminada é ignorado em silêncio
        if direcao not in DELTAS:
            raise ValueError(direcao)
        moto.fila.append((direcao, ciclo_cliente))

    def marcar_eliminacao(self, pid: int, motivo: str) -> None:
        """Desconexão/timeout/quit: a moto sai no próximo ciclo calculado.

        Se já houver um motivo pendente, o primeiro vale (ex.: BYE seguido do
        fechamento da conexão continua sendo "quit").
        """
        moto = self.motos.get(pid)
        if moto is None or not moto.viva or self.terminou:
            return
        self._pendentes.setdefault(pid, motivo)

    # ------------------------------------------------------------------ ciclo
    def avancar(self) -> ResultadoCiclo:
        if self.terminou:
            raise RuntimeError("partida já terminou")
        self.ciclo += 1
        c = self.ciclo
        eventos: list[dict] = []
        viradas: list[Virada] = []

        # (1) eliminações pendentes
        for pid in sorted(self._pendentes):
            moto = self.motos[pid]
            if moto.viva:
                self._eliminar(moto, self._pendentes[pid], eventos)
        self._pendentes.clear()

        vivas = [m for m in self.motos.values() if m.viva]

        # (2) direções: consome pedidos até achar um que muda a direção.
        # Inversão ou mesma direção é descartada em silêncio e não "gasta" o ciclo.
        for m in vivas:
            while m.fila:
                d, ciclo_cli = m.fila.popleft()
                if d == m.dir or d == OPOSTA[m.dir]:
                    continue
                m.dir = d
                viradas.append(Virada(m.id, d, ciclo_cli, c))
                break

        # (3) novas células
        destino: dict[int, tuple[int, int]] = {}
        for m in vivas:
            dx, dy = DELTAS[m.dir]
            destino[m.id] = (m.x + dx, m.y + dy)

        contagem: dict[tuple[int, int], int] = {}
        for pos in destino.values():
            contagem[pos] = contagem.get(pos, 0) + 1

        # (4) colisões, todas avaliadas sobre a arena do ciclo anterior
        mortos: list[tuple[Moto, str]] = []
        for m in vivas:
            x, y = destino[m.id]
            if not (0 <= x < self.largura and 0 <= y < self.altura):
                mortos.append((m, "wall"))
            elif self.grid[y][x] is not None:
                mortos.append((m, "trail"))
            elif contagem[(x, y)] > 1:
                mortos.append((m, "head_on"))
        for m, motivo in mortos:
            self._eliminar(m, motivo, eventos)

        # (5) sobreviventes andam e deixam rastro.
        # Moto eliminada fica na última célula válida e não pinta a célula do choque.
        for m in vivas:
            if m.viva:
                m.x, m.y = destino[m.id]
                self.grid[m.y][m.x] = m.id

        # (7) fim
        if sum(1 for m in self.motos.values() if m.viva) <= 1:
            self.terminou = True
            for m in self.motos.values():
                m.fila.clear()

        eventos.sort(key=lambda e: e["id"])
        return ResultadoCiclo(c, eventos, viradas, self.terminou)

    def _eliminar(self, moto: Moto, motivo: str, eventos: list[dict]) -> None:
        moto.viva = False
        moto.ciclo_eliminacao = self.ciclo
        moto.motivo = motivo
        moto.fila.clear()
        eventos.append({"event": "ELIMINATED", "id": moto.id, "reason": motivo})

    # ------------------------------------------------------------------ consultas
    def vivas(self) -> list[int]:
        return sorted(m.id for m in self.motos.values() if m.viva)

    def resultado(self) -> tuple[str, Moto | None]:
        """("WIN", moto) ou ("DRAW", None). Só faz sentido depois do fim."""
        vivas = [m for m in self.motos.values() if m.viva]
        if len(vivas) == 1:
            return "WIN", vivas[0]
        return "DRAW", None

    def celula_livre(self, x: int, y: int) -> bool:
        return 0 <= x < self.largura and 0 <= y < self.altura and self.grid[y][x] is None

    def grid_texto(self) -> list[str]:
        return ["".join(VAZIO if c is None else str(c) for c in linha) for linha in self.grid]

    # ------------------------------------------------------------------ mensagens
    def jogadores_inicio_json(self) -> list[dict]:
        return [{"id": m.id, "name": m.nome, "x": m.x, "y": m.y, "dir": m.dir}
                for m in sorted(self.motos.values(), key=lambda m: m.id)]

    def estado_json(self, eventos: list[dict]) -> dict:
        return {
            "type": "STATE",
            "cycle": self.ciclo,
            "bikes": [{"id": m.id, "x": m.x, "y": m.y, "dir": m.dir, "alive": m.viva}
                      for m in sorted(self.motos.values(), key=lambda m: m.id)],
            "grid": self.grid_texto(),
            "events": eventos,
        }

    def game_over_json(self) -> dict:
        res, vencedor = self.resultado()
        elims = sorted((m for m in self.motos.values() if not m.viva),
                       key=lambda m: (m.ciclo_eliminacao, m.id))
        return {
            "type": "GAME_OVER",
            "cycle": self.ciclo,
            "result": res,
            "winner": None if vencedor is None else {"id": vencedor.id, "name": vencedor.nome},
            "eliminations": [{"id": m.id, "name": m.nome, "cycle": m.ciclo_eliminacao,
                              "reason": m.motivo} for m in elims],
        }
