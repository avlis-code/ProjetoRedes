#!/bin/sh
# Uso: scripts/lobby.sh [porta]
cd "$(dirname "$0")/.." || exit 1
PORTA="${1:-5000}"
python3 rede.py ip
echo "Passe um dos IPs acima para os colegas. Lobby na porta $PORTA."
exec python3 lobby.py --porta "$PORTA"
