# Tron em rede: relatório

Opção 1 do trabalho: Tron com host autoritativo, TCP em tudo, lobby cliente/servidor.
A especificação completa está em `protocolo/protocolo-v2.md`; este relatório explica as decisões e mostra os testes.

| Parte | Responsável | Seção |
|---|---|---|
| 1. Lobby e framing | _preencher_ | 2 |
| 2. Host autoritativo | _preencher_ | 3 |
| 3. Cliente e jogador | _preencher_ | 4 |
| 4. Falhas, testes e relatório | Matheus Rodrigues | 5 e 6 |

## 1. Arquitetura

_Preencher em conjunto: diagrama da seção 1 do protocolo, papel de cada processo e por que o lobby sai de cena depois do `JOIN_OK`._

## 2. Lobby e framing

_Responsável pela parte 1. Sugestão: formato JSON por linha, limite de 16 KiB, como o `LeitorLinhas` trata meia mensagem e mensagens grudadas, máquina de estados do lobby (tabela 4.1), uso do IP de origem do `accept()`._

## 3. Host autoritativo

_Responsável pela parte 2. Sugestão: loop de ciclos com `selectors`, ordem de cálculo do ciclo, colisões e empate, fila de 2 viradas, justificativa de TCP e de 10 ciclos por segundo (decisões D1 a D5)._

## 4. Cliente e jogador

_Responsável pela parte 3. Sugestão: máquina de estados do jogador (seção 6), entrada de teclado, desenho da tela, configuração de IPs e portas._

## 5. Detecção e tratamento de falhas

### 5.1 Os dois tipos de falha

Uma conexão TCP pode morrer de duas formas, e cada uma pede uma técnica diferente.

1. **Conexão fechada.** O processo terminou (mesmo morto com `kill -9`) ou chamou `close()`. O sistema operacional manda FIN ou RST, e o `recv()` do outro lado devolve `b""` ou levanta `ConnectionResetError`. A detecção é imediata.
2. **Silêncio.** O processo travou, a máquina congelou ou a rede caiu. Nada é enviado, então o TCP não avisa. O keepalive do TCP existe, mas o padrão dos sistemas é esperar 2 horas antes de testar, inútil para um jogo.

Para o segundo caso usamos heartbeat na camada de aplicação, com a mesma regra nos dois sentidos da conexão jogador ↔ host:

| Regra | Valor | Motivo |
|---|---|---|
| Manda `PING` se não enviou nada há | 1 s | O jogador só fala quando aperta tecla; sem isso, ficaria mudo por vários segundos. |
| Qualquer mensagem recebida conta como sinal de vida | | O host manda `STATE` a cada 100 ms, então durante o jogo o próprio `STATE` é o heartbeat do host. |
| Conexão morta após silêncio de | 3 s | Três heartbeats perdidos seguidos. Um soluço de Wi-Fi de 1 ou 2 s não elimina ninguém; 3 s ainda é curto para quem está jogando (30 ciclos). |
| Jogador que não lê (mais de 256 KiB pendentes no host) | timeout | Sem isso, um processo travado com o socket aberto faria o buffer do host crescer sem limite. |

### 5.2 Quem decide quem caiu

Só o host. Os jogadores nunca eliminam uma moto por conta própria; eles só desenham o `STATE`. Quando o host detecta a falha de um jogador, marca a eliminação e ela entra no **próximo** ciclo calculado, anunciada no `STATE` com `reason` `disconnect`, `timeout` ou `quit`. Como todos leem o mesmo `STATE`, não existe como dois jogadores discordarem sobre o ciclo da eliminação. Esse é o principal ganho do host autoritativo sobre o lockstep, onde todos os pares precisariam chegar a um acordo.

A única decisão local de um jogador é "o host morreu", e ela sempre leva ao mesmo resultado: partida abortada, sem vencedor. Por isso não importa que cada jogador perceba em um instante um pouco diferente (no teste, 2971 ms para dois e 3021 ms para o terceiro).

Caso interessante: se só a rede do Beto cair, o Beto vê "conexão com o host perdida" e os outros veem "Beto eliminado por timeout". As duas visões são coerentes, porque o Beto não declara vencedor nenhum.

### 5.3 O que cada um faz ao detectar

| Quem detecta | Fase | Ação |
|---|---|---|
| Host, sobre um jogador | sala de espera | remove da sala, manda `ROSTER` aos outros e `MATCH_UPDATE` ao lobby |
| Host, sobre um jogador | contagem ou jogo | elimina a moto no próximo ciclo; a partida continua |
| Jogador, sobre o host | qualquer | mostra "conexão com o host perdida" e encerra, sem vencedor |
| Host saindo por vontade própria (Ctrl+C) | qualquer | manda `GAME_ABORTED host_quit` a todos antes de fechar |
| Qualquer um, sobre o lobby | durante a partida | só registra no log |

### 5.4 Independência do lobby

Tudo o que o jogo precisa do lobby é o par `host_ip:host_port`, entregue no `JOIN_OK`. Depois disso nenhuma mensagem do jogo passa por ele. O host fala com o lobby numa thread separada, por uma fila, então o loop de ciclos nunca espera o lobby. As duas únicas esperas são limitadas: no máximo 2 s pelo `START_OK` (a partida começa mesmo sem resposta) e no máximo 5 s pelo `END_OK`, quando a partida já acabou.

## 6. Testes

### 6.1 Como as falhas foram provocadas

O script `cenarios.py` sobe lobby, host e três jogadores como processos separados, cada um com seus sockets, provoca a falha no momento certo e confere nos logs o que cada participante concluiu. Os tempos de detecção são medidos pelos horários dos logs (todos na mesma máquina, então os relógios são o mesmo).

| Técnica | O que simula na vida real |
|---|---|
| Jogador fecha o socket (`--cair-no-ciclo`) | programa do jogador fecha ou dá erro |
| Jogador para de ler e de enviar (`--travar-no-ciclo`) | programa travado, ou a rede só dele caiu |
| Jogador manda `BYE` (`--sair-no-ciclo`) | jogador desiste |
| `SIGTERM` no host | Ctrl+C no host |
| `SIGKILL` no host | processo do host morto; o sistema fecha os sockets |
| `SIGSTOP` no host | host travado, máquina congelada ou cabo puxado: nada mais chega e nada fecha |
| `SIGKILL` / `SIGSTOP` no lobby | lobby morto / lobby travado no meio da partida |

O mesmo script roda dentro da suíte de testes (`testes/test_falhas.py`), então todo cenário do relatório é verificado automaticamente a cada mudança no código.

### 6.2 Resultados

Rodada de 22/11/2026, macOS, localhost, `tick_ms = 100` (200 no cenário de timeout), contagem de 1 s.

| Cenário | Falha | O que aconteceu | Detecção |
|---|---|---|---|
| Partida completa | nenhuma | 4 jogadores via lobby; host e jogadores imprimem a mesma linha de resultado (vencedor, eliminações e hash da arena); `END_MATCH` tira a partida do lobby | |
| Queda na sala | jogador morto antes do início | host tira o jogador da sala; o lobby passa a listar só o host | 0 ms |
| Desconexão | Beto fecha o socket depois do `STATE` 10 | Beto eliminado no ciclo 11 com `disconnect`; os outros 3 terminam com o mesmo resultado | ciclo seguinte |
| Timeout | Caio trava depois do `STATE` 5 | Caio eliminado no ciclo 20 com `timeout` (5 + 15 ciclos de 200 ms); os outros 3 concordam | 3,00 s |
| Saída | Duda manda `BYE` | Duda eliminado no ciclo 11 com `quit` | ciclo seguinte |
| Host sai | `SIGTERM` | todos recebem `GAME_ABORTED host_quit`; lobby limpa a lista | 0 ms |
| Host morre | `SIGKILL` | todos veem a conexão fechada e abortam sem vencedor; lobby limpa a lista | 4 ms |
| Host trava | `SIGSTOP` | todos abortam por "3 s sem sinal"; o lobby ainda lista a partida | 2971 a 3021 ms |
| Lobby cai | `SIGKILL` no lobby no meio do jogo | partida vai até o fim; todos com o mesmo resultado; o host só registra no log quando tenta o `END_MATCH` | |
| Lobby trava | `SIGSTOP` no lobby no meio do jogo | partida vai até o fim normalmente; o host sai 6 s depois dos jogadores (1 s de espera do fim + 5 s esperando o `END_OK`) | |
| Lobby fora do ar | lobby não existe | host não cria a partida (código 1); jogador desiste com a mensagem do erro | imediata |

RTT jogador ↔ host medido pelo `PING`/`PONG`: mediana 0 ms, máximo 1 ms em localhost. Nenhuma virada foi aplicada com atraso. Entre máquinas na mesma rede Wi-Fi o `rede.py testar` mede o valor real; _preencher com a medida feita nas máquinas do grupo_.

Observações que os testes revelaram:

1. **Host travado deixa uma partida fantasma no lobby.** Com `SIGSTOP` a conexão TCP do host com o lobby não fecha, então o lobby continua listando a partida até o seu próprio timeout de inatividade (60 s). Não afeta ninguém que está jogando, mas um jogador novo poderia tentar entrar e receber erro de conexão.
2. **A detecção de timeout no host varia entre 2 e 3 s após o travamento.** O relógio conta a partir do último byte recebido, e o jogador pode ter mandado o último `PING` até 1 s antes de travar.
3. **Um jogador travado anda reto até o timeout.** O host continua movendo a moto dele na última direção. Numa arena pequena ela pode bater na parede antes dos 3 s e ser eliminada por `wall`, e não por `timeout`. Por isso o cenário de timeout usa arena 80 × 40 e ciclos de 200 ms.

### 6.3 Perguntas sobre situações não demonstradas

**Dois jogadores caem no mesmo ciclo?** As duas eliminações entram no mesmo `STATE`. Se eram os dois últimos vivos, o resultado é `DRAW`.

**O host cai durante a contagem?** Igual a cair no jogo: os jogadores abortam pelo `recv` vazio ou pelo timeout de 3 s.

**O lobby cai antes de a partida começar?** Quem já recebeu o `JOIN_OK` continua conectado no host e a partida pode começar normalmente. Ninguém novo consegue descobrir a sala.

**Um jogador que caiu pode voltar?** Não. Um `HELLO` depois do `GAME_START` recebe `REJECT MATCH_STARTED`. Reconectar exigiria identificar o jogador antigo e devolver a moto, o que deixamos como trabalho futuro.

**E se a rede ficar lenta por mais de 3 s, sem cair?** O host elimina o jogador por `timeout` (falso positivo). É o preço de detectar rápido. Como só o host decide, todos continuam vendo a mesma coisa.

**Por que não usar o keepalive do TCP?** O padrão é 2 horas até o primeiro teste, e mudar exige opções diferentes em cada sistema operacional. O heartbeat na aplicação funciona igual em todos e ainda mede o RTT.

**Por que não migrar o host quando ele cai?** Todos precisariam ter o estado completo e concordar sobre quem assume e a partir de qual ciclo, que é justamente o problema de consenso que o host autoritativo evita. A especificação exige só o encerramento controlado (decisão D6).

### 6.4 Teste em várias máquinas

Roteiro usado:

1. Em cada máquina: `python3 rede.py ip` para descobrir o IP. No WSL2 o IP mostrado é de uma rede virtual que as outras máquinas não alcançam; nesse caso rode lobby e host no Python do Windows.
2. Na máquina do lobby: `scripts/lobby.sh` (ou `scripts\lobby.bat`). No Windows, rode antes `scripts\liberar_firewall_windows.bat` como administrador.
3. Na máquina do host: `scripts/host.sh IP_DO_LOBBY Ana`. O script testa o lobby antes de subir.
4. Nas outras: `scripts/jogador.sh IP_DO_LOBBY Beto`.
5. Se algo não conectar, `python3 rede.py testar IP PORTA` explica o motivo (porta fechada, firewall, rede diferente).

Para demonstrar a queda do lobby, feche o terminal do lobby com a partida em andamento. Para a queda de rede de um jogador, desligue o Wi-Fi da máquina dele: ele vê "host perdido" em 3 s e os outros veem a eliminação por `timeout`.

_Preencher com o resultado do teste real: máquinas e sistemas usados, RTT medido, problemas encontrados._

## 7. Conclusão

_Preencher em conjunto._
