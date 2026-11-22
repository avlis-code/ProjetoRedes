"""Ligação do host com o lobby.

O host só fala com o lobby através de um objeto com a interface `LigacaoLobby`.
Hoje existe só o `LobbyStub`, que não abre conexão nenhuma e apenas registra
no log o que seria enviado. Quando o lobby real existir, basta escrever uma
classe com os mesmos métodos (ex.: `LobbyCliente`, que faz REGISTER,
CREATE_MATCH etc. pela seção 3.1) e trocar a linha em host.py que cria o stub.

Regras que a implementação real precisa respeitar (seções 3.1 e 5.1):
  - nenhum método pode derrubar o host: erro de rede vira log e o método volta;
  - `iniciar_partida` espera START_OK por no máximo ESPERA_START (2 s) e o
    host inicia a partida de qualquer jeito;
  - durante a partida o lobby pode sumir; os métodos continuam sendo chamados
    e devem só registrar que o lobby está fora;
  - `atualizar_jogadores` e `encerrar_partida` não devem travar o loop do host
    por muito tempo (o ideal é uma thread com fila, decisão D9).
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time

import protocol as P

log = logging.getLogger("lobby")

PING_LOBBY = 10.0  # s sem falar com o lobby -> PING (o lobby derruba após 60 s calado)


class ErroLobby(Exception):
    """Lobby recusou o pedido (`codigo` = code do ERROR) ou está inacessível (codigo None)."""

    def __init__(self, mensagem: str, codigo: str | None = None):
        super().__init__(mensagem)
        self.codigo = codigo


class ConexaoLobby:
    """Conexão síncrona com o lobby: um pedido, uma resposta (seção 3.1).

    Usada pelo LobbyCliente (host) e pelo jogador. Não é thread-safe: só uma
    thread deve chamar pedir() de cada vez.
    """

    def __init__(self, ip: str, porta: int, timeout: float = P.TIMEOUT_LOBBY):
        try:
            self.sock = socket.create_connection((ip, porta), timeout=timeout)
        except OSError as e:
            raise ErroLobby(f"não conectou no lobby {ip}:{porta} ({e})")
        self.sock.settimeout(timeout)
        self.leitor = P.LineReader(self.sock)
        self.ultimo_envio = time.monotonic()

    def pedir(self, msg: dict, esperado: str) -> dict:
        """Envia `msg` e devolve a resposta do tipo `esperado`.

        ERROR vira ErroLobby(codigo=...); queda ou 5 s sem resposta viram
        ErroLobby(codigo=None).
        """
        try:
            P.send_msg(self.sock, msg)
            self.ultimo_envio = time.monotonic()
            resp = self.leitor.read_msg()
        except P.ProtocolError as e:
            raise ErroLobby(f"resposta inválida do lobby ({e})")
        except (OSError, socket.timeout) as e:
            raise ErroLobby(f"lobby não respondeu a {msg['type']} ({e})")
        if resp is None:
            raise ErroLobby("lobby fechou a conexão")
        if resp["type"] == "ERROR":
            raise ErroLobby(f"{resp['code']}: {resp.get('message')}", resp["code"])
        if resp["type"] != esperado:
            raise ErroLobby(f"esperava {esperado}, veio {resp['type']}")
        return resp

    def registrar(self, nome: str) -> None:
        self.pedir({"type": "REGISTER", "name": nome, "version": P.VERSAO}, "REGISTER_OK")

    def ping(self) -> None:
        self.pedir(P.ping(int(time.monotonic() * 1000)), "PONG")

    def fechar(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class LigacaoLobby:
    """Interface. Os métodos devolvem rápido e não levantam exceção,
    exceto criar_partida, que levanta ErroLobby se não houver lobby."""

    def criar_partida(self, porta: int, max_jogadores: int, nome_sala: str | None) -> int:
        """CREATE_MATCH. Chamado depois que o host já está escutando. Devolve match_id."""
        raise NotImplementedError

    def atualizar_jogadores(self, nomes: list[str]) -> None:
        """MATCH_UPDATE com a lista real (host primeiro, em ordem de id)."""
        raise NotImplementedError

    def iniciar_partida(self) -> bool:
        """START_MATCH; True se chegou START_OK em até 2 s."""
        raise NotImplementedError

    def cancelar_partida(self) -> None:
        """CANCEL_MATCH (host desistiu antes de iniciar)."""
        raise NotImplementedError

    def encerrar_partida(self, vencedor: str | None) -> None:
        """END_MATCH depois do GAME_OVER (vencedor None = empate ou abortada)."""
        raise NotImplementedError

    def fechar(self) -> None:
        """Fecha a conexão com o lobby, se houver."""


class LobbyCliente(LigacaoLobby):
    """Ligação real com o lobby, usada pelo host.

    `criar_partida` é síncrono: sem lobby não há partida (seção 5.1, INICIANDO ->
    encerra), então ele levanta ErroLobby. Depois disso, todos os pedidos vão
    para uma fila atendida por uma thread própria (decisão D9). Assim o loop do
    host nunca espera o lobby, a não ser no START_MATCH (no máx. 2 s). Se o
    lobby cair, a thread registra no log uma vez e descarta os próximos pedidos.
    """

    def __init__(self, ip: str, porta: int, nome: str, advertise_ip: str | None = None):
        self.ip, self.porta, self.nome = ip, porta, nome
        self.advertise_ip = advertise_ip
        self.match_id: int | None = None
        self.conexao: ConexaoLobby | None = None
        self.vivo = False
        self.fila: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

    # ------------------------------------------------------------------ síncrono
    def criar_partida(self, porta, max_jogadores, nome_sala):
        self.conexao = ConexaoLobby(self.ip, self.porta)
        self.conexao.registrar(self.nome)
        pedido = {"type": "CREATE_MATCH", "port": porta, "max_players": max_jogadores}
        if nome_sala:
            pedido["match_name"] = nome_sala
        if self.advertise_ip:
            pedido["advertise_ip"] = self.advertise_ip
        resp = self.conexao.pedir(pedido, "MATCH_CREATED")
        self.match_id = resp["match_id"]
        self.vivo = True
        log.info("partida %d criada no lobby %s:%d; o lobby nos vê como %s:%d",
                 self.match_id, self.ip, self.porta, resp["host_ip"], resp["host_port"])
        self.thread = threading.Thread(target=self._trabalhar, name="lobby", daemon=True)
        self.thread.start()
        return self.match_id

    # ------------------------------------------------------------------ assíncrono
    def atualizar_jogadores(self, nomes):
        self._pedir({"type": "MATCH_UPDATE", "match_id": self.match_id, "players": list(nomes)},
                    "UPDATE_OK")

    def iniciar_partida(self):
        feito = self._pedir({"type": "START_MATCH", "match_id": self.match_id}, "START_OK")
        return feito is not None and feito.wait(P.ESPERA_START) and feito.ok

    def cancelar_partida(self):
        self._pedir({"type": "CANCEL_MATCH", "match_id": self.match_id}, "CANCEL_OK")

    def encerrar_partida(self, vencedor):
        self._pedir({"type": "END_MATCH", "match_id": self.match_id, "winner": vencedor}, "END_OK")

    def fechar(self):
        """Espera a fila esvaziar (END_MATCH/CANCEL_MATCH saírem) por até 5 s e fecha."""
        if self.thread is not None:
            self.fila.put(None)
            self.thread.join(P.TIMEOUT_LOBBY)
        if self.conexao is not None:
            self.conexao.fechar()

    def _pedir(self, msg, esperado):
        if not self.vivo:
            log.info("lobby fora do ar; %s não enviado", msg["type"])
            return None
        feito = threading.Event()
        feito.ok = False
        self.fila.put((msg, esperado, feito))
        return feito

    def _trabalhar(self):
        while True:
            try:
                item = self.fila.get(timeout=PING_LOBBY)
            except queue.Empty:
                item = "PING"
            if item is None:
                return
            try:
                if item == "PING":
                    self.conexao.ping()
                    continue
                msg, esperado, feito = item
                self.conexao.pedir(msg, esperado)
                feito.ok = True
                feito.set()
                log.debug("lobby confirmou %s", msg["type"])
            except ErroLobby as e:
                if e.codigo is not None:  # o lobby respondeu ERROR: só registra
                    log.warning("lobby recusou %s: %s", item[0]["type"] if item != "PING" else "PING", e)
                    if item != "PING":
                        item[2].set()
                    continue
                log.warning("conexão com o lobby perdida (%s); a partida continua sem ele", e)
                self.vivo = False
                if item != "PING":
                    item[2].set()
                self.conexao.fechar()
                return


class LobbyStub(LigacaoLobby):
    """Faz de conta que o lobby existe. O match_id vem da linha de comando."""

    def __init__(self, match_id: int = 1):
        self.match_id = match_id
        self.chamadas: list[tuple] = []  # útil para testes

    def _reg(self, *chamada):
        self.chamadas.append(chamada)
        log.info("[stub do lobby] %s", " ".join(str(c) for c in chamada))

    def criar_partida(self, porta, max_jogadores, nome_sala):
        self._reg("CREATE_MATCH", f"port={porta}", f"max_players={max_jogadores}",
                  f"match_name={nome_sala!r}", f"-> match_id={self.match_id}")
        return self.match_id

    def atualizar_jogadores(self, nomes):
        self._reg("MATCH_UPDATE", nomes)

    def iniciar_partida(self):
        self._reg("START_MATCH", f"match_id={self.match_id}")
        return True

    def cancelar_partida(self):
        self._reg("CANCEL_MATCH", f"match_id={self.match_id}")

    def encerrar_partida(self, vencedor):
        self._reg("END_MATCH", f"match_id={self.match_id}", f"winner={vencedor}")
