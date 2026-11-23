#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import logging
import math
import select
import socket
import sys
import threading
import time
from dataclasses import dataclass

import protocol as P
import tela
from entrada import ErroSemTerminal, Teclado, tecla_para_direcao
from lobby_link import PING_LOBBY, ConexaoLobby, ErroLobby
from ui import (ErroTerminal, InfoSala, Opcoes, Terminal, ascii_automatico, montar_fim,
                montar_jogo, montar_menu, montar_sala, state_inicial)

log = logging.getLogger("jogador")

ESTADOS_PARTIDA = ("CONTAGEM", "JOGANDO", "ESPECTADOR")
MAX_TURN_POR_CICLO = 2

MENSAGENS_ABORTO = {
    "cancelled": "O host cancelou a sala",
    "host_quit": "O host saiu da partida",
    "internal_error": "Erro interno do host",
}
HOST_PERDIDO = "Conexão com o host perdida"


@dataclass
class Tempos:
    hello: float = P.TIMEOUT_HELLO
    vida: float = P.TIMEOUT_VIDA
    ping: float = P.PING_INTERVALO
    passo: float = 0.03
    lista_salas: float = 3.0
    ping_lobby: float = PING_LOBBY
    aviso: float = 1.5


class ThreadLobby:
    def __init__(self, conexao: ConexaoLobby, intervalo: float) -> None:
        self.conexao = conexao
        self.intervalo = intervalo
        self.vivo = True
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    def iniciar(self) -> None:
        if not self.vivo or self._thread is not None:
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._rodar, name="lobby", daemon=True)
        self._thread.start()

    def parar(self, timeout: float = P.TIMEOUT_LOBBY) -> None:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _rodar(self) -> None:
        while not self._parar.is_set():
            espera = self.intervalo - (time.monotonic() - self.conexao.ultimo_envio)
            if espera > 0:
                if self._parar.wait(espera):
                    return
                continue
            try:
                self.conexao.ping()
            except ErroLobby as e:
                if e.codigo is not None:
                    log.warning("lobby respondeu ERROR ao PING: %s", e)
                    continue
                log.warning("conexão com o lobby perdida (%s); a partida não é afetada", e)
                self.vivo = False
                self.conexao.fechar()
                return


class Jogador:
    def __init__(self, args, teclado, terminal, relogio=time.monotonic,
                 tempos: Tempos | None = None, perguntar=input, dizer=print) -> None:
        self.args = args
        self.teclado = teclado
        self.terminal = terminal
        self.relogio = relogio
        self.tempos = tempos or Tempos()
        self.perguntar = perguntar
        self.dizer = dizer
        self.opcoes = Opcoes(ascii=bool(args.ascii), cor=not args.sem_cor, celula=args.celula)
        self.nome: str = args.nome or ""
        self.sem_lobby = args.host is not None

        self.estado = "CONECTANDO_LOBBY"
        self.saida = 1
        self.linhas_saida: list[str] = []

        self.lobby: ConexaoLobby | None = None
        self.thread_lobby: ThreadLobby | None = None
        self.salas: list[dict] = []
        self.selecionada = 0
        self.aviso: str | None = None
        self.vezes_menu = 0
        self._ctrl_c_tratado = False

        self._nova_partida(InfoSala(None, None, args.host or "", args.porta))

    def executar(self) -> int:
        try:
            if not self._preparar():
                return self.saida
            with self.teclado, self.terminal:
                self._sessao()
        except (ErroSemTerminal, ErroTerminal) as e:
            self.linhas_saida.append(f"ABORTADA {e}")
            self.saida = 2
        except (KeyboardInterrupt, EOFError):
            self._sair_ctrl_c()
            self.saida = 1
        finally:
            if self.thread_lobby is not None:
                self.thread_lobby.parar(0.1)
            if self.lobby is not None:
                self.lobby.fechar()
        return self.saida

    def _preparar(self) -> bool:
        a = self.args
        while not P.nome_valido(self.nome):
            if self.nome:
                self.dizer("Nome inválido (1 a 16 letras, números, _ ou -)")
            self.nome = self.perguntar("Seu nome: ").strip()
        if self.sem_lobby:
            return True
        if not a.lobby_ip:
            a.lobby_ip = self.perguntar("IP do lobby: ").strip()
        return self._entrar_no_lobby()

    def _entrar_no_lobby(self) -> bool:
        a = self.args
        self.estado = "CONECTANDO_LOBBY"
        self.dizer(f"Conectando ao lobby {a.lobby_ip}:{a.lobby_porta}...")
        try:
            self.lobby = ConexaoLobby(a.lobby_ip, a.lobby_porta)
        except ErroLobby as e:
            log.warning("%s", e)
            self.dizer(f"Não conectei no lobby {a.lobby_ip}:{a.lobby_porta}. "
                       f"Teste a rede com: python3 rede.py testar {a.lobby_ip} {a.lobby_porta}")
            self.linhas_saida.append(f"ABORTADA lobby inacessível em {a.lobby_ip}:{a.lobby_porta}")
            return False
        while True:
            try:
                self.lobby.registrar(self.nome)
                log.info("registrado no lobby %s:%d como %s", a.lobby_ip, a.lobby_porta, self.nome)
                return True
            except ErroLobby as e:
                if e.codigo == P.NAME_TAKEN:
                    self.dizer("Nome já usado")
                elif e.codigo == P.BAD_NAME:
                    self.dizer("Nome inválido (1 a 16 letras, números, _ ou -)")
                else:
                    log.warning("REGISTER falhou: %s", e)
                    self.dizer(f"O lobby recusou o registro: {e}")
                    self.linhas_saida.append(f"ABORTADA lobby: {e}")
                    return False
                self.nome = ""
                while not P.nome_valido(self.nome):
                    self.nome = self.perguntar("Outro nome: ").strip()

    def _sessao(self) -> None:
        if self.sem_lobby:
            info = InfoSala(None, None, self.args.host, self.args.porta)
            if self._jogar(info) == "FIM":
                self._tela_fim()
            return
        direto = self.args.partida
        while True:
            escolha = self._entrar_direto(direto) if direto is not None else self._menu()
            direto = None
            if escolha is None:
                return
            resultado = self._jogar(escolha)
            if resultado == "MENU":
                self.aviso = self.resultado if isinstance(self.resultado, str) else None
                if not self._retomar_lobby():
                    self._mostrar_lobby_indisponivel()
                    return
                continue
            if self._tela_fim() != "MENU":
                return
            if not self._retomar_lobby():
                self._mostrar_lobby_indisponivel()
                return

    def _menu(self) -> InfoSala | None:
        self.estado = "NO_LOBBY"
        self.vezes_menu += 1
        self.terminal.nova_tela()
        proxima_lista = 0.0
        mudou = True
        tam = None
        while True:
            if self.relogio() >= proxima_lista:
                if not self._atualizar_salas():
                    return None
                proxima_lista = self.relogio() + self.tempos.lista_salas
                mudou = True
            elif time.monotonic() - self.lobby.ultimo_envio >= self.tempos.ping_lobby:
                try:
                    self.lobby.ping()
                except ErroLobby as e:
                    if e.codigo is None:
                        return self._lobby_caiu_no_menu(e)
            if self.terminal.tamanho() != tam:
                tam = self.terminal.tamanho()
                mudou = True
            if mudou:
                self.terminal.mostrar(montar_menu(
                    self.salas, self.selecionada, self.nome,
                    f"{self.args.lobby_ip}:{self.args.lobby_porta} (ok)", self.aviso, self.opcoes))
                mudou = False
            primeira = self.teclado.ler(self.tempos.passo)
            if primeira is None:
                continue
            for tecla in [primeira] + self.teclado.ler_todas():
                mudou = True
                direcao = tecla_para_direcao(tecla)
                if tecla == "CTRL_C":
                    raise KeyboardInterrupt
                if tecla in ("q", "ESC"):
                    return None
                if direcao == "UP":
                    self.selecionada = max(0, self.selecionada - 1)
                elif direcao == "DOWN":
                    self.selecionada = min(max(0, len(self.salas) - 1), self.selecionada + 1)
                elif tecla == "r":
                    proxima_lista = 0.0
                elif tecla == "ENTER" and self.salas:
                    sala = self.salas[self.selecionada]
                    if sala["status"] != "ABERTA":
                        self.aviso = "partida já iniciada"
                        continue
                    escolha = self._join(sala)
                    if escolha is not None:
                        return escolha
                    if self.lobby_caiu:
                        return None
                    proxima_lista = 0.0

    def _atualizar_salas(self) -> bool:
        selecionada_id = (self.salas[self.selecionada]["match_id"]
                          if 0 <= self.selecionada < len(self.salas) else None)
        try:
            self.salas = self.lobby.pedir({"type": "LIST_MATCHES"}, "MATCH_LIST")["matches"]
        except ErroLobby as e:
            if e.codigo is None:
                self._lobby_caiu_no_menu(e)
                return False
            self.aviso = f"{e.codigo}: {e}"
            return True
        ids = [s["match_id"] for s in self.salas]
        if selecionada_id in ids:
            self.selecionada = ids.index(selecionada_id)
        self.selecionada = min(self.selecionada, max(0, len(self.salas) - 1))
        return True

    @property
    def lobby_caiu(self) -> bool:
        return self.lobby is None

    def _lobby_caiu_no_menu(self, e: Exception) -> None:
        log.warning("lobby caiu no menu: %s", e)
        if self.lobby is not None:
            self.lobby.fechar()
        self.lobby = None
        self._mostrar_lobby_indisponivel()
        return None

    def _join(self, sala: dict) -> InfoSala | None:
        try:
            ok = self.lobby.pedir({"type": "JOIN_MATCH", "match_id": sala["match_id"]}, "JOIN_OK")
        except ErroLobby as e:
            if e.codigo is None:
                self._lobby_caiu_no_menu(e)
                return None
            log.info("JOIN_MATCH %d recusado: %s", sala["match_id"], e)
            self.aviso = str(e)
            return None
        log.info("lobby: entrando na sala %d '%s' de %s em %s:%d", ok["match_id"],
                 sala["match_name"], ok["host"], ok["host_ip"], ok["host_port"])
        self.aviso = None
        return InfoSala(ok["match_id"], sala["match_name"], ok["host_ip"], ok["host_port"],
                        max_jogadores=sala["max_players"])

    def _entrar_direto(self, match_id: int) -> InfoSala | None:
        if not self._atualizar_salas():
            return None
        sala = next((s for s in self.salas if s["match_id"] == match_id), None)
        if sala is None or sala["status"] != "ABERTA":
            motivo = f"sala {match_id} não existe ou não está aberta"
            self.linhas_saida.append(f"ABORTADA {motivo}")
            self.aviso = motivo
            return self._menu()
        escolha = self._join(sala)
        if escolha is None and not self.lobby_caiu:
            return self._menu()
        return escolha

    def _retomar_lobby(self) -> bool:
        if self.sem_lobby or self.lobby is None:
            return False
        if self.thread_lobby is not None:
            self.thread_lobby.parar()
            vivo = self.thread_lobby.vivo
            self.thread_lobby = None
            if not vivo:
                self.lobby = None
                return False
        try:
            self.lobby.ping()
            return True
        except ErroLobby as e:
            if e.codigo is not None:
                return True
            log.warning("lobby indisponível ao voltar ao menu: %s", e)
            self.lobby.fechar()
            self.lobby = None
            return False

    def _mostrar_lobby_indisponivel(self) -> None:
        self.terminal.nova_tela()
        self.terminal.mostrar([" Lobby indisponível. Encerrando."])
        log.info("lobby indisponível: encerrando")
        time.sleep(self.tempos.aviso)

    def _lobby_ok(self) -> bool | None:
        if self.sem_lobby:
            return None
        if self.thread_lobby is not None:
            return self.thread_lobby.vivo
        return self.lobby is not None

    def _nova_partida(self, info: InfoSala) -> None:
        self.info = info
        self.sock: socket.socket | None = None
        self.leitor = P.LeitorLinhas()
        self.meu_id: int | None = None
        self.jogadores: list[dict] = []
        self.nomes: dict[int, str] = {}
        self.arena = (0, 0)
        self.inicio_contagem = 0.0
        self.start_in_ms = 0
        self.ultimo_ciclo = 0
        self.ultimo_state: dict | None = None
        self.eliminado_por: tuple[str, int] | None = None
        self.turns_no_ciclo = 0
        self.ultima_dir_no_ciclo: str | None = None
        self.ultimo_rx = self.ultimo_tx = self.conectado_em = 0.0
        self.resultado: dict | str | None = None
        self.foi_ao_jogo = False
        self._bye_enviado = False
        self.mudou = True
        self._tela_atual = None
        self._contagem_mostrada: int | None = None
        self._tamanho_mostrado = None
        self._lobby_mostrado = None

    def _jogar(self, info: InfoSala) -> str:
        self._nova_partida(info)
        self.estado = "CONECTANDO_HOST"
        if not self.sem_lobby:
            self.thread_lobby = ThreadLobby(self.lobby, self.tempos.ping_lobby)
            self.thread_lobby.iniciar()
        self._desenhar()
        try:
            self.sock = socket.create_connection((info.host_ip, info.host_porta),
                                                 timeout=self.tempos.hello)
        except OSError as e:
            log.warning("não conectou em %s:%d (%s)", info.host_ip, info.host_porta, e)
            self._abortar(f"Não conectei em {info.host_ip}:{info.host_porta} ({e}). "
                          f"Confira o firewall da máquina do host")
            return "MENU"
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(None)
        self.conectado_em = self.ultimo_rx = self.relogio()
        self._enviar(P.hello(self.nome))
        log.info("conectado em %s:%d, HELLO enviado", info.host_ip, info.host_porta)
        try:
            while self.estado != "FIM":
                pronto, _, _ = select.select([self.sock], [], [], self.tempos.passo)
                if pronto:
                    self._receber()
                if self.estado == "FIM":
                    break
                for tecla in self.teclado.ler_todas():
                    self._tratar_tecla(tecla)
                    if self.estado == "FIM":
                        break
                self._manutencao()
                self._desenhar()
        except KeyboardInterrupt:
            self._sair_ctrl_c()
            raise
        finally:
            self._fechar_socket()
        return "FIM" if self.foi_ao_jogo else "MENU"

    def _fechar_socket(self) -> None:
        if self.sock is None:
            return
        if self._bye_enviado:
            try:
                self.sock.shutdown(socket.SHUT_WR)
                self.sock.settimeout(0.05)
                fim = time.monotonic() + 0.2
                while time.monotonic() < fim:
                    try:
                        if not self.sock.recv(65536):
                            break
                    except socket.timeout:
                        continue
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None

    def _receber(self) -> None:
        try:
            dados = self.sock.recv(65536)
        except OSError as e:
            log.warning("erro ao ler do host: %s", e)
            self._abortar(HOST_PERDIDO)
            return
        if not dados:
            log.warning("o host fechou a conexão")
            self._abortar(HOST_PERDIDO)
            return
        self.ultimo_rx = self.relogio()
        try:
            linhas = self.leitor.alimentar(dados)
        except P.LinhaMuitoLonga as e:
            log.warning("linha longa demais vinda do host: %s", e)
            self._abortar(HOST_PERDIDO)
            return
        for linha in linhas:
            self.processar(linha)
            if self.estado == "FIM":
                return

    def _enviar(self, msg: dict) -> None:
        if self.sock is None:
            return
        try:
            self.sock.sendall(P.codificar(msg))
            self.ultimo_tx = self.relogio()
        except OSError as e:
            log.warning("erro ao enviar %s: %s", msg["type"], e)
            self._abortar(HOST_PERDIDO)

    def _manutencao(self) -> None:
        if self.estado == "FIM":
            return
        agora = self.relogio()
        if self.estado == "CONECTANDO_HOST":
            if agora - self.conectado_em > self.tempos.hello:
                log.warning("%.1f s sem WELCOME", agora - self.conectado_em)
                self._abortar("O host não respondeu")
            return
        if agora - self.ultimo_rx > self.tempos.vida:
            log.warning("%.1f s sem sinal do host", agora - self.ultimo_rx)
            self._abortar(HOST_PERDIDO)
            return
        if agora - self.ultimo_tx >= self.tempos.ping:
            self._enviar(P.ping(int(time.monotonic() * 1000)))

    def _abortar(self, motivo: str) -> None:
        if self.estado == "FIM":
            return
        log.warning("partida abortada: %s", motivo)
        self.resultado = motivo
        self.linhas_saida.append(f"ABORTADA {motivo}")
        self.saida = 1
        self.estado = "FIM"

    def processar(self, linha: bytes) -> None:
        try:
            msg = P.decodificar(linha)
        except P.ErroProtocolo as e:
            log.warning("mensagem inválida do host ignorada: %s", e)
            return
        tipo = msg["type"]
        if tipo not in P.JOGADOR_PERMITIDAS.get(self.estado, ()):
            log.warning("%s inesperado no estado %s, ignorado", tipo, self.estado)
            return
        if tipo == "PING":
            self._enviar(P.pong(msg["t"]))
        elif tipo == "PONG":
            log.debug("RTT com o host: %d ms", int(time.monotonic() * 1000) - msg["t"])
        elif tipo == "ERROR":
            log.warning("ERROR do host: %s %s", msg["code"], msg.get("message"))
        elif tipo == "WELCOME":
            self._welcome(msg)
        elif tipo == "REJECT":
            self._abortar(f"Recusado pelo host: {msg['code']} {msg['message']}")
        elif tipo == "ROSTER":
            self._lista_jogadores(msg["players"])
            log.info("sala: %s", [p["name"] for p in msg["players"]])
        elif tipo == "GAME_START":
            self._game_start(msg)
        elif tipo == "STATE":
            self._state(msg)
        elif tipo == "GAME_OVER":
            self._game_over(msg)
        elif tipo == "GAME_ABORTED":
            texto = MENSAGENS_ABORTO.get(msg["reason"], f"Partida encerrada pelo host ({msg['reason']})")
            if msg["cycle"] is not None:
                texto += f" no ciclo {msg['cycle']}"
            self._abortar(texto)

    def _lista_jogadores(self, players: list[dict]) -> None:
        self.jogadores = list(players)
        self.nomes = {p["id"]: p["name"] for p in players}
        self.mudou = True

    def _welcome(self, msg: dict) -> None:
        self.meu_id = msg["id"]
        self.info = dataclasses.replace(
            self.info, match_id=msg["match_id"],
            min_jogadores=msg["min_players"], max_jogadores=msg["max_players"])
        self._lista_jogadores(msg["players"])
        self.estado = "AGUARDANDO_INICIO"
        log.info("WELCOME: id %d na partida %d", msg["id"], msg["match_id"])

    def _game_start(self, msg: dict) -> None:
        self.estado = "CONTAGEM"
        self.foi_ao_jogo = True
        self.arena = (msg["arena"]["width"], msg["arena"]["height"])
        self._lista_jogadores([{"id": p["id"], "name": p["name"]} for p in msg["players"]])
        self.start_in_ms = msg["start_in_ms"]
        self.inicio_contagem = self.relogio()
        self.ultimo_state = state_inicial(msg)
        self._zerar_turns()
        log.info("GAME_START: %dx%d, começa em %d ms", self.arena[0], self.arena[1],
                 msg["start_in_ms"])

    def _state(self, msg: dict) -> None:
        if msg["cycle"] <= self.ultimo_ciclo:
            log.warning("STATE do ciclo %d descartado (último %d)", msg["cycle"], self.ultimo_ciclo)
            return
        if msg["cycle"] != self.ultimo_ciclo + 1:
            log.info("pulou do ciclo %d para %d", self.ultimo_ciclo, msg["cycle"])
        self.ultimo_ciclo = msg["cycle"]
        self.ultimo_state = msg
        self._zerar_turns()
        self.mudou = True
        if self.estado == "CONTAGEM":
            self.estado = "JOGANDO"
        for e in msg["events"]:
            log.info("ciclo %d: id %d eliminado (%s)", msg["cycle"], e["id"], e["reason"])
            if e["id"] == self.meu_id and self.estado == "JOGANDO":
                self.estado = "ESPECTADOR"
                self.eliminado_por = (e["reason"], msg["cycle"])

    def _game_over(self, msg: dict) -> None:
        grid = self.ultimo_state["grid"] if self.ultimo_state else None
        self.resultado = msg
        self.linhas_saida.append(tela.resumo_resultado(msg, grid))
        self.saida = 0
        self.estado = "FIM"
        log.info("GAME_OVER: %s", msg)

    def _tratar_tecla(self, tecla: str) -> None:
        if tecla == "CTRL_C":
            raise KeyboardInterrupt
        if tecla == "q":
            if self.estado == "AGUARDANDO_INICIO":
                self._enviar_bye("usuário saiu")
                self._abortar("Você saiu da sala")
            elif self.estado in ESTADOS_PARTIDA:
                self._enviar_bye("usuário saiu")
                self._abortar("Você saiu da partida")
            elif self.estado == "CONECTANDO_HOST":
                self._abortar("Você desistiu de entrar na sala")
            return
        direcao = tecla_para_direcao(tecla)
        if direcao is not None and self.estado in ("CONTAGEM", "JOGANDO"):
            self._enviar_turn(direcao)

    def _enviar_bye(self, motivo: str) -> None:
        self._enviar(P.bye(motivo))
        self._bye_enviado = True

    def _zerar_turns(self) -> None:
        self.turns_no_ciclo = 0
        self.ultima_dir_no_ciclo = None

    def _enviar_turn(self, direcao: str) -> None:
        if self.turns_no_ciclo >= MAX_TURN_POR_CICLO or direcao == self.ultima_dir_no_ciclo:
            return
        self._enviar(P.turn(direcao, self.ultimo_ciclo))
        self.turns_no_ciclo += 1
        self.ultima_dir_no_ciclo = direcao
        log.debug("TURN %s (ciclo %d)", direcao, self.ultimo_ciclo)

    def _sair_ctrl_c(self) -> None:
        if self._ctrl_c_tratado:
            return
        self._ctrl_c_tratado = True
        if self.sock is not None and self.estado not in ("FIM", "CONECTANDO_HOST"):
            try:
                self.sock.sendall(P.codificar(P.bye("Ctrl+C")))
                self._bye_enviado = True
            except OSError:
                pass
        self._fechar_socket()
        if self.estado != "FIM":
            self.linhas_saida.append("ABORTADA Ctrl+C")
        log.info("saindo por Ctrl+C")

    def _contagem(self) -> int:
        restante = self.start_in_ms / 1000 - (self.relogio() - self.inicio_contagem)
        return max(0, math.ceil(restante))

    def _desenhar(self) -> None:
        tela_atual = "jogo" if self.estado in ESTADOS_PARTIDA else self.estado
        if tela_atual != self._tela_atual:
            self.terminal.nova_tela()
            self._tela_atual = tela_atual
            self.mudou = True
        tam = self.terminal.tamanho()
        contagem = self._contagem() if self.estado == "CONTAGEM" else None
        lobby_ok = self._lobby_ok()
        if (tam, contagem, lobby_ok) != (self._tamanho_mostrado, self._contagem_mostrada,
                                         self._lobby_mostrado):
            self.mudou = True
        if not self.mudou:
            return
        self.mudou = False
        self._tamanho_mostrado, self._contagem_mostrada, self._lobby_mostrado = tam, contagem, lobby_ok
        if self.estado == "CONECTANDO_HOST":
            nome = f"'{self.info.nome}' " if self.info.nome else ""
            linhas = [f" Entrando na sala {nome}({self.info.host_ip}:{self.info.host_porta})..."]
        elif self.estado == "AGUARDANDO_INICIO":
            linhas = montar_sala(self.info, self.jogadores, self.meu_id, lobby_ok, self.opcoes)
        elif self.estado in ESTADOS_PARTIDA:
            linhas = montar_jogo(self.estado, self.ultimo_state, self.arena, self.nomes,
                                 self.meu_id, self.eliminado_por, contagem, lobby_ok, tam,
                                 self.opcoes)
        else:
            return
        self.terminal.mostrar(linhas)

    def _tela_fim(self) -> str:
        self.estado = "FIM"
        if self.thread_lobby is not None:
            self.thread_lobby.parar()
        lobby_ok = self._lobby_ok()
        self.terminal.nova_tela()
        tam = None
        while True:
            if self.terminal.tamanho() != tam:
                tam = self.terminal.tamanho()
                self.terminal.nova_tela()
                self.terminal.mostrar(montar_fim(self.resultado or "?", self.nomes, lobby_ok,
                                                 self.opcoes))
            tecla = self.teclado.ler(self.tempos.passo)
            if tecla == "CTRL_C":
                raise KeyboardInterrupt
            if tecla in ("q", "ESC"):
                return "SAIR"
            if tecla == "ENTER":
                if lobby_ok:
                    return "MENU"
                if lobby_ok is False:
                    self._mostrar_lobby_indisponivel()
                return "SAIR"


def ler_argumentos(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Jogador do Tron em rede (teclado e tela)")
    ap.add_argument("--nome", default=None, help="nome do jogador (se faltar, é perguntado)")
    ap.add_argument("--lobby-ip", default=None, help="IP do lobby (se faltar e não houver --host, é perguntado)")
    ap.add_argument("--lobby-porta", type=int, default=5000)
    ap.add_argument("--host", default=None, help="conecta direto no host, sem lobby e sem menu")
    ap.add_argument("--porta", type=int, default=6000, help="porta do host (com --host)")
    ap.add_argument("--partida", type=int, default=None, help="entra direto na sala ID do lobby")
    ap.add_argument("--ascii", action="store_true", help="só caracteres ASCII")
    ap.add_argument("--sem-cor", action="store_true", help="sem cores (rastro com o dígito do id)")
    ap.add_argument("--celula", type=int, choices=(1, 2), default=None,
                    help="largura da célula em colunas (padrão: automático)")
    ap.add_argument("--log", default=None, help="arquivo de log (padrão: jogador_<nome>.log)")
    ap.add_argument("-v", "--verbose", action="store_true", help="log em nível DEBUG")
    args = ap.parse_args(argv)
    if args.host is not None and args.lobby_ip is not None:
        ap.error("--host e --lobby-ip não se combinam")
    if args.host is not None and args.partida is not None:
        ap.error("--partida só vale com lobby")
    return args


def configurar_log(args) -> None:
    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        raiz.removeHandler(h)
    arquivo = logging.FileHandler(args.log or f"jogador_{args.nome}.log", encoding="utf-8")
    arquivo.setFormatter(logging.Formatter(
        f"%(asctime)s.%(msecs)03d [{args.nome}] %(threadName)s %(message)s", datefmt="%H:%M:%S"))
    raiz.addHandler(arquivo)
    raiz.setLevel(logging.DEBUG if args.verbose else logging.INFO)


def main(argv=None) -> int:
    args = ler_argumentos(argv)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("O jogador precisa de um terminal interativo (stdin e stdout).", file=sys.stderr)
        return 2
    try:
        while not P.nome_valido(args.nome):
            if args.nome:
                print("Nome inválido (1 a 16 letras, números, _ ou -)")
            args.nome = input("Seu nome: ").strip()
    except (KeyboardInterrupt, EOFError):
        return 1
    if not args.ascii and ascii_automatico(sys.stdout):
        args.ascii = True
    configurar_log(args)
    j = Jogador(args, Teclado(), Terminal())
    codigo = j.executar()
    for linha in j.linhas_saida:
        print(linha, flush=True)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
