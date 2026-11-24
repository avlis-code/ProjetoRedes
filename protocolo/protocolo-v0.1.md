# Especificação do protocolo — Tron em rede (rascunho v0.1)

> Opção assumida: **Opção 1, Tron com host autoritativo**. Se o grupo optar pelo Uno, veja a seção 9: o framing, o lobby e o tratamento de erros continuam valendo, mas as mensagens de jogo e as máquinas de estado mudam bastante.

O objetivo deste documento é que outra equipe consiga implementar um participante compatível só lendo o texto. Por isso cada mensagem tem campos, tipos e um exemplo, e cada estado diz exatamente o que aceita.

---

## 1. Visão geral

```
                 (1) TCP  — só antes da partida
   Ana (host) ───────────────┐
   Beto ─────────────────────┼──►  LOBBY :5000
   Caio ─────────────────────┘

                 (2) TCP  — durante a partida (estrela, host no centro)
   Beto ──────►  Ana (host) :6000  ◄────── Caio
```

| Papel | O que faz |
|---|---|
| **Lobby** | Servidor TCP sempre ligado. Registra nomes, lista/cria partidas, entrega o endereço do host. Não sabe nada do jogo. |
| **Host** | Jogador que criou a partida. Abre uma porta, aceita os outros jogadores, calcula **todo** o estado do jogo e envia a cada ciclo. Também joga (a entrada dele é local, não passa pela rede). |
| **Jogador comum** | Conecta no host, envia apenas mudanças de direção e heartbeats, desenha o estado recebido. |

**Independência do lobby:** a única coisa que o jogador precisa do lobby é o par `host_ip:host_port`. Depois do `JOIN_OK`, nenhuma mensagem do jogo depende do lobby. A conexão com o lobby pode continuar aberta (para voltar à lista no fim), mas a perda dela durante a partida só gera uma linha de log.

### 1.1 Caminho completo de uma partida

```
Ana(host)            Lobby              Beto               Caio
   │ REGISTER ──────►  │                  │                  │
   │ ◄──── REGISTER_OK │                  │                  │
   │ CREATE_MATCH ───► │ (IP = origem do accept, porta = 6000)
   │ ◄─ MATCH_CREATED  │                  │                  │
   │                   │ ◄──── REGISTER   │                  │
   │                   │ ◄── JOIN_MATCH   │                  │
   │                   │ JOIN_OK ───────► │ (host = 192.168.0.10:6000)
   │ ◄────────────────────────── HELLO    │                  │
   │ WELCOME ───────────────────────────► │                  │
   │ MATCH_UPDATE ───► │                  │                  │
   │       ...  Caio faz o mesmo  ...                        │
   │ ROSTER ────────────────────────────► │ ───────────────► │
   │ START_MATCH ────► │ (marca INICIADA; espera no máx. 2 s)
   │ GAME_START ────────────────────────► │ ───────────────► │
   │   (contagem de start_in_ms)          │                  │
   │ ◄──────────────────────────── TURN   │                  │
   │ STATE c=1 ─────────────────────────► │ ───────────────► │
   │ STATE c=2 ...  (lobby pode cair aqui, nada muda)        │
   │ GAME_OVER ─────────────────────────► │ ───────────────► │
   │ END_MATCH ──────► │ (se o lobby ainda existir)
```

---

## 2. Transporte e framing

**Transporte: TCP em tudo** (lobby e partida). Justificativa resumida na decisão D1 (seção 8).

**Framing:** cada mensagem é **um objeto JSON em UTF-8 em uma única linha, terminado por `\n`** (byte 0x0A).

Regras de recepção (valem para lobby, host e jogador):

1. O receptor acumula os bytes de cada conexão em um buffer e só processa linhas completas. Um `recv()` pode trazer meia mensagem ou várias.
2. Linhas vazias (ou só com espaços/`\r`) são ignoradas.
3. **Tamanho máximo de linha: 16 KiB.** Se o buffer passar disso sem conter `\n`, a conexão é fechada (proteção contra lixo ou cliente quebrado).
4. `recv()` retornando `b""` = o outro lado fechou. Exceção (`ConnectionResetError`, `OSError`) = conexão perdida. Os dois casos são tratados como **desconexão**.
5. O `json.dumps` nunca gera `\n` dentro do objeto (strings são escapadas), então o delimitador é seguro.

**Formato comum:**

```json
{"type": "NOME_DO_TIPO", "...": "campos do tipo"}
```

- `type` (string, obrigatório, MAIÚSCULAS): identifica a mensagem.
- Campos desconhecidos são **ignorados** (permite evoluir o protocolo sem quebrar quem é mais antigo).
- Campo obrigatório ausente ou com tipo errado → `ERROR` com `code: "BAD_FIELD"` (ver seção 7).
- Versão do protocolo: enviada uma vez, em `REGISTER` (lobby) e em `HELLO` (host), no campo `version` (inteiro, hoje `1`).

**Socket:** todo socket da partida usa `TCP_NODELAY` (desliga o algoritmo de Nagle; sem isso, mensagens pequenas como `TURN` podem esperar até ~40 ms para sair). Servidores fazem `bind` em `0.0.0.0`.

### 2.1 Convenções de campos

| Campo | Tipo | Significado |
|---|---|---|
| `name` | string, 1–16 chars, `[A-Za-z0-9_-]` | nome do jogador, único no lobby |
| `match_id` | inteiro ≥ 1 | atribuído pelo lobby |
| `id` | inteiro 0–3 | número do jogador **dentro da partida**, atribuído pelo host. O host é sempre `0`. |
| `dir` | `"UP"`, `"DOWN"`, `"LEFT"`, `"RIGHT"` | direção |
| `x`, `y` | inteiros | coluna e linha da arena. `(0,0)` é o canto superior esquerdo; `y` cresce para baixo. |
| `cycle` | inteiro ≥ 0 | número do ciclo. Ciclo 0 = posições iniciais; ciclo N = estado após N movimentos. |
| tempos | inteiros em **ms** | sempre durações relativas, nunca horários absolutos (os relógios das máquinas não são sincronizados) |

---

## 3. Catálogo de mensagens

Legenda da coluna "Sentido": **J** = jogador (qualquer cliente do lobby, inclusive o host), **H** = host, **L** = lobby.

### 3.1 Lobby (TCP, cliente ↔ lobby)

O lobby é estritamente **pedido → resposta**: para cada pedido ele envia exatamente uma resposta, na mesma ordem, e nunca envia nada sem ter sido pedido. Por isso não precisamos de identificador de requisição.

| Tipo | Sentido | Campos | Resposta de sucesso |
|---|---|---|---|
| `REGISTER` | J → L | `name`, `version` | `REGISTER_OK` |
| `LIST_MATCHES` | J → L | — | `MATCH_LIST` |
| `CREATE_MATCH` | H → L | `port`, `match_name`?, `max_players`?, `advertise_ip`? | `MATCH_CREATED` |
| `JOIN_MATCH` | J → L | `match_id` | `JOIN_OK` |
| `MATCH_UPDATE` | H → L | `match_id`, `players` | `UPDATE_OK` |
| `START_MATCH` | H → L | `match_id` | `START_OK` |
| `CANCEL_MATCH` | H → L | `match_id` | `CANCEL_OK` |
| `END_MATCH` | H → L | `match_id`, `winner`? | `END_OK` |
| `PING` / `PONG` | J ↔ L | `t` | — |

Qualquer pedido pode ser respondido com `ERROR` (seção 3.7) em vez da resposta de sucesso.

**REGISTER** — primeira mensagem obrigatória após conectar.
```json
{"type": "REGISTER", "name": "Ana", "version": 1}
{"type": "REGISTER_OK", "name": "Ana"}
```
Erros: `BAD_NAME`, `NAME_TAKEN`, `BAD_VERSION`.

**LIST_MATCHES / MATCH_LIST** — lista todas as partidas, inclusive as iniciadas (para exibição).
```json
{"type": "LIST_MATCHES"}
{"type": "MATCH_LIST", "matches": [
  {"match_id": 3, "match_name": "sala da Ana", "host": "Ana",
   "players": ["Ana", "Beto"], "max_players": 4, "status": "ABERTA"},
  {"match_id": 4, "match_name": "x", "host": "Duda",
   "players": ["Duda", "Edu", "Fabi"], "max_players": 4, "status": "INICIADA"}
]}
```
`status`: `"ABERTA"` ou `"INICIADA"`.

**CREATE_MATCH / MATCH_CREATED** — quem cria vira host. O host **já deve estar escutando** na `port` antes de enviar.
```json
{"type": "CREATE_MATCH", "port": 6000, "match_name": "sala da Ana", "max_players": 4}
{"type": "MATCH_CREATED", "match_id": 3, "host_ip": "192.168.0.10", "host_port": 6000}
```
- O lobby **usa como `host_ip` o IP de origem da conexão** (retornado pelo `accept()`), e não um IP informado pelo cliente. Assim ninguém anuncia `127.0.0.1` por engano.
- `advertise_ip` (opcional) só existe para o caso em que o host roda na mesma máquina do lobby e se conectou por `127.0.0.1`: aí o host informa o IP de rede dele. Se o IP de origem for de loopback e `advertise_ip` não vier, o lobby aceita mas registra um aviso no log.
- `max_players` opcional, padrão 4, aceito entre 3 e 4.
- O lobby devolve `host_ip` para o host saber como é visto pelos outros (útil para depurar).

Erros: `BAD_FIELD` (porta fora de 1–65535, max_players inválido).

**JOIN_MATCH / JOIN_OK** — o lobby só entrega o endereço; quem aceita ou recusa de verdade é o host (seção 3.2).
```json
{"type": "JOIN_MATCH", "match_id": 3}
{"type": "JOIN_OK", "match_id": 3, "host": "Ana", "host_ip": "192.168.0.10", "host_port": 6000}
```
Erros: `MATCH_NOT_FOUND`, `MATCH_STARTED`, `MATCH_FULL` (pela última contagem recebida em `MATCH_UPDATE`).

**MATCH_UPDATE / UPDATE_OK** — o host informa a lista real de jogadores da sala sempre que ela muda. O **host é a fonte da verdade** sobre quem está na partida; o lobby só guarda uma cópia para exibir em `MATCH_LIST`.
```json
{"type": "MATCH_UPDATE", "match_id": 3, "players": ["Ana", "Beto", "Caio"]}
{"type": "UPDATE_OK"}
```

**START_MATCH / START_OK** — marca a partida como `INICIADA` (o lobby passa a recusar `JOIN_MATCH`).
```json
{"type": "START_MATCH", "match_id": 3}
{"type": "START_OK", "match_id": 3}
```
O host espera o `START_OK` por **no máximo 2 s**. Se não chegar, ou se a conexão com o lobby já tiver caído, o host **inicia a partida mesmo assim** e registra no log. O lobby nunca bloqueia uma partida.

**CANCEL_MATCH / CANCEL_OK** — o host desiste antes de iniciar. O lobby remove a partida.
```json
{"type": "CANCEL_MATCH", "match_id": 3}
{"type": "CANCEL_OK", "match_id": 3}
```

**END_MATCH / END_OK** — enviado pelo host depois do `GAME_OVER`, só se o lobby ainda estiver acessível. O lobby remove a partida da lista.
```json
{"type": "END_MATCH", "match_id": 3, "winner": "Beto"}
{"type": "END_OK", "match_id": 3}
```
`winner` é `null` em caso de empate ou partida abortada.

**Limpeza automática:** se a conexão do host com o lobby fechar, o lobby remove a partida dele da lista (esteja ela aberta ou iniciada). Isso não afeta nada do jogo: o lobby só está limpando a lista dele.

### 3.2 Entrada na partida (TCP, jogador ↔ host)

| Tipo | Sentido | Campos |
|---|---|---|
| `HELLO` | J → H | `name`, `version` |
| `WELCOME` | H → J | `id`, `match_id`, `players`, `min_players`, `max_players` |
| `REJECT` | H → J | `code`, `message` |
| `ROSTER` | H → todos | `players` |
| `BYE` | J → H | `reason`? |

**HELLO** — primeira mensagem do jogador após conectar no host. Prazo: **5 s** após o `accept()`, senão o host fecha a conexão.
```json
{"type": "HELLO", "name": "Beto", "version": 1}
```

**WELCOME** — aceito. `id` é o número do jogador na partida (o host é 0; os demais recebem o menor livre entre 1 e 3).
```json
{"type": "WELCOME", "id": 1, "match_id": 3, "min_players": 3, "max_players": 4,
 "players": [{"id": 0, "name": "Ana"}, {"id": 1, "name": "Beto"}]}
```

**REJECT** — recusado; o host fecha a conexão logo depois de enviar.
```json
{"type": "REJECT", "code": "MATCH_FULL", "message": "partida cheia (4/4)"}
```
`code`: `MATCH_FULL`, `MATCH_STARTED`, `NAME_TAKEN`, `BAD_VERSION`, `BAD_NAME`.

**ROSTER** — o host envia a lista atualizada a todos os conectados sempre que alguém entra ou sai antes do início.
```json
{"type": "ROSTER", "players": [{"id": 0, "name": "Ana"}, {"id": 1, "name": "Beto"}, {"id": 2, "name": "Caio"}]}
```

**BYE** — saída voluntária. Antes do início, o jogador sai da sala. Durante o jogo, a moto é eliminada com motivo `quit`. Depois de enviar, o jogador fecha a conexão.
```json
{"type": "BYE", "reason": "usuário saiu"}
```

### 3.3 Início (host → jogadores)

**GAME_START** — tudo o que o jogador precisa para o jogo inteiro. Depois desta mensagem nenhuma informação vem do lobby.
```json
{"type": "GAME_START",
 "arena": {"width": 40, "height": 20},
 "tick_ms": 100,
 "start_in_ms": 3000,
 "players": [
   {"id": 0, "name": "Ana",  "x": 4,  "y": 4,  "dir": "RIGHT"},
   {"id": 1, "name": "Beto", "x": 35, "y": 15, "dir": "LEFT"},
   {"id": 2, "name": "Caio", "x": 35, "y": 4,  "dir": "DOWN"}
 ]}
```
- O host só pode enviar com pelo menos `min_players` (3) conectados.
- O estado descrito é o **ciclo 0**: cada moto ocupa a célula inicial (essa célula já conta como rastro).
- `start_in_ms` é uma contagem regressiva **relativa**: o host envia o `STATE` do ciclo 1 em `start_in_ms + tick_ms` após enviar o `GAME_START`. Os jogadores usam o valor só para exibir a contagem; o que manda é a chegada dos `STATE`.
- Não há confirmação (`READY`): o TCP garante a entrega, e quem cair na contagem é tratado como desconexão (seção 3.5).

### 3.4 Durante o jogo

| Tipo | Sentido | Campos |
|---|---|---|
| `TURN` | J → H | `dir`, `cycle` |
| `STATE` | H → todos | `cycle`, `bikes`, `grid`, `events` |

**TURN** — pedido de mudança de direção.
```json
{"type": "TURN", "dir": "UP", "cycle": 41}
```
- `cycle` = o último ciclo que o jogador **recebeu** quando apertou a tecla (0 se ainda estiver na contagem).
- Regras no host (ver decisões D2 e D3):
  1. O pedido entra numa fila do jogador com **no máximo 2** posições; se a fila estiver cheia, o mais antigo é descartado.
  2. A cada ciclo o host consome **no máximo um** pedido de cada fila e o aplica no ciclo que está calculando (`cycle_atual + 1`). Nunca é aplicado retroativamente.
  3. Pedido de **inversão** (ex.: está indo para `LEFT` e pede `RIGHT`) ou para a mesma direção é descartado em silêncio, sem `ERROR`: é jogada legal sem efeito, não erro de protocolo.
  4. Atraso = `ciclo_aplicado − 1 − cycle`. Se for maior que 0, o host só registra no log (serve para o relatório mostrar o efeito da latência).
  5. `cycle` maior que o último ciclo enviado pelo host é impossível num cliente correto → `ERROR BAD_FIELD`, pedido descartado.
- `TURN` de jogador já eliminado é ignorado em silêncio (pode ter sido enviado antes de ele saber que morreu).

**STATE** — estado completo do ciclo N, calculado pelo host. Os jogadores **não** calculam colisões; só desenham.
```json
{"type": "STATE", "cycle": 42,
 "bikes": [
   {"id": 0, "x": 12, "y": 4,  "dir": "RIGHT", "alive": true},
   {"id": 1, "x": 30, "y": 15, "dir": "UP",    "alive": true},
   {"id": 2, "x": 35, "y": 9,  "dir": "DOWN",  "alive": false}
 ],
 "grid": [
   "........................................",
   "....0000000000.....................2....",
   "........................................"
 ],
 "events": [{"event": "ELIMINATED", "id": 2, "reason": "trail"}]}
```
- (Exemplo encurtado: a mensagem real tem as 20 linhas.)
- `grid`: lista com `height` strings de `width` caracteres. `.` = vazio; `0`–`3` = rastro do jogador com aquele `id`. A cabeça da moto também aparece como rastro; a posição atual vem em `bikes`.
- `events`: o que aconteceu **neste** ciclo (lista vazia na maioria dos ciclos).
  - `ELIMINATED` com `reason`: `wall` (saiu da arena), `trail` (entrou em rastro), `head_on` (duas ou mais motos na mesma célula), `disconnect` (conexão fechou), `timeout` (sem sinal por 3 s), `quit` (enviou `BYE`).
- Uma moto eliminada continua com o rastro na arena e não se move mais.
- Ordem de cálculo no host (importante para todos verem a mesma coisa, inclusive em motos que "trocam de lugar"): (1) aplica as eliminações por desconexão detectadas desde o último ciclo; (2) aplica as direções; (3) calcula a nova célula de cada moto viva; (4) elimina quem saiu da arena, quem entrou em célula já ocupada por rastro e quem caiu na mesma célula que outra; (5) marca as novas células como rastro; (6) envia o `STATE`; (7) verifica o fim.

### 3.5 Falhas e heartbeat

| Tipo | Sentido | Campos |
|---|---|---|
| `PING` | qualquer → qualquer | `t` |
| `PONG` | resposta ao `PING` | `t` (o mesmo recebido) |

```json
{"type": "PING", "t": 1731941234567}
{"type": "PONG", "t": 1731941234567}
```

Regra única, simétrica, válida em todos os estados das conexões jogador ↔ host:

- **Envio:** se um lado não enviou nada na conexão nos últimos **1000 ms**, envia um `PING`. Na prática, durante o jogo o host nunca precisa (envia `STATE` a cada 100 ms), mas os jogadores sim (só mandam `TURN` quando apertam tecla).
- **Resposta:** quem recebe `PING` responde `PONG` com o mesmo `t`. `t` é o `time.monotonic()` do emissor em ms; só o emissor interpreta esse valor (serve para medir RTT e citar no relatório).
- **Detecção:** **qualquer** mensagem recebida conta como sinal de vida. Sem nada por **3000 ms**, a conexão é considerada morta e fechada.

O que cada lado faz ao detectar a falha (por timeout ou por `recv` vazio/erro):

| Quem detecta | Fase | Ação |
|---|---|---|
| Host, sobre um jogador | antes do início | remove da sala, envia `ROSTER` aos outros e `MATCH_UPDATE` ao lobby |
| Host, sobre um jogador | contagem ou jogo | marca para eliminar; no próximo ciclo calculado a moto é eliminada com `reason: "disconnect"` ou `"timeout"`, anunciado no `STATE`. Partida continua. |
| Jogador, sobre o host | qualquer | partida **abortada** localmente: mostra "conexão com o host perdida", vai para `FIM`, sem vencedor |
| Qualquer um, sobre o lobby | durante a partida | só log. Nada muda no jogo. |

**Quem decide quem caiu?** Só o host. Os jogadores nunca eliminam uma moto por conta própria. Como existe uma única autoridade, não há como dois jogadores discordarem sobre o ciclo em que alguém foi eliminado: todos leem o mesmo `STATE`. A única decisão local é "o host morreu", e ela leva sempre ao mesmo resultado (partida abortada, sem vencedor), não importa em que momento cada jogador percebeu.

**Ponto bom para a apresentação:** se a rede de um jogador cair, mas o host continuar vivo, o jogador isolado verá "host perdido" e os outros verão "Beto eliminado por timeout". As duas visões são coerentes: o jogador isolado não declara vencedor nenhum.

### 3.6 Fim

**GAME_OVER** — enviado logo após o `STATE` do ciclo final, no mesmo ciclo.
```json
{"type": "GAME_OVER", "cycle": 187, "result": "WIN",
 "winner": {"id": 1, "name": "Beto"},
 "eliminations": [
   {"id": 2, "name": "Caio", "cycle": 63,  "reason": "trail"},
   {"id": 0, "name": "Ana",  "cycle": 187, "reason": "wall"}
 ]}
```
- `result`: `"WIN"` (sobrou exatamente um) ou `"DRAW"` (todos os restantes eliminados no mesmo ciclo; `winner` = `null`).
- A partida termina quando restam 0 ou 1 motos vivas após um ciclo.
- Depois de enviar, o host espera **1 s** (para as mensagens saírem) e fecha as conexões. Depois envia `END_MATCH` ao lobby, se ele estiver acessível.

**GAME_ABORTED** — encerramento controlado sem resultado (host saiu, cancelou a sala, erro interno).
```json
{"type": "GAME_ABORTED", "reason": "host_quit", "cycle": 95}
```
`reason`: `host_quit`, `cancelled`, `internal_error`. `cycle` é `null` se a partida ainda não tinha começado.

Isso cobre a saída **voluntária** do host (ex.: Ctrl+C capturado). Se o host travar ou a máquina cair, ninguém recebe nada e os jogadores detectam pelo timeout de 3 s (seção 3.5). Os dois casos terminam no mesmo estado `FIM`.

### 3.7 Erro

```json
{"type": "ERROR", "code": "UNEXPECTED", "message": "JOIN_MATCH não é permitido no estado HOSPEDANDO", "ref": "JOIN_MATCH"}
```

| `code` | Quando |
|---|---|
| `BAD_JSON` | a linha não é JSON válido, ou não é um objeto |
| `UNKNOWN_TYPE` | falta `type`, ou ele não existe no protocolo |
| `BAD_FIELD` | campo obrigatório ausente, tipo errado ou valor fora da faixa |
| `UNEXPECTED` | tipo conhecido, mas não permitido no estado atual |
| `BAD_VERSION` | `version` diferente da suportada |
| `BAD_NAME` / `NAME_TAKEN` | nome inválido ou já usado |
| `MATCH_NOT_FOUND` / `MATCH_FULL` / `MATCH_STARTED` | problemas no `JOIN_MATCH` |
| `NOT_HOST` | `START_MATCH`, `CANCEL_MATCH`, `MATCH_UPDATE` ou `END_MATCH` de quem não é host daquela partida |

`ref` = o `type` da mensagem que causou o erro, quando foi possível ler; senão `null`. **Nunca se responde `ERROR` a um `ERROR`** (evita laço infinito): um `ERROR` recebido é só registrado no log.

---

## 4. Máquina de estados do lobby (por conexão de cliente)

```
            conexão aceita
                  │
                  ▼
         ┌─────────────────┐
         │ NAO_REGISTRADO  │  outra msg → ERROR UNEXPECTED
         └────────┬────────┘
                  │ REGISTER válido / REGISTER_OK
                  ▼
         ┌─────────────────┐  LIST_MATCHES, JOIN_MATCH → resposta,
         │   REGISTRADO    │◄─────────────┐ continua no estado
         └────────┬────────┘              │
                  │ CREATE_MATCH          │ CANCEL_MATCH / CANCEL_OK
                  ▼                       │ END_MATCH / END_OK
         ┌─────────────────┐              │
         │HOSPEDANDO_ABERTA│──────────────┤
         └────────┬────────┘              │
                  │ START_MATCH / START_OK│
                  ▼                       │
         ┌──────────────────┐             │
         │HOSPEDANDO_INICIADA│────────────┘
         └──────────────────┘

  Em qualquer estado: conexão fechada → remove o jogador e,
  se ele hospedava uma partida, remove a partida da lista.
```

Um jogador comum que recebeu `JOIN_OK` continua em `REGISTRADO` no lobby: o lobby não precisa saber se ele está jogando. A presença dele na partida é informada pelo host via `MATCH_UPDATE`.

### 4.1 Mensagens permitidas por estado (lobby)

| Estado | Permitidas | Transição |
|---|---|---|
| `NAO_REGISTRADO` | `REGISTER`, `PING` | `REGISTER` ok → `REGISTRADO` |
| `REGISTRADO` | `LIST_MATCHES`, `CREATE_MATCH`, `JOIN_MATCH`, `PING` | `CREATE_MATCH` ok → `HOSPEDANDO_ABERTA` |
| `HOSPEDANDO_ABERTA` | `LIST_MATCHES`, `MATCH_UPDATE`, `START_MATCH`, `CANCEL_MATCH`, `PING` | `START_MATCH` → `HOSPEDANDO_INICIADA`; `CANCEL_MATCH` → `REGISTRADO` |
| `HOSPEDANDO_INICIADA` | `LIST_MATCHES`, `MATCH_UPDATE`, `END_MATCH`, `PING` | `END_MATCH` → `REGISTRADO` |

Qualquer outra mensagem → `ERROR UNEXPECTED`, estado inalterado.

**Dados compartilhados:** tabela de nomes e tabela de partidas, protegidas por um único `threading.Lock`. A verificação "partida aberta e com vaga" no `JOIN_MATCH` e a marcação de `INICIADA` no `START_MATCH` são feitas com o lock segurado.

---

## 5. Máquina de estados do host

O host tem dois níveis: o **estado da partida** (um só) e o **estado de cada conexão de jogador**.

### 5.1 Estado da partida (no host)

```
  ┌─────────────┐ REGISTER_OK + MATCH_CREATED
  │  INICIANDO  │───────────────────────────────┐
  └─────┬───────┘                               ▼
        │ erro do lobby /           ┌────────────────────────┐
        │ lobby inacessível         │ AGUARDANDO_JOGADORES   │◄─ HELLO / BYE / queda
        ▼                           └───┬───────────────┬────┘   (atualiza ROSTER)
     (encerra)            host cancela  │               │ host aperta "iniciar"
                          GAME_ABORTED  │               │ e há ≥ 3 jogadores
                          CANCEL_MATCH  │               │ START_MATCH (espera ≤ 2 s)
                                        │               │ GAME_START
                                        │               ▼
                                        │      ┌─────────────┐
                                        │      │  CONTAGEM   │  start_in_ms
                                        │      └──────┬──────┘
                                        │             │ tempo esgotado
                                        │             ▼
                                        │      ┌─────────────┐  a cada tick_ms:
                                        │      │   EM_JOGO   │  calcula + STATE
                                        │      └──────┬──────┘
                                        │             │ ≤ 1 moto viva → GAME_OVER
                                        │             │ host sai     → GAME_ABORTED
                                        ▼             ▼
                                     ┌───────────────────┐
                                     │        FIM        │ fecha conexões (após 1 s),
                                     └───────────────────┘ END_MATCH ao lobby se possível
```

- Em `INICIANDO`, o host abre o socket de escuta **antes** do `CREATE_MATCH`.
- Em `AGUARDANDO_JOGADORES`, se o lobby cair, a sala continua existindo para quem já tem o endereço, mas ninguém novo consegue descobri-la. O host pode iniciar normalmente.
- A moto do host ser eliminada **não** muda o estado da partida: o host continua calculando os ciclos até o fim, e o jogador do host vira espectador.

### 5.2 Estado de cada conexão de jogador (no host)

```
  accept() ─► AGUARDANDO_HELLO ──HELLO ok / WELCOME──► NA_SALA ──GAME_START──► JOGANDO
                    │                                    │                      │
                    │ HELLO inválido / REJECT            │ BYE ou queda         │ moto eliminada
                    │ 5 s sem HELLO                      ▼                      ▼
                    └────────────────────────────►  FECHADA  ◄── BYE/queda ── ELIMINADO
                                                                              (recebe STATE até o fim)
```

### 5.3 Mensagens que o host aceita, por estado da conexão

| Estado da conexão | Aceitas | Ação |
|---|---|---|
| `AGUARDANDO_HELLO` | `HELLO` | valida → `WELCOME` + `ROSTER` a todos + `MATCH_UPDATE`, ou `REJECT` + fecha |
| | `PING` | `PONG` |
| | outra | `ERROR UNEXPECTED` e **fecha** (quem não se apresenta não fala com o host) |
| `NA_SALA` | `BYE` | remove, `ROSTER`, `MATCH_UPDATE` |
| | `TURN` | **aceito** se a partida já está em `CONTAGEM` (fila para o ciclo 1); senão `ERROR UNEXPECTED` |
| | `PING`/`PONG` | resposta/sinal de vida |
| | outra | `ERROR UNEXPECTED`, continua |
| `JOGANDO` | `TURN` | fila de direções (seção 3.4) |
| | `BYE` | elimina no próximo ciclo (`quit`) |
| | `PING`/`PONG` | resposta/sinal de vida |
| | `HELLO` repetido | `ERROR UNEXPECTED`, ignora |
| | outra | `ERROR UNEXPECTED`, continua |
| `ELIMINADO` | `TURN` | ignora em silêncio |
| | `BYE` | fecha a conexão |
| | `PING`/`PONG` | resposta/sinal de vida |
| | outra | `ERROR UNEXPECTED`, continua |

(Na prática, `NA_SALA` vira `JOGANDO` no momento do `GAME_START`, então "`TURN` em `NA_SALA` durante a contagem" é só uma garantia contra corrida entre threads.)

Mensagens recebidas do **lobby** pelo host seguem o pedido → resposta da seção 3.1; resposta inesperada é registrada e ignorada.

---

## 6. Máquina de estados do jogador comum

```
  ┌───────────────────┐ conecta no lobby, REGISTER
  │ CONECTANDO_LOBBY  │──── falha / NAME_TAKEN ──► (pede outro nome ou encerra)
  └─────────┬─────────┘
            │ REGISTER_OK
            ▼
  ┌───────────────────┐   LIST_MATCHES → MATCH_LIST (continua)
  │     NO_LOBBY      │◄──────────────────────────────────────────┐
  └─────────┬─────────┘                                           │
            │ JOIN_MATCH → JOIN_OK                                │
            ▼                                                     │
  ┌───────────────────┐   conecta em host_ip:host_port, HELLO     │
  │ CONECTANDO_HOST   │── REJECT / falha / 5 s sem resposta ──────┤
  └─────────┬─────────┘                                           │
            │ WELCOME                                             │
            ▼                                                     │
  ┌───────────────────┐   ROSTER (atualiza a tela)                │
  │ AGUARDANDO_INICIO │── usuário sai: BYE ───────────────────────┤
  └─────────┬─────────┘── GAME_ABORTED(cancelled) / host caiu ────┤
            │ GAME_START                                          │
            ▼                                                     │
  ┌───────────────────┐                                           │
  │     CONTAGEM      │   pode enviar TURN (vale para o ciclo 1)  │
  └─────────┬─────────┘                                           │
            │ primeiro STATE                                      │
            ▼                                                     │
  ┌───────────────────┐   STATE → desenha; tecla → TURN           │
  │     JOGANDO       │                                           │
  └─────────┬─────────┘                                           │
            │ STATE com ELIMINATED do meu id                      │
            ▼                                                     │
  ┌───────────────────┐   STATE → desenha (sem enviar TURN)       │
  │    ESPECTADOR     │                                           │
  └─────────┬─────────┘                                           │
            │ GAME_OVER / GAME_ABORTED / host caiu (de JOGANDO,   │
            ▼              CONTAGEM ou ESPECTADOR também)         │
  ┌───────────────────┐                                           │
  │        FIM        │── lobby ainda conectado → volta ──────────┘
  └───────────────────┘── lobby caiu → encerra o programa
```

O host passa pelos mesmos estados do lado "lobby" (`CONECTANDO_LOBBY` → `NO_LOBBY`) e, ao enviar `CREATE_MATCH`, troca para a máquina da seção 5.

### 6.1 Mensagens que o jogador aceita do host, por estado

| Estado | Aceitas | Inesperadas (registra no log e ignora) |
|---|---|---|
| `CONECTANDO_HOST` | `WELCOME`, `REJECT`, `PING`, `ERROR` | `ROSTER`, `STATE`, `GAME_START`... |
| `AGUARDANDO_INICIO` | `ROSTER`, `GAME_START`, `GAME_ABORTED`, `PING`, `PONG`, `ERROR` | `STATE`, `GAME_OVER`, `WELCOME` |
| `CONTAGEM` | `STATE`, `GAME_ABORTED`, `PING`, `PONG`, `ERROR` | `ROSTER`, `GAME_START` repetido |
| `JOGANDO` / `ESPECTADOR` | `STATE`, `GAME_OVER`, `GAME_ABORTED`, `PING`, `PONG`, `ERROR` | `ROSTER`, `GAME_START`, `WELCOME` |
| `FIM` | — (conexão já fechada) | tudo |

Regras extras do jogador:
- `STATE` com `cycle` **menor ou igual** ao último recebido é descartado (não deveria acontecer em TCP, mas protege contra bug do host).
- Se faltar um ciclo (ex.: recebeu 40 e depois 42), o jogador desenha o 42 normalmente: como o `STATE` traz a arena inteira, nada se perde (essa é a principal vantagem da decisão D4).
- O jogador **nunca** responde `ERROR` ao host por mensagem inesperada: o host é a autoridade, e não há o que ele possa corrigir. Só registra no log.

---

## 7. Tratamento de erros (resumo)

| Situação | Lobby | Host (de um jogador) | Jogador (do host) |
|---|---|---|---|
| JSON malformado | `ERROR BAD_JSON`, continua | `ERROR BAD_JSON`, continua | log, ignora |
| Tipo desconhecido / sem `type` | `ERROR UNKNOWN_TYPE` | `ERROR UNKNOWN_TYPE` | log, ignora |
| Campo inválido | `ERROR BAD_FIELD` | `ERROR BAD_FIELD`, descarta a mensagem | log, ignora |
| Fora de ordem (não permitida no estado) | `ERROR UNEXPECTED` | `ERROR UNEXPECTED` (fecha se ainda em `AGUARDANDO_HELLO`) | log, ignora |
| Muitos erros seguidos | **10 erros seguidos → fecha a conexão** | idem | — |
| Linha > 16 KiB sem `\n` | fecha | fecha (= desconexão) | fecha (= host perdido) |
| Mensagem que nunca chega | cliente: timeout de 5 s por resposta, depois considera o lobby fora | 5 s para `HELLO`; 3 s sem sinal → desconexão | 5 s para `WELCOME`; 3 s sem sinal → host perdido |
| `ERROR` recebido | — | log (nunca responde) | log, mostra na tela |

O contador de "erros seguidos" zera a cada mensagem válida. A ideia é tolerar bugs pontuais sem deixar um cliente quebrado inundar o servidor.

---

## 8. Decisões em aberto e recomendações

| # | Decisão | Opções | Recomendação |
|---|---|---|---|
| **D1** | Transporte no jogo | TCP; UDP; TCP no lobby + UDP no jogo | **TCP em tudo, com `TCP_NODELAY`.** |
| **D2** | `TURN` leva o número do ciclo? | sim; não | **Sim**, mas só informativo. |
| **D3** | Várias teclas no mesmo ciclo | só a última vale; fila | **Fila de até 2, um por ciclo.** |
| **D4** | `STATE` com a arena inteira ou só mudanças | arena inteira; só cabeças das motos (delta) | **Arena inteira.** |
| **D5** | Taxa de ciclos | 5 a 10 por segundo | **10/s (`tick_ms = 100`)**, configurável por argumento. |
| **D6** | Queda do host | encerrar; migrar o host | **Encerrar de forma controlada.** |
| **D7** | Quem sabe a lista real de jogadores | lobby; host | **Host**, com `MATCH_UPDATE` para o lobby. |
| **D8** | Esperar `READY` de todos antes de começar | sim; não | **Não.** |
| **D9** | Jogador fica conectado ao lobby durante o jogo | sim; não | **Sim**, mas numa thread separada e sem nenhuma dependência. |
| **D10** | Arena e posições iniciais | — | **40 × 20**, motos perto dos cantos, apontando para dentro. |

**D1 — Por que TCP também no jogo.** O jogo roda em rede local, com RTT típico de 1 a 5 ms, muito menor que o ciclo de 100 ms. Perda de pacote em LAN é rara; quando acontece, o TCP retransmite em dezenas de milissegundos e o head-of-line blocking atrasa no máximo um ou dois ciclos, o que o jogador mal percebe. Em troca, o `TURN` (que **não pode** ser perdido, senão a moto não vira) chega sempre e em ordem, sem precisarmos implementar sequência, ACK e retransmissão. Com UDP teríamos de fazer isso tudo pelo menos para o `TURN`. O argumento a favor do UDP (não esperar um `STATE` velho quando já existe um novo) existe, mas pesa pouco com estados pequenos e LAN. Na apresentação, vale mencionar que numa rede com mais perda (internet, Wi-Fi ruim) a escolha poderia mudar: `STATE` por UDP (o mais novo substitui o antigo) e `TURN` por TCP.

**D2 — `cycle` no `TURN`.** O host **não** aplica a direção no ciclo indicado (isso exigiria voltar no tempo e recalcular, e outros jogadores já teriam visto o ciclo antigo). O campo serve para o host **medir o atraso** de cada entrada e registrar no log, o que dá números concretos para a justificativa de latência no relatório. Também deixa claro o que acontece com input atrasado: ele vale no próximo ciclo calculado, nunca antes.

**D3 — Fila de 2.** Com "só a última vale", um jogador que vai para a direita e aperta ↑ e ← rapidamente dentro de 100 ms perderia o ↑ e o ← seria descartado como inversão: a moto seguiria reto. A fila de 2 aplica ↑ num ciclo e ← no seguinte, que é o que o jogador quis. Mais de 2 começa a acumular comandos velhos.

**D4 — Arena inteira.** 40 × 20 = 800 células, cerca de 1 KB por `STATE` em JSON. A 10 ciclos/s e 3 jogadores, são ~30 KB/s saindo do host: insignificante numa LAN. Vantagens: o jogador não precisa reconstruir nada, um `STATE` perdido ou descartado por bug não deixa a tela errada para sempre, e fica trivial provar que todos veem exatamente a mesma arena. A alternativa (mandar só as posições das motos, já que cada uma anda exatamente uma célula por ciclo) é bem menor e funcionaria com TCP, mas transfere ao jogador a responsabilidade de acumular o rastro sem errar. Se a arena crescer muito (ex.: 200 × 100), vale trocar.

**D5 — 10 ciclos/s.** 100 ms é muito maior que o RTT da LAN, então o `TURN` quase sempre chega antes do próximo ciclo (atraso 0 no log). A 5/s o jogo fica lento; acima de 10/s o terminal com `curses` e o tempo de reação humano começam a limitar mais que a rede. O host agenda os ciclos com `time.monotonic()` a partir de um instante inicial (`t0 + N × tick_ms`), e não com `sleep(tick)` repetido, para não acumular deriva.

**D6 — Sem migração de host.** Migrar exigiria que todos tivessem o estado completo e concordassem sobre quem assume e a partir de qual ciclo, ou seja, todo o problema de consenso que o host autoritativo existe para evitar. A especificação exige apenas o encerramento controlado. Fica como "trabalho futuro" no relatório.

**D7 — Host como fonte da verdade.** O lobby não consegue saber se o host aceitou ou recusou alguém. Se o lobby contasse os `JOIN_MATCH`, a lista dele ficaria errada a cada recusa ou desistência. Com `MATCH_UPDATE`, só existe uma lista real.

**D8 — Sem `READY`.** O TCP já garante que o `GAME_START` chega. Esperar confirmação de todos cria um novo caso de falha (e se alguém nunca confirma?) sem ganho real. Quem cair durante a contagem é eliminado no ciclo 1.

**D9 — Conexão com o lobby durante o jogo.** Manter aberta permite voltar à lista ao fim sem reconectar. O cuidado é que a thread do lobby não pode segurar nenhum lock usado pelo jogo, e um erro nela não pode derrubar o processo. Para a demonstração "lobby cai com partida em andamento", isso é exatamente o que vai ser testado.

---

## 9. Se o grupo escolher o Uno (Opção 2)

Continuam valendo: framing (seção 2), lobby (seção 3.1, mas `JOIN_OK` passa a trazer o endereço de **todos** os pares, não só do host), `PING`/`PONG` e o formato de `ERROR`.

Mudam:
- **Topologia:** malha completa (cada par conecta em todos), sem host.
- **Mensagens novas:** `COMMIT` (hash do valor secreto) e `REVEAL` (o valor), uma rodada a cada embaralhamento; `PLAY_CARD`, `DRAW_CARD`, `PASS`; `REJECT_MOVE` (um par avisa que considera uma jogada inválida); `PEER_DOWN` / `REMOVE_PEER` (proposta e confirmação de que um par saiu).
- **Máquinas de estado:** em vez de AGUARDANDO/JOGANDO de um host, cada par passa por `COMMIT` → `REVEAL` → `MINHA_VEZ` / `VEZ_DE_OUTRO`, valida cada jogada recebida e precisa de uma regra de acordo sobre quem saiu (ex.: só remove depois que a maioria dos pares vivos confirmar).

Se a escolha mudar, este documento precisa de uma nova versão das seções 3.2 a 3.6, 5 e 6.

---

## Apêndice A — Tabelas de estados prontas para o código

```python
# protocol.py (trecho) — mesmas tabelas das seções 4.1, 5.3 e 6.1

LOBBY_PERMITIDAS = {
    "NAO_REGISTRADO":      {"REGISTER", "PING"},
    "REGISTRADO":          {"LIST_MATCHES", "CREATE_MATCH", "JOIN_MATCH", "PING"},
    "HOSPEDANDO_ABERTA":   {"LIST_MATCHES", "MATCH_UPDATE", "START_MATCH", "CANCEL_MATCH", "PING"},
    "HOSPEDANDO_INICIADA": {"LIST_MATCHES", "MATCH_UPDATE", "END_MATCH", "PING"},
}

HOST_PERMITIDAS = {   # mensagens que o host aceita de cada conexão de jogador
    "AGUARDANDO_HELLO": {"HELLO", "PING"},
    "NA_SALA":          {"BYE", "TURN", "PING", "PONG", "ERROR"},
    "JOGANDO":          {"TURN", "BYE", "PING", "PONG", "ERROR"},
    "ELIMINADO":        {"TURN", "BYE", "PING", "PONG", "ERROR"},  # TURN ignorado
}

JOGADOR_PERMITIDAS = {  # mensagens que o jogador aceita do host
    "CONECTANDO_HOST":   {"WELCOME", "REJECT", "PING", "ERROR"},
    "AGUARDANDO_INICIO": {"ROSTER", "GAME_START", "GAME_ABORTED", "PING", "PONG", "ERROR"},
    "CONTAGEM":          {"STATE", "GAME_ABORTED", "PING", "PONG", "ERROR"},
    "JOGANDO":           {"STATE", "GAME_OVER", "GAME_ABORTED", "PING", "PONG", "ERROR"},
    "ESPECTADOR":        {"STATE", "GAME_OVER", "GAME_ABORTED", "PING", "PONG", "ERROR"},
}

MAX_LINHA      = 16 * 1024   # bytes
PING_INTERVALO = 1.0         # s sem enviar nada → manda PING
TIMEOUT_VIDA   = 3.0         # s sem receber nada → conexão morta
TIMEOUT_HELLO  = 5.0         # s para o HELLO / WELCOME
TIMEOUT_LOBBY  = 5.0         # s por resposta do lobby
ESPERA_START   = 2.0         # s máx. esperando START_OK
MAX_ERROS      = 10          # erros seguidos antes de desconectar
```

## Apêndice B — Tempos e limites

| Parâmetro | Valor | Onde |
|---|---|---|
| Delimitador | `\n` | todas as conexões |
| Linha máxima | 16 KiB | todas |
| Heartbeat | `PING` após 1 s sem enviar | jogador ↔ host (opcional com o lobby) |
| Timeout de vida | 3 s sem receber | jogador ↔ host |
| Prazo do `HELLO`/`WELCOME` | 5 s | jogador ↔ host |
| Prazo de resposta do lobby | 5 s | cliente → lobby |
| Espera do `START_OK` | 2 s (depois inicia mesmo assim) | host → lobby |
| Ciclo | 100 ms (`tick_ms`) | jogo |
| Contagem inicial | 3000 ms (`start_in_ms`) | jogo |
| Fila de `TURN` | 2 por jogador, 1 consumido por ciclo | host |
| Jogadores | mín. 3, máx. 4 | partida |
| Erros seguidos tolerados | 10 | lobby e host |
