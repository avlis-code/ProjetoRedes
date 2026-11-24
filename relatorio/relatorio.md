# Tron em rede: relatório

**Integrantes**

| Nome | Matrícula |
|---|---|
| Mateus Santos da Silva | 190018011 |
| Matheus Rodrigues da Silva | 222007012 |
| Pablo Cunha de Jesus | 222014910 |
| Willian Adrian Cardoso Paz Leandro | 241034596 |

Escolhemos a Opção 1 do trabalho: um Tron para 3 ou 4 jogadores, com host autoritativo, TCP em todas as conexões e um lobby cliente/servidor para as pessoas se encontrarem. O programa é todo em Python 3, só com a biblioteca padrão.

A especificação completa do protocolo está em `protocolo/protocolo-v0.3.md`. Este relatório trata sobre o desenvolvimento: o que cada um faz, por que escolhemos cada protocolo e como testamos.

O protocolo passou por três versões, e cada uma nasceu de algo que aprendemos. A v0.1 foi o rascunho, escrito antes de qualquer código. A v0.2 registrou o que só descobrimos ao implementar o host e o lobby (por exemplo, que uma virada inválida não deve "gastar" o ciclo). A v0.3 chegou com o jogador de teclado: não mudou nenhuma mensagem, mas passou a descrever como o jogador se comporta tela por tela (apêndice D). O histórico completo está no começo do documento.

| Parte                         | Responsável       | Seção |
| ----------------------------- | ----------------- | ----- |
| 1. Lobby e framing            | Willian Adrian Cardoso Paz Leandro | 2     |
| 2. Host autoritativo          | Mateus Santos da Silva | 3     |
| 3. Cliente e jogador          | Pablo Cunha de Jesus | 4     |
| 4. Falhas, testes e relatório | Matheus Rodrigues da Silva | 5 e 6 |

## 1. Arquitetura

O jogo tem três papéis, e cada um roda como um processo separado (pode ser na mesma máquina ou em máquinas diferentes):

```
             (1) antes da partida: TCP com o lobby
   Ana (host) ───────────────┐
   Beto ─────────────────────┼──►  LOBBY :5000
   Caio ─────────────────────┘

             (2) durante a partida: TCP em estrela, host no centro
   Beto ──────►  Ana (host) :6000  ◄────── Caio
```

- **Lobby** (`lobby.py`): é o ponto de encontro. Guarda os nomes, a lista de salas e o endereço de cada host. Não sabe nada sobre a lógica das regras do jogo.
- **Host** (`host.py`): é o jogador que criou a sala. Ele abre uma porta, aceita os outros, calcula tudo o que acontece no jogo e manda o resultado de cada ciclo para todos. Ele também joga, e a entrada dele é local.
- **Jogador** (`jogador.py`): conecta no host, manda só as viradas e os heartbeats, e desenha na tela o que o host mandou.

A ideia mais importante da arquitetura é que **o lobby sai de cena depois do `JOIN_OK`**. Tudo o que o jogador precisa do lobby é um par `ip:porta`. A partir daí, nenhuma mensagem do jogo passa pelo lobby. Foi assim que conseguimos cumprir o requisito de "o lobby pode cair com a partida em andamento": se ele cair, a partida nem fica sabendo.

Escolhemos o host autoritativo em vez de cada jogador calcular o jogo (lockstep) porque assim existe uma só fonte do estado. Ninguém precisa entrar em acordo sobre quem bateu primeiro ou em que ciclo alguém caiu: o host decide e todos só desenham. Isso simplificou muito a parte de falhas.

> **[PREENCHER: se quiserem, contem aqui como o grupo chegou a essa divisão e o que foi discutido antes de escolher a Opção 1.]**

## 2. Lobby e framing

### 2.1 Entrega das mensagens

Todas as conexões (lobby e jogo) usam o mesmo formato: **uma mensagem JSON por linha**, terminada em `\n`. Escolhemos isso porque dá para ler com os olhos no log, testar com `nc`/`telnet` e decodificar com o `json` da biblioteca padrão.

O cuidado principal é que o TCP é um fluxo de bytes, não de mensagens. Um `recv()` pode trazer meia mensagem, ou três grudadas. Quem resolve isso é o `LeitorLinhas` (`protocol.py`):

- guarda os bytes num buffer e só entrega uma linha quando encontra o `\n`;
- se vierem várias linhas juntas, entrega todas de uma vez;
- ignora linhas vazias;
- se passar de **16 KiB sem `\n`**, desiste e fecha a conexão. Sem esse limite, um cliente com defeito poderia fazer o buffer crescer sem fim.

O `protocol.py` é compartilhado por lobby, host e jogador. Ele também confere os campos de cada tipo de mensagem, então um erro de digitação num campo aparece como `ERROR BAD_FIELD` e não como um travamento misterioso.

### 2.2 O lobby

O lobby partiu do código original de um colega do grupo. Mantivemos a estrutura dele e adaptamos as mensagens para seguir o protocolo:

- **uma thread por cliente** e um único lock protegendo as tabelas de nomes e salas, com o envio feito fora do lock para que um cliente lento não trave os outros;
- **pedido → resposta**: cada pedido recebe exatamente uma resposta, e o lobby nunca manda nada sem ter sido perguntado. Isso deixou o lado cliente bem simples (`ConexaoLobby.pedir()` em `lobby_link.py`);
- **máquina de estados por cliente** (tabela 4.1 do protocolo): `NAO_REGISTRADO` → `REGISTRADO` → `HOSPEDANDO_ABERTA` → `HOSPEDANDO_INICIADA`. Uma mensagem fora de hora recebe `ERROR UNEXPECTED`, e 10 erros seguidos derrubam a conexão;
- **o endereço do host vem do `accept()`**: o lobby usa o IP de origem da conexão do host, e não um IP que o host diz ter. Assim o host não precisa descobrir o próprio IP, o que costuma dar errado em máquinas com várias placas de rede;
- **host e lobby na mesma máquina**: se o host chegou por `127.0.0.1`, o lobby entrega a cada jogador o IP da máquina pelo qual aquele jogador chegou. A ideia veio do lobby original do colega;
- **quem sabe a lista real de jogadores é o host** (decisão D7). O lobby só guarda uma cópia, atualizada pelo `MATCH_UPDATE`, porque só o host sabe se aceitou ou recusou alguém;
- **heartbeat**: os clientes mandam `PING` depois de 10 s calados, e o lobby fecha conexões sem nenhuma mensagem por 60 s.

## 3. Host autoritativo

### 3.1 Um loop, uma thread

O host roda o jogo inteiro numa única thread, com `selectors`. Todos os sockets são não bloqueantes e cada conexão tem o seu buffer de saída. A cada volta do loop o host:

1. espera eventos de rede até o próximo ciclo (no máximo 50 ms);
2. lê o que chegou e trata as mensagens conforme o estado de cada conexão;
3. confere os timeouts (heartbeat);
4. se chegou a hora, calcula um ciclo e manda o `STATE` para todos.

Preferimos uma thread só porque assim o estado do jogo nunca é acessado por duas threads ao mesmo tempo, e não precisamos de lock nenhum no jogo. A única outra thread é a que fala com o lobby, e ela só recebe pedidos por uma fila, então o loop do jogo nunca espera o lobby.

Os ciclos são agendados a partir de um instante inicial (`t0 + N × tick_ms`, com `time.monotonic()`), e não com `sleep(tick)` repetido.

### 3.2 O que acontece dentro de um ciclo

A lógica do jogo ficou separada da rede, em `tron.py`, o que facilitou testar as regras sem abrir socket nenhum. Cada ciclo segue sempre a mesma ordem:

1. aplica as eliminações pendentes (jogador que caiu, travou ou saiu);
2. tira da fila de cada moto a primeira virada válida. Inversão ou repetir a direção atual é descartada sem gastar o ciclo (D11);
3. calcula a próxima célula de cada moto;
4. confere as colisões, todas sobre a arena do ciclo anterior: parede (`wall`), rastro (`trail`) ou duas motos na mesma célula (`head_on`);
5. as motos que sobreviveram andam e deixam rastro. A moto eliminada fica parada na última célula válida (D12);
6. se sobrou uma moto ou nenhuma, a partida acaba com `WIN` ou `DRAW`.

### 3.3 Decisões que mais pesaram

- **TCP também no jogo (D1).** Numa rede local o RTT é de 1 a 5 ms, muito menor que os 100 ms de um ciclo. Com TCP, a virada (que não pode se perder) chega sempre e em ordem, sem termos que inventar confirmação e retransmissão. Numa rede com muita perda, a escolha poderia mudar: `STATE` por UDP e `TURN` por TCP.
- **10 ciclos por segundo (D5).** A 5 por segundo o jogo fica arrastado; acima de 10, o terminal e o tempo de reação das pessoas limitam mais que a rede. Nos scripts de demonstração usamos 150 ms por ciclo, que ficou mais confortável para jogar com o teclado.
- **Fila de 2 viradas por jogador (D3).** Quem aperta ↑ e ← rápido dentro de um mesmo ciclo quer as duas viradas. Com "só a última vale", a moto seguiria reto.
- **A arena inteira em todo `STATE` (D4).** São cerca de 1 KB por ciclo, nada para uma rede local. Em troca, o jogador não precisa reconstruir o rastro, e um `STATE` perdido não deixa a tela errada para sempre.
- **O `TURN` leva o número do ciclo só para medir (D2).** O host nunca volta no tempo para aplicar uma virada atrasada; ele usa o número para registrar no log quantos ciclos de atraso cada entrada teve.

### 3.4 Detalhes que só apareceram ao implementar

- **Erro antes do `HELLO` fecha a conexão.** Se alguém conecta na porta do host e manda lixo, provavelmente não é um jogador do nosso jogo.
- **Jogador que não lê** (mais de 256 KiB esperando para sair) é tratado como `timeout`, senão o buffer do host cresceria sem limite.
- **Despedida educada.** Ao encerrar, o host fecha só o lado de escrita do socket (`shutdown`) e espera um pouco os jogadores fecharem. Fechar direto poderia fazer o sistema mandar um RST e o jogador perder o último `GAME_OVER` ou `GAME_ABORTED`. Esse detalhe entrou no protocolo na v0.3.
- **Início manual.** Sem `--iniciar-com`, o host digita `iniciar` ou `cancelar` no terminal. A leitura desses comandos roda numa thread que para depois do `iniciar`, para não disputar o teclado com a moto do host.
- **O host leva vantagem.** A entrada dele é local e não passa pela rede. Numa rede local a diferença é de poucos milissegundos, bem menos que um ciclo.

## 4. Cliente e jogador

Esta é a parte que transforma o protocolo num jogo que dá para jogar de verdade: o `jogador.py`, com menu, teclado e tela, e o modo do host jogar pelo teclado (`host_tela.py`). Até aqui os testes eram feitos com o `cliente_teste.py`, um bot que dirige sozinho.

### 4.1 O caminho de uma pessoa pelo jogo

O `jogador.py` segue a máquina de estados da seção 6 do protocolo, e cada estado tem a sua tela. O apêndice D do protocolo descreve tudo isto em detalhe, para quem quiser reproduzir:

1. **Nome e lobby.** Se faltar `--nome` ou `--lobby-ip`, o programa pergunta antes de entrar em tela cheia. Se o nome já estiver em uso, pede outro.
2. **Menu de salas** (`NO_LOBBY`). Mostra as salas abertas, atualiza sozinho a cada 3 s e deixa escolher com ↑/↓ ou W/S. Enter entra, R atualiza e Q sai. Sala já iniciada aparece apagada.
3. **Sala de espera** (`AGUARDANDO_INICIO`). Mostra quem já entrou, quem é o host e o mínimo de jogadores. Q manda `BYE` e volta ao menu.
4. **Contagem e jogo** (`CONTAGEM`, `JOGANDO`). Contagem regressiva no meio da arena e, depois, as setas ou WASD viram a moto.
5. **Espectador** (`ESPECTADOR`). Quem é eliminado continua vendo a partida até o fim, com o motivo e o ciclo da eliminação no rodapé.
6. **Fim** (`FIM`). Mostra o vencedor ou o empate, a lista de eliminações, ou o motivo de a partida ter sido abortada. Enter volta ao menu se o lobby ainda estiver no ar; Q sai.

Ao sair, depois de devolver o terminal ao normal, o jogador imprime a mesma linha `RESULTADO ...` que o host imprime. Se as linhas de todos forem iguais (vencedor, eliminações e um hash da arena final), todos viram exatamente a mesma partida. Os cenários da seção 6 usam essa linha para conferir a consistência.

Também dá para entrar direto num host, sem lobby e sem menu (`--host IP --porta P`), o que ajudou muito nos testes.

### 4.2 Teclado sem biblioteca externa

O enunciado pede só a biblioteca padrão, e o `curses` não existe no Python do Windows. Por isso escrevemos a leitura do teclado à mão em `entrada.py`:

- **Linux e macOS:** o terminal vai para o modo `cbreak` (`termios`/`tty`), em que cada tecla chega na hora, sem esperar Enter. As setas chegam como sequências de escape (`ESC [ A` é ↑), e o código junta os pedaços quando uma sequência chega partida em dois `read()`.
- **Windows:** usa `msvcrt.kbhit()` e `getwch()`. As setas chegam como um prefixo (`\x00` ou `\xe0`) seguido de um código.
- O modo original do terminal é sempre restaurado na saída, inclusive no Ctrl+C e por `atexit`, para ninguém ficar com o terminal "quebrado".

### 4.3 Desenhando a tela

A tela também é feita sem `curses`, com códigos ANSI (`ui.py`):

- o jogo abre numa tela alternativa, como o `vim` ou o `less`, e ao sair o terminal volta como estava;
- o `Terminal` guarda as linhas que já mostrou e **só reescreve as que mudaram**. Como a cada ciclo só mudam poucas células, isso evita o pisca-pisca de redesenhar tudo 10 vezes por segundo;
- quando o terminal é largo o bastante, cada célula ocupa 2 colunas, para a arena não ficar "achatada";
- `--ascii` troca `█ ▲ ◀` por `# ^ <` (e é ligado sozinho quando o terminal não é UTF-8), e `--sem-cor` desenha o rastro com o número do jogador;
- se o terminal for pequeno demais (menos de `altura + 4` linhas ou `largura + 2` colunas), aparece um aviso no lugar da arena e a partida continua normalmente.

Durante o jogo nada é impresso na tela além do desenho. O log vai para um arquivo (`jogador_<nome>.log`).

### 4.4 Regras de rede do lado do jogador

- **Manda no máximo 2 `TURN` por ciclo** e não repete a mesma direção, de acordo com a fila de 2 do host.
- **Heartbeat**: manda `PING` depois de 1 s sem enviar nada e considera o host perdido depois de 3 s sem receber nada.
- **Nunca responde `ERROR` ao host.** Mensagem inesperada, e até um `ERROR` vindo do host, vai para o log e é ignorada. O host é a autoridade, e não há nada que o jogador possa corrigir; mostrar isso na tela só confundiria quem está jogando. Na v0.2 o protocolo dizia para mostrar o `ERROR` na tela, e na v0.3 ajustamos o texto ao que o código faz.
- **Mantém a conexão com o lobby** durante a partida, numa thread separada que só manda o `PING` de 10 s (D9). Se o lobby cair, a barra do jogo troca `lobby: ok` por `lobby: fora` e a partida segue normalmente.
- **Ctrl+C ou Q** mandam `BYE` antes de fechar a conexão, então o host registra `quit` em vez de esperar o timeout.

### 4.5 O host jogando pelo teclado

Com `--entrada teclado`, a moto do host vai pelas mesmas setas ou WASD e, no `GAME_START`, o terminal do host vira a mesma tela do jogador (`host_tela.py`, que reaproveita o `ui.py` e o `entrada.py`). Antes do início o terminal continua normal, para o host poder digitar `iniciar` ou `cancelar`. Q ou Ctrl+C encerram a partida para todos com `GAME_ABORTED host_quit`. Sem terminal interativo (por exemplo, com a saída redirecionada), o host segue sem tela e a moto anda reta.

### 4.6 Configuração de IPs e portas

Para não ter que lembrar dos argumentos na hora da apresentação, criamos scripts em `scripts/` (`.sh` para Linux/macOS e `.bat` para Windows):

- `lobby` mostra os IPs da máquina (para passar aos colegas) e sobe o lobby na porta 5000;
- `host IP_DO_LOBBY Nome` e `jogador IP_DO_LOBBY Nome` testam o lobby com o `rede.py` **antes** de subir e só continuam se ele responder. Assim um IP errado ou um firewall aparece com uma explicação, e não como um travamento;
- o host dos scripts usa arena 56 × 24, 150 ms por ciclo e o teclado. Essa arena cabe na janela padrão de 120 × 30 do Windows Terminal;
- `liberar_firewall_windows.bat` (como administrador) abre a porta 5000 e as portas de 6000 a 6010.

O `rede.py` ajuda quando algo não conecta: `rede.py ip` lista os IPs da máquina (e avisa se for WSL), e `rede.py testar IP PORTA` tenta conectar, mede o RTT com `PING`/`PONG` e explica o erro (porta fechada, firewall, rede diferente).

## 5. Detecção e tratamento de falhas

### 5.1 Os dois tipos de falha

Uma conexão TCP pode morrer de duas formas, e cada uma pede uma técnica diferente.

1. **Conexão fechada.** O processo terminou (mesmo morto com `kill -9`) ou chamou `close()`. O sistema operacional manda FIN ou RST, e o `recv()` do outro lado devolve `b""` ou levanta `ConnectionResetError`. A detecção é imediata.
2. **Silêncio.** O processo travou, a máquina congelou ou a rede caiu. Nada é enviado, então o TCP não avisa. O keepalive do TCP existe, mas o padrão dos sistemas é esperar 2 horas antes de testar, o que não serve para um jogo.

Para o segundo caso usamos heartbeat na camada de aplicação, com a mesma regra nos dois sentidos da conexão jogador ↔ host:

| Regra                                                  | Valor   | Motivo                                                                                                                                         |
| ------------------------------------------------------ | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Manda `PING` se não enviou nada há                     | 1 s     | O jogador só fala quando aperta tecla; sem isso, ficaria mudo por vários segundos.                                                             |
| Qualquer mensagem recebida conta como sinal de vida    |         | O host manda `STATE` a cada 100 ms, então durante o jogo o próprio `STATE` é o heartbeat do host.                                              |
| Conexão morta após silêncio de                         | 3 s     | Três heartbeats perdidos seguidos. Um soluço de Wi-Fi de 1 ou 2 s não elimina ninguém, e 3 s ainda é curto para quem está jogando (30 ciclos). |
| Jogador que não lê (mais de 256 KiB pendentes no host) | timeout | Sem isso, um processo travado com o socket aberto faria o buffer do host crescer sem limite.                                                   |

### 5.2 Quem decide quem caiu

Só o host. Os jogadores nunca eliminam uma moto por conta própria; eles só desenham o `STATE`. Quando o host detecta a falha de um jogador, marca a eliminação, e ela entra no **próximo** ciclo calculado, anunciada no `STATE` com `reason` `disconnect`, `timeout` ou `quit`. Como todos leem o mesmo `STATE`, dois jogadores nunca discordam sobre o ciclo da eliminação. Esse é o principal ganho do host autoritativo sobre o lockstep, em que todos os pares precisariam chegar a um acordo.

A única decisão local de um jogador é "o host morreu", e ela sempre leva ao mesmo resultado: partida abortada, sem vencedor. Por isso não importa que cada jogador perceba num instante um pouco diferente (no teste, 2971 ms para dois e 3021 ms para o terceiro).

Um caso interessante: se só a rede do Beto cair, o Beto vê "conexão com o host perdida" e os outros veem "Beto eliminado por timeout". As duas visões são coerentes, porque o Beto não declara vencedor nenhum.

### 5.3 O que cada um faz ao detectar

| Quem detecta                                                             | Fase              | Ação                                                                                                              |
| ------------------------------------------------------------------------ | ----------------- | ----------------------------------------------------------------------------------------------------------------- |
| Host, sobre um jogador                                                   | sala de espera    | remove da sala, manda `ROSTER` aos outros e `MATCH_UPDATE` ao lobby                                               |
| Host, sobre um jogador                                                   | contagem ou jogo  | elimina a moto no próximo ciclo; a partida continua                                                               |
| Jogador, sobre o host                                                    | sala de espera    | volta ao menu de salas com o aviso do motivo                                                                      |
| Jogador, sobre o host                                                    | contagem ou jogo  | mostra a tela de fim com "conexão com o host perdida", sem vencedor; Enter volta ao menu se o lobby estiver no ar |
| Host saindo por vontade própria (Ctrl+C, Q na tela do host ou `SIGTERM`) | qualquer          | manda `GAME_ABORTED host_quit` a todos antes de fechar (`cancelled`, se ainda estava na sala)                     |
| Jogador, sobre o lobby                                                   | durante a partida | registra no log e a barra do jogo passa a mostrar `lobby: fora`; ao fim, encerra em vez de voltar ao menu         |
| Host, sobre o lobby                                                      | durante a partida | só registra no log (percebe ao tentar mandar o `END_MATCH`)                                                       |

Uma limitação que vale conhecer: o jogador só percebe a queda do lobby no próximo `PING` ao lobby, que sai a cada 10 s. Então o `lobby: fora` pode levar até 10 s para aparecer. Se a partida acabar antes disso, a tela de fim ainda oferece "Enter volta ao lobby", e só depois do Enter aparece "Lobby indisponível". Nada no jogo é afetado.

### 5.4 Independência do lobby

Tudo o que o jogo precisa do lobby é o par `host_ip:host_port`, entregue no `JOIN_OK`. Depois disso nenhuma mensagem do jogo passa por ele. O host fala com o lobby numa thread separada, por uma fila, então o loop de ciclos nunca espera o lobby. As duas únicas esperas são limitadas: no máximo 2 s pelo `START_OK` (a partida começa mesmo sem resposta) e no máximo 5 s pelo `END_OK`, quando a partida já acabou.

## 6. Testes

### 6.1 Como testamos

Testamos em três camadas, das mais rápidas às mais realistas:

1. **Sem rede** (`test_tron.py`, `test_protocol.py`, `test_entrada.py`, `test_ui.py`): regras do jogo, validação das mensagens, decodificação das teclas (incluindo setas que chegam partidas) e montagem das telas.
2. **Jogador e tela do host com peças falsas** (`test_jogador.py`, `test_host_tela.py`, com `falsos.py`): um teclado que "aperta" teclas de um roteiro, um terminal que guarda o que seria desenhado e um host e um lobby falsos que seguem um script de mensagens. Assim dá para testar, por exemplo, o que o jogador faz quando o host manda lixo ou some no meio da partida.
3. **Processos de verdade em localhost** (`test_integracao.py`, `test_lobby.py`, `test_jogador_integracao.py`, `test_falhas.py`): sobem lobby, host e jogadores como processos separados, cada um com seus sockets, como se fossem máquinas diferentes.

São 157 testes no total (`python3 -m unittest discover -s testes`, cerca de 2 min). O que eles não cobrem é o teclado e o terminal de verdade (`msvcrt`, `termios` e os códigos ANSI do console). Isso nós conferimos à mão, jogando.

Para as falhas, o script `cenarios.py` sobe lobby, host e três jogadores (o `cliente_teste.py`, que dirige sozinho e sabe simular falhas), provoca a falha no momento certo e confere nos logs o que cada participante concluiu. Os tempos de detecção são medidos pelos horários dos logs; como tudo roda na mesma máquina, o relógio é o mesmo.

| Técnica                                               | O que simula na vida real                                                    |
| ----------------------------------------------------- | ---------------------------------------------------------------------------- |
| Jogador fecha o socket (`--cair-no-ciclo`)            | programa do jogador fecha ou dá erro                                         |
| Jogador para de ler e de enviar (`--travar-no-ciclo`) | programa travado, ou a rede só dele caiu                                     |
| Jogador manda `BYE` (`--sair-no-ciclo`)               | jogador desiste                                                              |
| `SIGTERM` no host                                     | Ctrl+C no host                                                               |
| `SIGKILL` no host                                     | processo do host morto; o sistema fecha os sockets                           |
| `SIGSTOP` no host                                     | host travado, máquina congelada ou cabo puxado: nada mais chega e nada fecha |
| `SIGKILL` / `SIGSTOP` no lobby                        | lobby morto / lobby travado no meio da partida                               |

O mesmo script roda dentro da suíte (`testes/test_falhas.py`), então todo cenário deste relatório é verificado de novo a cada mudança no código.

### 6.2 Resultados

Rodada de 22/11/2026, macOS, localhost, `tick_ms = 100` (200 no cenário de timeout), contagem de 1 s.

| Cenário          | Falha                                    | O que aconteceu                                                                                                                                          | Detecção       |
| ---------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| Partida completa | nenhuma                                  | 4 jogadores via lobby; host e jogadores imprimem a mesma linha de resultado (vencedor, eliminações e hash da arena); `END_MATCH` tira a partida do lobby |                |
| Queda na sala    | jogador morto antes do início            | host tira o jogador da sala; o lobby passa a listar só o host                                                                                            | 0 ms           |
| Desconexão       | Beto fecha o socket depois do `STATE` 10 | Beto eliminado no ciclo 11 com `disconnect`; os outros 3 terminam com o mesmo resultado                                                                  | ciclo seguinte |
| Timeout          | Caio trava depois do `STATE` 5           | Caio eliminado no ciclo 20 com `timeout` (5 + 15 ciclos de 200 ms); os outros 3 concordam                                                                | 3,00 s         |
| Saída            | Duda manda `BYE`                         | Duda eliminado no ciclo 11 com `quit`                                                                                                                    | ciclo seguinte |
| Host sai         | `SIGTERM`                                | todos recebem `GAME_ABORTED host_quit`; lobby limpa a lista                                                                                              | 0 ms           |
| Host morre       | `SIGKILL`                                | todos veem a conexão fechada e abortam sem vencedor; lobby limpa a lista                                                                                 | 4 ms           |
| Host trava       | `SIGSTOP`                                | todos abortam por "3 s sem sinal"; o lobby ainda lista a partida                                                                                         | 2971 a 3021 ms |
| Lobby cai        | `SIGKILL` no lobby no meio do jogo       | partida vai até o fim; todos com o mesmo resultado; o host só registra no log quando tenta o `END_MATCH`                                                 |                |
| Lobby trava      | `SIGSTOP` no lobby no meio do jogo       | partida vai até o fim normalmente; o host sai 6 s depois dos jogadores (1 s de espera do fim + 5 s esperando o `END_OK`)                                 |                |
| Lobby fora do ar | lobby não existe                         | host não cria a partida (código 1); jogador desiste com a mensagem do erro                                                                               | imediata       |

Na noite de 23/11/2026 rodamos tudo de novo no macOS, com o código final de todas as partes: 157 testes da suíte passando e os 11 cenários aprovados, com os mesmos ciclos de eliminação, timeout em 3,00 s e host travado percebido entre 2974 e 3018 ms. Também repetimos todos os cenários em 23/11/2026 no Linux, já com o jogador com teclado e a tela do host integrados, e os resultados foram os mesmos: eliminações nos mesmos ciclos, timeout em 3,00 s, host travado percebido entre 3010 e 3014 ms, queda na sala percebida em 1 ms e o host saindo 6,0 s depois dos jogadores com o lobby travado.

RTT jogador ↔ host medido pelo `PING`/`PONG`: mediana 0 ms, máximo 1 ms em localhost. Nenhuma virada foi aplicada com atraso. Entre máquinas na mesma rede Wi-Fi, o `rede.py testar` mede o valor real.

Observações que os testes revelaram:

1. **Host travado deixa uma partida fantasma no lobby.** Com `SIGSTOP`, a conexão TCP do host com o lobby não fecha, então o lobby continua listando a partida até o seu próprio timeout de inatividade (60 s). Não afeta ninguém que está jogando, mas um jogador novo poderia tentar entrar e receber erro de conexão.
2. **A detecção de timeout no host varia entre 2 e 3 s depois do travamento.** O relógio conta a partir do último byte recebido, e o jogador pode ter mandado o último `PING` até 1 s antes de travar.
3. **Um jogador travado anda reto até o timeout.** O host continua movendo a moto dele na última direção. Numa arena pequena ela pode bater na parede antes dos 3 s e ser eliminada por `wall`, e não por `timeout`. Por isso o cenário de timeout usa arena 80 × 40 e ciclos de 200 ms.
4. **O cenário `host_sai` reprovava às vezes sem motivo real.** O horário do log tem só milissegundos, mas o instante da falha é guardado com microssegundos. Quando o jogador percebia no mesmo milissegundo, a conta dava algo como −0,4 ms e a checagem "entre 0 e 1000 ms" reprovava. O host estava certo; corrigimos a medida em `cenarios.py`, que agora aceita 1 ms de folga. No Windows esse cenário continua falhando por outro motivo: lá o `SIGTERM` mata o processo na hora, e o host não chega a mandar o `GAME_ABORTED`.

### 6.3 Perguntas sobre situações não demonstradas

**Dois jogadores caem no mesmo ciclo?** As duas eliminações entram no mesmo `STATE`. Se eram os dois últimos vivos, o resultado é `DRAW`.

**O host cai durante a contagem?** É igual a cair no jogo: os jogadores abortam pelo `recv` vazio ou pelo timeout de 3 s.

**O lobby cai antes de a partida começar?** Quem já recebeu o `JOIN_OK` continua conectado no host, e a partida pode começar normalmente. Só que ninguém novo consegue descobrir a sala.

**Um jogador que caiu pode voltar?** Não. Um `HELLO` depois do `GAME_START` recebe `REJECT MATCH_STARTED`. Reconectar exigiria reconhecer o jogador antigo e devolver a moto a ele, o que deixamos como trabalho futuro.

**E se a rede ficar lenta por mais de 3 s, sem cair?** O host elimina o jogador por `timeout` (um falso positivo). É o preço de detectar rápido. Como só o host decide, todos continuam vendo a mesma coisa.

**Por que não usar o keepalive do TCP?** O padrão é esperar 2 horas até o primeiro teste, e mudar isso exige opções diferentes em cada sistema operacional. O heartbeat na aplicação funciona igual em todos e ainda mede o RTT.

**Por que não migrar o host quando ele cai?** Todos precisariam ter o estado completo e concordar sobre quem assume e a partir de qual ciclo, que é justamente o problema de consenso que o host autoritativo evita. A especificação exige só o encerramento controlado.

### 6.4 Teste em várias máquinas

Roteiro usado:

1. Em cada máquina, rode `python3 rede.py ip` para descobrir o IP. **Atenção no WSL2:** o IP mostrado é de uma rede virtual que as outras máquinas não alcançam. Há duas saídas: rodar lobby e host no Python do Windows, ou, no Windows 11, ativar `networkingMode=mirrored` na seção `[wsl2]` do arquivo `.wslconfig` (e reiniciar o WSL com `wsl --shutdown`), para o WSL usar o mesmo IP do Windows.
2. Na máquina do lobby: `scripts/lobby.sh` (ou `scripts\lobby.bat`). No Windows, rode antes `scripts\liberar_firewall_windows.bat` como administrador.
3. Na máquina do host: `scripts/host.sh IP_DO_LOBBY Ana`. O script testa o lobby antes de subir e abre o host com arena 56 × 24, 150 ms por ciclo e a moto do host no teclado. Quando todos tiverem entrado, digite `iniciar`.
4. Nas outras: `scripts/jogador.sh IP_DO_LOBBY Beto`, que abre o `jogador.py` com teclado e tela. Opções extras passam direto, por exemplo `--ascii` num console que não mostra `█ ▲ ◀`.
5. Se algo não conectar, `python3 rede.py testar IP PORTA` explica o motivo (porta fechada, firewall, rede diferente).

Para demonstrar a queda do lobby, feche o terminal do lobby com a partida em andamento: o jogo continua, e em até 10 s a barra dos jogadores mostra `lobby: fora`. Para a queda de rede de um jogador, desligue o Wi-Fi da máquina dele: ele vê "conexão com o host perdida" em 3 s, e os outros veem a eliminação dele por `timeout`. Para as falhas simuladas (`--cair-no-ciclo` etc.), use o `cliente_teste.py` diretamente.

### 6.5 Exemplos de execução

Trechos reais da rodada de 23/11/2026 (`python3 cenarios.py`, pasta `resultados/2026-11-23_232344`). Cada jogador imprime, ao fim, uma linha `RESULTADO` com o vencedor, as eliminações (`nome@ciclo:motivo`) e um hash da arena final. Se dois participantes vissem colisões diferentes, o hash seria diferente.

**Partida completa.** Ana cria a sala, Beto, Caio e Duda entram pelo lobby e a partida vai até o fim. Log do host:

```
23:23:44.580 [Ana] host: GAME_START com ['Ana', 'Beto', 'Caio', 'Duda']; ciclo 1 em 1100 ms
23:23:48.981 [Ana] host: ciclo 34: Caio eliminado (trail)
23:23:50.281 [Ana] host: ciclo 47: Ana eliminado (trail)
23:23:50.281 [Ana] host: a moto do host foi eliminada; host continua como espectador
23:23:51.980 [Ana] host: ciclo 64: Duda eliminado (trail)
23:23:52.983 [Ana] lobby: lobby confirmou END_MATCH
```

Saída dos quatro processos (Ana, Beto, Caio e Duda), idêntica:

```
RESULTADO WIN vencedor=Beto ciclo=64 eliminacoes=Caio@34:trail,Ana@47:trail,Duda@64:trail arena=1b8afff87eae
```

**Desconexão de um jogador.** Beto fecha o socket depois do `STATE` do ciclo 10. O host percebe no mesmo milissegundo, elimina a moto no ciclo seguinte e a partida continua com os outros três:

```
23:24:03.215 [Beto] FALHA SIMULADA queda no ciclo 10
23:24:03.215 [Ana] host: Beto caiu (disconnect): eliminado no próximo ciclo
23:24:03.314 [Ana] host: ciclo 11: Beto eliminado (disconnect)
23:24:09.213 [Ana] host: ciclo 70: Duda eliminado (trail)
```

Saída de Ana, Caio e Duda:

```
RESULTADO WIN vencedor=Caio ciclo=187 eliminacoes=Beto@11:disconnect,Duda@70:trail,Ana@187:trail arena=99aabdfc1ab0
```

**Falha do lobby com a partida em andamento.** O processo do lobby é morto (`SIGKILL`) logo depois do `START_MATCH`. A última linha do lobby é `partida 1 iniciada com ['Ana', 'Beto', 'Caio', 'Duda']`. A partida segue sem ele, e o host só nota a ausência ao tentar avisar o fim:

```
23:25:07.811 [Ana] host: GAME_START com ['Ana', 'Beto', 'Caio', 'Duda']; ciclo 1 em 1100 ms
23:25:12.212 [Ana] host: ciclo 34: Caio eliminado (trail)
23:25:13.112 [Ana] host: ciclo 43: Duda eliminado (trail)
23:25:13.312 [Ana] host: ciclo 45: Beto eliminado (trail)
23:25:14.314 [Ana] lobby: conexão com o lobby perdida (lobby fechou a conexão); a partida continua sem ele
```

Saída dos quatro processos:

```
RESULTADO WIN vencedor=Ana ciclo=45 eliminacoes=Caio@34:trail,Duda@43:trail,Beto@45:trail arena=6b5bc76ef68a
```

Para reproduzir: `python3 cenarios.py completa desconexao lobby_cai` (ou só `python3 cenarios.py` para todos). O resumo com cada checagem fica em `resultados/<data>/resumo.md`, e os logs de cada participante na mesma pasta.

## 7. Conclusão

### 7.1 Como o trabalho andou

O projeto começou com a escolha da Opção 1: o Tron com host autoritativo. Essa opção pareceu ser mais legal para conseguir observar na prática como uma aplicação de redes troca pacotes com outros hosts. Antes de escrever qualquer código, escrevemos o protocolo. A primeira versão (v0.1) tentava responder às perguntas que outra equipe faria para implementar um jogador compatível: que mensagens existem, com quais campos, o que cada participante aceita em cada estado e o que acontece quando alguém some.
O protocolo não ficou parado. Quando o host e o lobby ficaram prontos, a implementação mostrou detalhes que o papel não tinha previsto. Por exemplo, uma virada inválida não deveria "gastar" o ciclo, e uma moto eliminada não tem uma célula coerente para pintar. Esses detalhes viraram a v0.2. Com o jogador de teclado veio a v0.3. Ela não mudou nenhuma mensagem, mas passou a descrever o comportamento do jogador tela por tela. Foi um ir e voltar entre documento e código: a especificação guiou o código, e o código corrigiu a especificação.
Cada parte foi feita por uma pessoa, e a integração foi o momento de juntar tudo. O lobby, por exemplo, nasceu do código de um colega. Em vez de reescrevê-lo, fizemos uma cópia adaptada ao protocolo, mantendo a estrutura dele (uma thread por cliente e um lock para as tabelas). Algumas ideias do original entraram no protocolo, como entregar o IP certo quando host e lobby estão na mesma máquina.

### 7.2 O que funcionou

Separar o lobby da partida. O jogo só precisa do lobby para descobrir o endereço do host. Por isso matar ou travar o lobby no meio de uma partida não muda nada para quem está jogando, e os cenários `lobby_cai` e `lobby_trava` mostram isso.
Ter uma só fonte do estado do jogo. Como só o host calcula o jogo, em todos os cenários do relatório os jogadores terminaram com exatamente a mesma linha de resultado, com o mesmo hash da arena. Nunca precisamos resolver um desacordo entre jogadores, porque ele não tem como acontecer.
Testar as falhas de verdade. O `cenarios.py` derruba, trava e mata processos reais, mede quanto tempo cada um levou para perceber e roda de novo a cada mudança, dentro da suíte de testes.

### 7.3 O que aprendemos

A lição mais importante foi a diferença entre uma conexão fechada e uma conexão em silêncio. Quando um processo morre, o sistema operacional avisa o outro lado na hora. Quando ele trava, ou a rede cai, o TCP não avisa nada, e só um heartbeat na aplicação descobre.

Também aprendemos que boa parte do código de rede é tratar o que pode dar errado: meia mensagem num `recv`, mensagens grudadas, um cliente que manda lixo, um jogador que não lê, a última mensagem que se perde num RST quando se fecha o socket cedo demais. Os testes ensinaram coisas que não tínhamos previsto. Um host travado deixa uma partida fantasma no lobby por até 60 s. A detecção de timeout varia entre 2 e 3 s, e não exatamente 3. Uma moto travada continua andando e pode bater na parede antes de o timeout ser declarado. A revisão final ainda encontrou dois testes que reprovavam por causa da medida, e não do código: um arredondamento de milissegundos no `host_sai` e um host falso que pingava rápido demais no `test_ping_pong`.

Por fim, fazer o jogador com teclado e tela só com a biblioteca padrão mostrou como Windows e Linux são diferentes no terminal (`msvcrt` contra `termios`, setas que chegam como códigos diferentes, console sem ANSI).

### 7.4 Limitações conhecidas

Um jogador que caiu não pode voltar. Reconectar exigiria reconhecer o jogador antigo e devolver a moto a ele.
Se o host cai, a partida acaba. Não há migração de host.
Um host travado deixa uma partida fantasma no lobby até o timeout de inatividade de 60 s.
O aviso `lobby: fora` pode levar até 10 s para aparecer, porque o jogador só percebe a queda no próximo `PING`.
O host leva uma pequena vantagem, porque a entrada dele não passa pela rede. Numa rede local a diferença é de poucos milissegundos.
O teclado e o terminal de verdade só foram testados à mão, porque os testes automáticos usam peças falsas.

### 7.5 O que faríamos com mais tempo

Reconexão de jogador durante a partida.
`STATE` por UDP (o mais novo substitui o antigo) e `TURN` por TCP, para redes com mais perda que uma rede local.
Perceber a queda do lobby na hora, esperando com `select` no socket dele em vez de só no próximo `PING`.
