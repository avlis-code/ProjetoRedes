"""Testes da lógica pura do Tron (sem rede). Rode: python3 -m unittest discover -s testes"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tron  # noqa: E402
from tron import Partida  # noqa: E402

TRES = [(0, "Ana"), (1, "Beto"), (2, "Caio")]


def colocar(p, pid, x, y, d):
    """Reposiciona uma moto para montar cenários (limpa a célula antiga)."""
    m = p.motos[pid]
    p.grid[m.y][m.x] = None
    m.x, m.y, m.dir = x, y, d
    p.grid[y][x] = pid


class TestInicio(unittest.TestCase):
    def test_posicoes_do_exemplo_do_protocolo(self):
        p = Partida(40, 20, TRES)
        self.assertEqual(p.jogadores_inicio_json(), [
            {"id": 0, "name": "Ana", "x": 4, "y": 4, "dir": "RIGHT"},
            {"id": 1, "name": "Beto", "x": 35, "y": 15, "dir": "LEFT"},
            {"id": 2, "name": "Caio", "x": 35, "y": 4, "dir": "DOWN"},
        ])

    def test_celula_inicial_ja_e_rastro(self):
        p = Partida(40, 20, TRES)
        self.assertEqual(p.grid[4][4], 0)
        self.assertEqual(p.grid_texto()[4][4], "0")
        self.assertEqual(p.ciclo, 0)

    def test_quatro_jogadores_posicoes_distintas(self):
        pos = tron.posicoes_iniciais(40, 20)
        self.assertEqual(len({(x, y) for x, y, _ in pos.values()}), 4)
        self.assertEqual(pos[3], (4, 15, "UP"))

    def test_ids_nao_contiguos(self):
        p = Partida(40, 20, [(0, "Ana"), (1, "Beto"), (3, "Duda")])
        self.assertEqual(p.vivas(), [0, 1, 3])


class TestMovimento(unittest.TestCase):
    def test_anda_uma_celula_e_deixa_rastro(self):
        p = Partida(40, 20, TRES)
        r = p.avancar()
        self.assertEqual(r.ciclo, 1)
        self.assertEqual((p.motos[0].x, p.motos[0].y), (5, 4))
        self.assertEqual(p.grid[4][4], 0)
        self.assertEqual(p.grid[4][5], 0)
        self.assertEqual((p.motos[1].x, p.motos[1].y), (34, 15))
        self.assertEqual((p.motos[2].x, p.motos[2].y), (35, 5))
        self.assertEqual(r.eventos, [])
        self.assertFalse(r.terminou)

    def test_virada_aplicada_no_proximo_ciclo(self):
        p = Partida(40, 20, TRES)
        p.enfileirar_virada(0, "DOWN", 0)
        r = p.avancar()
        self.assertEqual(p.motos[0].dir, "DOWN")
        self.assertEqual((p.motos[0].x, p.motos[0].y), (4, 5))
        self.assertEqual(len(r.viradas), 1)
        self.assertEqual(r.viradas[0].atraso, 0)

    def test_atraso_medido(self):
        p = Partida(40, 20, TRES)
        p.avancar()
        p.avancar()
        p.enfileirar_virada(0, "UP", 0)  # cliente viu o ciclo 0, aplicado no 3
        r = p.avancar()
        self.assertEqual(r.viradas[0].atraso, 2)

    def test_inversao_descartada(self):
        p = Partida(40, 20, TRES)
        p.enfileirar_virada(0, "LEFT")
        p.avancar()
        self.assertEqual(p.motos[0].dir, "RIGHT")
        self.assertEqual(p.motos[0].x, 5)

    def test_mesma_direcao_descartada_sem_gastar_o_ciclo(self):
        p = Partida(40, 20, TRES)
        p.enfileirar_virada(0, "RIGHT")
        p.enfileirar_virada(0, "UP")
        r = p.avancar()
        self.assertEqual(p.motos[0].dir, "UP")
        self.assertEqual([v.dir for v in r.viradas], ["UP"])

    def test_fila_de_dois_um_por_ciclo(self):
        # D3: indo para a direita, aperta UP e LEFT rapidamente
        p = Partida(40, 20, TRES)
        p.enfileirar_virada(0, "UP")
        p.enfileirar_virada(0, "LEFT")
        p.avancar()
        self.assertEqual(p.motos[0].dir, "UP")
        p.avancar()
        self.assertEqual(p.motos[0].dir, "LEFT")

    def test_fila_cheia_descarta_o_mais_antigo(self):
        p = Partida(40, 20, TRES)
        p.enfileirar_virada(0, "UP")
        p.enfileirar_virada(0, "DOWN")
        p.enfileirar_virada(0, "UP")  # UP mais antigo cai; fica [DOWN, UP]
        p.avancar()
        self.assertEqual(p.motos[0].dir, "DOWN")
        p.avancar()  # UP agora é inversão de DOWN: descartado
        self.assertEqual(p.motos[0].dir, "DOWN")


class TestColisoes(unittest.TestCase):
    def test_parede(self):
        p = Partida(10, 10, TRES)
        colocar(p, 0, 9, 5, "RIGHT")
        r = p.avancar()
        self.assertFalse(p.motos[0].viva)
        self.assertEqual(r.eventos, [{"event": "ELIMINATED", "id": 0, "reason": "wall"}])
        self.assertEqual((p.motos[0].x, p.motos[0].y), (9, 5))  # fica na última célula

    def test_rastro_proprio(self):
        p = Partida(40, 20, TRES)
        for d in ("DOWN", "LEFT", "UP"):
            p.enfileirar_virada(0, d)
            p.avancar()
        # (4,4) -> (4,5) -> (3,5) -> (3,4); agora RIGHT entra em (4,4)
        p.enfileirar_virada(0, "RIGHT")
        r = p.avancar()
        self.assertFalse(p.motos[0].viva)
        self.assertEqual(r.eventos[0]["reason"], "trail")

    def test_rastro_de_outro(self):
        p = Partida(40, 20, TRES)
        colocar(p, 0, 10, 10, "RIGHT")
        p.grid[10][11] = 1
        p.avancar()
        self.assertEqual(p.motos[0].motivo, "trail")

    def test_mesma_celula_elimina_todas(self):
        p = Partida(40, 20, [(0, "A"), (1, "B"), (2, "C"), (3, "D")])
        colocar(p, 0, 9, 10, "RIGHT")
        colocar(p, 1, 11, 10, "LEFT")
        colocar(p, 2, 10, 9, "DOWN")
        colocar(p, 3, 30, 3, "RIGHT")
        r = p.avancar()
        self.assertEqual([e["id"] for e in r.eventos], [0, 1, 2])
        self.assertTrue(all(e["reason"] == "head_on" for e in r.eventos))
        self.assertIsNone(p.grid[10][10])  # célula do choque não vira rastro
        self.assertTrue(r.terminou)
        self.assertEqual(p.resultado()[0], "WIN")
        self.assertEqual(p.resultado()[1].id, 3)

    def test_trocar_de_lugar_elimina_as_duas(self):
        p = Partida(40, 20, TRES)
        colocar(p, 0, 10, 10, "RIGHT")
        colocar(p, 1, 11, 10, "LEFT")
        p.avancar()
        self.assertEqual(p.motos[0].motivo, "trail")
        self.assertEqual(p.motos[1].motivo, "trail")

    def test_entrar_na_celula_que_outra_acabou_de_deixar(self):
        # A segue B de perto: a célula que B deixa é rastro de B, então A morre
        p = Partida(40, 20, TRES)
        colocar(p, 0, 10, 10, "RIGHT")
        colocar(p, 1, 11, 10, "RIGHT")
        p.avancar()
        self.assertFalse(p.motos[0].viva)
        self.assertTrue(p.motos[1].viva)

    def test_rastro_de_eliminada_permanece(self):
        p = Partida(10, 10, TRES)
        colocar(p, 0, 9, 5, "RIGHT")
        p.avancar()
        self.assertEqual(p.grid[5][9], 0)

    def test_eliminada_nao_se_move(self):
        p = Partida(40, 20, TRES)
        p.marcar_eliminacao(2, "disconnect")
        p.avancar()
        pos = (p.motos[2].x, p.motos[2].y)
        p.avancar()
        self.assertEqual((p.motos[2].x, p.motos[2].y), pos)


class TestFalhasEFim(unittest.TestCase):
    def test_desconexao_eliminada_no_proximo_ciclo(self):
        p = Partida(40, 20, TRES)
        p.marcar_eliminacao(1, "disconnect")
        self.assertTrue(p.motos[1].viva)
        r = p.avancar()
        self.assertEqual(r.eventos, [{"event": "ELIMINATED", "id": 1, "reason": "disconnect"}])
        self.assertEqual(p.motos[1].ciclo_eliminacao, 1)
        self.assertEqual((p.motos[1].x, p.motos[1].y), (35, 15))  # não andou

    def test_primeiro_motivo_vale(self):
        p = Partida(40, 20, TRES)
        p.marcar_eliminacao(1, "quit")
        p.marcar_eliminacao(1, "disconnect")
        p.avancar()
        self.assertEqual(p.motos[1].motivo, "quit")

    def test_turn_de_eliminado_ignorado(self):
        p = Partida(40, 20, TRES)
        p.marcar_eliminacao(1, "quit")
        p.avancar()
        p.enfileirar_virada(1, "UP")
        self.assertEqual(len(p.motos[1].fila), 0)

    def test_vitoria(self):
        p = Partida(40, 20, TRES)
        p.marcar_eliminacao(1, "disconnect")
        p.marcar_eliminacao(2, "timeout")
        r = p.avancar()
        self.assertTrue(r.terminou)
        go = p.game_over_json()
        self.assertEqual(go["result"], "WIN")
        self.assertEqual(go["winner"], {"id": 0, "name": "Ana"})
        self.assertEqual(go["cycle"], 1)
        self.assertEqual([e["reason"] for e in go["eliminations"]], ["disconnect", "timeout"])

    def test_empate_quando_todos_morrem_no_mesmo_ciclo(self):
        p = Partida(10, 10, TRES)
        colocar(p, 0, 9, 1, "RIGHT")
        colocar(p, 1, 0, 8, "LEFT")
        colocar(p, 2, 5, 9, "DOWN")
        r = p.avancar()
        self.assertTrue(r.terminou)
        go = p.game_over_json()
        self.assertEqual(go["result"], "DRAW")
        self.assertIsNone(go["winner"])
        self.assertEqual(len(go["eliminations"]), 3)

    def test_eliminacoes_ordenadas_por_ciclo(self):
        p = Partida(40, 20, TRES)
        p.marcar_eliminacao(2, "quit")
        p.avancar()
        p.marcar_eliminacao(1, "quit")
        p.avancar()
        go = p.game_over_json()
        self.assertEqual([(e["id"], e["cycle"]) for e in go["eliminations"]], [(2, 1), (1, 2)])

    def test_nao_avanca_depois_do_fim(self):
        p = Partida(40, 20, TRES)
        p.marcar_eliminacao(1, "quit")
        p.marcar_eliminacao(2, "quit")
        p.avancar()
        with self.assertRaises(RuntimeError):
            p.avancar()

    def test_partida_ate_o_fim_sem_entradas(self):
        # Ninguém vira: todos batem na parede ou em rastro em algum momento
        p = Partida(40, 20, [(0, "A"), (1, "B"), (2, "C"), (3, "D")])
        for _ in range(100):
            if p.avancar().terminou:
                break
        self.assertTrue(p.terminou)


class TestMensagens(unittest.TestCase):
    def test_state_tem_formato_do_protocolo(self):
        import protocol
        p = Partida(40, 20, TRES)
        r = p.avancar()
        msg = p.estado_json(r.eventos)
        self.assertEqual(len(msg["grid"]), 20)
        self.assertTrue(all(len(linha) == 40 for linha in msg["grid"]))
        protocol.validar(msg)
        dados = protocol.codificar(msg)
        self.assertLess(len(dados), protocol.MAX_LINHA)
        self.assertEqual(protocol.decodificar(dados.rstrip(b"\n")), msg)

    def test_game_over_valida(self):
        import protocol
        p = Partida(40, 20, TRES)
        p.marcar_eliminacao(1, "quit")
        p.marcar_eliminacao(2, "quit")
        p.avancar()
        protocol.validar(p.game_over_json())


if __name__ == "__main__":
    unittest.main()
