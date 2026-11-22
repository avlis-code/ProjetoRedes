#!/usr/bin/env python3

import argparse
import ipaddress
import logging
import re
import socket
import threading
from collections import deque

from protocol import LineReader, ProtocolError, send_msg

MIN_PLAYERS = 3
MAX_PLAYERS = 4
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,20}")

PERMITIDAS = {
    "NAO_REGISTRADO": {"REGISTER", "PING"},
    "REGISTRADO": {"LIST_MATCHES", "CREATE_MATCH", "JOIN_MATCH", "PING"},
    "EM_PARTIDA": {"START", "LEAVE", "PING"},
    "INICIADA": {"LEAVE", "PING"},
}
TIPOS_CONHECIDOS = set().union(*PERMITIDAS.values())

log = logging.getLogger("lobby")


def erro(motivo):
    return {"type": "ERROR", "reason": motivo}


class Client:
    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.name = None
        self.state = "NAO_REGISTRADO"
        self.match = None
        self.outbox = deque()
        self.send_lock = threading.Lock()

    def flush(self):
        with self.send_lock:
            while self.outbox:
                try:
                    send_msg(self.conn, self.outbox.popleft())
                except OSError:
                    self.outbox.clear()
                    return


class Match:
    def __init__(self, match_id, title, host, host_ip, host_port):
        self.id = match_id
        self.title = title
        self.host = host
        self.host_ip = host_ip
        self.host_port = host_port
        self.players = [host]
        self.status = "ABERTA"

    def resumo(self):
        return {
            "match_id": self.id,
            "title": self.title,
            "host": self.host.name,
            "players": [p.name for p in self.players],
            "max_players": MAX_PLAYERS,
            "status": self.status,
        }


def endereco_do_host(match, destino):
    ip = match.host_ip
    if ipaddress.ip_address(ip).is_loopback:
        ip = destino.conn.getsockname()[0]
    return {"ip": ip, "port": match.host_port}


class Lobby:
    def __init__(self):
        self.lock = threading.Lock()
        self.jogadores = {}
        self.partidas = {}
        self.proximo_id = 1
        self.tratadores = {
            "REGISTER": self._register,
            "LIST_MATCHES": self._list_matches,
            "CREATE_MATCH": self._create_match,
            "JOIN_MATCH": self._join_match,
            "START": self._start,
            "LEAVE": self._leave,
            "PING": lambda c, m: [(c, {"type": "PONG"})],
        }

    def _despachar(self, c, msg):
        t = msg["type"]
        if t not in TIPOS_CONHECIDOS:
            return [(c, erro(f"tipo desconhecido: {t}"))]
        if t not in PERMITIDAS[c.state]:
            return [(c, erro(f"mensagem {t} inesperada no estado {c.state}"))]
        return self.tratadores[t](c, msg)

    def _enfileirar(self, out):
        """Põe as mensagens nas filas (lock preso). Devolve os destinatários a descarregar."""
        destinos = []
        for destino, msg in out:
            destino.outbox.append(msg)
            if destino not in destinos:
                destinos.append(destino)
        return destinos

    def processar(self, c, msg):
        with self.lock:
            destinos = self._enfileirar(self._despachar(c, msg))
        for d in destinos:
            d.flush()

    def responder_erro(self, c, motivo):
        with self.lock:
            destinos = self._enfileirar([(c, erro(motivo))])
        for d in destinos:
            d.flush()

    def _register(self, c, msg):
        name = msg.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            return [(c, erro("nome inválido (use 1 a 20 letras, números, _ ou -)"))]
        if name in self.jogadores:
            return [(c, erro("nome já em uso"))]
        c.name = name
        c.state = "REGISTRADO"
        self.jogadores[name] = c
        log.info("registrado: %s (%s:%s)", name, *c.addr)
        return [(c, {"type": "REGISTERED", "name": name})]

    def _list_matches(self, c, msg):
        lista = [m.resumo() for m in self.partidas.values()]
        return [(c, {"type": "MATCH_LIST", "matches": lista})]

    def _create_match(self, c, msg):
        port = msg.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            return [(c, erro("'port' deve ser um inteiro entre 1024 e 65535"))]
        title = msg.get("title", f"Partida de {c.name}")
        if not isinstance(title, str) or not 1 <= len(title) <= 40:
            return [(c, erro("'title' deve ter de 1 a 40 caracteres"))]
       
        host_ip = msg.get("host_ip", c.addr[0])
        try:
            if not isinstance(host_ip, str):
                raise ValueError
            ipaddress.ip_address(host_ip)
        except ValueError:
            return [(c, erro("'host_ip' inválido"))]

        m = Match(self.proximo_id, title, c, host_ip, port)
        self.proximo_id += 1
        self.partidas[m.id] = m
        c.match = m
        c.state = "EM_PARTIDA"
        log.info("partida %d criada por %s (host %s:%d)", m.id, c.name, host_ip, port)
        return [(c, {"type": "MATCH_CREATED", "match_id": m.id})]

    def _join_match(self, c, msg):
        match_id = msg.get("match_id")
        m = self.partidas.get(match_id) if isinstance(match_id, int) else None
        if m is None:
            return [(c, erro("partida não existe"))]
        if m.status != "ABERTA":
            return [(c, erro("partida já iniciada"))]
        if len(m.players) >= MAX_PLAYERS:
            return [(c, erro("partida cheia"))]
        m.players.append(c)
        c.match = m
        c.state = "EM_PARTIDA"
        log.info("%s entrou na partida %d", c.name, m.id)
        out = [(c, {"type": "JOINED", "match_id": m.id,
                    "players": [p.name for p in m.players]})]
        out += [(p, {"type": "PLAYER_JOINED", "name": c.name}) for p in m.players if p is not c]
        return out

    def _start(self, c, msg):
        m = c.match
        if m.host is not c:
            return [(c, erro("só o host pode iniciar a partida"))]
        if len(m.players) < MIN_PLAYERS:
            return [(c, erro(f"são necessários pelo menos {MIN_PLAYERS} jogadores"))]
        m.status = "INICIADA"
        nomes = [p.name for p in m.players]
        log.info("partida %d iniciada com %s", m.id, nomes)
        out = []
        for p in m.players:
            p.state = "INICIADA"
            out.append((p, {"type": "MATCH_STARTED", "match_id": m.id,
                            "host": endereco_do_host(m, p), "players": nomes}))
        return out

    def _leave(self, c, msg):
        log.info("%s saiu da partida", c.name)
        out = self._sair_da_partida(c)
        c.state = "REGISTRADO"
        return out + [(c, {"type": "LEFT"})]

    def _sair_da_partida(self, c):
        """Tira c da partida em que está. Devolve as notificações para os demais."""
        m, c.match = c.match, None
        if m is None:
            return []
        if c in m.players:
            m.players.remove(c)
        out = []
        if m.status == "ABERTA":
            if c is m.host:
                del self.partidas[m.id]
                log.info("partida %d cancelada (host saiu)", m.id)
                for p in m.players:
                    p.match = None
                    p.state = "REGISTRADO"
                    out.append((p, {"type": "MATCH_CANCELLED", "reason": "o host saiu"}))
            else:
                out = [(p, {"type": "PLAYER_LEFT", "name": c.name}) for p in m.players]
        elif not m.players:
            del self.partidas[m.id]
        return out

    def desconectar(self, c):
        with self.lock:
            destinos = self._enfileirar(self._sair_da_partida(c))
            if c.name is not None and self.jogadores.get(c.name) is c:
                del self.jogadores[c.name]
        for d in destinos:
            d.flush()
    def atender(self, conn, addr, idle_timeout):
        c = Client(conn, addr)
        reader = LineReader(conn)
        conn.settimeout(idle_timeout or None)
        log.info("conexão de %s:%s", *addr)
        try:
            while True:
                try:
                    msg = reader.read_msg()
                except ProtocolError as e:
                    if e.fatal:
                        log.warning("%s: %s, fechando", addr, e)
                        break
                    self.responder_erro(c, str(e))
                    continue
                if msg is None:
                    break
                self.processar(c, msg)
        except socket.timeout:
            log.info("%s sem atividade por %ss, desconectando", c.name or addr, idle_timeout)
        except OSError as e:
            log.info("%s: erro de conexão (%s)", c.name or addr, e)
        finally:
            self.desconectar(c)
            conn.close()
            log.info("desconectado: %s", c.name or addr)


def main():
    ap = argparse.ArgumentParser(description="Servidor de lobby")
    ap.add_argument("--bind", default="0.0.0.0", help="endereço de escuta (padrão: todos)")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--idle-timeout", type=float, default=60,
                    help="segundos sem nenhuma mensagem antes de derrubar o cliente (0 = nunca)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port))
    srv.listen()
    log.info("lobby escutando em %s:%d", args.bind, args.port)

    lobby = Lobby()
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=lobby.atender, args=(conn, addr, args.idle_timeout),
                             daemon=True).start()
    except KeyboardInterrupt:
        log.info("encerrando lobby")


if __name__ == "__main__":
    main()
