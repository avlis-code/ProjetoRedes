import io
import os
import subprocess
import sys
import unittest

PASTA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PASTA)

from entrada import (ErroSemTerminal, Teclado, decodificar_bytes,
                     decodificar_windows, tecla_para_direcao)


class TestDecodificarBytes(unittest.TestCase):
    def test_setas_csi_e_ss3(self):
        self.assertEqual(decodificar_bytes(b"\x1b[A"), ["UP"])
        self.assertEqual(decodificar_bytes(b"\x1b[B\x1b[C\x1b[D"), ["DOWN", "RIGHT", "LEFT"])
        self.assertEqual(decodificar_bytes(b"\x1bOB"), ["DOWN"])
        self.assertEqual(decodificar_bytes(b"\x1bOA\x1bOD"), ["UP", "LEFT"])

    def test_letras_viram_minusculas(self):
        self.assertEqual(decodificar_bytes(b"w"), ["w"])
        self.assertEqual(decodificar_bytes(b"W"), ["w"])
        self.assertEqual(decodificar_bytes(b"Qr"), ["q", "r"])

    def test_esc_sozinho(self):
        self.assertEqual(decodificar_bytes(b"\x1b"), ["ESC"])
        self.assertEqual(decodificar_bytes(b"\x1bq"), ["ESC", "q"])

    def test_varias_teclas_numa_leitura(self):
        self.assertEqual(decodificar_bytes(b"\x1b[Aw\r"), ["UP", "w", "ENTER"])

    def test_enter_e_ctrl_c(self):
        self.assertEqual(decodificar_bytes(b"\n"), ["ENTER"])
        self.assertEqual(decodificar_bytes(b"\r"), ["ENTER"])
        self.assertEqual(decodificar_bytes(b"\x03"), ["CTRL_C"])

    def test_sequencia_cortada_no_meio(self):
        self.assertEqual(decodificar_bytes(b"\x1b["), [])
        self.assertEqual(decodificar_bytes(b"\x1bO"), [])
        self.assertEqual(decodificar_bytes(b"w\x1b[1;"), ["w"])

    def test_sequencia_desconhecida_ignorada(self):
        self.assertEqual(decodificar_bytes(b"\x1b[5~q"), ["q"])
        self.assertEqual(decodificar_bytes(b"\x1b[1;5A"), [])

    def test_controles_e_utf8_invalido(self):
        self.assertEqual(decodificar_bytes(b"\t\x7f"), [])
        self.assertEqual(decodificar_bytes(b"\xff\xfew"), ["w"])
        self.assertEqual(decodificar_bytes(b""), [])


class TestDecodificarWindows(unittest.TestCase):
    def test_setas(self):
        self.assertEqual(decodificar_windows(["\xe0", "H"]), ["UP"])
        self.assertEqual(decodificar_windows(["\x00", "K"]), ["LEFT"])
        self.assertEqual(decodificar_windows(["\xe0", "P", "\xe0", "M"]), ["DOWN", "RIGHT"])

    def test_teclas_comuns(self):
        self.assertEqual(decodificar_windows(["\r"]), ["ENTER"])
        self.assertEqual(decodificar_windows(["\x1b"]), ["ESC"])
        self.assertEqual(decodificar_windows(["\x03"]), ["CTRL_C"])
        self.assertEqual(decodificar_windows(["A", "s"]), ["a", "s"])

    def test_prefixo_sozinho_ou_desconhecido(self):
        self.assertEqual(decodificar_windows(["\xe0"]), [])
        self.assertEqual(decodificar_windows(["\x00", ";"]), [])
        self.assertEqual(decodificar_windows(["\xe0", "G", "w"]), ["w"])


class TestDirecao(unittest.TestCase):
    def test_setas_e_wasd(self):
        casos = {"UP": "UP", "w": "UP", "DOWN": "DOWN", "s": "DOWN",
                 "LEFT": "LEFT", "a": "LEFT", "RIGHT": "RIGHT", "d": "RIGHT"}
        for tecla, direcao in casos.items():
            self.assertEqual(tecla_para_direcao(tecla), direcao, tecla)

    def test_outras_teclas(self):
        for tecla in ("q", "ENTER", "ESC", "CTRL_C", "r", "x"):
            self.assertIsNone(tecla_para_direcao(tecla))


class TestModulo(unittest.TestCase):
    def test_importar_nao_carrega_msvcrt_nem_termios(self):
        codigo = ("import sys; sys.path.insert(0, %r); import entrada; "
                  "print('msvcrt' in sys.modules, 'termios' in sys.modules, 'tty' in sys.modules)"
                  % PASTA)
        r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        _, termios, tty = r.stdout.split()
        self.assertEqual((termios, tty), ("False", "False"))

    @unittest.skipIf(sys.platform == "win32", "no Windows o teclado usa msvcrt, sem termios")
    def test_sem_terminal_interativo(self):
        with self.assertRaises(ErroSemTerminal):
            with Teclado(stdin=io.StringIO()):
                pass


if __name__ == "__main__":
    unittest.main()
