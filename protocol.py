"""Funções compartilhadas por lobby e jogadores: envio e leitura de mensagens.

Formato: uma mensagem JSON por linha (terminada em \\n). Todo JSON tem o campo "type".
Como o TCP entrega um fluxo de bytes (e não mensagens), quem recebe acumula bytes num
buffer e só processa quando encontra uma linha completa.
"""
import json

MAX_LINE = 64 * 1024


class ProtocolError(Exception):
    """Mensagem inválida. Se fatal=True, o certo é fechar a conexão."""

    def __init__(self, motivo, fatal=False):
        super().__init__(motivo)
        self.fatal = fatal


def send_msg(sock, obj):
    sock.sendall((json.dumps(obj) + "\n").encode())


class LineReader:
    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def read_msg(self):
        """Devolve um dict, ou None se o outro lado fechou a conexão.

        Levanta ProtocolError se a linha não for um JSON válido com campo "type"
        (a conexão continua utilizável: a linha ruim já foi consumida).
        Pode levantar socket.timeout / OSError se a conexão falhar.
        """
        while b"\n" not in self.buffer:
            if len(self.buffer) > MAX_LINE:
                raise ProtocolError("linha grande demais", fatal=True)
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            msg = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            raise ProtocolError("JSON malformado")
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            raise ProtocolError("a mensagem precisa ser um objeto JSON com campo 'type'")
        return msg
