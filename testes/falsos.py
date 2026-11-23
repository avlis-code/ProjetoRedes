from __future__ import annotations

import collections
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import protocol as P

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PRAZO = 10.0


class TecladoFalso:
    def __init__(self, passos=(), prazo: float = PRAZO):
        self.passos = list(passos)
        self.prazo = prazo
        self.jogador = None
        self.entrou = self.saiu = False
        self._pendentes: collections.deque = collections.deque()
        self._inicio: float | None = None

    def __enter__(self):
        self.entrou = True
        return self

    def __exit__(self, *exc):
        self.saiu = True

    def _proximas(self) -> list[str]:
        if self._inicio is None:
            self._inicio = time.monotonic()
        if not self.passos:
            if time.monotonic() - self._inicio > self.prazo:
                return ["q"]
            return []
        passo = self.passos[0]
        if isinstance(passo, tuple):
            predicado, teclas = passo
            if not predicado(self.jogador):
                return []
        else:
            teclas = passo
        self.passos.pop(0)
        if "KBI" in teclas:
            raise KeyboardInterrupt
        return list(teclas)

    def ler_todas(self, limite: int = 16) -> list[str]:
        self._pendentes.extend(self._proximas())
        teclas = []
        while self._pendentes and len(teclas) < limite:
            teclas.append(self._pendentes.popleft())
        return teclas

    def ler(self, timeout: float = 0.0):
        if not self._pendentes:
            self._pendentes.extend(self._proximas())
        if not self._pendentes:
            time.sleep(timeout)
            return None
        return self._pendentes.popleft()


class TerminalFalso:
    def __init__(self, tamanho=(100, 30)):
        self.tam = tamanho
        self.quadros: list[list[str]] = []
        self.entrou = self.saiu = False

    def __enter__(self):
        self.entrou = True
        return self

    def __exit__(self, *exc):
        self.saiu = True

    def tamanho(self):
        return self.tam

    def nova_tela(self):
        pass

    def mostrar(self, linhas):
        self.quadros.append(list(linhas))

    def texto(self, i: int = -1) -> str:
        return "\n".join(ANSI.sub("", l) for l in self.quadros[i])

    def todos(self) -> str:
        return "\n\n".join(self.texto(i) for i in range(len(self.quadros)))


class _Conexao:
    def __init__(self, sock):
        self.sock = sock
        self.leitor = P.LeitorLinhas()
        self.trava = threading.Lock()
        self.ultimo_envio = time.monotonic()

    def manda(self, dados: bytes) -> bool:
        with self.trava:
            try:
                self.sock.sendall(dados)
                self.ultimo_envio = time.monotonic()
                return True
            except OSError:
                return False


class HostFalso(threading.Thread):
    def __init__(self, roteiro, ping_a_cada: float = 0.1):
        super().__init__(daemon=True)
        self.roteiro = list(roteiro)
        self.ping_a_cada = ping_a_cada
        self.escuta = socket.socket()
        self.escuta.bind(("127.0.0.1", 0))
        self.escuta.listen(1)
        self.porta = self.escuta.getsockname()[1]
        self.recebidas: list[dict] = []
        self._trava = threading.Lock()
        self._calado = False
        self._fechado = threading.Event()
        self.conn: _Conexao | None = None
        self.start()

    def tipos(self) -> list[str]:
        with self._trava:
            return [m["type"] for m in self.recebidas]

    def de_tipo(self, tipo: str) -> list[dict]:
        with self._trava:
            return [m for m in self.recebidas if m["type"] == tipo]

    def esperar_fim(self, limite: float = PRAZO) -> None:
        self.join(limite)

    def run(self):
        self.escuta.settimeout(PRAZO)
        try:
            sock, _ = self.escuta.accept()
        except OSError:
            return
        finally:
            self.escuta.close()
        self.conn = _Conexao(sock)
        leitor = threading.Thread(target=self._ler, daemon=True)
        leitor.start()
        for acao in self.roteiro:
            if self._fechado.is_set():
                break
            self._executar(acao)
        self._fechado.wait(PRAZO)
        try:
            sock.close()
        except OSError:
            pass

    def _executar(self, acao):
        tipo = acao[0]
        if tipo == "espera":
            n = acao[2] if len(acao) > 2 else 1
            fim = time.monotonic() + PRAZO
            while len(self.de_tipo(acao[1])) < n and time.monotonic() < fim:
                if self._fechado.is_set():
                    return
                time.sleep(0.005)
        elif tipo == "manda":
            self.conn.manda(P.codificar(acao[1]))
        elif tipo == "bytes":
            self.conn.manda(acao[1])
        elif tipo == "pausa":
            time.sleep(acao[1])
        elif tipo == "silencio":
            self._calado = True
            time.sleep(acao[1])
            self._calado = False
        elif tipo == "chama":
            acao[1]()
        elif tipo == "fecha":
            try:
                self.conn.sock.close()
            except OSError:
                pass
            self._fechado.set()

    def _ler(self):
        sock = self.conn.sock
        sock.settimeout(0.02)
        while not self._fechado.is_set():
            if not self._calado and time.monotonic() - self.conn.ultimo_envio >= self.ping_a_cada:
                self.conn.manda(P.codificar(P.ping(int(time.monotonic() * 1000))))
            try:
                dados = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not dados:
                break
            for linha in self.conn.leitor.alimentar(dados):
                msg = P.decodificar(linha)
                with self._trava:
                    self.recebidas.append(msg)
                if msg["type"] == "PING" and not self._calado:
                    self.conn.manda(P.codificar(P.pong(msg["t"])))
        self._fechado.set()


class LobbyFalso(threading.Thread):
    def __init__(self, salas, host_porta: int = 1, erro_join: str | None = None, ocupados=()):
        super().__init__(daemon=True)
        self.salas = salas
        self.host_porta = host_porta
        self.erro_join = erro_join
        self.ocupados = set(ocupados)
        self.contagem: collections.Counter = collections.Counter()
        self.escuta = socket.socket()
        self.escuta.bind(("127.0.0.1", 0))
        self.escuta.listen(4)
        self.porta = self.escuta.getsockname()[1]
        self.conexoes: list[socket.socket] = []
        self.caido = False
        self.start()

    def run(self):
        while not self.caido:
            try:
                sock, _ = self.escuta.accept()
            except OSError:
                return
            self.conexoes.append(sock)
            threading.Thread(target=self._atender, args=(sock,), daemon=True).start()

    def _atender(self, sock):
        leitor = P.LineReader(sock)
        while not self.caido:
            try:
                msg = leitor.read_msg()
            except (OSError, P.ErroProtocolo):
                return
            if msg is None:
                return
            self.contagem[msg["type"]] += 1
            resp = self._responder(msg)
            try:
                P.send_msg(sock, resp)
            except OSError:
                return

    def _responder(self, msg):
        tipo = msg["type"]
        if tipo == "REGISTER":
            if msg["name"] in self.ocupados:
                return P.erro(P.NAME_TAKEN, f"o nome {msg['name']} já está em uso", "REGISTER")
            return {"type": "REGISTER_OK", "name": msg["name"]}
        if tipo == "LIST_MATCHES":
            return {"type": "MATCH_LIST", "matches": self.salas}
        if tipo == "JOIN_MATCH":
            if self.erro_join:
                return P.erro(self.erro_join, "recusado pelo lobby falso", "JOIN_MATCH")
            return {"type": "JOIN_OK", "match_id": msg["match_id"], "host": "Ana",
                    "host_ip": "127.0.0.1", "host_port": self.host_porta}
        if tipo == "PING":
            return P.pong(msg["t"])
        return P.erro(P.UNEXPECTED, f"{tipo} não esperado no lobby falso", tipo)

    def cair(self):
        self.caido = True
        for s in [self.escuta] + self.conexoes:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
