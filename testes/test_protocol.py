"""Testes do framing e da validação (sem rede)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import protocol as P  # noqa: E402


class TestFraming(unittest.TestCase):
    def test_codificar_uma_linha(self):
        dados = P.codificar({"type": "TURN", "dir": "UP", "cycle": 3})
        self.assertTrue(dados.endswith(b"\n"))
        self.assertEqual(dados.count(b"\n"), 1)

    def test_string_com_quebra_de_linha_escapada(self):
        dados = P.codificar(P.bye("linha1\nlinha2"))
        self.assertEqual(dados.count(b"\n"), 1)

    def test_utf8(self):
        dados = P.codificar(P.bye("usuário saiu"))
        self.assertIn("usuário".encode("utf-8"), dados)
        self.assertEqual(P.decodificar(dados.strip())["reason"], "usuário saiu")

    def test_meia_mensagem_e_varias_juntas(self):
        r = P.LeitorLinhas()
        a = P.codificar(P.ping(1))
        b = P.codificar(P.ping(2))
        self.assertEqual(r.alimentar(a[:5]), [])
        linhas = r.alimentar(a[5:] + b + b[:3])
        self.assertEqual(len(linhas), 2)
        self.assertEqual(r.alimentar(b[3:]), [b.strip()])

    def test_linhas_vazias_e_crlf_ignorados(self):
        r = P.LeitorLinhas()
        self.assertEqual(r.alimentar(b"\n  \r\n{\"type\":\"LIST_MATCHES\"}\r\n"),
                         [b"{\"type\":\"LIST_MATCHES\"}"])

    def test_linha_longa_sem_quebra(self):
        r = P.LeitorLinhas()
        with self.assertRaises(P.LinhaMuitoLonga):
            r.alimentar(b"x" * (P.MAX_LINHA + 1))

    def test_linha_longa_com_quebra(self):
        r = P.LeitorLinhas()
        with self.assertRaises(P.LinhaMuitoLonga):
            r.alimentar(b"x" * (P.MAX_LINHA + 1) + b"\n")


class TestDecodificar(unittest.TestCase):
    def erro(self, linha):
        with self.assertRaises(P.ErroProtocolo) as cm:
            P.decodificar(linha)
        return cm.exception

    def test_json_invalido(self):
        self.assertEqual(self.erro(b"{nao json").codigo, P.BAD_JSON)

    def test_utf8_invalido(self):
        self.assertEqual(self.erro(b"\xff\xfe").codigo, P.BAD_JSON)

    def test_nao_objeto(self):
        self.assertEqual(self.erro(b"[1,2]").codigo, P.BAD_JSON)

    def test_sem_type(self):
        e = self.erro(b'{"dir":"UP"}')
        self.assertEqual(e.codigo, P.UNKNOWN_TYPE)
        self.assertIsNone(e.ref)

    def test_type_desconhecido(self):
        e = self.erro(b'{"type":"VOAR"}')
        self.assertEqual(e.codigo, P.UNKNOWN_TYPE)
        self.assertEqual(e.ref, "VOAR")

    def test_campo_ausente(self):
        e = self.erro(b'{"type":"TURN","dir":"UP"}')
        self.assertEqual(e.codigo, P.BAD_FIELD)
        self.assertEqual(e.ref, "TURN")
        self.assertEqual(e.como_mensagem()["type"], "ERROR")

    def test_direcao_invalida(self):
        self.assertEqual(self.erro(b'{"type":"TURN","dir":"NE","cycle":1}').codigo, P.BAD_FIELD)

    def test_bool_nao_e_inteiro(self):
        self.assertEqual(self.erro(b'{"type":"TURN","dir":"UP","cycle":true}').codigo, P.BAD_FIELD)

    def test_ciclo_negativo(self):
        self.assertEqual(self.erro(b'{"type":"TURN","dir":"UP","cycle":-1}').codigo, P.BAD_FIELD)

    def test_campos_desconhecidos_ignorados(self):
        m = P.decodificar(b'{"type":"TURN","dir":"UP","cycle":1,"extra":42}')
        self.assertEqual(m["dir"], "UP")

    def test_opcionais(self):
        P.decodificar(b'{"type":"BYE"}')
        P.decodificar(b'{"type":"CREATE_MATCH","port":6000}')
        P.decodificar(b'{"type":"END_MATCH","match_id":3,"winner":null}')
        self.assertEqual(self.erro(b'{"type":"CREATE_MATCH","port":6000,"max_players":5}').codigo,
                         P.BAD_FIELD)
        self.assertEqual(self.erro(b'{"type":"CREATE_MATCH","port":70000}').codigo, P.BAD_FIELD)

    def test_exemplos_do_documento(self):
        exemplos = [
            '{"type": "REGISTER", "name": "Ana", "version": 1}',
            '{"type": "JOIN_OK", "match_id": 3, "host": "Ana", "host_ip": "192.168.0.10", "host_port": 6000}',
            '{"type": "MATCH_UPDATE", "match_id": 3, "players": ["Ana", "Beto", "Caio"]}',
            '{"type": "WELCOME", "id": 1, "match_id": 3, "min_players": 3, "max_players": 4,'
            ' "players": [{"id": 0, "name": "Ana"}, {"id": 1, "name": "Beto"}]}',
            '{"type": "REJECT", "code": "MATCH_FULL", "message": "partida cheia (4/4)"}',
            '{"type": "GAME_START", "arena": {"width": 40, "height": 20}, "tick_ms": 100,'
            ' "start_in_ms": 3000, "players": [{"id": 0, "name": "Ana", "x": 4, "y": 4, "dir": "RIGHT"}]}',
            '{"type": "GAME_OVER", "cycle": 187, "result": "WIN", "winner": {"id": 1, "name": "Beto"},'
            ' "eliminations": [{"id": 2, "name": "Caio", "cycle": 63, "reason": "trail"}]}',
            '{"type": "GAME_ABORTED", "reason": "host_quit", "cycle": null}',
            '{"type": "ERROR", "code": "UNEXPECTED", "message": "x", "ref": "JOIN_MATCH"}',
            '{"type": "MATCH_LIST", "matches": [{"match_id": 3, "match_name": "sala da Ana",'
            ' "host": "Ana", "players": ["Ana", "Beto"], "max_players": 4, "status": "ABERTA"}]}',
        ]
        for e in exemplos:
            P.decodificar(e.encode("utf-8"))


class TestSocketBloqueante(unittest.TestCase):
    def test_send_msg_e_read_msg(self):
        import socket
        a, b = socket.socketpair()
        leitor = P.LineReader(b)
        P.send_msg(a, P.ping(1))
        a.sendall(b"\nlixo\n" + P.codificar(P.ping(2))[:4])
        self.assertEqual(leitor.read_msg(), {"type": "PING", "t": 1})
        with self.assertRaises(P.ProtocolError) as cm:
            leitor.read_msg()
        self.assertFalse(cm.exception.fatal)
        a.sendall(P.codificar(P.ping(2))[4:])
        self.assertEqual(leitor.read_msg()["t"], 2)
        a.sendall(b"x" * (P.MAX_LINHA + 1))
        with self.assertRaises(P.ProtocolError) as cm:
            leitor.read_msg()
        self.assertTrue(cm.exception.fatal)
        a.close()
        self.assertIsNone(P.LineReader(b).read_msg())
        b.close()


class TestNome(unittest.TestCase):
    def test_nomes(self):
        self.assertTrue(P.nome_valido("Ana_1-x"))
        self.assertFalse(P.nome_valido(""))
        self.assertFalse(P.nome_valido("a" * 17))
        self.assertFalse(P.nome_valido("João"))
        self.assertFalse(P.nome_valido("com espaço"))
        self.assertFalse(P.nome_valido(3))


if __name__ == "__main__":
    unittest.main()
