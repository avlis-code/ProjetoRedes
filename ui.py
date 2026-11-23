from __future__ import annotations

import atexit
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass



@dataclass(frozen=True)
class Opcoes:
    ascii: bool = False
    cor: bool = True
    celula: int | None = None


@dataclass(frozen=True)
class InfoSala:
    match_id: int | None
    nome: str | None
    host_ip: str
    host_porta: int
    min_jogadores: int = 3
    max_jogadores: int = 4


GLIFOS_UNICODE = {
    "rastro": "█", "morto": "✖", "marcador": "●",
    "UP": "▲", "DOWN": "▼", "LEFT": "◀", "RIGHT": "▶",
    "se": "┌", "h": "─", "sd": "┐", "v": "│", "ie": "└", "id": "┘",
    "setas": "←↑↓→", "cima_baixo": "↑/↓",
}
GLIFOS_ASCII = {
    "rastro": "#", "morto": "X", "marcador": "*",
    "UP": "^", "DOWN": "v", "LEFT": "<", "RIGHT": ">",
    "se": "+", "h": "-", "sd": "+", "v": "|", "ie": "+", "id": "+",
    "setas": "setas", "cima_baixo": "W/S",
}

CORES = {0: "31", 1: "32", 2: "33", 3: "36"}
RESET = "\x1b[0m"
NEGRITO = "1"
APAGADO = "2"
FAIXA_AVISO = "41;97"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def glifos(opcoes: Opcoes) -> dict[str, str]:
    return GLIFOS_ASCII if opcoes.ascii else GLIFOS_UNICODE


def _pintar(texto: str, codigo: str | None, opcoes: Opcoes) -> str:
    if not codigo or not opcoes.cor or not texto:
        return texto
    return f"\x1b[{codigo}m{texto}{RESET}"


def _cor_id(pid: int, negrito: bool = False) -> str:
    base = CORES.get(pid, "37")
    return f"{NEGRITO};{base}" if negrito else base


def largura_visivel(s: str) -> int:
    return len(_ANSI_RE.sub("", s))


def cortar_visivel(s: str, n: int) -> str:
    if largura_visivel(s) <= n:
        return s
    saida = []
    visiveis = 0
    teve_escape = False
    i = 0
    while i < len(s) and visiveis < n:
        m = _ANSI_RE.match(s, i)
        if m:
            saida.append(m.group())
            teve_escape = True
            i = m.end()
            continue
        saida.append(s[i])
        visiveis += 1
        i += 1
    if teve_escape:
        saida.append(RESET)
    return "".join(saida)


def _completar(s: str, n: int) -> str:
    return s + " " * max(0, n - largura_visivel(s))


def _so_ascii(s: str) -> str:
    saida = []
    for ch in unicodedata.normalize("NFKD", s):
        if ord(ch) < 128:
            saida.append(ch)
        elif not unicodedata.combining(ch):
            saida.append("?")
    return "".join(saida)


def _finalizar(linhas: list[str], opcoes: Opcoes) -> list[str]:
    if opcoes.ascii:
        return [_so_ascii(l) for l in linhas]
    return linhas


def escolher_celula(opcoes: Opcoes, largura_arena: int, colunas: int) -> int:
    if opcoes.celula in (1, 2):
        return opcoes.celula
    return 2 if colunas >= 2 * largura_arena + 4 else 1


def state_inicial(game_start: dict) -> dict:
    largura = game_start["arena"]["width"]
    altura = game_start["arena"]["height"]
    linhas = [["."] * largura for _ in range(altura)]
    bikes = []
    for p in game_start["players"]:
        if 0 <= p["x"] < largura and 0 <= p["y"] < altura:
            linhas[p["y"]][p["x"]] = str(p["id"])
        bikes.append({"id": p["id"], "x": p["x"], "y": p["y"], "dir": p["dir"], "alive": True})
    return {"type": "STATE", "cycle": 0, "bikes": bikes,
            "grid": ["".join(l) for l in linhas], "events": []}


def ascii_automatico(stream) -> bool:
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
    return enc not in ("utf8", "utf8sig")


def _txt_lobby(lobby_ok: bool | None) -> str:
    if lobby_ok is None:
        return ""
    return "lobby: ok" if lobby_ok else "lobby: fora"


def _cabecalho(esquerda: str, direita: str, largura: int) -> str:
    if not direita:
        return esquerda
    espaco = max(2, largura - largura_visivel(esquerda) - largura_visivel(direita))
    return esquerda + " " * espaco + direita


LARGURA_TEXTO = 72


def montar_menu(salas: list[dict], selecionada: int, nome: str, lobby_txt: str,
                aviso: str | None, opcoes: Opcoes) -> list[str]:
    g = glifos(opcoes)
    linhas = [_cabecalho(f" TRON em rede           você: {nome}", f"lobby: {lobby_txt}",
                         LARGURA_TEXTO),
              " " + g["h"] * (LARGURA_TEXTO - 1)]
    if not salas:
        linhas.append("")
        linhas.append(" Nenhuma sala aberta. Peça a alguém para criar uma com host.py.")
    else:
        linhas.append(f"   {'#':<4}{'Sala':<17}{'Host':<17}{'Jogadores':<12}Status")
        for i, s in enumerate(salas):
            marca = ">" if i == selecionada else " "
            ocupacao = f"{len(s['players'])}/{s['max_players']}"
            texto = (f" {marca} {str(s['match_id']):<4}{s['match_name'][:16]:<17}"
                     f"{s['host'][:16]:<17}{ocupacao:<12}{s['status']}")
            if s["status"] != "ABERTA":
                texto = _pintar(texto, APAGADO, opcoes)
            elif i == selecionada:
                texto = _pintar(texto, NEGRITO, opcoes)
            linhas.append(texto)
    linhas.append("")
    if aviso:
        linhas.append(" " + _pintar(f" {aviso} ", FAIXA_AVISO, opcoes))
        linhas.append("")
    linhas.append(f" {g['cima_baixo']} escolhe   Enter entra   R atualiza   Q sai")
    return _finalizar(linhas, opcoes)


def montar_sala(sala: InfoSala, jogadores: list[dict], meu_id: int,
                lobby_ok: bool | None, opcoes: Opcoes) -> list[str]:
    g = glifos(opcoes)
    nomes = {j["id"]: j["name"] for j in jogadores}
    titulo = f" Sala '{sala.nome}'" if sala.nome else " Sala"
    if sala.match_id is not None:
        titulo += f" (partida {sala.match_id})"
    host = nomes.get(0, "?")
    linhas = [f"{titulo}    host: {host}  {sala.host_ip}:{sala.host_porta}",
              f" Jogadores ({len(jogadores)}/{sala.max_jogadores}, mínimo {sala.min_jogadores}):"]
    largura_nome = max([len(j["name"]) for j in jogadores] + [1])
    for j in sorted(jogadores, key=lambda j: j["id"]):
        marcas = []
        if j["id"] == 0:
            marcas.append("(host)")
        if j["id"] == meu_id:
            marcas.append("(você)")
        marcador = _pintar(g["marcador"], _cor_id(j["id"]), opcoes)
        texto = f"   {j['id']}  {marcador} {j['name']:<{largura_nome}}  {' '.join(marcas)}".rstrip()
        linhas.append(texto)
    linhas.append("")
    linhas.append(_cabecalho(" Aguardando o host iniciar...",
                             "   ".join(t for t in ("Q sai", _txt_lobby(lobby_ok)) if t),
                             LARGURA_TEXTO))
    return _finalizar(linhas, opcoes)


def montar_jogo(estado: str, ultimo_state: dict, arena: tuple[int, int], jogadores: dict[int, str],
                meu_id: int, eliminado_por: tuple[str, int] | None, contagem: int | None,
                lobby_ok: bool | None, tamanho: tuple[int, int], opcoes: Opcoes) -> list[str]:
    g = glifos(opcoes)
    largura, altura = arena
    colunas, n_linhas = tamanho
    cel = escolher_celula(opcoes, largura, colunas)
    larg_moldura = largura * cel + 2

    meu_nome = jogadores.get(meu_id, "?")
    esquerda = f" TRON  {meu_nome} (id {meu_id})  ciclo {ultimo_state.get('cycle', 0)}  {estado}"
    cabecalho = _cabecalho(esquerda, _txt_lobby(lobby_ok),
                           max(larg_moldura, largura_visivel(esquerda) + 12))

    if n_linhas < altura + 4 or colunas < larg_moldura:
        return _finalizar([cabecalho, "",
                           f" Aumente o terminal: precisa de {larg_moldura}x{altura + 4}, "
                           f"tem {colunas}x{n_linhas}"], opcoes)

    motos = {(b["x"], b["y"]): b for b in ultimo_state.get("bikes", [])}
    grid = ultimo_state.get("grid", [])
    celulas: list[list[tuple]] = []
    for y in range(altura):
        linha_grid = grid[y] if y < len(grid) else ""
        linha: list[tuple] = []
        for x in range(largura):
            c = linha_grid[x] if x < len(linha_grid) else "."
            b = motos.get((x, y))
            if b is not None:
                ch = g[b["dir"]] if b["alive"] else g["morto"]
                cor = _cor_id(b["id"], negrito=b["id"] == meu_id)
            elif c in "0123":
                ch = g["rastro"] if opcoes.cor else c
                cor = _cor_id(int(c))
            else:
                ch, cor = " ", None
            linha.extend([(cor, ch)] * cel)
        celulas.append(linha)

    if estado == "CONTAGEM" and contagem is not None and altura > 0:
        msg = f" COMEÇA EM {contagem} " if contagem > 0 else " VAI! "
        msg = msg[:largura * cel]
        y = altura // 2
        inicio = max(0, (largura * cel - len(msg)) // 2)
        for k, ch in enumerate(msg):
            celulas[y][inicio + k] = (NEGRITO, ch)

    linhas = [cabecalho, g["se"] + g["h"] * (largura * cel) + g["sd"]]
    for linha in celulas:
        linhas.append(g["v"] + _render(linha, opcoes) + g["v"])
    linhas.append(g["ie"] + g["h"] * (largura * cel) + g["id"])

    if estado == "ESPECTADOR":
        if eliminado_por:
            rodape = (f" Você foi eliminado ({eliminado_por[0]}) no ciclo {eliminado_por[1]}. "
                      f"Assistindo. Q sai.")
        else:
            rodape = " Você foi eliminado. Assistindo. Q sai."
    else:
        vivos = {b["id"]: b["alive"] for b in ultimo_state.get("bikes", [])}
        itens = []
        for pid, nome in sorted(jogadores.items()):
            marca = g["marcador"] if vivos.get(pid, True) else g["morto"]
            texto = _pintar(marca, _cor_id(pid), opcoes) + " " + nome
            if pid == meu_id:
                texto += " (você)"
            itens.append(texto)
        rodape = " " + "  ".join(itens) + f"       {g['setas']} ou WASD virar   Q sair"
    linhas.append(rodape)
    return _finalizar(linhas, opcoes)


def _render(celulas: list[tuple], opcoes: Opcoes) -> str:
    if not opcoes.cor:
        return "".join(ch for _, ch in celulas)
    partes = []
    atual = None
    for cor, ch in celulas:
        if cor != atual:
            if atual is not None:
                partes.append(RESET)
            if cor is not None:
                partes.append(f"\x1b[{cor}m")
            atual = cor
        partes.append(ch)
    if atual is not None:
        partes.append(RESET)
    return "".join(partes)


def montar_fim(game_over_ou_motivo: dict | str, jogadores: dict[int, str],
               lobby_ok: bool | None, opcoes: Opcoes) -> list[str]:
    linhas = [" FIM DE PARTIDA"]
    if isinstance(game_over_ou_motivo, dict):
        go = game_over_ou_motivo
        w = go.get("winner")
        if go["result"] == "WIN" and w:
            nome = _pintar(w["name"], _cor_id(w["id"], negrito=True), opcoes)
            linhas.append(f" Vencedor: {nome}   (ciclo {go['cycle']})")
        else:
            linhas.append(f" EMPATE   (ciclo {go['cycle']})")
        elims = go.get("eliminations", [])
        if elims:
            linhas.append(" Eliminações:")
            largura_nome = max(len(e["name"]) for e in elims)
            for e in elims:
                ciclo = f"ciclo {e['cycle']}"
                linhas.append(f"   {e['name']:<{largura_nome}}   {ciclo:<10} {e['reason']}")
    else:
        linhas.append(f" PARTIDA ABORTADA: {game_over_ou_motivo}")
    linhas.append("")
    if lobby_ok:
        linhas.append(" Enter volta ao lobby      Q sai")
    elif lobby_ok is False:
        linhas.append(" Enter ou Q sai   (lobby: fora)")
    else:
        linhas.append(" Enter ou Q sai")
    return _finalizar(linhas, opcoes)


class ErroTerminal(Exception):
    pass


ENTRAR = "\x1b[?1049h\x1b[?25l"
SAIR = RESET + "\x1b[?25h\x1b[?1049l"

_MODO_VT = 0x0004


def ativar_ansi_windows() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        h = kernel32.GetStdHandle(-11)
        modo = wintypes.DWORD()
        if not kernel32.GetConsoleMode(h, ctypes.byref(modo)):
            return False
        if modo.value & _MODO_VT:
            return True
        if kernel32.SetConsoleMode(h, modo.value | _MODO_VT):
            return True
        os.system("")
        if kernel32.GetConsoleMode(h, ctypes.byref(modo)) and modo.value & _MODO_VT:
            return True
    except (AttributeError, OSError, ImportError):
        return False
    return False


class Terminal:
    def __init__(self, saida=None, tamanho=None, ativar_ansi=ativar_ansi_windows) -> None:
        self.saida = saida if saida is not None else sys.stdout
        self._tamanho = tamanho if tamanho is not None else self._tamanho_real
        self._ativar_ansi = ativar_ansi
        self._anteriores: list[str] | None = None
        self._tamanho_anterior: tuple[int, int] | None = None
        self.ativo = False

    @staticmethod
    def _tamanho_real() -> tuple[int, int]:
        t = shutil.get_terminal_size()
        return (t.columns, t.lines)

    def __enter__(self) -> Terminal:
        if not self._ativar_ansi():
            raise ErroTerminal("este terminal não suporta ANSI; use o Windows Terminal")
        reconfigurar = getattr(self.saida, "reconfigure", None)
        if reconfigurar is not None:
            try:
                reconfigurar(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
        self.ativo = True
        atexit.register(self._restaurar)
        self._escrever(ENTRAR)
        self._anteriores = None
        return self

    def __exit__(self, *exc) -> None:
        self._restaurar()

    def _restaurar(self) -> None:
        if not self.ativo:
            return
        self.ativo = False
        try:
            self._escrever(SAIR)
        except (OSError, ValueError):
            pass

    def _escrever(self, texto: str) -> None:
        self.saida.write(texto)
        self.saida.flush()

    def tamanho(self) -> tuple[int, int]:
        c, l = self._tamanho()
        return (int(c), int(l))

    def nova_tela(self) -> None:
        self._anteriores = None

    def mostrar(self, linhas: list[str]) -> None:
        colunas, n_linhas = self.tamanho()
        tam = (colunas, n_linhas)
        completo = self._anteriores is None or tam != self._tamanho_anterior
        anteriores = [] if completo else self._anteriores
        novas = []
        partes = ["\x1b[0m\x1b[2J"] if completo else []
        for i, linha in enumerate(linhas[:n_linhas]):
            limite = colunas - 1 if i == n_linhas - 1 else colunas
            linha = cortar_visivel(linha.replace("\n", " "), max(0, limite))
            novas.append(linha)
            if completo or i >= len(anteriores) or anteriores[i] != linha:
                partes.append(f"\x1b[{i + 1};1H{linha}{RESET}\x1b[K")
        for i in range(len(novas), min(len(anteriores), n_linhas)):
            partes.append(f"\x1b[{i + 1};1H\x1b[K")
        self._anteriores = novas
        self._tamanho_anterior = tam
        if partes:
            self._escrever("".join(partes))
