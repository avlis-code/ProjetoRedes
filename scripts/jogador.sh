#!/bin/sh
# Uso: scripts/jogador.sh IP_DO_LOBBY NOME [opções extras do cliente_teste.py]
# Ex.: scripts/jogador.sh 192.168.0.5 Beto --cair-no-ciclo 30
cd "$(dirname "$0")/.." || exit 1
[ -z "$2" ] && { echo "Uso: $0 IP_DO_LOBBY NOME [opções]"; exit 1; }
LOBBY="$1"
NOME="$2"
shift 2
python3 rede.py testar "$LOBBY" 5000 --vezes 3 || exit 1
exec python3 cliente_teste.py --lobby-ip "$LOBBY" --nome "$NOME" --mostrar \
    --log "jogador_$NOME.log" -v "$@"
