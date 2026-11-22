#!/usr/bin/env python3
"""Diagnóstico de rede para rodar o jogo em várias máquinas.

    python3 rede.py ip                       # IPs desta máquina para passar aos colegas
    python3 rede.py escutar --porta 6000     # abre a porta em 0.0.0.0 e responde PING
    python3 rede.py testar 192.168.0.10 6000 # de outra máquina: conecta, mede RTT, explica o erro

O `testar` manda PING e espera PONG, então também funciona contra o lobby e
contra um host aguardando jogadores (os dois aceitam PING antes de qualquer
outra mensagem).
"""

from __future__ import annotations

import argparse
import platform
import socket
import statistics
import sys
import time

import protocol as P


def ips_locais() -> list[str]:
    ips = set()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # UDP não envia nada; só escolhe a interface de saída
        ips.add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def em_wsl() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def cmd_ip(_args) -> int:
    ips = ips_locais()
    print(f"Máquina: {socket.gethostname()} ({platform.system()})")
    if not ips:
        print("Nenhum IP de rede encontrado: a máquina está conectada no Wi-Fi/cabo?")
        return 1
    for ip in ips:
        print(f"  {ip}")
    if em_wsl():
        print("Atenção: isto é WSL. O IP acima é da rede virtual do WSL e outras máquinas "
              "não o alcançam. Rode lobby e host no Python do Windows, ou ative o "
              "networkingMode=mirrored no .wslconfig.")
    return 0


def cmd_escutar(args) -> int:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", args.porta))
    except OSError as e:
        print(f"Não consegui abrir a porta {args.porta}: {e} (já tem lobby/host rodando nela?)")
        return 1
    srv.listen(8)
    print(f"Escutando em 0.0.0.0:{args.porta}. IPs desta máquina: {', '.join(ips_locais()) or '?'}")
    print("Na outra máquina: python3 rede.py testar <um dos IPs> "
          f"{args.porta}   (Ctrl+C para sair)")
    try:
        while True:
            conn, end = srv.accept()
            print(f"Conexão de {end[0]}:{end[1]}")
            leitor = P.LeitorLinhas()
            conn.settimeout(10)
            try:
                while True:
                    dados = conn.recv(4096)
                    if not dados:
                        break
                    for linha in leitor.alimentar(dados):
                        msg = P.decodificar(linha)
                        if msg["type"] == "PING":
                            conn.sendall(P.codificar(P.pong(msg["t"])))
            except (OSError, P.ErroProtocolo):
                pass
            finally:
                conn.close()
    except KeyboardInterrupt:
        return 0
    finally:
        srv.close()


def explicar(e: OSError, ip: str, porta: int) -> str:
    if isinstance(e, ConnectionRefusedError):
        return (f"A máquina {ip} respondeu, mas nada escuta na porta {porta}. "
                "Confira se o lobby/host está rodando e a porta está certa.")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return (f"Sem resposta de {ip}:{porta}. Quase sempre é firewall (no Windows, libere "
                "o Python ou rode scripts\\liberar_firewall_windows.bat como administrador), "
                "máquinas em redes diferentes, rede de visitantes com isolamento de clientes, "
                "ou servidor dentro do WSL2.")
    if isinstance(e, socket.gaierror):
        return f"Endereço inválido: {ip}"
    return f"Erro de rede: {e}"


def cmd_testar(args) -> int:
    ip, porta = args.ip, args.porta
    try:
        t0 = time.monotonic()
        s = socket.create_connection((ip, porta), timeout=3)
        conexao_ms = (time.monotonic() - t0) * 1000
    except OSError as e:
        print(f"FALHOU: {explicar(e, ip, porta)}")
        return 1
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(3)
    print(f"Conectou em {ip}:{porta} em {conexao_ms:.1f} ms (handshake TCP)")
    leitor = P.LineReader(s)
    rtts = []
    try:
        for _ in range(args.vezes):
            t = int(time.monotonic() * 1000)
            P.send_msg(s, P.ping(t))
            while True:
                msg = leitor.read_msg()
                if msg is None:
                    raise ConnectionError("o outro lado fechou a conexão")
                if msg["type"] == "PONG" and msg["t"] == t:
                    break
            rtts.append(time.monotonic() * 1000 - t)
            time.sleep(0.2)
    except (OSError, P.ErroProtocolo, ConnectionError) as e:
        print(f"Conectou, mas o PING/PONG falhou: {e}. É um lobby/host deste projeto do outro lado?")
        return 1
    finally:
        s.close()
    print(f"PING/PONG: {len(rtts)} amostras, mediana {statistics.median(rtts):.1f} ms, "
          f"máximo {max(rtts):.1f} ms")
    print(f"Com ciclos de 100 ms, o RTT é {statistics.median(rtts) / 100:.1%} de um ciclo.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diagnóstico de rede do Tron")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ip", help="mostra os IPs desta máquina")
    e = sub.add_parser("escutar", help="abre uma porta e responde PING")
    e.add_argument("--porta", type=int, default=6000)
    t = sub.add_parser("testar", help="conecta numa máquina e mede o RTT")
    t.add_argument("ip")
    t.add_argument("porta", type=int)
    t.add_argument("--vezes", type=int, default=5)
    a = ap.parse_args(argv)
    return {"ip": cmd_ip, "escutar": cmd_escutar, "testar": cmd_testar}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
