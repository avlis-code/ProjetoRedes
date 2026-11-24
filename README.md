# Tron em rede: lobby + host autoritativo

**Integrantes**

| Nome | Matrícula |
|---|---|
| Mateus Santos da Silva | 190018011 |
| Matheus Rodrigues da Silva | 222007012 |
| Pablo Cunha de Jesus | 222014910 |
| Willian Adrian Cardoso Paz Leandro | 241034596 |

Python 3.8+ só com a biblioteca padrão. Segue `protocolo/protocolo-v0.3.md`.

## Arquivos

| Arquivo | O que tem |
|---|---|
| `protocol.py` | Compartilhado por lobby, host e jogador: framing (JSON por linha, 16 KiB), `decodificar()` com validação de todos os tipos de mensagem, códigos de erro, tabelas de estados do apêndice A, constantes de tempo. |
| `tron.py` | Lógica pura do jogo, sem rede: arena, posições iniciais, fila de 2 viradas, colisões (`wall`, `trail`, `head_on`), eliminações pendentes (`disconnect`, `timeout`, `quit`), fim com `WIN`/`DRAW`, montagem de `STATE` e `GAME_OVER`. |
| `host.py` | Host autoritativo: aceita jogadores (`HELLO`/`WELCOME`/`REJECT`/`ROSTER`), `GAME_START`, ciclos a cada `tick_ms`, difunde `STATE`, detecta quedas (heartbeat de 1 s, timeout de 3 s), `GAME_OVER`, `GAME_ABORTED` no Ctrl+C. |
| `lobby.py` | Servidor de lobby (seções 3.1 e 4). Adaptado do lobby do colega para seguir o protocolo. |
| `lobby_link.py` | Lado cliente do lobby: `ConexaoLobby` (pedido → resposta), `LobbyCliente` (usado pelo host, numa thread própria) e `LobbyStub` (para rodar sem lobby). |
| `host_tela.py` | Moto do host pelo teclado (`--entrada teclado`), no Windows e no Linux, com a mesma tela cheia do jogador. |
| `jogador.py` | Jogador com teclado e tela (parte 3, seção 6 e apêndice D do protocolo v0.3): menu de salas do lobby, sala de espera, jogo com setas/WASD, tela de fim e volta ao menu. Thread própria só para o `PING` ao lobby. |
| `entrada.py` | Teclado sem biblioteca externa: `msvcrt` no Windows, `termios` no Linux/macOS. Decodifica setas, WASD, Enter, Esc. |
| `ui.py` | Telas por escapes ANSI (sem `curses`): `Terminal` em tela cheia que reescreve só as linhas que mudaram, e as telas de menu, sala, jogo e fim. Modos `--ascii` e `--sem-cor`. |
| `cliente_teste.py` | Jogador de teste: conecta direto no host, manda `HELLO` e dirige com um bot. Simula falhas. |
| `bot.py`, `tela.py` | Piloto automático e desenho da arena no terminal. |
| `cenarios.py` | Cenários de falha do relatório: sobe lobby, host e jogadores, provoca a falha (socket fechado, travamento, `BYE`, `SIGTERM`, `SIGKILL`, `SIGSTOP`) e mede quem detectou o quê e em quanto tempo. |
| `rede.py` | Diagnóstico para várias máquinas: IPs locais, porta de teste e `PING`/`PONG` com explicação do erro (firewall, porta fechada, WSL). |
| `scripts/` | `lobby`, `host` e `jogador` em `.sh` e `.bat`, e `liberar_firewall_windows.bat`. |
| `relatorio/` | Relatório; cada parte escreve a sua seção. |
| `testes/` | `test_tron.py`, `test_protocol.py`, `test_entrada.py` e `test_ui.py` (sem rede); `test_jogador.py` e `test_host_tela.py` (jogador e moto do host com teclado, terminal, host e lobby falsos, em `falsos.py`); `test_integracao.py`, `test_lobby.py`, `test_jogador_integracao.py` e `test_falhas.py` (processos reais em localhost). |

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

## Jogar com o teclado (`jogador.py`)

```bash
python3 jogador.py --nome Beto --lobby-ip 192.168.0.5          # menu de salas do lobby
python3 jogador.py --nome Beto --host 192.168.0.10 --porta 6000  # direto no host, sem lobby
```

Sem `--nome` (ou sem `--lobby-ip` e `--host`), o programa pergunta antes de entrar em tela cheia. No Windows use `python` no lugar de `python3`, de preferência no Windows Terminal.

| Tela | Teclas |
|---|---|
| Menu de salas (atualiza a cada 3 s) | ↑/↓ ou W/S escolhem, Enter entra, R atualiza, Q sai |
| Sala de espera | Q sai (manda `BYE` e volta ao menu) |
| Contagem e jogo | setas ou WASD viram a moto, Q sai (`BYE`) |
| Fim | Enter volta ao menu (se o lobby estiver no ar), Q sai |

Ctrl+C em qualquer tela manda `BYE` ao host e devolve o terminal ao normal. A barra do jogo mostra `lobby: ok` ou `lobby: fora`: matar o lobby no meio da partida só muda isso.

Outras opções: `--partida ID` (entra direto na sala, sem menu), `--ascii` (só caracteres ASCII, para consoles que não mostram `█ ▲ ◀`), `--sem-cor` (o rastro vira o dígito do id), `--celula 1|2` (largura da célula; o padrão escolhe sozinho), `--log arquivo` (padrão `jogador_<nome>.log`; nada é impresso na tela durante o jogo) e `-v`. O terminal precisa de pelo menos `altura + 4` linhas e `largura + 2` colunas (42 × 24 na arena padrão); menor que isso, a arena dá lugar a um aviso e a partida continua.

No fim, depois de restaurar o terminal, o jogador imprime uma linha `RESULTADO ...` (igual à do host) ou `ABORTADA <motivo>` por partida. Código de saída: 0 partida completa (a última), 1 abortada, recusada ou saiu, 2 terminal sem suporte.

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

Outras opções do host: `--tick-ms 100`, `--start-in-ms 3000`, `--largura 40 --altura 20`, `--max-jogadores 3|4`, `--entrada bot|nenhuma|teclado`, `--seed`, `--log arquivo`, `-v`.

**Jogar no host:** com `--entrada teclado` (Windows, Linux e macOS) a moto do host vai pelas setas ou WASD, e no `GAME_START` o terminal do host vira a mesma tela do jogador (contagem, arena, legenda, `lobby: ok/fora`). Q ou Ctrl+C encerram a partida para todos (`GAME_ABORTED host_quit`); no fim aparece o resultado e Enter ou Q saem. Antes do início o terminal continua normal para `iniciar`/`cancelar`. Nesse modo o log vai para `host_<nome>.log`, e `--ascii`/`--sem-cor` valem como no jogador. Sem terminal interativo (ex.: saída redirecionada), o host segue sem tela e a moto anda reto.

**Tamanho e ritmo:** os scripts `scripts/host.*` usam arena 56×24 e 150 ms por ciclo (~6,7 casas/s) e o teclado. Para mudar, edite `--largura`, `--altura` e `--tick-ms` neles; os jogadores recebem tudo no `GAME_START`. A tela precisa de `altura + 4` linhas e `largura + 2` colunas (com célula dupla, `2 × largura + 4`): 56×24 pede 116×28, que cabe no padrão de 120×30 do Windows Terminal, PowerShell e `cmd.exe`.

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

157 testes, cerca de 2 min. Os de integração sobem processos reais em localhost; `test_falhas.py` roda todos os cenários de `cenarios.py` e leva a maior parte do tempo, porque os timeouts são os de verdade (3 s). Para pular esses: `python3 -m unittest testes.test_tron testes.test_protocol testes.test_entrada testes.test_ui testes.test_jogador testes.test_host_tela testes.test_integracao testes.test_lobby testes.test_jogador_integracao`.

No Windows, `host_sai` do `test_falhas.py` falha: o cenário manda `SIGTERM`, que no Windows mata o processo na hora (`TerminateProcess`), então o host não chega a mandar o `GAME_ABORTED`. É limitação do cenário, não do host; no Linux ele passa.

Os testes do jogador e da tela do host usam teclado e terminal falsos: não exercitam o `msvcrt`, o `termios` nem o ANSI do console. Isso se confere à mão (roteiro no apêndice D.8 do protocolo v0.3).

## Cenários de falha e várias máquinas

```bash
python3 cenarios.py --listar        # 11 cenários
python3 cenarios.py                 # roda todos; logs e resumo.md em resultados/<data_hora>/
python3 cenarios.py host_trava      # só um
```

O `resumo.md` traz as checagens e as medidas (tempo de detecção, ciclo da eliminação, RTT) prontas para o relatório. `host_morre`, `host_trava` e `lobby_trava` usam sinais do Unix e são pulados no Windows.

Em várias máquinas (roteiro completo em `relatorio/relatorio.md`, seção 6.4):

```bash
python3 rede.py ip                          # em cada máquina
scripts/lobby.sh                            # máquina do lobby
scripts/host.sh 192.168.0.5 Ana             # máquina do host
scripts/jogador.sh 192.168.0.5 Beto         # demais (jogador.py, com teclado)
python3 rede.py testar 192.168.0.5 5000     # se algo não conectar
```

No Windows use os `.bat` e rode `scripts\liberar_firewall_windows.bat` como administrador. Os scripts de jogador abrem o `jogador.py` (teclado e tela) e aceitam as opções dele, ex.: `scripts/jogador.sh 192.168.0.5 Beto --ascii`. Para os cenários de falha (`--cair-no-ciclo` etc.) use o `cliente_teste.py` diretamente.

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
5. **A moto do host não tem atraso de rede** (a entrada é local, com `--entrada teclado`), então ele leva vantagem. Vale citar no relatório.
6. Com início automático, `MATCH_FULL` quase nunca aparece (a sala cheia já começa). Com início manual ele aparece normalmente.
7. **Heartbeat com o lobby:** clientes mandam `PING` após 10 s sem falar com o lobby, e o lobby fecha conexões sem nenhuma mensagem por 60 s (`--inatividade`). O protocolo deixava isso como opcional.
8. **Host e lobby na mesma máquina:** se o host chegou ao lobby por `127.0.0.1` sem `advertise_ip`, o `JOIN_OK` leva o IP local do lobby visto por quem pergunta (ideia do lobby original do colega).
