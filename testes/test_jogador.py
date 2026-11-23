import logging
import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jogador
import tela
from falsos import HostFalso, LobbyFalso, TecladoFalso, TerminalFalso
from jogador import Jogador, Tempos

logging.getLogger().addHandler(logging.NullHandler())

RAPIDO = Tempos(hello=0.5, vida=0.5, ping=0.1, passo=0.01, lista_salas=0.2,
                ping_lobby=0.2, aviso=0.05)

LARGURA, ALTURA = 10, 6
JOGADORES = [{"id": 0, "name": "Ana"}, {"id": 1, "name": "Beto"}, {"id": 2, "name": "Caio"}]
WELCOME = {"type": "WELCOME", "id": 1, "match_id": 3, "min_players": 3, "max_players": 4,
           "players": JOGADORES[:2]}
ROSTER = {"type": "ROSTER", "players": JOGADORES}
GAME_START = {"type": "GAME_START", "arena": {"width": LARGURA, "height": ALTURA},
              "tick_ms": 50, "start_in_ms": 100,
              "players": [{"id": 0, "name": "Ana", "x": 1, "y": 1, "dir": "RIGHT"},
                          {"id": 1, "name": "Beto", "x": 8, "y": 4, "dir": "LEFT"},
                          {"id": 2, "name": "Caio", "x": 8, "y": 1, "dir": "DOWN"}]}


def state(ciclo, dir_beto="LEFT", vivos=(0, 1, 2), eventos=()):
    grid = [["."] * LARGURA for _ in range(ALTURA)]
    bikes = [{"id": 0, "x": 1 + ciclo % 8, "y": 1, "dir": "RIGHT", "alive": 0 in vivos},
             {"id": 1, "x": 8 - ciclo % 8, "y": 4, "dir": dir_beto, "alive": 1 in vivos},
             {"id": 2, "x": 8, "y": 1 + ciclo % 5, "dir": "DOWN", "alive": 2 in vivos}]
    for b in bikes:
        grid[b["y"]][b["x"]] = str(b["id"])
    return {"type": "STATE", "cycle": ciclo, "bikes": bikes,
            "grid": ["".join(l) for l in grid],
            "events": [{"event": "ELIMINATED", "id": i, "reason": r} for i, r in eventos]}


GAME_OVER_WIN = {"type": "GAME_OVER", "cycle": 2, "result": "WIN",
                 "winner": {"id": 1, "name": "Beto"},
                 "eliminations": [{"id": 0, "name": "Ana", "cycle": 2, "reason": "wall"},
                                  {"id": 2, "name": "Caio", "cycle": 2, "reason": "trail"}]}
GAME_OVER_DRAW = {"type": "GAME_OVER", "cycle": 2, "result": "DRAW", "winner": None,
                  "eliminations": [{"id": 0, "name": "Ana", "cycle": 2, "reason": "head_on"},
                                   {"id": 1, "name": "Beto", "cycle": 2, "reason": "head_on"},
                                   {"id": 2, "name": "Caio", "cycle": 2, "reason": "wall"}]}

INICIO = [("espera", "HELLO"), ("manda", WELCOME), ("manda", ROSTER), ("pausa", 0.05)]
ATE_O_JOGO = INICIO + [("manda", GAME_START), ("pausa", 0.15), ("manda", state(1))]


def estado(nome):
    return lambda j: j.estado == nome


def ciclo(n):
    return lambda j: j.ultimo_ciclo == n


def nunca_pergunta(_):
    raise AssertionError("não deveria perguntar nada")


def criar(argv, passos, tamanho=(100, 30), perguntar=nunca_pergunta):
    args = jogador.ler_argumentos(argv)
    teclado = TecladoFalso(passos)
    terminal = TerminalFalso(tamanho)
    j = Jogador(args, teclado, terminal, tempos=RAPIDO, perguntar=perguntar,
                dizer=lambda *a, **k: None)
    teclado.jogador = j
    return j, teclado, terminal


def sem_lobby(host, passos, **kw):
    return criar(["--nome", "Beto", "--host", "127.0.0.1", "--porta", str(host.porta)], passos, **kw)


def com_lobby(lobby, passos, extra=(), **kw):
    return criar(["--nome", "Beto", "--lobby-ip", "127.0.0.1", "--lobby-porta", str(lobby.porta),
                  *extra], passos, **kw)


def sala(match_id=3, status="ABERTA"):
    return {"match_id": match_id, "match_name": f"sala {match_id}", "host": "Ana",
            "players": ["Ana"], "max_players": 4, "status": status}


class TestPartida(unittest.TestCase):
    def test_partida_completa_win(self):
        st2 = state(2)
        host = HostFalso(ATE_O_JOGO + [("pausa", 0.1), ("manda", st2), ("manda", GAME_OVER_WIN)])
        j, teclado, term = sem_lobby(host, [(estado("FIM"), ["q"])])
        self.assertEqual(j.executar(), 0)
        self.assertEqual(j.linhas_saida, [tela.resumo_resultado(GAME_OVER_WIN, st2["grid"])])
        tudo = term.todos()
        self.assertIn("Jogadores (3/4, mínimo 3)", tudo)
        self.assertIn("(você)", tudo)
        self.assertIn("COMEÇA EM", tudo)
        self.assertIn("ciclo 1  JOGANDO", tudo)
        self.assertIn("FIM DE PARTIDA", term.texto())
        self.assertIn("Vencedor: Beto", term.texto())
        self.assertTrue(teclado.saiu and term.saiu)
        self.assertNotIn("lobby", tudo)

    def test_partida_completa_draw(self):
        host = HostFalso(ATE_O_JOGO + [("manda", state(2, vivos=())), ("manda", GAME_OVER_DRAW)])
        j, _, term = sem_lobby(host, [(estado("FIM"), ["ENTER"])])
        self.assertEqual(j.executar(), 0)
        self.assertTrue(j.linhas_saida[0].startswith("RESULTADO DRAW vencedor=-"))
        self.assertIn("EMPATE", term.texto())

    def test_eliminado_vira_espectador(self):
        host = HostFalso(ATE_O_JOGO + [
            ("manda", state(2, vivos=(0, 2), eventos=[(1, "trail")])),
            ("pausa", 0.15), ("manda", GAME_OVER_WIN)])
        j, _, term = sem_lobby(host, [(estado("ESPECTADOR"), ["UP", "LEFT"]),
                                      (estado("FIM"), ["q"])])
        self.assertEqual(j.executar(), 0)
        self.assertIn("Você foi eliminado (trail) no ciclo 2. Assistindo. Q sai.", term.todos())
        self.assertIn("ESPECTADOR", term.todos())
        self.assertEqual(host.de_tipo("TURN"), [])

    def test_state_repetido_ou_fora_de_ordem(self):
        host = HostFalso(ATE_O_JOGO + [("manda", state(2)), ("manda", state(2)),
                                       ("manda", state(1)), ("manda", state(4)),
                                       ("espera", "TURN"), ("manda", GAME_OVER_WIN)])
        j, _, _ = sem_lobby(host, [(ciclo(4), ["UP"]), (estado("FIM"), ["q"])])
        self.assertEqual(j.executar(), 0)
        self.assertEqual(j.ultimo_state["cycle"], 4)
        self.assertEqual([t["cycle"] for t in host.de_tipo("TURN")], [4])

    def test_mensagens_inesperadas_nunca_recebem_error(self):
        host = HostFalso(INICIO + [
            ("manda", state(1)), ("manda", WELCOME), ("bytes", b"lixo\n"),
            ("manda", {"type": "ERROR", "code": "UNEXPECTED", "message": "x", "ref": None}),
            ("bytes", b'{"type":"VOAR"}\n'), ("bytes", b'{"type":"TURN"}\n'),
            ("manda", GAME_START), ("pausa", 0.15), ("manda", state(1)), ("manda", ROSTER),
            ("manda", GAME_START), ("manda", state(2)), ("manda", GAME_OVER_WIN)])
        j, _, _ = sem_lobby(host, [(estado("FIM"), ["q"])])
        self.assertEqual(j.executar(), 0)
        host.esperar_fim()
        self.assertNotIn("ERROR", host.tipos())
        self.assertEqual(set(host.tipos()) - {"HELLO", "PING", "PONG"}, set())

    def test_ping_pong(self):
        host = HostFalso(INICIO + [("manda", {"type": "PING", "t": 12345}), ("pausa", 0.4),
                                   ("manda", {"type": "GAME_ABORTED", "reason": "cancelled",
                                              "cycle": None})])
        j, _, _ = sem_lobby(host, [])
        j.executar()
        host.esperar_fim()
        self.assertIn({"type": "PONG", "t": 12345}, host.de_tipo("PONG"))
        self.assertGreaterEqual(len(host.de_tipo("PING")), 1)


class TestFalhas(unittest.TestCase):
    def abortado(self, roteiro, mensagem, passos=(), em_fim=True):
        host = HostFalso(roteiro)
        passos = list(passos) + ([(estado("FIM"), ["q"])] if em_fim else [])
        j, _, term = sem_lobby(host, passos)
        self.assertEqual(j.executar(), 1)
        self.assertEqual(len(j.linhas_saida), 1, j.linhas_saida)
        self.assertTrue(j.linhas_saida[0].startswith("ABORTADA "), j.linhas_saida)
        self.assertIn(mensagem, j.linhas_saida[0])
        return j, term, host

    def test_game_aborted_cancelled(self):
        _, term, _ = self.abortado(INICIO + [("manda", {"type": "GAME_ABORTED",
                                                        "reason": "cancelled", "cycle": None})],
                                   "O host cancelou a sala", em_fim=False)
        self.assertNotIn("FIM DE PARTIDA", term.todos())

    def test_game_aborted_host_quit(self):
        _, term, _ = self.abortado(ATE_O_JOGO + [("manda", {"type": "GAME_ABORTED",
                                                            "reason": "host_quit", "cycle": 5})],
                                   "O host saiu da partida no ciclo 5")
        self.assertIn("PARTIDA ABORTADA: O host saiu da partida no ciclo 5", term.texto())

    def test_game_aborted_internal_error(self):
        self.abortado(ATE_O_JOGO + [("manda", {"type": "GAME_ABORTED",
                                               "reason": "internal_error", "cycle": 1})],
                      "Erro interno do host no ciclo 1")

    def test_reject(self):
        self.abortado([("espera", "HELLO"), ("manda", {"type": "REJECT", "code": "MATCH_FULL",
                                                       "message": "partida cheia (4/4)"})],
                      "MATCH_FULL partida cheia (4/4)", em_fim=False)

    def test_sem_welcome(self):
        self.abortado([("espera", "HELLO"), ("pausa", 1.5)], "O host não respondeu", em_fim=False)

    def test_host_calado(self):
        self.abortado(ATE_O_JOGO + [("silencio", 1.5)], "Conexão com o host perdida")

    def test_host_fecha(self):
        self.abortado(ATE_O_JOGO + [("fecha",)], "Conexão com o host perdida")

    def test_linha_longa_demais(self):
        self.abortado(ATE_O_JOGO + [("bytes", b"x" * (17 * 1024))], "Conexão com o host perdida")

    def test_nao_conecta(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
        s.close()
        j, _, _ = criar(["--nome", "Beto", "--host", "127.0.0.1", "--porta", str(porta)], [])
        self.assertEqual(j.executar(), 1)
        self.assertIn(f"Não conectei em 127.0.0.1:{porta}", j.linhas_saida[0])

    def test_ctrl_c_envia_bye(self):
        host = HostFalso(ATE_O_JOGO + [("pausa", 2)])
        j, teclado, term = sem_lobby(host, [(estado("JOGANDO"), ["KBI"])])
        self.assertEqual(j.executar(), 1)
        host.esperar_fim()
        self.assertIn("BYE", host.tipos())
        self.assertTrue(term.saiu and teclado.saiu)
        self.assertEqual(j.linhas_saida, ["ABORTADA Ctrl+C"])

    def test_q_no_jogo(self):
        host = HostFalso(ATE_O_JOGO + [("pausa", 2)])
        j, _, term = sem_lobby(host, [(estado("JOGANDO"), ["q"]), (estado("FIM"), ["q"])])
        self.assertEqual(j.executar(), 1)
        host.esperar_fim()
        self.assertIn("BYE", host.tipos())
        self.assertIn("PARTIDA ABORTADA: Você saiu da partida", term.texto())

    def test_q_na_sala(self):
        host = HostFalso(INICIO + [("pausa", 2)])
        j, _, term = sem_lobby(host, [(estado("AGUARDANDO_INICIO"), ["q"])])
        self.assertEqual(j.executar(), 1)
        host.esperar_fim()
        self.assertIn("BYE", host.tipos())
        self.assertNotIn("FIM DE PARTIDA", term.todos())

    def test_terminal_pequeno(self):
        host = HostFalso(ATE_O_JOGO + [("espera", "TURN"), ("manda", GAME_OVER_WIN)])
        j, _, term = sem_lobby(host, [(estado("JOGANDO"), ["UP"]), (estado("FIM"), ["q"])],
                               tamanho=(12, 8))
        self.assertEqual(j.executar(), 0)
        self.assertIn("Aumente o terminal: precisa de 12x10, tem 12x8", term.todos())
        self.assertEqual(len(host.de_tipo("TURN")), 1)


class TestTurn(unittest.TestCase):
    def test_regras_do_turn(self):
        host = HostFalso(INICIO + [
            ("pausa", 0.2),
            ("manda", GAME_START), ("espera", "TURN", 1),
            ("manda", state(1)), ("espera", "TURN", 3),
            ("manda", state(2)), ("espera", "TURN", 4),
            ("manda", state(3, dir_beto="RIGHT")), ("espera", "TURN", 6),
            ("manda", state(4, vivos=(0, 2), eventos=[(1, "wall")])),
            ("pausa", 0.2), ("manda", GAME_OVER_WIN)])
        j, _, _ = sem_lobby(host, [
            (estado("AGUARDANDO_INICIO"), ["UP"]),
            (estado("CONTAGEM"), ["LEFT"]),
            (ciclo(1), ["UP", "LEFT", "DOWN"]),
            (ciclo(2), ["UP", "UP"]),
            (ciclo(3), ["UP", "LEFT"]),
            (estado("ESPECTADOR"), ["DOWN"]),
            (estado("FIM"), ["q"])])
        self.assertEqual(j.executar(), 0)
        host.esperar_fim()
        turns = [(t["dir"], t["cycle"]) for t in host.de_tipo("TURN")]
        self.assertEqual(turns, [("LEFT", 0), ("UP", 1), ("LEFT", 1), ("UP", 2),
                                 ("UP", 3), ("LEFT", 3)])


class TestLobby(unittest.TestCase):
    def test_lobby_cai_durante_a_partida(self):
        lobby_ref = []
        host = HostFalso(ATE_O_JOGO + [("chama", lambda: lobby_ref[0].cair()), ("pausa", 0.6),
                                       ("manda", state(2)), ("pausa", 0.1),
                                       ("manda", GAME_OVER_WIN)])
        lobby = LobbyFalso([sala()], host_porta=host.porta)
        lobby_ref.append(lobby)
        j, _, term = com_lobby(lobby, [(lambda j: j.estado == "NO_LOBBY" and j.salas, ["ENTER"]),
                                       (estado("FIM"), ["ENTER"])])
        self.assertEqual(j.executar(), 0)
        tudo = term.todos()
        self.assertIn("lobby: ok", tudo)
        jogo_fora = [i for i in range(len(term.quadros))
                     if "lobby: fora" in term.texto(i) and "TRON  Beto" in term.texto(i)]
        self.assertTrue(jogo_fora, "a barra do jogo não mostrou lobby: fora")
        self.assertIn("ciclo 2", term.texto(jogo_fora[-1]))
        self.assertIn("Lobby indisponível", term.texto())
        self.assertTrue(j.linhas_saida[-1].startswith("RESULTADO WIN"))

    def test_volta_ao_menu_sem_novo_register(self):
        host = HostFalso(ATE_O_JOGO + [("manda", state(2)), ("manda", GAME_OVER_WIN)])
        lobby = LobbyFalso([sala()], host_porta=host.porta)
        j, _, term = com_lobby(lobby, [(lambda j: j.estado == "NO_LOBBY" and j.salas, ["ENTER"]),
                                       (estado("FIM"), ["ENTER"]),
                                       (lambda j: j.vezes_menu >= 2 and j.estado == "NO_LOBBY",
                                        ["q"])])
        self.assertEqual(j.executar(), 0)
        self.assertEqual(lobby.contagem["REGISTER"], 1)
        self.assertGreaterEqual(lobby.contagem["PING"], 1)
        self.assertEqual(j.vezes_menu, 2)
        self.assertIn("TRON em rede", term.texto())
        self.assertIn("Sala 'sala 3' (partida 3)", term.todos())

    def test_name_taken_pergunta_outro_nome(self):
        lobby = LobbyFalso([], ocupados={"Beto"})
        respostas = iter(["Beto2"])
        j, _, term = com_lobby(lobby, [(estado("NO_LOBBY"), ["q"])],
                               perguntar=lambda _: next(respostas))
        self.assertEqual(j.executar(), 1)
        self.assertEqual(lobby.contagem["REGISTER"], 2)
        self.assertEqual(j.nome, "Beto2")
        self.assertIn("Nenhuma sala aberta", term.texto())

    def test_menu_iniciada_e_match_full(self):
        lobby = LobbyFalso([sala(3, "INICIADA"), sala(4)], erro_join="MATCH_FULL")
        j, _, term = com_lobby(lobby, [
            (lambda j: j.estado == "NO_LOBBY" and j.salas, ["ENTER"]),
            (lambda j: j.aviso == "partida já iniciada", ["DOWN", "ENTER"]),
            (lambda j: j.aviso and "MATCH_FULL" in j.aviso, ["q"])])
        self.assertEqual(j.executar(), 1)
        self.assertEqual(lobby.contagem["JOIN_MATCH"], 1)
        tudo = term.todos()
        self.assertIn("partida já iniciada", tudo)
        self.assertIn("MATCH_FULL", tudo)

    def test_partida_direto(self):
        host = HostFalso(INICIO + [("manda", {"type": "GAME_ABORTED", "reason": "cancelled",
                                              "cycle": None})])
        lobby = LobbyFalso([sala(7)], host_porta=host.porta)
        j, _, term = com_lobby(lobby, [(estado("NO_LOBBY"), ["q"])], extra=["--partida", "7"])
        self.assertEqual(j.executar(), 1)
        self.assertEqual(lobby.contagem["JOIN_MATCH"], 1)
        self.assertIn("HELLO", host.tipos())
        self.assertIn("O host cancelou a sala", term.texto())
        self.assertEqual(j.vezes_menu, 1)

    def test_lobby_inacessivel(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
        s.close()
        ditos = []
        args = jogador.ler_argumentos(["--nome", "Beto", "--lobby-ip", "127.0.0.1",
                                       "--lobby-porta", str(porta)])
        j = Jogador(args, TecladoFalso(), TerminalFalso(), tempos=RAPIDO, dizer=ditos.append)
        self.assertEqual(j.executar(), 1)
        self.assertTrue(any("Não conectei no lobby" in d and "rede.py testar" in d for d in ditos))


class TestArgumentos(unittest.TestCase):
    def test_host_e_lobby_nao_combinam(self):
        with self.assertRaises(SystemExit):
            with open(os.devnull, "w") as nulo:
                antigo, sys.stderr = sys.stderr, nulo
                try:
                    jogador.ler_argumentos(["--host", "1.2.3.4", "--lobby-ip", "1.2.3.4"])
                finally:
                    sys.stderr = antigo


if __name__ == "__main__":
    unittest.main()
