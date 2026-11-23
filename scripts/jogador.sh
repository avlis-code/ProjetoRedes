#!/bin/sh
# Uso: scripts/jogador.sh IP_DO_LOBBY NOME [opções extras do jogador.py]
# Ex.: scripts/jogador.sh 192.168.0.5 Beto --ascii
# Jogador com teclado e tela (setas ou WASD). Os cenários de falha
# (--cair-no-ciclo e semelhantes) continuam no cliente_teste.py.
cd "$(dirname "$0")/.." || exit 1
[ -z "$2" ] && { echo "Uso: $0 IP_DO_LOBBY NOME [opções]"; exit 1; }
LOBBY="$1"
NOME="$2"
shift 2
python3 rede.py testar "$LOBBY" 5000 --vezes 3 || exit 1
exec python3 jogador.py --lobby-ip "$LOBBY" --nome "$NOME" \
    --log "jogador_$NOME.log" -v "$@"
