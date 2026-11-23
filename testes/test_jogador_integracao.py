import logging
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jogador
from falsos import TecladoFalso, TerminalFalso
from jogador import Jogador, Tempos
from test_integracao import esperar_porta, porta_livre, subir
from test_lobby import Pedinte

logging.getLogger().addHandler(logging.NullHandler())

RAPIDO = ["--tick-ms", "20", "--start-in-ms", "100"]


def tem_sala_aberta(j):
    return j.estado == "NO_LOBBY" and any(s["status"] == "ABERTA" for s in j.salas)


class TestJogadorDeVerdade(unittest.TestCase):
    def setUp(self):
        self.porta_lobby = porta_livre()
        self.lobby = subir("lobby.py", "--porta", str(self.porta_lobby))
        esperar_porta(self.porta_lobby)
        self.processos = [self.lobby]
        self.host = subir("host.py", "--porta", str(porta_livre()), "--nome", "Ana",
                          "--lobby-ip", "127.0.0.1", "--lobby-porta", str(self.porta_lobby),
                          "--entrada", "bot", "--iniciar-com", "3", "--seed", "7", *RAPIDO)
        self.processos.append(self.host)
        self._esperar_sala()
        self.cliente = subir("cliente_teste.py", "--lobby-ip", "127.0.0.1", "--lobby-porta",
                             str(self.porta_lobby), "--nome", "Caio", "--seed", "3")
        self.processos.append(self.cliente)

    def tearDown(self):
        for p in self.processos:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=10)

    def _esperar_sala(self):
        olheiro = Pedinte(self.porta_lobby)
        olheiro.pede({"type": "REGISTER", "name": "olheiro", "version": 1})
        fim = time.monotonic() + 10
        while not olheiro.pede({"type": "LIST_MATCHES"})["matches"]:
            self.assertLess(time.monotonic(), fim, "o host não criou a sala")
            time.sleep(0.05)
        olheiro.fechar()

    def _jogador(self, passos, tempos):
        args = jogador.ler_argumentos(["--nome", "Beto", "--lobby-ip", "127.0.0.1",
                                       "--lobby-porta", str(self.porta_lobby)])
        teclado = TecladoFalso(passos, prazo=30)
        terminal = TerminalFalso((100, 30))
        j = Jogador(args, teclado, terminal, tempos=tempos, dizer=lambda *a, **k: None)
        teclado.jogador = j
        return j, terminal

    def _resultado_do_host(self):
        h_out, h_err = self.host.communicate(timeout=60)
        self.assertEqual(self.host.returncode, 0, h_err)
        linhas = [l for l in h_out.splitlines() if l.startswith("RESULTADO")]
        self.assertEqual(len(linhas), 1, h_out)
        return linhas[0], h_err

    def test_resultado_igual_ao_do_host(self):
        j, terminal = self._jogador([(tem_sala_aberta, ["ENTER"]),
                                     (lambda j: j.estado == "FIM", ["q"])],
                                    Tempos(lista_salas=0.2, aviso=0.05))
        codigo = j.executar()
        resultado_host, _ = self._resultado_do_host()
        c_out = self.cliente.communicate(timeout=60)[0].strip()

        self.assertEqual(codigo, 0, j.linhas_saida)
        self.assertEqual(j.linhas_saida, [resultado_host])
        self.assertEqual(c_out, resultado_host)
        tudo = terminal.todos()
        self.assertIn("TRON em rede", tudo)
        self.assertIn("Aguardando o host iniciar", tudo)
        self.assertIn("FIM DE PARTIDA", terminal.texto())

    def test_lobby_cai_durante_a_partida(self):
        def derrubar_lobby(j):
            if j.estado in ("CONTAGEM", "JOGANDO"):
                self.lobby.kill()
                return True
            return False

        j, terminal = self._jogador([(tem_sala_aberta, ["ENTER"]),
                                     (derrubar_lobby, []),
                                     (lambda j: j.estado == "FIM", ["q"])],
                                    Tempos(lista_salas=0.2, ping_lobby=0.1, aviso=0.05))
        codigo = j.executar()
        resultado_host, h_err = self._resultado_do_host()

        self.assertEqual(codigo, 0, j.linhas_saida)
        self.assertEqual(j.linhas_saida, [resultado_host])
        quadros_jogo = [terminal.texto(i) for i in range(len(terminal.quadros))
                        if "TRON  Beto" in terminal.texto(i)]
        self.assertTrue(any("lobby: fora" in q for q in quadros_jogo),
                        "a barra do jogo não mostrou lobby: fora")
        self.assertIn("conexão com o lobby perdida", h_err)


if __name__ == "__main__":
    unittest.main()
