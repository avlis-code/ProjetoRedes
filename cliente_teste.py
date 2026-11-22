#!/usr/bin/env python3
"""Cliente de teste: conecta no host, manda HELLO e dirige com o bot.

Segue a máquina de estados do jogador (seção 6), mas sem lobby: recebe o
endereço do host pela linha de comando. Rode 2 ou 3 cópias com nomes
diferentes para encher a sala.

Com --lobby-ip, passa antes pelo lobby (REGISTER, LIST_MATCHES, JOIN_MATCH) e
usa o host_ip:host_port do JOIN_OK. A conexão com o lobby fica aberta durante a
partida (decisão D9), com PING a cada 10 s numa thread separada; se o lobby
cair, só aparece uma linha no log.

Cenários de falha para o relatório:
  --cair-no-ciclo N    fecha o socket de repente no ciclo N (host vê "disconnect")
  --travar-no-ciclo N  para de ler e de mandar qualquer coisa (host vê "timeout" em 3 s)
  --sair-no-ciclo N    manda BYE e sai (host vê "quit")

Saída: no fim imprime uma linha "RESULTADO ..." (igual à do host quando todos
concordam) ou "ABORTADA ...". Código de saída: 0 partida completa, 1 abortada
ou recusada, 3 falha simulada.
"""

from __future__ import annotations

import argparse
import logging
import random
import select
import socket
import sys
import threading
import time

import bot
import protocol as P
import tela
from lobby_link import PING_LOBBY, ConexaoLobby, ErroLobby

log = logging.getLogger("jogador")


class Jogador:
    def __init__(self, args):
        self.args = args
        self.rng = random.Random(args.seed)
        self.estado = "CONECTANDO_HOST"
        self.sock: socket.socket | None = None
        self.leitor = P.LeitorLinhas()
        self.meu_id: int | None = None
        self.ultimo_ciclo = 0
        self.ultimo_state: dict | None = None
        self.ultimo_rx = self.ultimo_tx = self.conectado_em = 0.0
        self.saida = 0

    # ------------------------------------------------------------------ rede
    def enviar(self, msg: dict) -> None:
        try:
            self.sock.sendall(P.codificar(msg))
            self.ultimo_tx = time.monotonic()
        except OSError as e:
            self.fim_abortado(f"erro ao enviar ({e})")

    def passar_pelo_lobby(self) -> bool:
        """REGISTER -> LIST_MATCHES (até achar sala aberta) -> JOIN_MATCH -> JOIN_OK."""
        a = self.args
        try:
            lobby = ConexaoLobby(a.lobby_ip, a.lobby_porta)
            lobby.registrar(a.nome)
            fim = time.monotonic() + a.esperar_sala
            while True:
                salas = lobby.pedir({"type": "LIST_MATCHES"}, "MATCH_LIST")["matches"]
                abertas = [m for m in salas if m["status"] == "ABERTA"
                           and (a.partida is None or m["match_id"] == a.partida)]
                if abertas:
                    break
                if time.monotonic() > fim:
                    print("ABORTADA nenhuma sala aberta no lobby", flush=True)
                    return False
                time.sleep(0.3)
            sala = abertas[0]
            ok = lobby.pedir({"type": "JOIN_MATCH", "match_id": sala["match_id"]}, "JOIN_OK")
        except ErroLobby as e:
            print(f"ABORTADA lobby: {e}", flush=True)
            return False
        log.info("lobby: entrando na sala %d '%s' de %s em %s:%d", ok["match_id"],
                 sala["match_name"], ok["host"], ok["host_ip"], ok["host_port"])
        a.host, a.porta = ok["host_ip"], ok["host_port"]
        threading.Thread(target=self._manter_lobby, args=(lobby,), daemon=True).start()
        return True

    @staticmethod
    def _manter_lobby(lobby: ConexaoLobby) -> None:
        """PING periódico ao lobby. Nada do jogo depende disto."""
        while True:
            time.sleep(PING_LOBBY)
            try:
                lobby.ping()
            except ErroLobby as e:
                log.info("conexão com o lobby perdida (%s); a partida não é afetada", e)
                lobby.fechar()
                return

    def conectar(self) -> bool:
        a = self.args
        if a.lobby_ip and not self.passar_pelo_lobby():
            return False
        try:
            self.sock = socket.create_connection((a.host, a.porta), timeout=P.TIMEOUT_HELLO)
        except OSError as e:
            print(f"ABORTADA não conectou em {a.host}:{a.porta} ({e})", flush=True)
            return False
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(None)
        self.conectado_em = self.ultimo_rx = time.monotonic()
        self.enviar(P.hello(a.nome))
        log.info("conectado em %s:%d, HELLO enviado", a.host, a.porta)
        return True

    def executar(self) -> int:
        if not self.conectar():
            return 1
        while self.estado != "FIM":
            pronto, _, _ = select.select([self.sock], [], [], 0.05)
            if pronto:
                try:
                    dados = self.sock.recv(65536)
                except OSError as e:
                    self.fim_abortado(f"conexão com o host perdida ({e})")
                    break
                if not dados:
                    self.fim_abortado("conexão com o host perdida (fechada)")
                    break
                self.ultimo_rx = time.monotonic()
                try:
                    linhas = self.leitor.alimentar(dados)
                except P.LinhaMuitoLonga:
                    self.fim_abortado("linha longa demais vinda do host")
                    break
                for linha in linhas:
                    self.processar(linha)
                    if self.estado == "FIM":
                        break
            self.manutencao()
        try:
            self.sock.close()
        except OSError:
            pass
        return self.saida

    def manutencao(self) -> None:
        if self.estado == "FIM":
            return
        agora = time.monotonic()
        if self.estado == "CONECTANDO_HOST":
            if agora - self.conectado_em > P.TIMEOUT_HELLO:
                self.fim_abortado("5 s sem WELCOME")
            return
        if agora - self.ultimo_rx > P.TIMEOUT_VIDA:
            self.fim_abortado("conexão com o host perdida (3 s sem sinal)")
            return
        if agora - self.ultimo_tx >= P.PING_INTERVALO:
            self.enviar(P.ping(int(agora * 1000)))

    # ------------------------------------------------------------------ mensagens
    def processar(self, linha: bytes) -> None:
        try:
            msg = P.decodificar(linha)
        except P.ErroProtocolo as e:
            log.warning("mensagem inválida do host ignorada: %s", e)  # nunca responde ERROR
            return
        tipo = msg["type"]
        if tipo not in P.JOGADOR_PERMITIDAS[self.estado]:
            log.warning("%s inesperado no estado %s, ignorado", tipo, self.estado)
            return
        if tipo == "PING":
            self.enviar(P.pong(msg["t"]))
        elif tipo == "PONG":
            log.debug("RTT %d ms", int(time.monotonic() * 1000) - msg["t"])
        elif tipo == "ERROR":
            log.warning("ERROR do host: %s %s", msg["code"], msg.get("message"))
        elif tipo == "WELCOME":
            self.meu_id = msg["id"]
            self.estado = "AGUARDANDO_INICIO"
            log.info("WELCOME: id %d na partida %d", msg["id"], msg["match_id"])
        elif tipo == "REJECT":
            print(f"ABORTADA recusado pelo host: {msg['code']} {msg['message']}", flush=True)
            self.estado = "FIM"
            self.saida = 1
        elif tipo == "ROSTER":
            log.info("sala: %s", [p["name"] for p in msg["players"]])
        elif tipo == "GAME_START":
            self.estado = "CONTAGEM"
            log.info("GAME_START: %dx%d, começa em %d ms", msg["arena"]["width"],
                     msg["arena"]["height"], msg["start_in_ms"])
        elif tipo == "STATE":
            self.state(msg)
        elif tipo == "GAME_OVER":
            grid = self.ultimo_state["grid"] if self.ultimo_state else None
            print(tela.resumo_resultado(msg, grid), flush=True)
            self.estado = "FIM"
        elif tipo == "GAME_ABORTED":
            self.fim_abortado(f"host encerrou: {msg['reason']} no ciclo {msg['cycle']}")

    def state(self, msg: dict) -> None:
        if msg["cycle"] <= self.ultimo_ciclo:
            log.warning("STATE do ciclo %d descartado (último %d)", msg["cycle"], self.ultimo_ciclo)
            return
        if msg["cycle"] != self.ultimo_ciclo + 1:
            log.info("pulou do ciclo %d para %d", self.ultimo_ciclo, msg["cycle"])
        self.ultimo_ciclo = msg["cycle"]
        self.ultimo_state = msg
        if self.estado == "CONTAGEM":
            self.estado = "JOGANDO"
        for e in msg["events"]:
            log.info("ciclo %d: id %d eliminado (%s)", msg["cycle"], e["id"], e["reason"])
            if e["id"] == self.meu_id and self.estado == "JOGANDO":
                self.estado = "ESPECTADOR"
        if self.args.mostrar:
            tela.desenhar(msg["grid"], msg["bikes"],
                          f"[{self.args.nome} id {self.meu_id}] ciclo {msg['cycle']} {self.estado}")
        if self.simular_falha(msg["cycle"]):
            return
        if self.estado == "JOGANDO":
            eu = next(b for b in msg["bikes"] if b["id"] == self.meu_id)
            d = bot.decidir(bot.livre_em_grid(msg["grid"]), eu["x"], eu["y"], eu["dir"],
                            self.rng, self.args.chance)
            if d:
                self.enviar(P.turn(d, msg["cycle"]))

    def simular_falha(self, ciclo: int) -> bool:
        a = self.args
        if a.cair_no_ciclo is not None and ciclo >= a.cair_no_ciclo:
            log.warning(f"FALHA SIMULADA queda no ciclo {ciclo}")
            print(f"FALHA SIMULADA queda no ciclo {ciclo}", flush=True)
            self.sock.close()
            sys.exit(3)
        if a.travar_no_ciclo is not None and ciclo >= a.travar_no_ciclo:
            log.warning(f"FALHA SIMULADA travado no ciclo {ciclo} por {a.travar_por} s")
            print(f"FALHA SIMULADA travado no ciclo {ciclo} por {a.travar_por} s", flush=True)
            time.sleep(a.travar_por)  # não lê nem manda nada, nem PING
            sys.exit(3)
        if a.sair_no_ciclo is not None and ciclo >= a.sair_no_ciclo:
            log.warning(f"FALHA SIMULADA BYE no ciclo {ciclo}")
            print(f"FALHA SIMULADA BYE no ciclo {ciclo}", flush=True)
            self.enviar(P.bye("cliente de teste saiu"))
            self.sock.close()
            sys.exit(3)
        return False

    def fim_abortado(self, motivo: str) -> None:
        if self.estado == "FIM":
            return
        log.warning("partida abortada: %s", motivo)
        print(f"ABORTADA {motivo}", flush=True)
        self.estado = "FIM"
        self.saida = 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cliente de teste do Tron (sem lobby)")
    ap.add_argument("--lobby-ip", default=None,
                    help="IP do lobby; se dado, o endereço do host vem do JOIN_OK")
    ap.add_argument("--lobby-porta", type=int, default=5000)
    ap.add_argument("--partida", type=int, default=None,
                    help="match_id a entrar (padrão: a primeira sala aberta)")
    ap.add_argument("--esperar-sala", type=float, default=10.0,
                    help="segundos esperando aparecer uma sala aberta no lobby")
    ap.add_argument("--host", default="127.0.0.1", help="IP do host da partida (sem lobby)")
    ap.add_argument("--porta", type=int, default=6000, help="porta do host (sem lobby)")
    ap.add_argument("--nome", required=True)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--chance", type=float, default=0.1, help="chance de virar a cada ciclo")
    ap.add_argument("--mostrar", action="store_true", help="desenha a arena a cada ciclo")
    ap.add_argument("--cair-no-ciclo", type=int, default=None)
    ap.add_argument("--travar-no-ciclo", type=int, default=None)
    ap.add_argument("--travar-por", type=float, default=6.0)
    ap.add_argument("--sair-no-ciclo", type=int, default=None)
    ap.add_argument("--log", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format=f"%(asctime)s.%(msecs)03d [{args.nome}] %(message)s",
                        datefmt="%H:%M:%S", filename=args.log)
    j = Jogador(args)
    try:
        return j.executar()
    except KeyboardInterrupt:  # Ctrl+C: sai de forma educada (seção 3.2, BYE)
        if j.sock is not None and j.estado != "FIM":
            try:
                j.sock.sendall(P.codificar(P.bye("Ctrl+C")))
                j.sock.close()
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
