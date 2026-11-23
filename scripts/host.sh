#!/bin/sh
# Uso: scripts/host.sh IP_DO_LOBBY [nome] [porta]   (no terminal: iniciar | cancelar)
cd "$(dirname "$0")/.." || exit 1
[ -z "$1" ] && { echo "Uso: $0 IP_DO_LOBBY [nome] [porta]"; exit 1; }
NOME="${2:-Ana}"
PORTA="${3:-6000}"
python3 rede.py testar "$1" 5000 --vezes 3 || exit 1
exec python3 host.py --nome "$NOME" --porta "$PORTA" --lobby-ip "$1" \
    --largura 56 --altura 24 --tick-ms 150 --entrada teclado \
    --log "host_$NOME.log" -v
