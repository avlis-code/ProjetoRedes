import logging
import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from entrada import ErroSemTerminal
from falsos import TecladoFalso, TerminalFalso
from host_tela import EntradaTecladoTela
from test_integracao import PASTA, esperar_porta, porta_livre
from tron import Partida
from ui import Opcoes

logging.getLogger().addHandler(logging.NullHandler())

GAME_OVER = {"type": "GAME_OVER", "cycle": 9, "result": "WIN", "winner": {"id": 0, "name": "Ana"},
             "eliminations": [{"id": 1, "name": "Beto", "cycle": 4, "reason": "wall"},
                              {"id": 2, "name": "Caio", "cycle": 9, "reason": "trail"}]}


def host_falso():
    p = Partida(10, 10, [(0, "Ana"), (1, "Beto"), (2, "Caio")])
    gs = {"type": "GAME_START", "arena": {"width": 10, "height": 10}, "tick_ms": 100,
          "start_in_ms": 3000, "players": p.jogadores_inicio_json()}
    return SimpleNamespace(partida=p, fase="CONTAGEM", game_start=gs,
                           inicio_contagem=time.monotonic(), ultimo_state=None,
                           lobby=SimpleNamespace(vivo=True), game_over=None, abortada=False)


def entrada(passos, tamanho=(100, 30)):
    teclado = TecladoFalso(passos)
    terminal = TerminalFalso(tamanho)
    return EntradaTecladoTela(Opcoes(), teclado=teclado, terminal=terminal), teclado, terminal


def avancar(h):
    r = h.partida.avancar()
    h.ultimo_state = h.partida.estado_json(r.eventos)
    h.fase = "EM_JOGO"


class TestTeclado(unittest.TestCase):
    def test_setas_e_wasd_vao_para_a_fila_da_moto_do_host(self):
        h = host_falso()
        e, _, _ = entrada([["UP", "d"]])
        e.iniciar_jogo(h)
        self.assertIsNone(e.direcao(h.partida, 0))
        self.assertEqual(list(h.partida.motos[0].fila), [("UP", 0), ("RIGHT", 0)])
        self.assertEqual(e.pendentes, [])

    def test_moto_do_host_eliminada_descarta_as_setas(self):
        h = host_falso()
        h.partida.motos[0].viva = False
        e, _, _ = entrada([["UP"]])
        e.iniciar_jogo(h)
        e.direcao(h.partida, 0)
        self.assertEqual(list(h.partida.motos[0].fila), [])

    def test_q_e_ctrl_c_saem(self):
        for tecla in ("q", "CTRL_C"):
            h = host_falso()
            e, _, _ = entrada([[], [tecla]])
            e.iniciar_jogo(h)
            with self.assertRaises(KeyboardInterrupt):
                e.atualizar(h)

    def test_sem_terminal_segue_sem_tela(self):
        class TecladoQuebrado(TecladoFalso):
            def __enter__(self):
                raise ErroSemTerminal("sem TTY")

        h = host_falso()
        terminal = TerminalFalso()
        e = EntradaTecladoTela(Opcoes(), teclado=TecladoQuebrado(), terminal=terminal)
        e.iniciar_jogo(h)
        self.assertFalse(e.ativo)
        e.atualizar(h)
        self.assertIsNone(e.direcao(h.partida, 0))
        e.fechar(h)
        self.assertFalse(terminal.entrou)


class TestTela(unittest.TestCase):
    def test_contagem_jogo_e_espectador(self):
        h = host_falso()
        e, _, term = entrada([])
        e.iniciar_jogo(h)
        self.assertIn("COMEÇA EM 3", term.texto())
        self.assertIn("Ana (id 0)", term.texto())
        self.assertIn("lobby: ok", term.texto())

        avancar(h)
        e.atualizar(h)
        self.assertIn("ciclo 1  JOGANDO", term.texto())
        self.assertNotIn("COMEÇA", term.texto())
        quadros = len(term.quadros)
        e.atualizar(h)
        self.assertEqual(len(term.quadros), quadros)

        h.partida.marcar_eliminacao(0, "quit")
        avancar(h)
        e.atualizar(h)
        self.assertIn("ESPECTADOR", term.texto())
        self.assertIn("Você foi eliminado (quit) no ciclo 2", term.texto())

    def test_lobby_fora_e_sem_lobby(self):
        h = host_falso()
        h.lobby = SimpleNamespace(vivo=False)
        e, _, term = entrada([])
        e.iniciar_jogo(h)
        self.assertIn("lobby: fora", term.texto())
        h = host_falso()
        h.lobby = object()
        e, _, term = entrada([])
        e.iniciar_jogo(h)
        self.assertNotIn("lobby", term.texto())

    def test_fim_espera_a_tecla(self):
        h = host_falso()
        e, teclado, term = entrada([[], ["ENTER"]])
        e.iniciar_jogo(h)
        h.game_over = GAME_OVER
        e.fechar(h)
        self.assertIn("FIM DE PARTIDA", term.texto())
        self.assertIn("Vencedor: Ana", term.texto())
        self.assertEqual(teclado.passos, [])
        self.assertTrue(term.saiu and teclado.saiu)
        self.assertFalse(e.ativo)

    def test_abortada_nao_espera(self):
        h = host_falso()
        e, teclado, term = entrada([[], ["ENTER"]])
        e.iniciar_jogo(h)
        h.game_over = GAME_OVER
        h.abortada = True
        e.fechar(h)
        self.assertEqual(teclado.passos, [["ENTER"]])
        self.assertNotIn("FIM DE PARTIDA", term.todos())
        self.assertTrue(term.saiu)


class TestHostDeVerdade(unittest.TestCase):
    def test_entrada_teclado_sem_terminal_joga_ate_o_fim(self):
        porta = str(porta_livre())
        log = os.path.join(tempfile.mkdtemp(), "host.log")
        host = subprocess.Popen([sys.executable, os.path.join(PASTA, "host.py"), "--porta", porta,
                                 "--nome", "Ana", "--entrada", "teclado", "--iniciar-com", "3",
                                 "--tick-ms", "20", "--start-in-ms", "100", "--log", log],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=PASTA)
        esperar_porta(int(porta))
        clientes = [subprocess.Popen([sys.executable, os.path.join(PASTA, "cliente_teste.py"),
                                      "--porta", porta, "--nome", n, "--seed", str(i)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, cwd=PASTA)
                    for i, n in enumerate(["Beto", "Caio"])]
        saidas = [c.communicate(timeout=30)[0].strip() for c in clientes]
        h_out, h_err = host.communicate(timeout=30)
        self.assertEqual(host.returncode, 0, h_err)
        self.assertTrue(h_out.startswith("RESULTADO"), h_out)
        self.assertEqual(saidas, [h_out.strip()] * 2)
        with open(log, encoding="utf-8", errors="replace") as f:
            self.assertIn("sem terminal interativo", f.read())


if __name__ == "__main__":
    unittest.main()
