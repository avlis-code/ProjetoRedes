"""Leitura de teclado do jogador, em Windows e Unix (apêndice D.4).

Só biblioteca padrão. O módulo importa em qualquer sistema: `msvcrt` (Windows)
e `termios`/`tty` (Unix) só são importados dentro dos métodos que os usam.

Nomes de tecla devolvidos: "UP", "DOWN", "LEFT", "RIGHT", "ENTER", "ESC",
"CTRL_C" ou o caractere em minúscula ("w", "q", "r"...).

As funções de decodificação (`decodificar_bytes`, `decodificar_windows`) são
puras e testadas sem teclado. A classe `Teclado` só cuida de abrir o terminal,
esperar as teclas e restaurar o terminal no fim.
"""

from __future__ import annotations

import atexit
import collections
import os
import select
import sys
import time

SETAS_UNIX = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}      # ESC [ x  e  ESC O x
SETAS_WINDOWS = {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}   # prefixo "\x00" ou "\xe0"
PREFIXOS_WINDOWS = ("\x00", "\xe0")

_DIRECAO = {"UP": "UP", "w": "UP", "DOWN": "DOWN", "s": "DOWN",
            "LEFT": "LEFT", "a": "LEFT", "RIGHT": "RIGHT", "d": "RIGHT"}

_PASSO_WINDOWS = 0.005  # s entre consultas a msvcrt.kbhit()


class ErroSemTerminal(Exception):
    """A entrada padrão não é um terminal interativo (o programa sai com código 2)."""


# ---------------------------------------------------------------------------
# Funções puras
# ---------------------------------------------------------------------------
def _caractere(c: str) -> str | None:
    """Tecla comum (já sem sequências de escape) -> nome, ou None se ignorada."""
    if c in ("\r", "\n"):
        return "ENTER"
    if c == "\x03":
        return "CTRL_C"
    if c == "\x1b":
        return "ESC"
    if c < " " or c == "\x7f":
        return None  # outros caracteres de controle (Tab, Backspace...) não valem
    return c.lower()


def decodificar_bytes(dados: bytes) -> list[str]:
    """Bytes lidos do terminal Unix -> lista de teclas.

    Setas chegam como ESC [ A/B/C/D ou ESC O A/B/C/D. Um ESC sozinho no fim é a
    tecla Esc. Uma sequência cortada no fim (ESC [ sem a letra) é descartada,
    assim como sequências desconhecidas (ex.: ESC [ 5 ~, Page Up).
    """
    texto = dados.decode("utf-8", errors="ignore")
    teclas: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c != "\x1b":
            t = _caractere(c)
            if t:
                teclas.append(t)
            i += 1
            continue
        if i + 1 >= n:            # ESC no fim: a tecla Esc
            teclas.append("ESC")
            break
        seg = texto[i + 1]
        if seg == "O":            # ESC O x (setas em modo aplicação)
            if i + 2 >= n:
                break             # cortada no meio: descarta
            seta = SETAS_UNIX.get(texto[i + 2])
            if seta:
                teclas.append(seta)
            i += 3
            continue
        if seg == "[":            # CSI: ESC [ parâmetros... byte final (@ a ~)
            j = i + 2
            while j < n and not ("@" <= texto[j] <= "~"):
                j += 1
            if j >= n:
                break             # cortada no meio: descarta
            if j == i + 2 and texto[j] in SETAS_UNIX:
                teclas.append(SETAS_UNIX[texto[j]])
            i = j + 1
            continue
        teclas.append("ESC")      # ESC seguido de outra coisa: Esc e depois a outra tecla
        i += 1
    return teclas


def decodificar_windows(chars: list[str]) -> list[str]:
    """Caracteres lidos com msvcrt.getwch() -> lista de teclas.

    Setas vêm como dois caracteres: "\\x00" ou "\\xe0" e depois H/P/K/M.
    Prefixo com código desconhecido (F1, Home...) ou sozinho no fim é descartado.
    """
    teclas: list[str] = []
    i, n = 0, len(chars)
    while i < n:
        c = chars[i]
        if c in PREFIXOS_WINDOWS:
            if i + 1 < n:
                seta = SETAS_WINDOWS.get(chars[i + 1])
                if seta:
                    teclas.append(seta)
            i += 2
            continue
        t = _caractere(c)
        if t:
            teclas.append(t)
        i += 1
    return teclas


def tecla_para_direcao(tecla: str) -> str | None:
    """Seta ou WASD -> direção do protocolo; qualquer outra tecla -> None."""
    return _DIRECAO.get(tecla)


# ---------------------------------------------------------------------------
# Teclado
# ---------------------------------------------------------------------------
class Teclado:
    """Leitura de teclas sem bloquear, em Windows (msvcrt) e Unix (termios).

    Uso:
        with Teclado() as t:
            tecla = t.ler(timeout=0.03)
            outras = t.ler_todas()

    No Unix o terminal fica em modo cbreak (sem eco, tecla a tecla) e o Ctrl+C
    continua gerando KeyboardInterrupt. O modo antigo é restaurado sempre, na
    saída do `with` e também por `atexit`.
    """

    def __init__(self, stdin=None) -> None:
        self.stdin = stdin if stdin is not None else sys.stdin
        self.windows = sys.platform == "win32"
        self._pendentes: collections.deque = collections.deque()
        self._fd: int | None = None
        self._antigo = None
        self._aberto = False

    def __enter__(self) -> Teclado:
        if not self.windows:
            self._abrir_unix()
        self._aberto = True
        return self

    def __exit__(self, *exc) -> None:
        self._restaurar_unix()
        self._aberto = False

    # ------------------------------------------------------------------ leitura
    def ler(self, timeout: float = 0.0) -> str | None:
        """Uma tecla, esperando até `timeout` segundos; None se nenhuma chegou."""
        if not self._pendentes:
            self._pendentes.extend(self._ler_sistema(timeout))
        return self._pendentes.popleft() if self._pendentes else None

    def ler_todas(self, limite: int = 16) -> list[str]:
        """Todas as teclas já disponíveis (no máximo `limite`), sem bloquear."""
        self._pendentes.extend(self._ler_sistema(0.0))
        teclas = []
        while self._pendentes and len(teclas) < limite:
            teclas.append(self._pendentes.popleft())
        return teclas

    def _ler_sistema(self, timeout: float) -> list[str]:
        if self.windows:
            return self._ler_windows(timeout)
        return self._ler_unix(timeout)

    # ------------------------------------------------------------------ Unix
    def _abrir_unix(self) -> None:
        try:
            interativo = self.stdin.isatty()
        except (AttributeError, ValueError):
            interativo = False
        if not interativo:
            raise ErroSemTerminal("a entrada padrão não é um terminal interativo")
        import termios
        import tty
        self._fd = self.stdin.fileno()
        self._antigo = termios.tcgetattr(self._fd)
        atexit.register(self._restaurar_unix)
        tty.setcbreak(self._fd)

    def _restaurar_unix(self) -> None:
        """Volta o terminal ao modo antigo. Pode ser chamada mais de uma vez."""
        if self._antigo is None or self._fd is None:
            return
        import termios
        try:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._antigo)
        except (OSError, termios.error):
            pass
        self._antigo = None

    def _ler_unix(self, timeout: float) -> list[str]:
        if self._fd is None:
            return []
        pronto, _, _ = select.select([self._fd], [], [], max(0.0, timeout))
        if not pronto:
            return []
        dados = os.read(self._fd, 32)
        # sequência de escape cortada no fim da leitura: espera um pouco pelo resto
        while dados.endswith((b"\x1b", b"\x1b[", b"\x1bO")):
            pronto, _, _ = select.select([self._fd], [], [], 0.01)
            if not pronto:
                break
            mais = os.read(self._fd, 32)
            if not mais:
                break
            dados += mais
        return decodificar_bytes(dados)

    # ------------------------------------------------------------------ Windows
    def _ler_windows(self, timeout: float) -> list[str]:
        """kbhit() + sleep curto até o timeout; nunca um getwch() bloqueante,
        para o Ctrl+C (que chega como KeyboardInterrupt) não atrasar."""
        import msvcrt
        fim = time.monotonic() + max(0.0, timeout)
        while not msvcrt.kbhit():
            if time.monotonic() >= fim:
                return []
            time.sleep(_PASSO_WINDOWS)
        chars = []
        while msvcrt.kbhit() and len(chars) < 32:
            c = msvcrt.getwch()
            chars.append(c)
            if c in PREFIXOS_WINDOWS:
                chars.append(msvcrt.getwch())  # o código da seta já está no buffer
        return decodificar_windows(chars)
