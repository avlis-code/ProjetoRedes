#!/usr/bin/env python3
"""Host autoritativo do Tron (seções 3.2 a 3.6 e 5 do protocolo).

Um único thread com `selectors`: aceita conexões, lê mensagens, manda PING,
detecta quedas e calcula um ciclo a cada `tick_ms`. Não há locks porque nada
roda em paralelo. Cada conexão tem um buffer de saída, então um jogador lento
não trava o envio do STATE para os outros.

Enquanto o lobby não existe, o host usa o `LobbyStub` (lobby_link.py) e
começa a partida sozinho quando a sala enche (ver --iniciar-com e --jogadores).

Exemplo:
    python3 host.py --porta 6000 --nome Ana --iniciar-com 4
    python3 cliente_teste.py --host 127.0.0.1 --porta 6000 --nome Beto   (x3)
"""

from __future__ import annotations

import argparse
import logging
import queue
import random
import select
import selectors
import signal
import socket
import sys
import threading
import time

import bot
import protocol as P
import tela
from lobby_link import ErroLobby, LigacaoLobby, LobbyCliente, LobbyStub
from tron import Partida

log = logging.getLogger("host")

# Fases da partida (seção 5.1). INICIANDO acontece dentro de executar().
AGUARDANDO_JOGADORES = "AGUARDANDO_JOGADORES"
CONTAGEM = "CONTAGEM"
EM_JOGO = "EM_JOGO"
FIM = "FIM"

# Estados de cada conexão (seção 5.2)
AGUARDANDO_HELLO = "AGUARDANDO_HELLO"
NA_SALA = "NA_SALA"
JOGANDO = "JOGANDO"
ELIMINADO = "ELIMINADO"
FECHADA = "FECHADA"

MAX_SAIDA = 256 * 1024  # bytes pendentes para um jogador que não lê: considera morto
ESPERA_FIM = 1.0        # s entre GAME_OVER/GAME_ABORTED e fechar as conexões


def agora_ms() -> int:
    return int(time.monotonic() * 1000)


class Conexao:
    def __init__(self, sock: socket.socket, endereco, agora: float):
        self.sock = sock
        self.endereco = endereco
        self.estado = AGUARDANDO_HELLO
        self.leitor = P.LeitorLinhas()
        self.saida = bytearray()
        self.mascara = selectors.EVENT_READ
        self.aceita_em = agora
        self.ultimo_rx = agora
        self.ultimo_tx = agora
        self.erros = 0
        self.id: int | None = None
        self.nome: str | None = None
        self.fechar_apos_envio = False

    def __repr__(self):
        quem = self.nome or f"{self.endereco[0]}:{self.endereco[1]}"
        return f"<{quem} {self.estado}>"


# ---------------------------------------------------------------------------
# Entrada local do jogador do host (a do host não passa pela rede)
# ---------------------------------------------------------------------------
class EntradaNenhuma:
    ativo = False

    def direcao(self, partida: Partida, pid: int) -> str | None:
        return None

    def iniciar_jogo(self, host) -> None:
        pass

    def atualizar(self, host) -> None:
        pass

    def fechar(self, host=None):
        pass


class EntradaBot(EntradaNenhuma):
    def __init__(self, rng: random.Random):
        self.rng = rng

    def direcao(self, partida, pid):
        m = partida.motos[pid]
        return bot.decidir(partida.celula_livre, m.x, m.y, m.dir, self.rng)


# ---------------------------------------------------------------------------
# Comandos digitados no terminal do host (início e cancelamento manuais)
# ---------------------------------------------------------------------------
class Comandos:
    """Lê linhas do teclado numa thread e entrega numa fila.

    `input()` bloqueia, então ele roda fora do loop principal; o loop só olha a
    fila (funciona igual em Linux e Windows). A thread para depois de "iniciar"
    para não disputar o teclado com a entrada da moto.
    """

    def __init__(self):
        self.fila: queue.Queue = queue.Queue()
        threading.Thread(target=self._ler, name="comandos", daemon=True).start()

    def _ler(self):
        while True:
            try:
                linha = input()
            except (EOFError, OSError):
                return
            cmd = linha.strip().lower()
            if cmd:
                self.fila.put(cmd)
            if cmd in ("i", "iniciar"):
                return

    def proximo(self) -> str | None:
        try:
            return self.fila.get_nowait()
        except queue.Empty:
            return None


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------
class Host:
    def __init__(self, args, lobby: LigacaoLobby, entrada):
        self.args = args
        self.lobby = lobby
        self.entrada = entrada
        self.sel = selectors.DefaultSelector()
        self.escuta: socket.socket | None = None
        self.conexoes: set[Conexao] = set()
        self.sala: dict[int, Conexao | None] = {0: None}  # id -> conexão (host = None)
        self.fase = AGUARDANDO_JOGADORES
        self.match_id = 0
        self.partida: Partida | None = None
        self.prox_tick = 0.0
        self.ultimo_ciclo_enviado = 0
        self.ultimo_grid: list[str] | None = None
        self.fim_em: float | None = None
        self.game_over: dict | None = None
        self.abortada = False
        self.manual = args.iniciar_com is None and not args.jogadores
        self.comandos: Comandos | None = None
        self.game_start: dict | None = None
        self.inicio_contagem = 0.0
        self.ultimo_state: dict | None = None

    # ------------------------------------------------------------------ início
    def abrir(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.args.ip, self.args.porta))
        s.listen(8)
        s.setblocking(False)
        self.escuta = s
        self.sel.register(s, selectors.EVENT_READ, None)
        porta = s.getsockname()[1]
        log.info("escutando em %s:%d", self.args.ip, porta)
        # o host já está escutando antes do CREATE_MATCH (seção 5.1)
        self.match_id = self.lobby.criar_partida(porta, self.args.max_jogadores, self.args.sala)
        self.lobby.atualizar_jogadores(self.nomes_sala())
        if self.manual:
            self.comandos = Comandos()
            self._mostrar_sala()

    def executar(self) -> int:
        try:
            self.abrir()
        except ErroLobby as e:
            # INICIANDO -> encerra (seção 5.1): sem lobby ninguém acha a partida
            log.error("não foi possível criar a partida no lobby: %s", e)
            print(f"ERRO lobby: {e}", flush=True)
            if self.escuta:
                self.escuta.close()
            return 1
        except OSError as e:
            log.error("não foi possível abrir a porta %d: %s", self.args.porta, e)
            print(f"ERRO porta {self.args.porta}: {e}", flush=True)
            return 1
        try:
            while True:
                agora = time.monotonic()
                espera = max(0.0, min(0.05, self._prazo(agora) - agora))
                for chave, mask in self.sel.select(espera):
                    if chave.data is None:
                        self._aceitar()
                        continue
                    conn: Conexao = chave.data
                    if mask & selectors.EVENT_READ and conn.estado != FECHADA:
                        self._ler(conn)
                    if mask & selectors.EVENT_WRITE and conn.estado != FECHADA:
                        self._descarregar(conn)
                agora = time.monotonic()
                self._manutencao(agora)
                self._avancar_fase(agora)
                self.entrada.atualizar(self)
                if self.fase == FIM and agora >= self.fim_em:
                    break
        except KeyboardInterrupt:
            log.info("host interrompido (Ctrl+C)")
            self.abortar("host_quit")
        except Exception:
            log.exception("erro interno")
            self.abortar("internal_error")
            self._encerrar()
            return 2
        self._encerrar()
        return 0

    def _prazo(self, agora: float) -> float:
        if self.fase in (CONTAGEM, EM_JOGO):
            return self.prox_tick
        if self.fase == FIM:
            return self.fim_em
        return agora + 0.05

    # ------------------------------------------------------------------ rede
    def _aceitar(self) -> None:
        try:
            sock, endereco = self.escuta.accept()
        except OSError:
            return
        sock.setblocking(False)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn = Conexao(sock, endereco, time.monotonic())
        self.conexoes.add(conn)
        self.sel.register(sock, selectors.EVENT_READ, conn)
        log.info("conexão de %s:%d", *endereco[:2])

    def _ler(self, conn: Conexao) -> None:
        try:
            dados = conn.sock.recv(65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as e:
            log.info("%s: erro no recv (%s)", conn, e)
            self._perda(conn, "disconnect")
            return
        if not dados:
            log.info("%s: conexão fechada pelo outro lado", conn)
            self._perda(conn, "disconnect")
            return
        conn.ultimo_rx = time.monotonic()
        try:
            linhas = conn.leitor.alimentar(dados)
        except P.LinhaMuitoLonga as e:
            log.warning("%s: %s, fechando", conn, e)
            self._perda(conn, "disconnect")
            return
        for linha in linhas:
            if conn.estado == FECHADA or conn.fechar_apos_envio:
                break
            self._processar(conn, linha)

    def _enviar(self, conn: Conexao, msg: dict) -> None:
        if conn.estado == FECHADA or conn.fechar_apos_envio:
            return
        conn.saida += P.codificar(msg)
        conn.ultimo_tx = time.monotonic()
        self._descarregar(conn)

    def _descarregar(self, conn: Conexao) -> None:
        if conn.saida:
            try:
                n = conn.sock.send(conn.saida)
                del conn.saida[:n]
            except (BlockingIOError, InterruptedError):
                pass
            except OSError as e:
                log.info("%s: erro no send (%s)", conn, e)
                self._perda(conn, "disconnect")
                return
        if len(conn.saida) > MAX_SAIDA:
            log.warning("%s: %d bytes acumulados sem leitura", conn, len(conn.saida))
            self._perda(conn, "timeout")
            return
        if not conn.saida and conn.fechar_apos_envio:
            self._fechar(conn)
            return
        mascara = selectors.EVENT_READ | (selectors.EVENT_WRITE if conn.saida else 0)
        if mascara != conn.mascara:
            conn.mascara = mascara
            self.sel.modify(conn.sock, mascara, conn)

    def _difundir(self, msg: dict) -> None:
        """Envia a todos que já passaram do HELLO (inclusive eliminados)."""
        dados = P.codificar(msg)  # codifica uma vez só
        agora = time.monotonic()
        for conn in list(self.conexoes):
            if conn.id is not None and conn.estado != FECHADA and not conn.fechar_apos_envio:
                conn.saida += dados
                conn.ultimo_tx = agora
                self._descarregar(conn)

    def _fechar(self, conn: Conexao) -> None:
        if conn.estado == FECHADA:
            return
        conn.estado = FECHADA
        self.conexoes.discard(conn)
        try:
            self.sel.unregister(conn.sock)
        except (KeyError, ValueError):
            pass
        try:
            conn.sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------ mensagens
    def _processar(self, conn: Conexao, linha: bytes) -> None:
        try:
            msg = P.decodificar(linha)
        except P.ErroProtocolo as e:
            self._erro(conn, e.codigo, e.mensagem, e.ref)
            return
        tipo = msg["type"]
        if tipo not in P.HOST_PERMITIDAS[conn.estado]:
            if tipo == "ERROR":  # nunca responde ERROR a ERROR
                log.warning("%s: ERROR recebido: %s", conn, msg)
                if conn.estado == AGUARDANDO_HELLO:
                    self._fechar(conn)
                return
            self._erro(conn, P.UNEXPECTED, f"{tipo} não é permitido no estado {conn.estado}", tipo)
            return
        conn.erros = 0

        if tipo == "PING":
            self._enviar(conn, P.pong(msg["t"]))
        elif tipo == "PONG":
            log.debug("%s: RTT %d ms", conn, agora_ms() - msg["t"])
        elif tipo == "ERROR":
            log.warning("%s: ERROR recebido: %s %s", conn, msg["code"], msg.get("message"))
        elif tipo == "HELLO":
            self._hello(conn, msg)
        elif tipo == "BYE":
            self._bye(conn, msg)
        elif tipo == "TURN":
            self._turn(conn, msg)

    def _erro(self, conn: Conexao, codigo: str, mensagem: str, ref: str | None) -> None:
        log.warning("%s: %s (%s)", conn, codigo, mensagem)
        if ref != "ERROR":
            self._enviar(conn, P.erro(codigo, mensagem, ref))
        conn.erros += 1
        if conn.estado == AGUARDANDO_HELLO:
            # quem não se apresenta não fala com o host
            conn.fechar_apos_envio = True
            self._descarregar(conn)
        elif conn.erros >= P.MAX_ERROS:
            log.warning("%s: %d erros seguidos, desconectando", conn, conn.erros)
            self._perda(conn, "disconnect")

    def _hello(self, conn: Conexao, msg: dict) -> None:
        nome = msg["name"]
        ocupados = [c.nome if c else self.args.nome for c in self.sala.values()]
        motivo = None
        if msg["version"] != P.VERSAO:
            motivo = (P.BAD_VERSION, f"versão {msg['version']} não suportada (use {P.VERSAO})")
        elif not P.nome_valido(nome):
            motivo = (P.BAD_NAME, "nome deve ter 1-16 caracteres de [A-Za-z0-9_-]")
        elif self.fase != AGUARDANDO_JOGADORES:
            motivo = (P.MATCH_STARTED, "a partida já começou")
        elif self.args.jogadores and nome not in self.args.jogadores:
            motivo = (P.BAD_NAME, f"{nome} não está na lista desta partida")
        elif nome in ocupados:
            motivo = (P.NAME_TAKEN, f"já existe um {nome} na sala")
        elif len(self.sala) >= self.args.max_jogadores:
            motivo = (P.MATCH_FULL, f"partida cheia ({len(self.sala)}/{self.args.max_jogadores})")
        if motivo:
            log.info("%s: REJECT %s", conn, motivo[0])
            self._enviar(conn, P.reject(*motivo))
            conn.fechar_apos_envio = True
            self._descarregar(conn)
            return

        pid = min(i for i in range(1, P.MAX_JOGADORES) if i not in self.sala)
        conn.id, conn.nome, conn.estado = pid, nome, NA_SALA
        self.sala[pid] = conn
        log.info("%s entrou na sala com id %d (%d/%d)", nome, pid, len(self.sala),
                 self.args.max_jogadores)
        self._enviar(conn, {"type": "WELCOME", "id": pid, "match_id": self.match_id,
                            "min_players": P.MIN_JOGADORES, "max_players": self.args.max_jogadores,
                            "players": self.lista_sala()})
        self._sala_mudou()

    def _bye(self, conn: Conexao, msg: dict) -> None:
        log.info("%s: BYE (%s)", conn, msg.get("reason"))
        if conn.estado == JOGANDO:
            self.partida.marcar_eliminacao(conn.id, "quit")
            conn.estado = ELIMINADO  # o jogador fecha a conexão logo depois
        elif conn.estado == NA_SALA:
            self._perda(conn, "quit")
        else:  # ELIMINADO
            self._fechar(conn)

    def _turn(self, conn: Conexao, msg: dict) -> None:
        if conn.estado == ELIMINADO:
            return  # pode ter sido enviado antes de ele saber que morreu
        if conn.estado == NA_SALA and self.fase == AGUARDANDO_JOGADORES:
            self._erro(conn, P.UNEXPECTED, "TURN antes do GAME_START", "TURN")
            return
        if msg["cycle"] > self.ultimo_ciclo_enviado:
            self._erro(conn, P.BAD_FIELD,
                       f"cycle {msg['cycle']} > último enviado {self.ultimo_ciclo_enviado}", "TURN")
            return
        self.partida.enfileirar_virada(conn.id, msg["dir"], msg["cycle"])

    # ------------------------------------------------------------------ sala
    def lista_sala(self) -> list[dict]:
        return [{"id": i, "name": c.nome if c else self.args.nome} for i, c in sorted(self.sala.items())]

    def nomes_sala(self) -> list[str]:
        return [j["name"] for j in self.lista_sala()]

    def _sala_mudou(self) -> None:
        self._difundir({"type": "ROSTER", "players": self.lista_sala()})
        self.lobby.atualizar_jogadores(self.nomes_sala())
        if self.manual:
            self._mostrar_sala()

    def _mostrar_sala(self) -> None:
        n = len(self.sala)
        print(f"Sala {self.match_id}: {', '.join(self.nomes_sala())} ({n}/{self.args.max_jogadores}). "
              f"Digite 'iniciar' (mínimo {P.MIN_JOGADORES}) ou 'cancelar'.", flush=True)

    def _perda(self, conn: Conexao, motivo: str) -> None:
        """Desconexão, timeout ou saída. O que fazer depende da fase (tabela da seção 3.5)."""
        estado = conn.estado
        self._fechar(conn)
        if conn.id is None or estado == FECHADA:
            return
        if self.fase == AGUARDANDO_JOGADORES:
            if self.sala.get(conn.id) is conn:
                del self.sala[conn.id]
                log.info("%s saiu da sala (%s)", conn.nome, motivo)
                self._sala_mudou()
        elif self.fase in (CONTAGEM, EM_JOGO):
            if estado == JOGANDO:
                log.info("%s caiu (%s): eliminado no próximo ciclo", conn.nome, motivo)
                self.partida.marcar_eliminacao(conn.id, motivo)

    def _manutencao(self, agora: float) -> None:
        for conn in list(self.conexoes):
            if conn.estado == FECHADA:
                continue
            if conn.fechar_apos_envio:
                if agora - conn.ultimo_tx > ESPERA_FIM:
                    self._fechar(conn)
                continue
            if conn.estado == AGUARDANDO_HELLO:
                if agora - conn.aceita_em > P.TIMEOUT_HELLO:
                    log.info("%s: 5 s sem HELLO, fechando", conn)
                    self._fechar(conn)
                    continue
            elif agora - conn.ultimo_rx > P.TIMEOUT_VIDA:
                log.info("%s: %.1f s sem sinal", conn, agora - conn.ultimo_rx)
                self._perda(conn, "timeout")
                continue
            if agora - conn.ultimo_tx >= P.PING_INTERVALO:
                self._enviar(conn, P.ping(agora_ms()))

    # ------------------------------------------------------------------ jogo
    def _pronto_para_iniciar(self) -> bool:
        if self.manual:
            return self._comando_manual()
        if len(self.sala) < P.MIN_JOGADORES:
            return False
        if self.args.jogadores:
            return set(self.args.jogadores) <= set(self.nomes_sala())
        return len(self.sala) >= self.args.iniciar_com

    def _comando_manual(self) -> bool:
        cmd = self.comandos.proximo()
        if cmd in ("i", "iniciar"):
            if len(self.sala) >= P.MIN_JOGADORES:
                return True
            print(f"Ainda não dá: {len(self.sala)} na sala, mínimo {P.MIN_JOGADORES}.", flush=True)
            self.comandos = Comandos()  # a thread parou no "iniciar"; volta a ler
        elif cmd in ("c", "cancelar"):
            log.info("host cancelou a sala")
            self.abortar("cancelled")
        elif cmd is not None:
            print("Comandos: iniciar, cancelar", flush=True)
        return False

    def _avancar_fase(self, agora: float) -> None:
        if self.fase == AGUARDANDO_JOGADORES and self._pronto_para_iniciar():
            self.iniciar()
        elif self.fase in (CONTAGEM, EM_JOGO):
            atrasados = 0
            while self.fase != FIM and time.monotonic() >= self.prox_tick:
                self._ciclo()
                self.prox_tick += self.args.tick_ms / 1000
                atrasados += 1
            if atrasados > 1:
                log.warning("host atrasado: %d ciclos calculados de uma vez", atrasados)

    def iniciar(self) -> None:
        """'Host aperta iniciar' (aqui: automático quando a sala enche)."""
        self.fase = CONTAGEM  # a partir daqui HELLO novo recebe MATCH_STARTED
        if not self.lobby.iniciar_partida():
            log.warning("lobby não confirmou START_MATCH; iniciando mesmo assim")
        self.partida = Partida(self.args.largura, self.args.altura,
                               [(j["id"], j["name"]) for j in self.lista_sala()])
        for c in self.sala.values():
            if c is not None:
                c.estado = JOGANDO
        self.game_start = {"type": "GAME_START",
                           "arena": {"width": self.args.largura, "height": self.args.altura},
                           "tick_ms": self.args.tick_ms, "start_in_ms": self.args.start_in_ms,
                           "players": self.partida.jogadores_inicio_json()}
        self._difundir(self.game_start)
        t0 = time.monotonic()
        self.inicio_contagem = t0
        self.prox_tick = t0 + (self.args.start_in_ms + self.args.tick_ms) / 1000
        self.ultimo_grid = self.partida.grid_texto()
        log.info("GAME_START com %s; ciclo 1 em %d ms", self.nomes_sala(),
                 self.args.start_in_ms + self.args.tick_ms)
        self.entrada.iniciar_jogo(self)

    def _ciclo(self) -> None:
        p = self.partida
        self.fase = EM_JOGO
        if p.motos[0].viva:
            d = self.entrada.direcao(p, 0)
            if d:
                p.enfileirar_virada(0, d, p.ciclo)
        r = p.avancar()
        for v in r.viradas:
            if v.atraso > 0:
                log.info("TURN de %s aplicado no ciclo %d com atraso %d (cliente viu %d)",
                         p.motos[v.id].nome, v.ciclo_aplicado, v.atraso, v.ciclo_cliente)
        for e in r.eventos:
            log.info("ciclo %d: %s eliminado (%s)", r.ciclo, p.motos[e["id"]].nome, e["reason"])
            c = self.sala.get(e["id"])
            if c is not None and c.estado == JOGANDO:
                c.estado = ELIMINADO
            if e["id"] == 0:
                log.info("a moto do host foi eliminada; host continua como espectador")
        estado = p.estado_json(r.eventos)
        self._difundir(estado)
        self.ultimo_ciclo_enviado = r.ciclo
        self.ultimo_grid = estado["grid"]
        self.ultimo_state = estado
        if self.args.mostrar and not self.entrada.ativo:
            vivos = ", ".join(p.motos[i].nome for i in p.vivas())
            tela.desenhar(estado["grid"], estado["bikes"],
                          f"[host {self.args.nome}] ciclo {r.ciclo}  vivos: {vivos}")
        if r.terminou:
            self.game_over = p.game_over_json()
            self._difundir(self.game_over)
            log.info("GAME_OVER: %s", self.game_over)
            self.fase = FIM
            self.fim_em = time.monotonic() + ESPERA_FIM

    # ------------------------------------------------------------------ fim
    def abortar(self, motivo: str) -> None:
        """Encerramento controlado sem resultado (seção 3.6, GAME_ABORTED)."""
        if self.fase == FIM:
            return
        self.abortada = True
        ciclo = self.partida.ciclo if self.partida else None
        if self.fase == AGUARDANDO_JOGADORES and motivo == "host_quit":
            motivo = "cancelled"
        self._difundir({"type": "GAME_ABORTED", "reason": motivo, "cycle": ciclo})
        self.fase = FIM
        self.fim_em = time.monotonic()
        self._esvaziar(ESPERA_FIM)

    def _esvaziar(self, limite: float) -> None:
        """Tenta mandar o que estiver pendente por até `limite` segundos."""
        fim = time.monotonic() + limite
        while time.monotonic() < fim and any(c.saida for c in self.conexoes):
            for chave, mask in self.sel.select(0.05):
                if chave.data is not None and mask & selectors.EVENT_WRITE:
                    self._descarregar(chave.data)

    def _despedir(self, limite: float = 0.2) -> None:
        abertos = []
        for conn in self.conexoes:
            if conn.estado == FECHADA:
                continue
            try:
                conn.sock.shutdown(socket.SHUT_WR)
                abertos.append(conn.sock)
            except OSError:
                pass
        fim = time.monotonic() + limite
        while abertos and time.monotonic() < fim:
            try:
                prontos, _, _ = select.select(abertos, [], [], max(0.0, fim - time.monotonic()))
            except (OSError, ValueError):
                return
            for s in prontos:
                try:
                    dados = s.recv(65536)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    dados = b""
                if not dados:
                    abertos.remove(s)

    def _encerrar(self) -> None:
        self._esvaziar(0.5)
        self._despedir()
        for conn in list(self.conexoes):
            self._fechar(conn)
        if self.escuta:
            self.sel.unregister(self.escuta)
            self.escuta.close()
        resultado = None
        if self.partida is None:
            self.lobby.cancelar_partida()
        elif self.game_over and not self.abortada:
            w = self.game_over["winner"]
            self.lobby.encerrar_partida(w["name"] if w else None)
            resultado = tela.resumo_resultado(self.game_over, self.ultimo_grid)
        else:
            self.lobby.encerrar_partida(None)
        self.entrada.fechar(self)
        if resultado:
            print(resultado, flush=True)
        self.lobby.fechar()


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------
def ler_argumentos(argv=None):
    ap = argparse.ArgumentParser(description="Host autoritativo do Tron")
    ap.add_argument("--ip", default="0.0.0.0", help="endereço de escuta (padrão 0.0.0.0)")
    ap.add_argument("--porta", type=int, default=6000, help="porta da partida (0 = qualquer)")
    ap.add_argument("--nome", default="Host", help="nome do jogador do host (id 0)")
    ap.add_argument("--sala", default=None, help="nome da sala (match_name)")
    ap.add_argument("--lobby-ip", default=None,
                    help="IP do lobby; sem ele o host usa o stub e não fala com lobby nenhum")
    ap.add_argument("--lobby-porta", type=int, default=5000)
    ap.add_argument("--advertise-ip", default=None,
                    help="IP do host para os outros jogadores, só necessário quando host e "
                         "lobby estão na mesma máquina e o host conecta por 127.0.0.1")
    ap.add_argument("--match-id", type=int, default=1, help="match_id usado pelo stub do lobby")
    ap.add_argument("--jogadores", default="",
                    help="início automático: só aceita estes nomes (separados por vírgula) "
                         "e começa quando todos chegarem")
    ap.add_argument("--iniciar-com", type=int, default=None,
                    help="início automático com N jogadores, host incluso. Sem esta opção e "
                         "sem --jogadores, o início é manual (digite 'iniciar' no terminal)")
    ap.add_argument("--max-jogadores", type=int, default=4, choices=(3, 4))
    ap.add_argument("--tick-ms", type=int, default=100)
    ap.add_argument("--start-in-ms", type=int, default=3000)
    ap.add_argument("--largura", type=int, default=40)
    ap.add_argument("--altura", type=int, default=20)
    ap.add_argument("--entrada", choices=("bot", "nenhuma", "teclado"), default="bot",
                    help="como a moto do host é controlada; teclado = setas ou WASD em tela "
                         "cheia (Windows, Linux e macOS)")
    ap.add_argument("--ascii", action="store_true", help="com --entrada teclado: só ASCII na tela")
    ap.add_argument("--sem-cor", action="store_true", help="com --entrada teclado: tela sem cores")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--mostrar", action="store_true", help="desenha a arena a cada ciclo")
    ap.add_argument("--log", default=None, help="arquivo de log (padrão: stderr)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    a.jogadores = [n.strip() for n in a.jogadores.split(",") if n.strip()]
    if a.jogadores and len(a.jogadores) + 1 > a.max_jogadores:
        ap.error("--jogadores tem gente demais para --max-jogadores")
    if a.iniciar_com is not None and not P.MIN_JOGADORES <= a.iniciar_com <= a.max_jogadores:
        ap.error("--iniciar-com deve estar entre 3 e --max-jogadores")
    if not P.nome_valido(a.nome):
        ap.error("--nome deve ter 1-16 caracteres de [A-Za-z0-9_-]")
    if a.tick_ms < 10 or a.start_in_ms < 0:
        ap.error("--tick-ms >= 10 e --start-in-ms >= 0")
    if a.largura < 10 or a.altura < 10 or (a.largura + 3) * a.altura + 600 > P.MAX_LINHA:
        ap.error("arena deve ter lados >= 10 e caber num STATE de 16 KiB")
    return a


def configurar_log(args, nome):
    handlers = None
    if args.log:
        handlers = [logging.FileHandler(args.log, encoding="utf-8")]
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=f"%(asctime)s.%(msecs)03d [{nome}] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def _sigterm(signum, frame):
    raise KeyboardInterrupt


def main(argv=None) -> int:
    args = ler_argumentos(argv)
    if args.entrada == "teclado" and args.log is None:
        args.log = f"host_{args.nome}.log"
    configurar_log(args, args.nome)
    rng = random.Random(args.seed)
    if args.entrada == "teclado":
        from host_tela import EntradaTecladoTela
        from ui import Opcoes, ascii_automatico
        entrada = EntradaTecladoTela(Opcoes(ascii=args.ascii or ascii_automatico(sys.stdout),
                                            cor=not args.sem_cor))
    elif args.entrada == "bot":
        entrada = EntradaBot(rng)
    else:
        entrada = EntradaNenhuma()

    if args.lobby_ip:
        lobby = LobbyCliente(args.lobby_ip, args.lobby_porta, args.nome, args.advertise_ip)
    else:
        lobby = LobbyStub(args.match_id)

    host = Host(args, lobby, entrada)
    signal.signal(signal.SIGTERM, _sigterm)  # kill = Ctrl+C: GAME_ABORTED host_quit
    return host.executar()


if __name__ == "__main__":
    sys.exit(main())
