"""Cenários de falha da parte 4 (heartbeat, timeouts, quedas de jogador, host e lobby).

Cada teste roda um cenário de cenarios.py com processos reais em localhost.
Leva cerca de 1 minuto porque os timeouts de 3 s são os do protocolo.
    python3 -m unittest testes.test_falhas -v
"""

import os
import sys
import tempfile
import unittest

PASTA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PASTA)

import cenarios  # noqa: E402


class TestCenariosDeFalha(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def rodar(self, nome):
        r = cenarios.rodar(nome, self._tmp.name)
        if r.pulado:
            self.skipTest(r.pulado)
        falhas = [t for ok, t in r.checagens if not ok]
        self.assertFalse(falhas, f"{nome}: " + "; ".join(falhas))
        self.assertTrue(r.checagens)


def _criar(nome):
    def teste(self):
        self.rodar(nome)
    teste.__doc__ = cenarios.CENARIOS[nome][1]
    return teste


for _nome in cenarios.CENARIOS:
    setattr(TestCenariosDeFalha, f"test_{_nome}", _criar(_nome))


if __name__ == "__main__":
    unittest.main()
