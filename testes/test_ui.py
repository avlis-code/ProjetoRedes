"""Testes de ui.py: telas montadas (funções puras) e Terminal com saída falsa."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui  # noqa: E402
from ui import (InfoSala, Opcoes, Terminal, cortar_visivel, escolher_celula,  # noqa: E402
                largura_visivel, montar_fim, montar_jogo, montar_menu, montar_sala,
                state_inicial)

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

GAME_START = {"type": "GAME_START", "arena": {"width": 40, "height": 20},
              "tick_ms": 100, "start_in_ms": 3000,
              "players": [{"id": 0, "name": "Ana", "x": 4, "y": 4, "dir": "RIGHT"},
                          {"id": 1, "name": "Beto", "x": 35, "y": 15, "dir": "LEFT"},
                          {"id": 2, "name": "Caio", "x": 35, "y": 4, "dir": "DOWN"}]}
JOGADORES = {0: "Ana", 1: "Beto", 2: "Caio"}


def state_exemplo():
    """Ana andou para a direita, Beto subiu, Caio morreu."""
    grid = [["."] * 40 for _ in range(20)]
    for x in range(4, 9):
        grid[4][x] = "0"
    for y in range(12, 16):
        grid[y][35] = "1"
    for y in range(4, 7):
        grid[y][35] = "2"
    return {"type": "STATE", "cycle": 42,
            "bikes": [{"id": 0, "x": 8, "y": 4, "dir": "RIGHT", "alive": True},
                      {"id": 1, "x": 35, "y": 12, "dir": "UP", "alive": True},
                      {"id": 2, "x": 35, "y": 6, "dir": "DOWN", "alive": False}],
            "grid": ["".join(l) for l in grid], "events": []}


def jogo(estado="JOGANDO", st=None, tamanho=(100, 30), opcoes=Opcoes(), contagem=None,
         eliminado_por=None, lobby_ok=True):
    return montar_jogo(estado, st or state_exemplo(), (40, 20), JOGADORES, 1, eliminado_por,
                       contagem, lobby_ok, tamanho, opcoes)


def sem_ansi(linhas):
    return [ANSI.sub("", l) for l in linhas]


SALAS = [{"match_id": 3, "match_name": "sala da Ana", "host": "Ana", "players": ["Ana", "Beto"],
          "max_players": 4, "status": "ABERTA"},
         {"match_id": 4, "match_name": "x", "host": "Duda", "players": ["Duda", "Edu", "Fabi"],
          "max_players": 4, "status": "INICIADA"}]
GAME_OVER = {"type": "GAME_OVER", "cycle": 187, "result": "WIN", "winner": {"id": 1, "name": "Beto"},
             "eliminations": [{"id": 2, "name": "Caio", "cycle": 63, "reason": "trail"},
                              {"id": 0, "name": "Ana", "cycle": 187, "reason": "wall"}]}


class TestAjudantes(unittest.TestCase):
    def test_largura_visivel(self):
        self.assertEqual(largura_visivel("abc"), 3)
        self.assertEqual(largura_visivel("\x1b[1;32mabc\x1b[0m"), 3)

    def test_cortar_visivel(self):
        s = "\x1b[31mabcdef\x1b[0m"
        cortada = cortar_visivel(s, 3)
        self.assertEqual(ANSI.sub("", cortada), "abc")
        self.assertTrue(cortada.endswith("\x1b[0m"))
        self.assertEqual(cortar_visivel("abc", 10), "abc")

    def test_escolher_celula(self):
        self.assertEqual(escolher_celula(Opcoes(), 40, 84), 2)
        self.assertEqual(escolher_celula(Opcoes(), 40, 83), 1)
        self.assertEqual(escolher_celula(Opcoes(celula=1), 40, 200), 1)
        self.assertEqual(escolher_celula(Opcoes(celula=2), 40, 50), 2)

    def test_state_inicial(self):
        st = state_inicial(GAME_START)
        self.assertEqual(st["cycle"], 0)
        self.assertEqual(len(st["grid"]), 20)
        self.assertEqual(st["grid"][4][4], "0")
        self.assertEqual(st["grid"][15][35], "1")
        self.assertEqual(st["grid"][4][35], "2")
        self.assertEqual(sum(l.count(".") for l in st["grid"]), 800 - 3)
        self.assertTrue(all(b["alive"] for b in st["bikes"]))

    def test_ascii_automatico(self):
        class S:
            def __init__(self, enc):
                self.encoding = enc
        self.assertFalse(ui.ascii_automatico(S("utf-8")))
        self.assertFalse(ui.ascii_automatico(S("UTF8")))
        self.assertTrue(ui.ascii_automatico(S("cp1252")))
        self.assertTrue(ui.ascii_automatico(S(None)))


class TestMontarJogo(unittest.TestCase):
    def test_tamanho_celula_1(self):
        linhas = jogo(tamanho=(80, 24))
        self.assertEqual(len(linhas), 24)
        for l in linhas[1:22]:
            self.assertEqual(largura_visivel(l), 42, repr(l))

    def test_tamanho_celula_2(self):
        linhas = jogo(tamanho=(100, 30))
        self.assertEqual(len(linhas), 24)
        for l in linhas[1:22]:
            self.assertEqual(largura_visivel(l), 82)

    def test_cabecas_e_moto_morta(self):
        linhas = sem_ansi(jogo(tamanho=(80, 24)))
        arena = linhas[2:22]
        self.assertEqual(arena[4][1 + 8], "▶")
        self.assertEqual(arena[12][1 + 35], "▲")
        self.assertEqual(arena[6][1 + 35], "✖")
        self.assertEqual(arena[4][1 + 4], "█")
        self.assertEqual(arena[0][1], " ")

    def test_cabeca_do_jogador_local_em_negrito(self):
        linhas = jogo(tamanho=(80, 24))
        self.assertIn("\x1b[1;32m▲", linhas[2 + 12])
        self.assertIn("\x1b[31m", linhas[2 + 4])  # Ana sem negrito

    def test_ascii(self):
        op = Opcoes(ascii=True)
        telas = [jogo(opcoes=op), jogo("CONTAGEM", opcoes=op, contagem=3),
                 jogo("ESPECTADOR", opcoes=op, eliminado_por=("trail", 63)),
                 jogo(opcoes=op, tamanho=(30, 10)),
                 montar_menu(SALAS, 0, "Beto", "127.0.0.1:5000 (ok)", "partida já iniciada", op),
                 montar_menu([], 0, "Beto", "x", None, op),
                 montar_sala(InfoSala(3, "sala da João", "10.0.0.1", 6000),
                             [{"id": 0, "name": "Ana"}, {"id": 1, "name": "Beto"}], 1, True, op),
                 montar_fim(GAME_OVER, JOGADORES, True, op),
                 montar_fim("O host saiu da partida", JOGADORES, None, op)]
        for linhas in telas:
            for l in linhas:
                self.assertTrue(all(ord(c) < 128 for c in l), repr(l))
        arena = sem_ansi(telas[0])[2:22]
        self.assertEqual(arena[12][1 + 35 * 2], "^")
        self.assertEqual(arena[6][1 + 35 * 2], "X")
        self.assertIn("COMECA EM 3", "".join(sem_ansi(telas[1])))

    def test_sem_cor_usa_digitos(self):
        linhas = jogo(tamanho=(80, 24), opcoes=Opcoes(cor=False))
        self.assertFalse(any("\x1b" in l for l in linhas))
        arena = linhas[2:22]
        self.assertEqual(arena[4][1 + 4], "0")
        self.assertEqual(arena[13][1 + 35], "1")
        self.assertEqual(arena[12][1 + 35], "▲")

    def test_celula_2_repete_o_caractere(self):
        arena = sem_ansi(jogo(tamanho=(100, 30)))[2:22]
        self.assertEqual(arena[4][1 + 8:1 + 10], "██")
        self.assertEqual(arena[4][1 + 16:1 + 18], "▶▶")

    def test_terminal_pequeno(self):
        linhas = sem_ansi(jogo(tamanho=(30, 10)))
        self.assertIn("Aumente o terminal: precisa de 42x24, tem 30x10", "\n".join(linhas))
        self.assertLess(len(linhas), 24)
        linhas = sem_ansi(jogo(tamanho=(80, 23)))  # uma linha a menos
        self.assertIn("precisa de 42x24, tem 80x23", "\n".join(linhas))

    def test_contagem(self):
        st = state_inicial(GAME_START)
        linhas = sem_ansi(jogo("CONTAGEM", st=st, contagem=3, tamanho=(80, 24)))
        self.assertIn("COMEÇA EM 3", linhas[2 + 10])
        self.assertIn("CONTAGEM", linhas[0])
        for l in linhas[1:22]:
            self.assertEqual(len(l), 42)

    def test_espectador(self):
        linhas = sem_ansi(jogo("ESPECTADOR", eliminado_por=("trail", 63)))
        self.assertIn("Você foi eliminado (trail) no ciclo 63. Assistindo. Q sai.", linhas[-1])

    def test_legenda(self):
        rodape = sem_ansi(jogo())[-1]
        self.assertIn("● Ana", rodape)
        self.assertIn("● Beto (você)", rodape)
        self.assertIn("✖ Caio", rodape)
        self.assertIn("WASD", rodape)

    def test_lobby_na_barra(self):
        self.assertIn("lobby: ok", sem_ansi(jogo(lobby_ok=True))[0])
        self.assertIn("lobby: fora", sem_ansi(jogo(lobby_ok=False))[0])
        self.assertNotIn("lobby", sem_ansi(jogo(lobby_ok=None))[0])
        self.assertIn("ciclo 42", sem_ansi(jogo())[0])
        self.assertIn("Beto (id 1)", sem_ansi(jogo())[0])


class TestTelasDeTexto(unittest.TestCase):
    def test_menu(self):
        linhas = montar_menu(SALAS, 0, "Beto", "192.168.0.5:5000 (ok)", None, Opcoes())
        texto = sem_ansi(linhas)
        self.assertIn("você: Beto", texto[0])
        self.assertIn("lobby: 192.168.0.5:5000 (ok)", texto[0])
        linha_ana = next(l for l in texto if "sala da Ana" in l)
        self.assertTrue(linha_ana.startswith(" > 3"))
        self.assertIn("2/4", linha_ana)
        linha_x = next(i for i, l in enumerate(texto) if "INICIADA" in l)
        self.assertTrue(texto[linha_x].startswith("   4"))
        self.assertIn("\x1b[2m", linhas[linha_x])  # apagada

        texto = sem_ansi(montar_menu(SALAS, 1, "Beto", "x", None, Opcoes()))
        self.assertTrue(next(l for l in texto if "INICIADA" in l).startswith(" > 4"))

    def test_menu_vazio_e_aviso(self):
        texto = "\n".join(sem_ansi(montar_menu([], 0, "Beto", "x", None, Opcoes())))
        self.assertIn("Nenhuma sala aberta. Peça a alguém para criar uma com host.py.", texto)
        texto = "\n".join(sem_ansi(montar_menu(SALAS, 0, "Beto", "x", "MATCH_FULL", Opcoes())))
        self.assertIn("MATCH_FULL", texto)
        self.assertIn("Enter entra", texto)

    def test_sala(self):
        jog = [{"id": 0, "name": "Ana"}, {"id": 1, "name": "Beto"}, {"id": 2, "name": "Caio"}]
        texto = sem_ansi(montar_sala(InfoSala(3, "sala da Ana", "192.168.0.10", 6000), jog, 1,
                                     True, Opcoes()))
        self.assertIn("Sala 'sala da Ana' (partida 3)", texto[0])
        self.assertIn("host: Ana  192.168.0.10:6000", texto[0])
        self.assertIn("(3/4, mínimo 3)", texto[1])
        self.assertIn("(host)", next(l for l in texto if "Ana" in l and l.startswith("   0")))
        self.assertIn("(você)", next(l for l in texto if l.startswith("   1")))
        self.assertIn("Q sai", texto[-1])
        self.assertIn("lobby: ok", texto[-1])

    def test_sala_sem_lobby(self):
        texto = sem_ansi(montar_sala(InfoSala(None, None, "127.0.0.1", 6000),
                                     [{"id": 0, "name": "Ana"}, {"id": 2, "name": "Beto"}], 2,
                                     None, Opcoes()))
        self.assertTrue(texto[0].startswith(" Sala    host: Ana"))
        self.assertNotIn("lobby", texto[-1])

    def test_fim(self):
        texto = sem_ansi(montar_fim(GAME_OVER, JOGADORES, True, Opcoes()))
        self.assertEqual(texto[0], " FIM DE PARTIDA")
        self.assertIn("Vencedor: Beto   (ciclo 187)", texto[1])
        self.assertIn("Eliminações:", texto[2])
        self.assertIn("Caio", texto[3])
        self.assertIn("ciclo 63", texto[3])
        self.assertIn("trail", texto[3])
        self.assertIn("Enter volta ao lobby", texto[-1])

        empate = dict(GAME_OVER, result="DRAW", winner=None)
        self.assertIn("EMPATE", sem_ansi(montar_fim(empate, JOGADORES, None, Opcoes()))[1])

        texto = sem_ansi(montar_fim("O host saiu da partida (ciclo 95)", JOGADORES, None, Opcoes()))
        self.assertIn("PARTIDA ABORTADA: O host saiu da partida (ciclo 95)", texto[1])
        self.assertIn("Enter ou Q sai", texto[-1])
        self.assertIn("lobby: fora", sem_ansi(montar_fim(GAME_OVER, JOGADORES, False, Opcoes()))[-1])


class SaidaFalsa:
    def __init__(self):
        self.escritas = []
        self.flushes = 0

    def write(self, s):
        self.escritas.append(s)

    def flush(self):
        self.flushes += 1


class TestTerminal(unittest.TestCase):
    def setUp(self):
        self.saida = SaidaFalsa()
        self.tam = [80, 24]
        self.t = Terminal(saida=self.saida, tamanho=lambda: tuple(self.tam), ativar_ansi=lambda: True)

    def test_entra_e_sai(self):
        with self.t:
            self.assertIn("\x1b[?1049h", self.saida.escritas[0])
            self.assertIn("\x1b[?25l", self.saida.escritas[0])
        fim = self.saida.escritas[-1]
        self.assertIn("\x1b[?25h", fim)
        self.assertIn("\x1b[?1049l", fim)
        self.assertIn("\x1b[0m", fim)

    def test_restaura_mesmo_com_excecao(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.t:
                raise KeyboardInterrupt
        self.assertIn("\x1b[?1049l", self.saida.escritas[-1])
        n = len(self.saida.escritas)
        self.t._restaurar()  # segunda vez não escreve nada
        self.assertEqual(len(self.saida.escritas), n)

    def test_sem_ansi(self):
        t = Terminal(saida=self.saida, tamanho=lambda: (80, 24), ativar_ansi=lambda: False)
        with self.assertRaises(ui.ErroTerminal):
            with t:
                pass

    def test_diff_por_linha(self):
        with self.t:
            self.saida.escritas.clear()
            self.t.mostrar(["a", "b", "c"])
            self.assertEqual(len(self.saida.escritas), 1)  # uma escrita por quadro
            primeiro = self.saida.escritas[0]
            for n in (1, 2, 3):
                self.assertIn(f"\x1b[{n};1H", primeiro)

            self.t.mostrar(["a", "B", "c"])
            self.assertEqual(len(self.saida.escritas), 2)
            segundo = self.saida.escritas[1]
            self.assertIn("\x1b[2;1HB", segundo)
            self.assertNotIn("\x1b[1;1H", segundo)
            self.assertNotIn("\x1b[3;1H", segundo)
            self.assertNotIn("\x1b[2J", segundo)

            self.t.mostrar(["a", "B", "c"])  # nada mudou: nada escrito
            self.assertEqual(len(self.saida.escritas), 2)

            self.t.mostrar(["a"])  # quadro mais curto limpa as sobras
            terceiro = self.saida.escritas[2]
            self.assertIn("\x1b[2;1H\x1b[K", terceiro)
            self.assertIn("\x1b[3;1H\x1b[K", terceiro)

    def test_redesenha_tudo_ao_mudar_tamanho_ou_trocar_tela(self):
        with self.t:
            self.t.mostrar(["a", "b"])
            self.tam[0] = 100
            self.t.mostrar(["a", "b"])
            self.assertIn("\x1b[1;1H", self.saida.escritas[-1])
            self.assertIn("\x1b[2;1H", self.saida.escritas[-1])
            self.t.nova_tela()
            self.t.mostrar(["a", "b"])
            self.assertIn("\x1b[1;1H", self.saida.escritas[-1])

    def test_nunca_quebra_linha_e_corta_na_largura(self):
        self.tam[:] = [10, 3]
        with self.t:
            self.t.mostrar(["x" * 20, "y" * 20, "z" * 20, "nao cabe"])
            s = self.saida.escritas[-1]
            self.assertNotIn("\n", s)
            self.assertIn("x" * 10 + "\x1b[0m", s)
            self.assertNotIn("x" * 11, s)
            self.assertIn("z" * 9 + "\x1b[0m", s)  # última linha: uma coluna a menos
            self.assertNotIn("z" * 10, s)
            self.assertNotIn("nao cabe", s)

    def test_quadro_do_jogo_inteiro(self):
        with self.t:
            linhas = jogo(tamanho=(80, 24))
            self.t.mostrar(linhas)
            self.assertNotIn("\n", self.saida.escritas[-1])


class TestAnsiWindows(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "só fora do Windows")
    def test_fora_do_windows_nao_faz_nada(self):
        self.assertTrue(ui.ativar_ansi_windows())

    def test_importavel(self):
        self.assertTrue(callable(ui.ativar_ansi_windows))


if __name__ == "__main__":
    unittest.main()
