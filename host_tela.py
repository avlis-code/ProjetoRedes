from __future__ import annotations

import logging
import math
import sys
import time

from entrada import ErroSemTerminal, Teclado, tecla_para_direcao
from ui import ErroTerminal, Opcoes, Terminal, montar_fim, montar_jogo, state_inicial

log = logging.getLogger("host")

ID_HOST = 0


def _tty(stream) -> bool:
    try:
        return bool(stream) and stream.isatty()
    except (AttributeError, ValueError):
        return False


class EntradaTecladoTela:
    def __init__(self, opcoes: Opcoes, teclado=None, terminal=None, relogio=time.monotonic) -> None:
        self.opcoes = opcoes
        self._teclado_injetado = teclado
        self._terminal_injetado = terminal
        self.relogio = relogio
        self.teclado = None
        self.terminal = None
        self.ativo = False
        self.pendentes: list[str] = []
        self._mostrado = None

    def iniciar_jogo(self, host) -> None:
        if self._terminal_injetado is None and not (_tty(sys.stdin) and _tty(sys.stdout)):
            log.warning("host sem terminal interativo; a moto do host vai andar reto")
            return
        teclado = self._teclado_injetado or Teclado()
        terminal = self._terminal_injetado or Terminal()
        try:
            teclado.__enter__()
        except ErroSemTerminal as e:
            log.warning("sem teclado para a moto do host (%s); ela vai andar reto", e)
            return
        try:
            terminal.__enter__()
        except ErroTerminal as e:
            teclado.__exit__(None, None, None)
            log.warning("sem tela cheia no host (%s); a moto do host vai andar reto", e)
            return
        self.teclado, self.terminal = teclado, terminal
        self.ativo = True
        self._mostrado = None
        terminal.nova_tela()
        self.atualizar(host)

    def fechar(self, host=None) -> None:
        if not self.ativo:
            return
        try:
            if host is not None and getattr(host, "game_over", None) and not host.abortada:
                self._esperar_no_fim(host)
        finally:
            self.ativo = False
            self.terminal.__exit__(None, None, None)
            self.teclado.__exit__(None, None, None)

    def atualizar(self, host) -> None:
        if not self.ativo:
            return
        for tecla in self.teclado.ler_todas():
            if tecla in ("q", "CTRL_C"):
                raise KeyboardInterrupt
            direcao = tecla_para_direcao(tecla)
            if direcao is not None:
                self.pendentes.append(direcao)
        partida = getattr(host, "partida", None)
        if partida is not None and not partida.motos[ID_HOST].viva:
            self.pendentes.clear()
        self._desenhar(host)

    def direcao(self, partida, pid):
        for d in self.pendentes:
            partida.enfileirar_virada(pid, d, partida.ciclo)
        self.pendentes.clear()
        return None

    def _desenhar(self, host) -> None:
        partida = getattr(host, "partida", None)
        if partida is None:
            return
        moto = partida.motos[ID_HOST]
        if host.fase == "CONTAGEM":
            estado = "CONTAGEM"
            restante = host.game_start["start_in_ms"] / 1000 - (self.relogio() - host.inicio_contagem)
            contagem = max(0, math.ceil(restante))
        else:
            estado = "JOGANDO" if moto.viva else "ESPECTADOR"
            contagem = None
        st = host.ultimo_state or state_inicial(host.game_start)
        lobby_ok = getattr(host.lobby, "vivo", None)
        tamanho = self.terminal.tamanho()
        assinatura = (st["cycle"], estado, contagem, tamanho, lobby_ok)
        if assinatura == self._mostrado:
            return
        self._mostrado = assinatura
        eliminado_por = None if moto.viva else (moto.motivo, moto.ciclo_eliminacao)
        self.terminal.mostrar(montar_jogo(
            estado, st, (partida.largura, partida.altura), self._nomes(partida), ID_HOST,
            eliminado_por, contagem, lobby_ok, tamanho, self.opcoes))

    @staticmethod
    def _nomes(partida) -> dict[int, str]:
        return {pid: m.nome for pid, m in partida.motos.items()}

    def _esperar_no_fim(self, host) -> None:
        nomes = self._nomes(host.partida)
        tamanho = None
        try:
            while True:
                if self.terminal.tamanho() != tamanho:
                    tamanho = self.terminal.tamanho()
                    self.terminal.nova_tela()
                    self.terminal.mostrar(montar_fim(host.game_over, nomes, None, self.opcoes))
                tecla = self.teclado.ler(0.05)
                if tecla in ("ENTER", "q", "ESC", "CTRL_C"):
                    return
        except KeyboardInterrupt:
            return
