#!/usr/bin/env python3
"""Servidor de lobby (seções 3.1 e 4 do protocolo).

Adaptado do lobby.py original do colega: mantém a estrutura dele (uma thread
por cliente, um único lock para as tabelas, fila de saída por cliente e envio
fora do lock), mas segue o protocolo.md:

  - usa o protocol.py compartilhado (framing, validação, códigos de erro);
  - é estritamente pedido -> resposta: cada pedido recebe exatamente uma
    resposta e o lobby nunca envia nada sem ter sido pedido;
  - quem sabe a lista real de jogadores é o host (MATCH_UPDATE, decisão D7);
    o lobby só guarda uma cópia para mostrar no MATCH_LIST;
  - o endereço do host é entregue já no JOIN_OK; o lobby nunca participa da
    partida e pode cair sem afetar quem já está jogando.

Uso:  python3 lobby.py --porta 5000
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import socket
import threading
from collections import deque

import protocol as P

log = logging.getLogger("lobby")


class Cliente:
    def __init__(self, conn: socket.socket, endereco):
        self.conn = conn
        self.endereco = endereco
        self.nome: str | None = None
        self.estado = "NAO_REGISTRADO"
        self.partida: Partida | None = None   # partida que este cliente hospeda
        self.erros = 0
        self.saida: deque = deque()
        self.trava_envio = threading.Lock()

    def descarregar(self) -> None:
        with self.trava_envio:
            while self.saida:
                try:
                    P.send_msg(self.conn, self.saida.popleft())
                except OSError:
                    self.saida.clear()
                    return

    def __str__(self):
        return self.nome or f"{self.endereco[0]}:{self.endereco[1]}"


class Partida:
    def __init__(self, match_id, nome, host: Cliente, host_ip, host_porta, max_jogadores):
        self.id = match_id
        self.nome = nome
        self.host = host
        self.host_ip = host_ip
        self.host_porta = host_porta
        self.max_jogadores = max_jogadores
        self.jogadores = [host.nome]   # cópia; atualizada pelo MATCH_UPDATE
        self.status = "ABERTA"

    def resumo(self) -> dict:
        return {"match_id": self.id, "match_name": self.nome, "host": self.host.nome,
                "players": list(self.jogadores), "max_players": self.max_jogadores,
                "status": self.status}


def eh_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


class Lobby:
    def __init__(self):
        self.trava = threading.Lock()
        self.clientes: dict[str, Cliente] = {}   # nome -> cliente registrado
        self.partidas: dict[int, Partida] = {}
        self.proximo_id = 1
        self.tratadores = {
            "REGISTER": self._register,
            "LIST_MATCHES": self._list_matches,
            "CREATE_MATCH": self._create_match,
            "JOIN_MATCH": self._join_match,
            "MATCH_UPDATE": self._match_update,
            "START_MATCH": self._start_match,
            "CANCEL_MATCH": self._cancel_match,
            "END_MATCH": self._end_match,
            "PING": lambda c, m: P.pong(m["t"]),
        }

    # ------------------------------------------------------------------ envio
    def responder(self, c: Cliente, msg: dict) -> None:
        c.saida.append(msg)
        c.descarregar()

    # ------------------------------------------------------------------ despacho
    def processar(self, c: Cliente, msg: dict) -> dict | None:
        """Devolve a resposta (ou None para um ERROR recebido)."""
        tipo = msg["type"]
        if tipo == "ERROR":  # nunca se responde ERROR a ERROR
            log.warning("%s mandou ERROR: %s", c, msg)
            return None
        with self.trava:
            if tipo not in P.LOBBY_PERMITIDAS[c.estado]:
                return P.erro(P.UNEXPECTED, f"{tipo} não é permitido no estado {c.estado}", tipo)
            return self.tratadores[tipo](c, msg)

    def _register(self, c, msg):
        nome = msg["name"]
        if msg["version"] != P.VERSAO:
            return P.erro(P.BAD_VERSION, f"versão {msg['version']} não suportada (use {P.VERSAO})",
                          "REGISTER")
        if not P.nome_valido(nome):
            return P.erro(P.BAD_NAME, "nome deve ter 1-16 caracteres de [A-Za-z0-9_-]", "REGISTER")
        if nome in self.clientes:
            return P.erro(P.NAME_TAKEN, f"o nome {nome} já está em uso", "REGISTER")
        c.nome = nome
        c.estado = "REGISTRADO"
        self.clientes[nome] = c
        log.info("registrado: %s (%s:%s)", nome, *c.endereco[:2])
        return {"type": "REGISTER_OK", "name": nome}

    def _list_matches(self, c, msg):
        return {"type": "MATCH_LIST", "matches": [m.resumo() for m in self.partidas.values()]}

    def _create_match(self, c, msg):
        # IP do host = IP de origem da conexão, não um IP informado (seção 3.1)
        host_ip = c.endereco[0]
        if eh_loopback(host_ip):
            anunciado = msg.get("advertise_ip")
            if anunciado is not None:
                try:
                    ipaddress.ip_address(anunciado)
                except ValueError:
                    return P.erro(P.BAD_FIELD, "advertise_ip não é um IP válido", "CREATE_MATCH")
                host_ip = anunciado
            else:
                log.info("%s criou partida por 127.0.0.1 sem advertise_ip: cada jogador "
                         "receberá o IP desta máquina pelo qual ele chegou ao lobby", c)
        nome = msg.get("match_name") or f"sala de {c.nome}"
        m = Partida(self.proximo_id, nome, c, host_ip, msg["port"], msg.get("max_players", 4))
        self.proximo_id += 1
        self.partidas[m.id] = m
        c.partida = m
        c.estado = "HOSPEDANDO_ABERTA"
        log.info("partida %d '%s' criada por %s (host %s:%d)", m.id, nome, c, host_ip, m.host_porta)
        return {"type": "MATCH_CREATED", "match_id": m.id, "host_ip": host_ip,
                "host_port": m.host_porta}

    def _join_match(self, c, msg):
        m = self.partidas.get(msg["match_id"])
        if m is None:
            return P.erro(P.MATCH_NOT_FOUND, f"partida {msg['match_id']} não existe", "JOIN_MATCH")
        if m.status != "ABERTA":
            return P.erro(P.MATCH_STARTED, "partida já iniciada", "JOIN_MATCH")
        if len(m.jogadores) >= m.max_jogadores:
            return P.erro(P.MATCH_FULL, f"partida cheia ({len(m.jogadores)}/{m.max_jogadores})",
                          "JOIN_MATCH")
        ip = m.host_ip
        if eh_loopback(ip):
            # Host na mesma máquina do lobby e sem advertise_ip: o IP pelo qual
            # ESTE jogador chegou ao lobby também alcança o host (ideia do original).
            ip = c.conn.getsockname()[0]
        log.info("%s pediu para entrar na partida %d", c, m.id)
        return {"type": "JOIN_OK", "match_id": m.id, "host": m.host.nome,
                "host_ip": ip, "host_port": m.host_porta}

    def _minha_partida(self, c, msg) -> Partida | dict:
        m = c.partida
        if m is None or msg["match_id"] != m.id:
            return P.erro(P.NOT_HOST, f"você não é host da partida {msg['match_id']}", msg["type"])
        return m

    def _match_update(self, c, msg):
        m = self._minha_partida(c, msg)
        if isinstance(m, dict):
            return m
        m.jogadores = list(msg["players"])
        log.info("partida %d: jogadores %s", m.id, m.jogadores)
        return {"type": "UPDATE_OK"}

    def _start_match(self, c, msg):
        m = self._minha_partida(c, msg)
        if isinstance(m, dict):
            return m
        m.status = "INICIADA"
        c.estado = "HOSPEDANDO_INICIADA"
        log.info("partida %d iniciada com %s", m.id, m.jogadores)
        return {"type": "START_OK", "match_id": m.id}

    def _remover_partida(self, c: Cliente, motivo: str) -> None:
        m, c.partida = c.partida, None
        if m is not None:
            self.partidas.pop(m.id, None)
            log.info("partida %d removida (%s)", m.id, motivo)

    def _cancel_match(self, c, msg):
        m = self._minha_partida(c, msg)
        if isinstance(m, dict):
            return m
        self._remover_partida(c, "cancelada pelo host")
        c.estado = "REGISTRADO"
        return {"type": "CANCEL_OK", "match_id": m.id}

    def _end_match(self, c, msg):
        m = self._minha_partida(c, msg)
        if isinstance(m, dict):
            return m
        self._remover_partida(c, f"terminou, vencedor {msg.get('winner')}")
        c.estado = "REGISTRADO"
        return {"type": "END_OK", "match_id": m.id}

    # ------------------------------------------------------------------ conexão
    def desconectar(self, c: Cliente) -> None:
        with self.trava:
            # limpeza automática: host caiu -> partida sai da lista (o jogo não é afetado)
            self._remover_partida(c, f"conexão do host {c} fechou")
            if c.nome is not None and self.clientes.get(c.nome) is c:
                del self.clientes[c.nome]

    def atender(self, conn: socket.socket, endereco, inatividade: float) -> None:
        c = Cliente(conn, endereco)
        leitor = P.LineReader(conn)
        conn.settimeout(inatividade or None)
        log.info("conexão de %s:%s", *endereco[:2])
        try:
            while True:
                try:
                    msg = leitor.read_msg()
                except P.ProtocolError as e:
                    if e.fatal:
                        log.warning("%s: %s, fechando", c, e)
                        break
                    resposta = e.como_mensagem()
                else:
                    if msg is None:
                        break
                    resposta = self.processar(c, msg)
                if resposta is None:
                    continue
                if resposta["type"] == "ERROR":
                    c.erros += 1
                    log.info("%s: %s %s", c, resposta["code"], resposta["message"])
                else:
                    c.erros = 0
                self.responder(c, resposta)
                if c.erros >= P.MAX_ERROS:
                    log.warning("%s: %d erros seguidos, desconectando", c, c.erros)
                    break
        except socket.timeout:
            log.info("%s sem atividade por %ss, desconectando", c, inatividade)
        except OSError as e:
            log.info("%s: erro de conexão (%s)", c, e)
        finally:
            self.desconectar(c)
            conn.close()
            log.info("desconectado: %s", c)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Servidor de lobby do Tron")
    ap.add_argument("--ip", default="0.0.0.0", help="endereço de escuta (padrão: todos)")
    ap.add_argument("--porta", type=int, default=5000)
    ap.add_argument("--inatividade", type=float, default=60,
                    help="segundos sem nenhuma mensagem antes de derrubar o cliente (0 = nunca). "
                         "Clientes mandam PING a cada 10 s, então 60 s é folgado.")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [lobby] %(message)s",
                        datefmt="%H:%M:%S")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.ip, args.porta))
    srv.listen()
    log.info("lobby escutando em %s:%d", args.ip, args.porta)

    lobby = Lobby()
    try:
        while True:
            conn, endereco = srv.accept()
            threading.Thread(target=lobby.atender, args=(conn, endereco, args.inatividade),
                             daemon=True).start()
    except KeyboardInterrupt:
        log.info("encerrando lobby")


if __name__ == "__main__":
    main()
