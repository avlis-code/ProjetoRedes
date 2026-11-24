#!/usr/bin/env python3
"""Cenários de falha do relatório, rodados de verdade numa máquina só.

Cada cenário sobe lobby, host e jogadores como processos separados (cada um
com o seu socket, igual a máquinas diferentes), provoca a falha no momento
certo e confere nos logs o que cada participante concluiu.

    python3 cenarios.py                  # todos
    python3 cenarios.py host_trava       # só um
    python3 cenarios.py --listar

Os logs e um resumo.md ficam em resultados/<data_hora>/. O resumo vai
direto para o relatório.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import statistics
import subprocess
import sys
import itertools
import time
from dataclasses import dataclass, field
from datetime import datetime

import protocol as P
from lobby_link import ConexaoLobby, ErroLobby

PASTA = os.path.dirname(os.path.abspath(__file__))
UNIX = hasattr(signal, "SIGSTOP")
OLHEIROS = itertools.count(1)
HORA = re.compile(r"^(\d\d):(\d\d):(\d\d)(?:[.,](\d{3}))?")


def porta_livre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def segundos_do_dia(t: float | None = None) -> float:
    d = datetime.fromtimestamp(time.time() if t is None else t)
    return d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6


def hora_da_linha(linha: str) -> float | None:
    m = HORA.match(linha)
    if not m:
        return None
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms or 0) / 1000


class Processo:
    def __init__(self, nome: str, script: str, args: list[str], pasta: str, log_proprio=True):
        self.nome = nome
        self.saida = os.path.join(pasta, f"{nome}.out")
        self.log = os.path.join(pasta, f"{nome}.log") if log_proprio else self.saida
        extra = ["--log", self.log] if log_proprio else []
        self._arq = open(self.saida, "w", encoding="utf-8")
        self.p = subprocess.Popen([sys.executable, os.path.join(PASTA, script), *args, *extra],
                                  stdout=self._arq, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, cwd=PASTA)

    def texto(self, qual="log") -> str:
        caminho = self.log if qual == "log" else self.saida
        try:
            with open(caminho, encoding="utf-8", errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def linhas(self, padrao: str, qual="log") -> list[str]:
        texto = self.texto("log") + self.texto("saida") if qual == "todos" else self.texto(qual)
        return [l for l in texto.splitlines() if re.search(padrao, l)]

    def hora(self, padrao: str) -> float | None:
        """Horário (segundos do dia) da primeira linha do log com o padrão."""
        for l in self.linhas(padrao):
            t = hora_da_linha(l)
            if t is not None:
                return t
        return None

    def esperar(self, padrao: str, limite: float = 15.0) -> bool:
        fim = time.monotonic() + limite
        while time.monotonic() < fim:
            if self.linhas(padrao, "todos"):
                return True
            if self.p.poll() is not None:
                return bool(self.linhas(padrao, "todos"))
            time.sleep(0.05)
        return False

    def terminar(self, limite: float = 30.0) -> int | None:
        try:
            return self.p.wait(timeout=limite)
        except subprocess.TimeoutExpired:
            self.matar()
            return None

    def sinal(self, sig) -> None:
        if self.p.poll() is None:
            self.p.send_signal(sig)

    def matar(self) -> None:
        if self.p.poll() is None:
            if UNIX:
                self.p.send_signal(signal.SIGCONT)
            self.p.kill()
            self.p.wait(timeout=10)

    def fechar(self) -> None:
        self.matar()
        self._arq.close()

    @property
    def resultado(self) -> str | None:
        r = self.linhas(r"^RESULTADO ", "saida")
        return r[-1] if r else None

    @property
    def abortada(self) -> str | None:
        r = self.linhas(r"^ABORTADA ", "saida")
        return r[-1] if r else None


@dataclass
class Relato:
    nome: str
    descricao: str
    checagens: list[tuple[bool, str]] = field(default_factory=list)
    medidas: list[str] = field(default_factory=list)
    pulado: str | None = None

    @property
    def ok(self) -> bool:
        return self.pulado is None and all(c for c, _ in self.checagens)


class Cenario:
    """Contexto de um cenário: sobe processos, injeta falhas, junta as checagens."""

    def __init__(self, nome: str, descricao: str, pasta: str, rapido: bool):
        self.relato = Relato(nome, descricao)
        self.pasta = os.path.join(pasta, nome)
        os.makedirs(self.pasta, exist_ok=True)
        self.rapido = rapido
        self.processos: list[Processo] = []
        self.porta_lobby: int | None = None
        self.injecoes: dict[str, float] = {}

    # ------------------------------------------------------------- processos
    def lobby(self) -> Processo:
        self.porta_lobby = porta_livre()
        p = Processo("lobby", "lobby.py", ["--porta", str(self.porta_lobby)], self.pasta,
                     log_proprio=False)
        self.processos.append(p)
        self._esperar_porta(self.porta_lobby)
        return p

    def host(self, *extra: str, nome="Ana", iniciar_com=4, tick=100, start=1000,
             largura=40, altura=20, seed=1) -> Processo:
        args = ["--porta", "0", "--nome", nome, "--iniciar-com", str(iniciar_com),
                "--tick-ms", str(tick), "--start-in-ms", str(start),
                "--largura", str(largura), "--altura", str(altura), "--seed", str(seed), "-v"]
        if self.porta_lobby:
            args += ["--lobby-ip", "127.0.0.1", "--lobby-porta", str(self.porta_lobby)]
        p = Processo(nome, "host.py", args + list(extra), self.pasta)
        self.processos.append(p)
        if not p.esperar(r"partida \d+ criada no lobby|escutando em", 10):
            raise RuntimeError(f"host não subiu:\n{p.texto('saida')}")
        return p

    def jogador(self, nome: str, *extra: str, seed=None) -> Processo:
        args = ["--nome", nome, "--seed", str(seed if seed is not None else len(self.processos)), "-v"]
        if self.porta_lobby:
            args += ["--lobby-ip", "127.0.0.1", "--lobby-porta", str(self.porta_lobby)]
        p = Processo(nome, "cliente_teste.py", args + list(extra), self.pasta)
        self.processos.append(p)
        if not p.esperar(r"WELCOME|ABORTADA", 10):
            raise RuntimeError(f"{nome} não entrou na sala:\n{p.texto('saida')}")
        return p

    def jogadores(self, nomes, extras: dict[str, list[str]] | None = None) -> list[Processo]:
        extras = extras or {}
        return [self.jogador(n, *extras.get(n, [])) for n in nomes]

    @staticmethod
    def _esperar_porta(porta: int) -> None:
        fim = time.monotonic() + 5
        while time.monotonic() < fim:
            try:
                socket.create_connection(("127.0.0.1", porta), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"porta {porta} não abriu")

    def injetar(self, rotulo: str, proc: Processo, sig) -> None:
        self.injecoes[rotulo] = segundos_do_dia()
        proc.sinal(sig)

    def salas_no_lobby(self) -> list[dict] | None:
        try:
            c = ConexaoLobby("127.0.0.1", self.porta_lobby)
            c.registrar(f"olheiro{next(OLHEIROS)}")
            salas = c.pedir({"type": "LIST_MATCHES"}, "MATCH_LIST")["matches"]
            c.fechar()
            return salas
        except ErroLobby:
            return None

    def esperar_sem_salas(self, limite=5.0) -> list[dict] | None:
        fim = time.monotonic() + limite
        salas = self.salas_no_lobby()
        while salas and time.monotonic() < fim:
            time.sleep(0.2)
            salas = self.salas_no_lobby()
        return salas

    # ------------------------------------------------------------- relato
    def checar(self, condicao, texto: str) -> bool:
        self.relato.checagens.append((bool(condicao), texto))
        return bool(condicao)

    def medir(self, texto: str) -> None:
        self.relato.medidas.append(texto)

    def atraso(self, rotulo: str, proc: Processo, padrao: str) -> float | None:
        t0, t1 = self.injecoes.get(rotulo), proc.hora(padrao)
        if t0 is None or t1 is None:
            return None
        return t1 - t0

    def consistencia(self, procs: list[Processo]) -> str | None:
        """Todos imprimiram a mesma linha RESULTADO? Devolve a linha, se sim."""
        res = {p.nome: p.resultado for p in procs}
        iguais = len(set(res.values())) == 1 and None not in res.values()
        self.checar(iguais, f"{', '.join(res)} terminam com a mesma linha RESULTADO "
                            "(vencedor, eliminações e hash da arena)")
        if not iguais:
            for n, r in res.items():
                self.medir(f"{n}: {r}")
        return next(iter(res.values())) if iguais else None

    def latencia_rede(self, host: Processo, jogadores: list[Processo]) -> None:
        rtts = [int(m.group(1)) for p in [host, *jogadores] for l in p.linhas(r"RTT \d+ ms")
                if (m := re.search(r"RTT (\d+) ms", l))]
        if rtts:
            self.medir(f"RTT jogador ↔ host (PING/PONG): {len(rtts)} amostras, mediana "
                       f"{statistics.median(rtts):.0f} ms, máximo {max(rtts)} ms")
        atrasados = host.linhas(r"aplicado no ciclo \d+ com atraso")
        self.medir(f"TURN aplicados com atraso de 1 ciclo ou mais: {len(atrasados)}")

    def encerrar(self) -> None:
        for p in self.processos:
            p.fechar()


def eliminacao(resultado: str | None, nome: str) -> tuple[int, str] | None:
    m = re.search(rf"{nome}@(\d+):(\w+)", resultado or "")
    return (int(m.group(1)), m.group(2)) if m else None


def ciclo_da_falha(proc: Processo) -> int | None:
    m = re.search(r"FALHA SIMULADA .*?ciclo (\d+)", proc.texto("saida"))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Cenários
# ---------------------------------------------------------------------------
def completa(c: Cenario) -> None:
    c.lobby()
    host = c.host()
    jog = c.jogadores(["Beto", "Caio", "Duda"])
    for p in [host, *jog]:
        c.checar(p.terminar() == 0, f"{p.nome} termina com código 0")
    linha = c.consistencia([host, *jog])
    if linha:
        c.medir(linha)
    c.checar(host.linhas(r"END_MATCH"), "host avisa o fim ao lobby (END_MATCH)")
    c.checar(c.esperar_sem_salas() == [], "lobby tira a partida da lista")
    c.latencia_rede(host, jog)


def sai_da_sala(c: Cenario) -> None:
    c.lobby()
    host = c.host()
    beto = c.jogador("Beto")
    c.injetar("queda", beto, signal.SIGKILL if UNIX else signal.SIGTERM)
    saiu = host.esperar(r"Beto saiu da sala \(disconnect\)", 5)
    c.checar(saiu, "host remove Beto da sala ao perceber a conexão fechada")
    atraso = c.atraso("queda", host, r"Beto saiu da sala")
    if atraso is not None:
        c.medir(f"host percebeu a saída de Beto em {atraso * 1000:.0f} ms")
    time.sleep(0.3)
    salas = c.salas_no_lobby() or []
    c.checar(salas and salas[0]["players"] == ["Ana"],
             f"lobby recebe MATCH_UPDATE e mostra só Ana (mostra {salas[0]['players'] if salas else '?'})")
    jog = c.jogadores(["Caio", "Duda", "Edu"])
    for p in [host, *jog]:
        p.terminar()
    c.consistencia([host, *jog])


def desconexao(c: Cenario) -> None:
    c.lobby()
    host = c.host(largura=80, altura=40)
    jog = c.jogadores(["Beto", "Caio", "Duda"], {"Beto": ["--cair-no-ciclo", "10"]})
    for p in [host, *jog]:
        p.terminar()
    beto = jog[0]
    c.checar(beto.p.returncode == 3, "Beto fecha o socket de repente (falha simulada)")
    linha = c.consistencia([host, *jog[1:]])
    e = eliminacao(linha, "Beto")
    c.checar(e and e[1] == "disconnect", f"Beto eliminado com reason disconnect ({e})")
    falha = ciclo_da_falha(beto)
    if e and falha is not None:
        c.checar(e[0] - falha <= 2, "eliminação anunciada no ciclo seguinte à queda")
        c.medir(f"Beto caiu depois do STATE {falha} e foi eliminado no ciclo {e[0]}")
    c.checar(host.p.returncode == 0, "partida continua até o fim com os outros")


def timeout(c: Cenario) -> None:
    # tick de 200 ms: a moto travada anda 15 casas em 3 s e não bate antes do timeout
    c.lobby()
    host = c.host(tick=200, largura=80, altura=40)
    jog = c.jogadores(["Beto", "Caio", "Duda"],
                      {"Caio": ["--travar-no-ciclo", "5", "--travar-por", "6"]})
    for p in [host, *jog]:
        p.terminar()
    caio = jog[1]
    linha = c.consistencia([host, jog[0], jog[2]])
    e = eliminacao(linha, "Caio")
    c.checar(e and e[1] == "timeout", f"Caio eliminado com reason timeout ({e})")
    t_falha = caio.hora(r"FALHA SIMULADA")
    t_det = host.hora(r"Caio caiu \(timeout\)")
    if t_falha is not None and t_det is not None:
        d = t_det - t_falha
        # o relógio do host conta do último byte recebido, que pode ser até 1 s antes
        c.checar(1.9 <= d <= 3.6, f"host detecta o silêncio em {d:.2f} s (esperado entre 2 e 3 s)")
        c.medir(f"Caio parou no STATE {ciclo_da_falha(caio)}; host declarou timeout {d:.2f} s "
                f"depois e a eliminação saiu no ciclo {e[0] if e else '?'}")


def saida(c: Cenario) -> None:
    c.lobby()
    host = c.host(largura=80, altura=40)
    jog = c.jogadores(["Beto", "Caio", "Duda"], {"Duda": ["--sair-no-ciclo", "10"]})
    for p in [host, *jog]:
        p.terminar()
    linha = c.consistencia([host, *jog[:2]])
    e = eliminacao(linha, "Duda")
    c.checar(e and e[1] == "quit", f"Duda eliminado com reason quit ({e})")
    c.checar(host.linhas(r"Duda.*BYE"), "host registra o BYE")


def _host_cai(c: Cenario, sig, rotulo: str, padrao_jog: str, limite: tuple[float, float]):
    c.lobby()
    host = c.host(largura=80, altura=40)
    jog = c.jogadores(["Beto", "Caio", "Duda"])
    jog[0].esperar(r"GAME_START", 10)
    time.sleep(1.0 + 1.0)  # contagem de 1 s + ~10 ciclos de jogo
    c.injetar(rotulo, host, sig)
    for p in jog:
        p.terminar(15)
    for p in jog:
        c.checar(p.p.returncode == 1 and p.abortada and re.search(padrao_jog, p.abortada),
                 f"{p.nome}: {p.abortada}")
        a = c.atraso(rotulo, p, r"partida abortada")
        if a is not None:
            c.checar(limite[0] - 0.001 <= a <= limite[1],
                     f"{p.nome} detecta em {a * 1000:.0f} ms "
                     f"(esperado entre {limite[0] * 1000:.0f} e {limite[1] * 1000:.0f} ms)")
    c.checar(not any(p.resultado for p in jog), "ninguém declara vencedor")
    return host, jog


def host_sai(c: Cenario) -> None:
    host, _ = _host_cai(c, signal.SIGTERM, "sigterm", r"host encerrou: host_quit", (0, 1.0))
    c.checar(host.terminar() == 0, "host manda GAME_ABORTED e sai com código 0")
    c.checar(c.esperar_sem_salas() == [], "lobby tira a partida da lista")


def host_morre(c: Cenario) -> None:
    if not UNIX:
        c.relato.pulado = "SIGKILL só existe em Linux/macOS"
        return
    _host_cai(c, signal.SIGKILL, "sigkill", r"conexão com o host perdida", (0, 1.0))
    c.checar(c.esperar_sem_salas() == [], "lobby percebe a conexão do host fechada e limpa a lista")


def host_trava(c: Cenario) -> None:
    if not UNIX:
        c.relato.pulado = "SIGSTOP só existe em Linux/macOS"
        return
    host, _ = _host_cai(c, signal.SIGSTOP, "sigstop", r"3 s sem sinal", (2.9, 3.6))
    salas = c.salas_no_lobby() or []
    c.checar(len(salas) == 1, "lobby continua listando a partida: a conexão TCP do host não fechou")
    host.matar()


def lobby_cai(c: Cenario) -> None:
    lobby = c.lobby()
    host = c.host(seed=3)
    jog = c.jogadores(["Beto", "Caio", "Duda"])
    jog[0].esperar(r"GAME_START", 10)
    time.sleep(1.5)
    c.injetar("lobby", lobby, signal.SIGKILL if UNIX else signal.SIGTERM)
    for p in [host, *jog]:
        c.checar(p.terminar() == 0, f"{p.nome} termina a partida normalmente")
    linha = c.consistencia([host, *jog])
    if linha:
        c.medir(linha)
    c.checar(host.linhas(r"conexão com o lobby perdida"),
             "host só registra a perda do lobby no log (ao tentar o END_MATCH)")


def lobby_trava(c: Cenario) -> None:
    if not UNIX:
        c.relato.pulado = "SIGSTOP só existe em Linux/macOS"
        return
    lobby = c.lobby()
    host = c.host(seed=3)
    jog = c.jogadores(["Beto", "Caio", "Duda"])
    jog[0].esperar(r"GAME_START", 10)
    time.sleep(1.5)
    c.injetar("lobby", lobby, signal.SIGSTOP)
    t_jogo = time.monotonic()
    for p in jog:
        c.checar(p.terminar() == 0, f"{p.nome} termina a partida normalmente")
    duracao_jog = time.monotonic() - t_jogo
    c.checar(host.terminar() == 0, "host termina com código 0")
    espera_host = time.monotonic() - t_jogo - duracao_jog
    linha = c.consistencia([host, *jog])
    if linha:
        c.medir(linha)
    c.checar(espera_host <= P.TIMEOUT_LOBBY + 1.5,
             f"host espera no máximo o prazo do lobby ({P.TIMEOUT_LOBBY:.0f} s) pelo END_OK "
             f"e sai ({espera_host:.1f} s depois dos jogadores)")
    lobby.matar()


def lobby_fora(c: Cenario) -> None:
    c.porta_lobby = porta_livre()  # ninguém escuta aqui
    p = Processo("Ana", "host.py", ["--porta", "0", "--nome", "Ana", "--lobby-ip", "127.0.0.1",
                                    "--lobby-porta", str(c.porta_lobby)], c.pasta)
    c.processos.append(p)
    c.checar(p.terminar(20) == 1 and "ERRO lobby" in p.texto("saida"),
             "host sem lobby não cria a partida e sai com código 1")
    j = c.jogador("Beto")
    c.checar(j.terminar(20) == 1 and j.abortada and "lobby" in j.abortada,
             f"jogador sem lobby desiste: {j.abortada}")


CENARIOS = {
    "completa": (completa, "Partida completa com 4 jogadores via lobby"),
    "sai_da_sala": (sai_da_sala, "Jogador cai antes do início"),
    "desconexao": (desconexao, "Jogador fecha o socket no meio da partida"),
    "timeout": (timeout, "Jogador trava (não lê nem manda nada)"),
    "saida": (saida, "Jogador sai com BYE no meio da partida"),
    "host_sai": (host_sai, "Host encerrado com Ctrl+C/SIGTERM"),
    "host_morre": (host_morre, "Processo do host morto com SIGKILL"),
    "host_trava": (host_trava, "Processo do host congelado com SIGSTOP"),
    "lobby_cai": (lobby_cai, "Lobby morto com a partida em andamento"),
    "lobby_trava": (lobby_trava, "Lobby congelado com SIGSTOP com a partida em andamento"),
    "lobby_fora": (lobby_fora, "Lobby fora do ar antes de criar a partida"),
}


def rodar(nome: str, pasta: str, rapido: bool = False) -> Relato:
    func, descricao = CENARIOS[nome]
    c = Cenario(nome, descricao, pasta, rapido)
    try:
        func(c)
    except Exception as e:  # noqa: BLE001  o cenário falha, o resumo continua
        c.checar(False, f"erro ao rodar o cenário: {e}")
    finally:
        c.encerrar()
    return c.relato


def resumo_md(relatos: list[Relato]) -> str:
    linhas = [f"# Cenários de falha ({datetime.now():%d/%m/%Y %H:%M})", "",
              "| Cenário | O que foi feito | Resultado |", "|---|---|---|"]
    for r in relatos:
        status = f"pulado: {r.pulado}" if r.pulado else ("OK" if r.ok else "FALHOU")
        linhas.append(f"| `{r.nome}` | {r.descricao} | {status} |")
    for r in relatos:
        linhas += ["", f"## {r.nome}: {r.descricao}", ""]
        if r.pulado:
            linhas.append(f"Pulado: {r.pulado}")
            continue
        linhas += [f"- [{'x' if ok else ' '}] {t}" for ok, t in r.checagens]
        if r.medidas:
            linhas += ["", "Medidas:", ""] + [f"- {m}" for m in r.medidas]
    return "\n".join(linhas) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Roda os cenários de falha e gera o resumo")
    ap.add_argument("cenarios", nargs="*", help="nomes dos cenários (padrão: todos)")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--saida", default=os.path.join(PASTA, "resultados"))
    a = ap.parse_args(argv)
    if a.listar:
        for n, (_, d) in CENARIOS.items():
            print(f"{n:12} {d}")
        return 0
    nomes = a.cenarios or list(CENARIOS)
    for n in nomes:
        if n not in CENARIOS:
            ap.error(f"cenário desconhecido: {n} (veja --listar)")
    pasta = os.path.join(a.saida, datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    os.makedirs(pasta, exist_ok=True)
    relatos = []
    for n in nomes:
        print(f"{n:12} ... ", end="", flush=True)
        r = rodar(n, pasta)
        relatos.append(r)
        print("pulado" if r.pulado else ("OK" if r.ok else "FALHOU"), flush=True)
        for ok, t in r.checagens:
            if not ok:
                print(f"             falhou: {t}")
    with open(os.path.join(pasta, "resumo.md"), "w", encoding="utf-8") as f:
        f.write(resumo_md(relatos))
    print(f"\nLogs e resumo em {pasta}")
    return 0 if all(r.ok or r.pulado for r in relatos) else 1


if __name__ == "__main__":
    sys.exit(main())
