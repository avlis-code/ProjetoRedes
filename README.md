# Tron em rede: lobby + host autoritativo

Python 3.8+ só com a biblioteca padrão. Segue `protocolo/protocolo.md` (rascunho v0.1).

## Arquivos

| Arquivo | O que tem |
|---|---|
| `protocol.py` | Compartilhado por lobby, host e jogador: framing (JSON por linha, 16 KiB), `decodificar()` com validação de todos os tipos de mensagem, códigos de erro, tabelas de estados do apêndice A, constantes de tempo. |
| `tron.py` | Lógica pura do jogo, sem rede: arena, posições iniciais, fila de 2 viradas, colisões (`wall`, `trail`, `head_on`), eliminações pendentes (`disconnect`, `timeout`, `quit`), fim com `WIN`/`DRAW`, montagem de `STATE` e `GAME_OVER`. |
| `host.py` | Host autoritativo: aceita jogadores (`HELLO`/`WELCOME`/`REJECT`/`ROSTER`), `GAME_START`, ciclos a cada `tick_ms`, difunde `STATE`, detecta quedas (heartbeat de 1 s, timeout de 3 s), `GAME_OVER`, `GAME_ABORTED` no Ctrl+C. |
| `lobby.py` | Servidor de lobby (seções 3.1 e 4). Adaptado do lobby do colega para seguir o protocolo. |
| `lobby_link.py` | Lado cliente do lobby: `ConexaoLobby` (pedido → resposta), `LobbyCliente` (usado pelo host, numa thread própria) e `LobbyStub` (para rodar sem lobby). |
| `cliente_teste.py` | Jogador de teste: conecta direto no host, manda `HELLO` e dirige com um bot. Simula falhas. |
| `bot.py`, `tela.py` | Piloto automático e desenho da arena no terminal. |
| `testes/` | `test_tron.py` e `test_protocol.py` (sem rede), `test_integracao.py` e `test_lobby.py` (localhost). |

## Rodar com o lobby (fluxo completo)

```bash
python3 lobby.py --porta 5000
python3 host.py --nome Ana --porta 6000 --lobby-ip 192.168.0.5 --mostrar
python3 cliente_teste.py --nome Beto --lobby-ip 192.168.0.5
python3 cliente_teste.py --nome Caio --lobby-ip 192.168.0.5 --mostrar
```

`192.168.0.5` é o IP da máquina do lobby (na mesma máquina, `127.0.0.1`). O cliente pede a lista ao lobby, entra na primeira sala aberta (ou na `--partida ID`) e conecta no `host_ip:host_port` que veio no `JOIN_OK`.

No terminal do host aparece a sala a cada mudança. Digite `iniciar` (precisa de 3 ou mais) ou `cancelar`. Com `--iniciar-com N` o início é automático.

Se o host e o lobby estiverem na mesma máquina, o lobby entrega a cada jogador o IP da máquina pelo qual aquele jogador chegou ao lobby, então funciona sem configuração. `--advertise-ip` só é necessário se isso não servir (ex.: máquina com várias redes).

**Lobby cai no meio da partida:** mate o `lobby.py` com Ctrl+C depois do início. O jogo segue; host e jogadores só registram "conexão com o lobby perdida" no log, e o host avisa que não conseguiu mandar o `END_MATCH`.

## Rodar sem lobby (stub)

Sem `--lobby-ip`, o host usa o `LobbyStub`. Em 4 terminais:

```bash
python3 host.py --porta 6000 --nome Ana --iniciar-com 4 --mostrar
python3 cliente_teste.py --host 127.0.0.1 --porta 6000 --nome Beto
python3 cliente_teste.py --host 127.0.0.1 --porta 6000 --nome Caio --mostrar
python3 cliente_teste.py --host 127.0.0.1 --porta 6000 --nome Duda
```

Em máquinas diferentes, troque `127.0.0.1` pelo IP da máquina do host. O host escuta em `0.0.0.0` (mude com `--ip`).

Aqui o cliente conecta direto em `--host`/`--porta`. O host começa sozinho quando a sala tem `--iniciar-com` jogadores (o host conta), ou espera `iniciar` se a opção não for dada. Com `--jogadores Beto,Caio,Duda` ele só aceita esses nomes e começa quando todos chegarem.

No fim, host e jogadores imprimem uma linha `RESULTADO ...` com vencedor, eliminações e um hash da arena final. Se as linhas forem iguais, todos chegaram ao mesmo resultado (bom para o relatório).

Outras opções do host: `--tick-ms 100`, `--start-in-ms 3000`, `--largura 40 --altura 20`, `--max-jogadores 3|4`, `--entrada bot|nenhuma|teclado` (moto do host; `teclado` = W A S D, só Linux/macOS), `--seed`, `--log arquivo`, `-v`.

## Cenários de falha (para o relatório)

```bash
python3 cliente_teste.py --nome Beto --cair-no-ciclo 30      # fecha o socket: host elimina com "disconnect"
python3 cliente_teste.py --nome Caio --travar-no-ciclo 30    # para de responder: "timeout" após 3 s
python3 cliente_teste.py --nome Duda --sair-no-ciclo 30      # manda BYE: "quit"
```

Para ver o `timeout` acontecer antes de a moto travada bater na parede, use uma arena maior (`--largura 80 --altura 60`).

Queda do host: Ctrl+C (ou `kill`) no host manda `GAME_ABORTED host_quit` a todos. Se o processo do host travar ou a máquina cair, os jogadores mostram "conexão com o host perdida" após 3 s.

## Testes

```bash
python3 -m unittest discover -s testes -v
```

62 testes, cerca de 15 s (os de integração sobem processos reais em localhost, inclusive a queda do lobby no meio da partida).

## Como o host fala com o lobby

O host só conhece a interface `LigacaoLobby` (`lobby_link.py`). O `LobbyCliente`:
- faz `REGISTER` e `CREATE_MATCH` de forma síncrona no início (sem lobby não há partida: o host sai com erro);
- depois disso, manda tudo por uma fila atendida por uma thread própria, então o loop do jogo nunca espera o lobby, exceto no `START_MATCH` (no máx. 2 s, e inicia mesmo sem resposta);
- manda `PING` a cada 10 s de silêncio (o lobby derruba clientes calados por 60 s);
- se o lobby cair, registra uma vez no log e descarta os pedidos seguintes.

## Decisões tomadas na implementação (o protocolo não dizia)

1. **Viradas inválidas não gastam o ciclo.** Se a fila tem `[inversão, UP]`, a inversão é descartada e o `UP` é aplicado no mesmo ciclo. Continua valendo "no máximo uma direção aplicada por ciclo".
2. **Moto eliminada fica na última célula válida.** A célula do choque (parede, rastro ou `head_on`) não é pintada, e no `head_on` a célula disputada fica vazia.
3. **Erro antes do HELLO fecha a conexão**, qualquer que seja (`BAD_JSON`, `UNEXPECTED`...), sempre depois de mandar o `ERROR`.
4. **Jogador que não lê** (mais de 256 KiB pendentes no host) é tratado como `timeout`, para não travar o host.
5. **A moto do host não tem atraso de rede** (a entrada é local), então ele leva vantagem. Vale citar no relatório.
6. Com início automático, `MATCH_FULL` quase nunca aparece (a sala cheia já começa). Com início manual ele aparece normalmente.
7. **Heartbeat com o lobby:** clientes mandam `PING` após 10 s sem falar com o lobby, e o lobby fecha conexões sem nenhuma mensagem por 60 s (`--inatividade`). O protocolo deixava isso como opcional.
8. **Host e lobby na mesma máquina:** se o host chegou ao lobby por `127.0.0.1` sem `advertise_ip`, o `JOIN_OK` leva o IP local do lobby visto por quem pergunta (ideia do lobby original do colega).
