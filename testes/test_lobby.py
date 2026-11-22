"""Testes do lobby e do fluxo completo lobby -> host -> jogadores (localhost)."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import protocol as P  # noqa: E402
from test_integracao import Bruto, esperar_porta, porta_livre, subir  # noqa: E402


class Pedinte(Bruto):
    def pede(self, msg):
        self.manda(msg)
        return self.recebe(ignorar=())


class TestLobby(unittest.TestCase):
    def setUp(self):
        self.porta = porta_livre()
        self.lobby = subir("lobby.py", "--porta", str(self.porta))
        esperar_porta(self.porta)

    def tearDown(self):
        for s in Bruto.abertos:
            s.close()
        Bruto.abertos.clear()
        self.lobby.terminate()
        self.lobby.communicate(timeout=10)

    def registrado(self, nome):
        c = Pedinte(self.porta)
        self.assertEqual(c.pede({"type": "REGISTER", "name": nome, "version": 1}),
                         {"type": "REGISTER_OK", "name": nome})
        return c

    def test_registro_e_erros(self):
        c = Pedinte(self.porta)
        self.assertEqual(c.pede({"type": "LIST_MATCHES"})["code"], "UNEXPECTED")
        self.assertEqual(c.pede(P.ping(7)), {"type": "PONG", "t": 7})
        self.assertEqual(c.pede({"type": "REGISTER", "name": "Ana", "version": 2})["code"], "BAD_VERSION")
        self.assertEqual(c.pede({"type": "REGISTER", "name": "a" * 17, "version": 1})["code"], "BAD_NAME")
        self.assertEqual(c.pede({"type": "REGISTER", "name": "Ana"})["code"], "BAD_FIELD")
        c.manda(b"\n\r\n")  # linhas vazias são ignoradas, sem resposta
        self.assertEqual(c.pede(b"{lixo\n")["code"], "BAD_JSON")
        self.assertEqual(c.pede({"type": "VOAR"})["code"], "UNKNOWN_TYPE")
        self.registrado("Ana")
        self.assertEqual(c.pede({"type": "REGISTER", "name": "Ana", "version": 1})["code"], "NAME_TAKEN")

    def test_dez_erros_seguidos_fecham(self):
        c = Pedinte(self.porta)
        c.manda(b"x\n" * P.MAX_ERROS)
        for _ in range(P.MAX_ERROS):
            self.assertEqual(c.recebe(ignorar=())["code"], "BAD_JSON")
        self.assertIsNone(c.recebe())

    def test_ciclo_de_vida_da_partida(self):
        ana = self.registrado("Ana")
        beto = self.registrado("Beto")
        r = ana.pede({"type": "CREATE_MATCH", "port": 6000, "match_name": "sala", "max_players": 3,
                      "advertise_ip": "192.168.0.10"})
        self.assertEqual(r, {"type": "MATCH_CREATED", "match_id": 1, "host_ip": "192.168.0.10",
                             "host_port": 6000})
        self.assertEqual(ana.pede({"type": "JOIN_MATCH", "match_id": 1})["code"], "UNEXPECTED")
        self.assertEqual(beto.pede({"type": "JOIN_MATCH", "match_id": 9})["code"], "MATCH_NOT_FOUND")
        self.assertEqual(beto.pede({"type": "JOIN_MATCH", "match_id": 1}),
                         {"type": "JOIN_OK", "match_id": 1, "host": "Ana",
                          "host_ip": "192.168.0.10", "host_port": 6000})
        self.assertEqual(beto.pede({"type": "START_MATCH", "match_id": 1})["code"], "UNEXPECTED")
        self.assertEqual(ana.pede({"type": "START_MATCH", "match_id": 2})["code"], "NOT_HOST")

        self.assertEqual(ana.pede({"type": "MATCH_UPDATE", "match_id": 1,
                                   "players": ["Ana", "Beto", "Caio"]}), {"type": "UPDATE_OK"})
        lista = beto.pede({"type": "LIST_MATCHES"})["matches"]
        self.assertEqual(lista, [{"match_id": 1, "match_name": "sala", "host": "Ana",
                                  "players": ["Ana", "Beto", "Caio"], "max_players": 3,
                                  "status": "ABERTA"}])
        self.assertEqual(beto.pede({"type": "JOIN_MATCH", "match_id": 1})["code"], "MATCH_FULL")

        self.assertEqual(ana.pede({"type": "START_MATCH", "match_id": 1}),
                         {"type": "START_OK", "match_id": 1})
        self.assertEqual(beto.pede({"type": "JOIN_MATCH", "match_id": 1})["code"], "MATCH_STARTED")
        self.assertEqual(ana.pede({"type": "CANCEL_MATCH", "match_id": 1})["code"], "UNEXPECTED")
        self.assertEqual(ana.pede({"type": "END_MATCH", "match_id": 1, "winner": "Beto"}),
                         {"type": "END_OK", "match_id": 1})
        self.assertEqual(beto.pede({"type": "LIST_MATCHES"})["matches"], [])

        # de volta a REGISTRADO: pode criar outra; se o host cair, a partida some da lista
        self.assertEqual(ana.pede({"type": "CREATE_MATCH", "port": 6001})["match_id"], 2)
        self.assertEqual(len(beto.pede({"type": "LIST_MATCHES"})["matches"]), 1)
        ana.fechar()
        time.sleep(0.2)
        self.assertEqual(beto.pede({"type": "LIST_MATCHES"})["matches"], [])

    def test_host_local_recebe_ip_de_quem_pergunta(self):
        ana = self.registrado("Ana")
        ana.pede({"type": "CREATE_MATCH", "port": 6000})
        beto = self.registrado("Beto")
        self.assertEqual(beto.pede({"type": "JOIN_MATCH", "match_id": 1})["host_ip"], "127.0.0.1")


class TestFluxoCompleto(unittest.TestCase):
    def setUp(self):
        self.porta_lobby = porta_livre()
        self.lobby = subir("lobby.py", "--porta", str(self.porta_lobby))
        esperar_porta(self.porta_lobby)
        self.processos = [self.lobby]

    def tearDown(self):
        for p in self.processos:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=10)

    def jogadores_na_sala(self, olheiro):
        salas = olheiro.pede({"type": "LIST_MATCHES"})["matches"]
        return salas[0]["players"] if salas else []

    def esperar_sala(self, olheiro, n):
        fim = time.monotonic() + 10
        while len(self.jogadores_na_sala(olheiro)) < n:
            self.assertLess(time.monotonic(), fim, "jogadores não chegaram à sala")
            time.sleep(0.1)

    def test_inicio_manual_e_queda_do_lobby(self):
        host = subir_com_stdin("host.py", "--porta", str(porta_livre()), "--nome", "Ana",
                               "--lobby-ip", "127.0.0.1", "--lobby-porta", str(self.porta_lobby),
                               "--tick-ms", "50", "--start-in-ms", "100",
                               "--largura", "80", "--altura", "60", "--seed", "5")
        self.processos.append(host)
        olheiro = Pedinte(self.porta_lobby)
        olheiro.pede({"type": "REGISTER", "name": "olheiro", "version": 1})
        self.esperar_sala(olheiro, 1)

        clientes = []
        for i, n in enumerate(["Beto", "Caio"]):
            c = subir("cliente_teste.py", "--lobby-ip", "127.0.0.1", "--lobby-porta",
                      str(self.porta_lobby), "--nome", n, "--seed", str(i))
            clientes.append(c)
            self.processos.append(c)
            if i == 0:
                self.esperar_sala(olheiro, 2)
                host.stdin.write("iniciar\n")  # só 2: o host recusa e continua esperando
                host.stdin.flush()
        self.esperar_sala(olheiro, 3)
        host.stdin.write("iniciar\n")
        host.stdin.flush()
        time.sleep(0.5)
        self.lobby.kill()  # lobby cai com a partida em andamento

        saidas = [c.communicate(timeout=60)[0].strip() for c in clientes]
        h_out, h_err = host.communicate(timeout=60)
        self.assertIn("Ainda não dá", h_out)
        resultado = [l for l in h_out.splitlines() if l.startswith("RESULTADO")]
        self.assertEqual(len(resultado), 1, h_out)
        self.assertEqual(saidas, [resultado[0]] * 2)
        self.assertIn("conexão com o lobby perdida", h_err)
        self.assertEqual(host.returncode, 0)

    def test_cancelar_sala(self):
        host = subir_com_stdin("host.py", "--porta", str(porta_livre()), "--nome", "Ana",
                               "--lobby-ip", "127.0.0.1", "--lobby-porta", str(self.porta_lobby))
        self.processos.append(host)
        olheiro = Pedinte(self.porta_lobby)
        olheiro.pede({"type": "REGISTER", "name": "olheiro", "version": 1})
        self.esperar_sala(olheiro, 1)
        beto = subir("cliente_teste.py", "--lobby-ip", "127.0.0.1", "--lobby-porta",
                     str(self.porta_lobby), "--nome", "Beto")
        self.processos.append(beto)
        self.esperar_sala(olheiro, 2)
        host.stdin.write("cancelar\n")
        host.stdin.flush()
        b_out = beto.communicate(timeout=20)[0]
        host.communicate(timeout=20)
        self.assertIn("ABORTADA host encerrou: cancelled", b_out)
        self.assertEqual(olheiro.pede({"type": "LIST_MATCHES"})["matches"], [])

    def test_host_sem_lobby_encerra(self):
        host = subir("host.py", "--porta", str(porta_livre()), "--nome", "Ana",
                     "--lobby-ip", "127.0.0.1", "--lobby-porta", str(porta_livre()))
        out, _ = host.communicate(timeout=20)
        self.assertEqual(host.returncode, 1)
        self.assertIn("ERRO lobby", out)


def subir_com_stdin(script, *args):
    import subprocess
    pasta = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.Popen([sys.executable, os.path.join(pasta, script), *args],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=pasta)


if __name__ == "__main__":
    unittest.main()
