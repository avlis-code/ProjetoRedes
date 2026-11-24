# Cenários de falha (23/11/2026 23:34)

| Cenário | O que foi feito | Resultado |
|---|---|---|
| `completa` | Partida completa com 4 jogadores via lobby | OK |
| `sai_da_sala` | Jogador cai antes do início | OK |
| `desconexao` | Jogador fecha o socket no meio da partida | OK |
| `timeout` | Jogador trava (não lê nem manda nada) | OK |
| `saida` | Jogador sai com BYE no meio da partida | OK |
| `host_sai` | Host encerrado com Ctrl+C/SIGTERM | OK |
| `host_morre` | Processo do host morto com SIGKILL | OK |
| `host_trava` | Processo do host congelado com SIGSTOP | OK |
| `lobby_cai` | Lobby morto com a partida em andamento | OK |
| `lobby_trava` | Lobby congelado com SIGSTOP com a partida em andamento | OK |
| `lobby_fora` | Lobby fora do ar antes de criar a partida | OK |

## completa: Partida completa com 4 jogadores via lobby

- [x] Ana termina com código 0
- [x] Beto termina com código 0
- [x] Caio termina com código 0
- [x] Duda termina com código 0
- [x] Ana, Beto, Caio, Duda terminam com a mesma linha RESULTADO (vencedor, eliminações e hash da arena)
- [x] host avisa o fim ao lobby (END_MATCH)
- [x] lobby tira a partida da lista

Medidas:

- RESULTADO WIN vencedor=Beto ciclo=64 eliminacoes=Caio@34:trail,Ana@47:trail,Duda@64:trail arena=1b8afff87eae
- RTT jogador ↔ host (PING/PONG): 9 amostras, mediana 0 ms, máximo 1 ms
- TURN aplicados com atraso de 1 ciclo ou mais: 0

## sai_da_sala: Jogador cai antes do início

- [x] host remove Beto da sala ao perceber a conexão fechada
- [x] lobby recebe MATCH_UPDATE e mostra só Ana (mostra ['Ana'])
- [x] Ana, Caio, Duda, Edu terminam com a mesma linha RESULTADO (vencedor, eliminações e hash da arena)

Medidas:

- host percebeu a saída de Beto em 1 ms

## desconexao: Jogador fecha o socket no meio da partida

- [x] Beto fecha o socket de repente (falha simulada)
- [x] Ana, Caio, Duda terminam com a mesma linha RESULTADO (vencedor, eliminações e hash da arena)
- [x] Beto eliminado com reason disconnect ((11, 'disconnect'))
- [x] eliminação anunciada no ciclo seguinte à queda
- [x] partida continua até o fim com os outros

Medidas:

- Beto caiu depois do STATE 10 e foi eliminado no ciclo 11

## timeout: Jogador trava (não lê nem manda nada)

- [x] Ana, Beto, Duda terminam com a mesma linha RESULTADO (vencedor, eliminações e hash da arena)
- [x] Caio eliminado com reason timeout ((15, 'timeout'))
- [x] host detecta o silêncio em 2.00 s (esperado entre 2 e 3 s)

Medidas:

- Caio parou no STATE 5; host declarou timeout 2.00 s depois e a eliminação saiu no ciclo 15

## saida: Jogador sai com BYE no meio da partida

- [x] Ana, Beto, Caio terminam com a mesma linha RESULTADO (vencedor, eliminações e hash da arena)
- [x] Duda eliminado com reason quit ((11, 'quit'))
- [x] host registra o BYE

## host_sai: Host encerrado com Ctrl+C/SIGTERM

- [x] Beto: ABORTADA host encerrou: host_quit no ciclo 10
- [x] Beto detecta em 0 ms (esperado entre 0 e 1000 ms)
- [x] Caio: ABORTADA host encerrou: host_quit no ciclo 10
- [x] Caio detecta em 0 ms (esperado entre 0 e 1000 ms)
- [x] Duda: ABORTADA host encerrou: host_quit no ciclo 10
- [x] Duda detecta em 0 ms (esperado entre 0 e 1000 ms)
- [x] ninguém declara vencedor
- [x] host manda GAME_ABORTED e sai com código 0
- [x] lobby tira a partida da lista

## host_morre: Processo do host morto com SIGKILL

- [x] Beto: ABORTADA conexão com o host perdida (fechada)
- [x] Beto detecta em 2 ms (esperado entre 0 e 1000 ms)
- [x] Caio: ABORTADA conexão com o host perdida (fechada)
- [x] Caio detecta em 1 ms (esperado entre 0 e 1000 ms)
- [x] Duda: ABORTADA conexão com o host perdida (fechada)
- [x] Duda detecta em 1 ms (esperado entre 0 e 1000 ms)
- [x] ninguém declara vencedor
- [x] lobby percebe a conexão do host fechada e limpa a lista

## host_trava: Processo do host congelado com SIGSTOP

- [x] Beto: ABORTADA conexão com o host perdida (3 s sem sinal)
- [x] Beto detecta em 3009 ms (esperado entre 2900 e 3600 ms)
- [x] Caio: ABORTADA conexão com o host perdida (3 s sem sinal)
- [x] Caio detecta em 3009 ms (esperado entre 2900 e 3600 ms)
- [x] Duda: ABORTADA conexão com o host perdida (3 s sem sinal)
- [x] Duda detecta em 3009 ms (esperado entre 2900 e 3600 ms)
- [x] ninguém declara vencedor
- [x] lobby continua listando a partida: a conexão TCP do host não fechou

## lobby_cai: Lobby morto com a partida em andamento

- [x] Ana termina a partida normalmente
- [x] Beto termina a partida normalmente
- [x] Caio termina a partida normalmente
- [x] Duda termina a partida normalmente
- [x] Ana, Beto, Caio, Duda terminam com a mesma linha RESULTADO (vencedor, eliminações e hash da arena)
- [x] host só registra a perda do lobby no log (ao tentar o END_MATCH)

Medidas:

- RESULTADO WIN vencedor=Ana ciclo=45 eliminacoes=Caio@34:trail,Duda@43:trail,Beto@45:trail arena=6b5bc76ef68a

## lobby_trava: Lobby congelado com SIGSTOP com a partida em andamento

- [x] Beto termina a partida normalmente
- [x] Caio termina a partida normalmente
- [x] Duda termina a partida normalmente
- [x] host termina com código 0
- [x] Ana, Beto, Caio, Duda terminam com a mesma linha RESULTADO (vencedor, eliminações e hash da arena)
- [x] host espera no máximo o prazo do lobby (5 s) pelo END_OK e sai (6.0 s depois dos jogadores)

Medidas:

- RESULTADO WIN vencedor=Ana ciclo=45 eliminacoes=Caio@34:trail,Duda@43:trail,Beto@45:trail arena=6b5bc76ef68a

## lobby_fora: Lobby fora do ar antes de criar a partida

- [x] host sem lobby não cria a partida e sai com código 1
- [x] jogador sem lobby desiste: ABORTADA lobby: não conectou no lobby 127.0.0.1:60509 ([Errno 111] Connection refused)
