"""Testes com rede de verdade (localhost): sobe o host e clientes em subprocessos.

Mais lentos que os outros (~10 s no total). Rode:
    python3 -m unittest testes.test_integracao -v
"""

import json
import os
import socket
import subprocess
import sys
import time
import unittest

PASTA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PASTA)

import protocol as P  # noqa: E402

RAPIDO = ["--tick-ms", "20", "--start-in-ms", "100"]


def porta_livre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def subir(script, *args):
    return subprocess.Popen([sys.executable, os.path.join(PASTA, script), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=PASTA)


def esperar_porta(porta, limite=5.0):
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        try:
            socket.create_connection(("127.0.0.1", porta), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("host não abriu a porta")


class Bruto:
    """Cliente cru para mandar mensagens válidas e inválidas ao host."""

    abertos = []

    def __init__(self, porta):
        self.sock = socket.create_connection(("127.0.0.1", porta), timeout=3)
        Bruto.abertos.append(self.sock)
        self.leitor = P.LeitorLinhas()
        self.fila = []

    def manda(self, msg):
        dados = msg if isinstance(msg, bytes) else P.codificar(msg)
        self.sock.sendall(dados)

    def recebe(self, ignorar=("PING",)):
        """Próxima mensagem (pulando PING), ou None se o host fechou."""
        while True:
            while self.fila:
                m = json.loads(self.fila.pop(0))
                if m["type"] not in ignorar:
                    return m
            try:
                dados = self.sock.recv(65536)
            except ConnectionResetError:
                return None
            if not dados:
                return None
            self.fila.extend(self.leitor.alimentar(dados))

    def fechar(self):
        self.sock.close()


class TestPartidaCompleta(unittest.TestCase):
    def test_todos_veem_o_mesmo_resultado(self):
        porta = str(porta_livre())
        host = subir("host.py", "--porta", porta, "--nome", "Ana", "--iniciar-com", "4",
                     "--seed", "7", *RAPIDO)
        esperar_porta(int(porta))
        clientes = [subir("cliente_teste.py", "--porta", porta, "--nome", n, "--seed", str(i))
                    for i, n in enumerate(["Beto", "Caio", "Duda"])]
        saidas = [c.communicate(timeout=30)[0].strip() for c in clientes]
        h_out, h_err = host.communicate(timeout=30)
        self.assertEqual(host.returncode, 0, h_err)
        self.assertTrue(h_out.startswith("RESULTADO"), h_out)
        for c, s in zip(clientes, saidas):
            self.assertEqual(c.returncode, 0)
            self.assertEqual(s, h_out.strip())
        self.assertIn("END_MATCH", h_err)

    def test_queda_de_jogador_elimina_e_partida_continua(self):
        porta = str(porta_livre())
        host = subir("host.py", "--porta", porta, "--nome", "Ana", "--iniciar-com", "3",
                     "--largura", "60", "--altura", "40", *RAPIDO)
        esperar_porta(int(porta))
        beto = subir("cliente_teste.py", "--porta", porta, "--nome", "Beto", "--cair-no-ciclo", "2")
        time.sleep(0.2)  # garante que Beto entra primeiro
        caio = subir("cliente_teste.py", "--porta", porta, "--nome", "Caio", "--seed", "3")
        beto.communicate(timeout=30)
        c_out = caio.communicate(timeout=30)[0].strip()
        h_out = host.communicate(timeout=30)[0].strip()
        self.assertEqual(beto.returncode, 3)
        self.assertIn("Beto@3:disconnect", h_out)
        self.assertEqual(c_out, h_out)


class TestMensagensInvalidas(unittest.TestCase):
    def setUp(self):
        self.porta = porta_livre()
        self.host = subir("host.py", "--porta", str(self.porta), "--nome", "Ana",
                          "--iniciar-com", "4", "--entrada", "nenhuma",
                          "--largura", "80", "--altura", "60", "--tick-ms", "200",
                          "--start-in-ms", "100")
        esperar_porta(self.porta)

    def tearDown(self):
        for s in Bruto.abertos:
            s.close()
        Bruto.abertos.clear()
        self.host.terminate()
        self.host.communicate(timeout=10)

    def test_antes_do_hello(self):
        c = Bruto(self.porta)
        c.manda(b"lixo\n")
        m = c.recebe()
        self.assertEqual((m["type"], m["code"]), ("ERROR", "BAD_JSON"))
        self.assertIsNone(c.recebe())  # quem não se apresenta é desconectado

        c = Bruto(self.porta)
        c.manda(P.turn("UP", 0))
        self.assertEqual(c.recebe()["code"], "UNEXPECTED")
        self.assertIsNone(c.recebe())

    def test_rejeicoes(self):
        for hello, codigo in [({"type": "HELLO", "name": "Beto", "version": 2}, "BAD_VERSION"),
                              (P.hello("João"), "BAD_NAME"),
                              (P.hello("Ana"), "NAME_TAKEN")]:
            c = Bruto(self.porta)
            c.manda(hello)
            m = c.recebe()
            self.assertEqual((m["type"], m["code"]), ("REJECT", codigo))
            self.assertIsNone(c.recebe())

    def test_na_sala_e_no_jogo(self):
        beto = Bruto(self.porta)
        beto.manda(P.hello("Beto"))
        w = beto.recebe()
        self.assertEqual((w["type"], w["id"]), ("WELCOME", 1))
        self.assertEqual(beto.recebe()["type"], "ROSTER")

        beto.manda(b"{quebrado\n")
        self.assertEqual(beto.recebe()["code"], "BAD_JSON")
        beto.manda(P.turn("UP", 0))
        self.assertEqual(beto.recebe()["code"], "UNEXPECTED")
        beto.manda({"type": "TURN", "dir": "NE", "cycle": 0})
        self.assertEqual(beto.recebe()["code"], "BAD_FIELD")
        beto.manda({"type": "VOAR"})
        self.assertEqual(beto.recebe()["code"], "UNKNOWN_TYPE")
        beto.manda(P.ping(42))
        self.assertEqual(beto.recebe(), {"type": "PONG", "t": 42})
        beto.manda(P.erro("X", "teste"))  # ERROR nunca é respondido
        beto.manda(P.ping(43))
        self.assertEqual(beto.recebe()["t"], 43)

        # 10 erros seguidos derrubam a conexão
        caio = Bruto(self.porta)
        caio.manda(P.hello("Caio"))
        self.assertEqual(caio.recebe()["type"], "WELCOME")
        caio.manda(b"x\n" * P.MAX_ERROS)
        codigos = []
        while True:
            m = caio.recebe(ignorar=("PING", "ROSTER"))
            if m is None:
                break
            codigos.append(m["code"])
        self.assertEqual(codigos, ["BAD_JSON"] * P.MAX_ERROS)
        self.assertEqual(len(beto.recebe()["players"]), 3)  # ROSTER: Caio entrou
        self.assertEqual(beto.recebe()["players"],         # ROSTER: Caio saiu
                         [{"id": 0, "name": "Ana"}, {"id": 1, "name": "Beto"}])

        # enche a sala: começa sozinho com 4
        for n in ("Caio", "Duda"):
            x = Bruto(self.porta)
            x.manda(P.hello(n))
            self.assertEqual(x.recebe()["type"], "WELCOME")
        while beto.recebe()["type"] != "GAME_START":
            pass

        atrasado = Bruto(self.porta)
        atrasado.manda(P.hello("Edu"))
        self.assertEqual(atrasado.recebe()["code"], "MATCH_STARTED")

        beto.manda(P.turn("UP", 0))  # durante a contagem: aceito para o ciclo 1
        beto.manda(P.turn("DOWN", 999))  # ciclo que o host ainda não mandou
        self.assertEqual(beto.recebe()["code"], "BAD_FIELD")
        s = beto.recebe()
        self.assertEqual((s["type"], s["cycle"]), ("STATE", 1))
        self.assertEqual(s["bikes"][1]["dir"], "UP")


if __name__ == "__main__":
    unittest.main()
