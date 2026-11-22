"""Módulo compartilhado do protocolo (lobby, host e jogador).

Segue a especificação em protocolo/protocolo.md:
  - framing: um objeto JSON UTF-8 por linha, terminado em "\\n" (seção 2);
  - validação de campos por tipo de mensagem (seção 3);
  - tabelas de mensagens permitidas por estado (apêndice A);
  - constantes de tempo e limites (apêndice B).

Só usa a biblioteca padrão. Não abre sockets: quem usa este módulo cuida da
rede e chama `LeitorLinhas.alimentar()` com os bytes recebidos.
"""

from __future__ import annotations

import json
import re

VERSAO = 1

# ---------------------------------------------------------------------------
# Constantes (apêndice B)
# ---------------------------------------------------------------------------
MAX_LINHA = 16 * 1024  # bytes
PING_INTERVALO = 1.0   # s sem enviar nada -> manda PING
TIMEOUT_VIDA = 3.0     # s sem receber nada -> conexão morta
TIMEOUT_HELLO = 5.0    # s para o HELLO / WELCOME
TIMEOUT_LOBBY = 5.0    # s por resposta do lobby
ESPERA_START = 2.0     # s máx. esperando START_OK
MAX_ERROS = 10         # erros seguidos antes de desconectar

MIN_JOGADORES = 3
MAX_JOGADORES = 4

DIRECOES = ("UP", "DOWN", "LEFT", "RIGHT")
MOTIVOS_ELIMINACAO = ("wall", "trail", "head_on", "disconnect", "timeout", "quit")
MOTIVOS_ABORTO = ("host_quit", "cancelled", "internal_error")

# Códigos de ERROR (seção 3.7) e de REJECT (seção 3.2)
BAD_JSON = "BAD_JSON"
UNKNOWN_TYPE = "UNKNOWN_TYPE"
BAD_FIELD = "BAD_FIELD"
UNEXPECTED = "UNEXPECTED"
BAD_VERSION = "BAD_VERSION"
BAD_NAME = "BAD_NAME"
NAME_TAKEN = "NAME_TAKEN"
MATCH_NOT_FOUND = "MATCH_NOT_FOUND"
MATCH_FULL = "MATCH_FULL"
MATCH_STARTED = "MATCH_STARTED"
NOT_HOST = "NOT_HOST"

_NOME_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


def nome_valido(nome) -> bool:
    """Nome de jogador: 1-16 caracteres de [A-Za-z0-9_-] (seção 2.1)."""
    return isinstance(nome, str) and bool(_NOME_RE.match(nome))


# ---------------------------------------------------------------------------
# Tabelas de estados (apêndice A)
# ---------------------------------------------------------------------------
LOBBY_PERMITIDAS = {
    "NAO_REGISTRADO":      {"REGISTER", "PING"},
    "REGISTRADO":          {"LIST_MATCHES", "CREATE_MATCH", "JOIN_MATCH", "PING"},
    "HOSPEDANDO_ABERTA":   {"LIST_MATCHES", "MATCH_UPDATE", "START_MATCH", "CANCEL_MATCH", "PING"},
    "HOSPEDANDO_INICIADA": {"LIST_MATCHES", "MATCH_UPDATE", "END_MATCH", "PING"},
}

HOST_PERMITIDAS = {   # mensagens que o host aceita de cada conexão de jogador
    "AGUARDANDO_HELLO": {"HELLO", "PING"},
    "NA_SALA":          {"BYE", "TURN", "PING", "PONG", "ERROR"},
    "JOGANDO":          {"TURN", "BYE", "PING", "PONG", "ERROR"},
    "ELIMINADO":        {"TURN", "BYE", "PING", "PONG", "ERROR"},  # TURN ignorado
}

JOGADOR_PERMITIDAS = {  # mensagens que o jogador aceita do host
    "CONECTANDO_HOST":   {"WELCOME", "REJECT", "PING", "ERROR"},
    "AGUARDANDO_INICIO": {"ROSTER", "GAME_START", "GAME_ABORTED", "PING", "PONG", "ERROR"},
    "CONTAGEM":          {"STATE", "GAME_ABORTED", "PING", "PONG", "ERROR"},
    "JOGANDO":           {"STATE", "GAME_OVER", "GAME_ABORTED", "PING", "PONG", "ERROR"},
    "ESPECTADOR":        {"STATE", "GAME_OVER", "GAME_ABORTED", "PING", "PONG", "ERROR"},
}


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------
class ErroProtocolo(Exception):
    """Mensagem recebida inválida. `codigo` é um código de ERROR da seção 3.7.

    `ref` é o `type` da mensagem que causou o erro, se deu para ler (senão None).
    `fatal` = True quando a conexão deve ser fechada (só LinhaMuitoLonga).
    """
    fatal = False

    def __init__(self, codigo: str, mensagem: str, ref: str | None = None):
        super().__init__(f"{codigo}: {mensagem}")
        self.codigo = codigo
        self.mensagem = mensagem
        self.ref = ref

    def como_mensagem(self) -> dict:
        """Monta o ERROR que deve ser enviado de volta ao emissor."""
        return erro(self.codigo, self.mensagem, self.ref)


class LinhaMuitoLonga(ErroProtocolo):
    """Buffer passou de MAX_LINHA sem "\\n": a conexão deve ser fechada."""
    fatal = True

    def __init__(self, mensagem: str):
        super().__init__(BAD_JSON, mensagem)


ProtocolError = ErroProtocolo  # nome usado no lobby.py


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------
def codificar(msg: dict) -> bytes:
    """Serializa uma mensagem para envio: JSON numa linha + "\\n"."""
    texto = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    dados = texto.encode("utf-8") + b"\n"
    if len(dados) > MAX_LINHA:
        raise ValueError(f"mensagem {msg.get('type')} com {len(dados)} bytes excede {MAX_LINHA}")
    return dados


class LeitorLinhas:
    """Acumula bytes de uma conexão e devolve só as linhas completas.

    Um recv() pode trazer meia mensagem ou várias; este objeto resolve isso
    (regras 1-3 da seção 2).
    """

    def __init__(self, max_linha: int = MAX_LINHA):
        self._buf = bytearray()
        self._max = max_linha

    def alimentar(self, dados: bytes) -> list[bytes]:
        self._buf.extend(dados)
        linhas = []
        while True:
            pos = self._buf.find(b"\n")
            if pos < 0:
                break
            linha = bytes(self._buf[:pos])
            del self._buf[:pos + 1]
            if len(linha) > self._max:
                raise LinhaMuitoLonga(f"linha com {len(linha)} bytes")
            linha = linha.strip(b" \t\r")
            if linha:
                linhas.append(linha)
        if len(self._buf) > self._max:
            raise LinhaMuitoLonga(f"{len(self._buf)} bytes sem \\n")
        return linhas


def decodificar(linha: bytes) -> dict:
    """Converte uma linha em mensagem já validada.

    Levanta ErroProtocolo com BAD_JSON, UNKNOWN_TYPE ou BAD_FIELD.
    """
    try:
        msg = json.loads(linha.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ErroProtocolo(BAD_JSON, "linha não é JSON válido em UTF-8")
    if not isinstance(msg, dict):
        raise ErroProtocolo(BAD_JSON, "mensagem não é um objeto JSON")
    tipo = msg.get("type")
    if not isinstance(tipo, str) or tipo not in ESQUEMAS:
        raise ErroProtocolo(UNKNOWN_TYPE, f"tipo desconhecido: {tipo!r}",
                            tipo if isinstance(tipo, str) else None)
    validar(msg)
    return msg


# ---------------------------------------------------------------------------
# Validação de campos
# ---------------------------------------------------------------------------
# Cada validador recebe o valor e devolve None se ok, ou uma descrição do erro.

def _int(minimo=None, maximo=None):
    def v(x):
        # bool é subclasse de int em Python; true/false não valem como número
        if not isinstance(x, int) or isinstance(x, bool):
            return "deve ser inteiro"
        if minimo is not None and x < minimo:
            return f"deve ser >= {minimo}"
        if maximo is not None and x > maximo:
            return f"deve ser <= {maximo}"
        return None
    return v


def _str(max_len=None):
    def v(x):
        if not isinstance(x, str):
            return "deve ser string"
        if max_len is not None and len(x) > max_len:
            return f"deve ter no máximo {max_len} caracteres"
        return None
    return v


def _enum(*opcoes):
    def v(x):
        return None if x in opcoes else f"deve ser um de {list(opcoes)}"
    return v


def _lista(item=None):
    def v(x):
        if not isinstance(x, list):
            return "deve ser lista"
        if item is not None:
            for i, e in enumerate(x):
                r = item(e)
                if r:
                    return f"[{i}] {r}"
        return None
    return v


def _obj(campos: dict):
    """Objeto com campos obrigatórios/opcionais (mesma notação dos esquemas)."""
    def v(x):
        if not isinstance(x, dict):
            return "deve ser objeto"
        return _checar_campos(x, campos)
    return v


def _ou_nulo(inner):
    def v(x):
        return None if x is None else inner(x)
    return v


_ID = _int(0, MAX_JOGADORES - 1)
_MATCH_ID = _int(1)
_PORTA = _int(1, 65535)
_NOME = _str(64)  # formato do nome é checado pela aplicação (BAD_NAME, não BAD_FIELD)
_DIR = _enum(*DIRECOES)
_CICLO = _int(0)

_JOGADOR = _obj({"id": _ID, "name": _NOME})
_JOGADOR_INICIAL = _obj({"id": _ID, "name": _NOME, "x": _int(), "y": _int(), "dir": _DIR})
_MOTO = _obj({"id": _ID, "x": _int(), "y": _int(), "dir": _DIR,
              "alive": lambda x: None if isinstance(x, bool) else "deve ser booleano"})
_EVENTO = _obj({"event": _str(), "id": _ID, "reason": _enum(*MOTIVOS_ELIMINACAO)})
_ELIMINACAO = _obj({"id": _ID, "name": _NOME, "cycle": _CICLO,
                    "reason": _enum(*MOTIVOS_ELIMINACAO)})
_PARTIDA = _obj({"match_id": _MATCH_ID, "match_name": _str(), "host": _NOME,
                 "players": _lista(_NOME), "max_players": _int(MIN_JOGADORES, MAX_JOGADORES),
                 "status": _enum("ABERTA", "INICIADA")})

# Campo terminado em "?" é opcional. Campos desconhecidos são ignorados.
ESQUEMAS: dict[str, dict] = {
    # --- lobby (3.1)
    "REGISTER":      {"name": _NOME, "version": _int()},
    "REGISTER_OK":   {"name": _NOME},
    "LIST_MATCHES":  {},
    "MATCH_LIST":    {"matches": _lista(_PARTIDA)},
    "CREATE_MATCH":  {"port": _PORTA, "match_name?": _str(64),
                      "max_players?": _int(MIN_JOGADORES, MAX_JOGADORES), "advertise_ip?": _str(64)},
    "MATCH_CREATED": {"match_id": _MATCH_ID, "host_ip": _str(64), "host_port": _PORTA},
    "JOIN_MATCH":    {"match_id": _MATCH_ID},
    "JOIN_OK":       {"match_id": _MATCH_ID, "host": _NOME, "host_ip": _str(64), "host_port": _PORTA},
    "MATCH_UPDATE":  {"match_id": _MATCH_ID, "players": _lista(_NOME)},
    "UPDATE_OK":     {},
    "START_MATCH":   {"match_id": _MATCH_ID},
    "START_OK":      {"match_id": _MATCH_ID},
    "CANCEL_MATCH":  {"match_id": _MATCH_ID},
    "CANCEL_OK":     {"match_id": _MATCH_ID},
    "END_MATCH":     {"match_id": _MATCH_ID, "winner?": _ou_nulo(_NOME)},
    "END_OK":        {"match_id": _MATCH_ID},
    # --- heartbeat (3.5), vale para lobby e partida
    "PING":          {"t": _int()},
    "PONG":          {"t": _int()},
    # --- entrada na partida (3.2)
    "HELLO":         {"name": _NOME, "version": _int()},
    "WELCOME":       {"id": _ID, "match_id": _MATCH_ID, "players": _lista(_JOGADOR),
                      "min_players": _int(1), "max_players": _int(1)},
    "REJECT":        {"code": _str(), "message": _str()},
    "ROSTER":        {"players": _lista(_JOGADOR)},
    "BYE":           {"reason?": _str(256)},
    # --- início e jogo (3.3, 3.4)
    "GAME_START":    {"arena": _obj({"width": _int(1), "height": _int(1)}),
                      "tick_ms": _int(1), "start_in_ms": _int(0),
                      "players": _lista(_JOGADOR_INICIAL)},
    "TURN":          {"dir": _DIR, "cycle": _CICLO},
    "STATE":         {"cycle": _CICLO, "bikes": _lista(_MOTO), "grid": _lista(_str()),
                      "events": _lista(_EVENTO)},
    # --- fim (3.6)
    "GAME_OVER":     {"cycle": _CICLO, "result": _enum("WIN", "DRAW"),
                      "winner": _ou_nulo(_JOGADOR), "eliminations": _lista(_ELIMINACAO)},
    "GAME_ABORTED":  {"reason": _str(), "cycle": _ou_nulo(_CICLO)},
    # --- erro (3.7)
    "ERROR":         {"code": _str(), "message?": _str(), "ref?": _ou_nulo(_str())},
}


def _checar_campos(msg: dict, campos: dict) -> str | None:
    for chave, validador in campos.items():
        opcional = chave.endswith("?")
        nome = chave.rstrip("?")
        if nome not in msg:
            if opcional:
                continue
            return f"campo '{nome}' ausente"
        r = validador(msg[nome])
        if r:
            return f"campo '{nome}' {r}"
    return None


def validar(msg: dict) -> None:
    """Confere os campos de uma mensagem com `type` conhecido.

    Levanta ErroProtocolo(BAD_FIELD) se faltar campo ou o tipo/valor for inválido.
    """
    tipo = msg["type"]
    problema = _checar_campos(msg, ESQUEMAS[tipo])
    if problema:
        raise ErroProtocolo(BAD_FIELD, problema, tipo)


# ---------------------------------------------------------------------------
# Atalhos para sockets bloqueantes (uma thread por conexão, como no lobby.py)
# ---------------------------------------------------------------------------
def send_msg(sock, msg: dict) -> None:
    """Envia uma mensagem inteira (sendall)."""
    sock.sendall(codificar(msg))


class LineReader:
    """Leitura bloqueante de mensagens de um socket.

    read_msg() devolve a próxima mensagem já validada, ou None se o outro lado
    fechou. Levanta ErroProtocolo (a linha ruim já foi consumida e a conexão
    continua; responda com e.como_mensagem()), LinhaMuitoLonga (e.fatal: feche)
    e socket.timeout / OSError se a conexão falhar.
    """

    def __init__(self, sock, max_linha: int = MAX_LINHA):
        self.sock = sock
        self._leitor = LeitorLinhas(max_linha)
        self._linhas: list[bytes] = []

    def read_msg(self) -> dict | None:
        while not self._linhas:
            dados = self.sock.recv(65536)
            if not dados:
                return None
            self._linhas.extend(self._leitor.alimentar(dados))
        return decodificar(self._linhas.pop(0))


# ---------------------------------------------------------------------------
# Construtores das mensagens mais usadas (evitam erro de digitação nos campos)
# ---------------------------------------------------------------------------
def erro(codigo: str, mensagem: str, ref: str | None = None) -> dict:
    return {"type": "ERROR", "code": codigo, "message": mensagem, "ref": ref}


def ping(t_ms: int) -> dict:
    return {"type": "PING", "t": t_ms}


def pong(t_ms: int) -> dict:
    return {"type": "PONG", "t": t_ms}


def hello(nome: str) -> dict:
    return {"type": "HELLO", "name": nome, "version": VERSAO}


def turn(direcao: str, ciclo: int) -> dict:
    return {"type": "TURN", "dir": direcao, "cycle": ciclo}


def bye(motivo: str | None = None) -> dict:
    m = {"type": "BYE"}
    if motivo is not None:
        m["reason"] = motivo
    return m


def reject(codigo: str, mensagem: str) -> dict:
    return {"type": "REJECT", "code": codigo, "message": mensagem}
