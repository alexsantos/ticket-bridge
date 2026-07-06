# CLAUDE.md — Ticket Bridge

Este ficheiro é a fonte de verdade sobre **porquê** o sistema está desenhado
como está. Serve para retomar contexto em sessões futuras (humanas ou com
assistência de IA) sem repetir a discussão de arquitetura desde o início.

## Problema original

Duas equipas usavam o OSTicket como sistema partilhado: uma equipa cria um
ticket para a outra, ambas acompanham o estado no mesmo sítio. O OSTicket
vai ser desligado. Cada equipa vai passar a usar a sua própria aplicação de
suporte à operação (diferente uma da outra), e é preciso preservar a
capacidade de correlacionar e sincronizar o estado de um "processo" entre
os dois (e potencialmente mais) sistemas.

## Decisão 1 — Bridge central, não ligação direta ponto-a-ponto

**Alternativa rejeitada**: cada sistema expõe uma API e chama diretamente a
API do outro.

**Porquê foi rejeitada**:
- Acopla os dois sistemas ao contrato um do outro; mudar o schema de um
  obriga o outro a reagir.
- Não escala para N sistemas — ligação direta é O(N²) integrações.
- Sem dono único da correlação, não há sítio central para auditoria,
  reprocessamento, ou diagnóstico quando uma sincronização falha a meio.
- Risco de loop de notificação (A atualiza → notifica B → B atualiza →
  notifica A → ...) sem mecanismo natural de corte.

**Decisão tomada**: um serviço-ponte (bridge) neutro, com uma tabela de
correlação própria (`conversations` / `conversation_participants`) que não
pertence a nenhum dos sistemas. Isto recria, de forma leve, o papel de "hub
de verdade" que o OSTicket desempenhava implicitamente — mas agora
explícito, federado, e independente do número de sistemas envolvidos.

## Decisão 2 — Outbox pattern em Postgres, não RabbitMQ/Pub-Sub

**Alternativa rejeitada**: fila de mensagens dedicada (RabbitMQ, como usado
noutros projetos do autor, ou Cloud Pub/Sub).

**Porquê foi rejeitada para este caso específico**:
- O requisito explícito era "sistema muito leve que possa correr apenas em
  Cloud Run". RabbitMQ pressupõe um consumidor sempre vivo, o que não
  combina com Cloud Run a escalar a zero entre picos de tráfego.
- Um broker externo é mais um componente a operar, atualizar e monitorizar
  para um volume de eventos que é, por natureza, baixo (mudanças de estado
  de tickets entre duas equipas, não um pipeline de alto débito).

**Decisão tomada**: a tabela `outbox` funciona como fila. A escrita do
evento de negócio (conversa/participante) e a inserção na fila acontecem na
**mesma transação Postgres**, eliminando o problema de "dual write" que
existiria com um broker externo separado da base de dados transacional.
`SELECT ... FOR UPDATE SKIP LOCKED` garante que múltiplas invocações
concorrentes do endpoint `/sync` nunca processam a mesma linha duas vezes,
sem coordenação externa.

**Trade-off aceite conscientemente**: latência de sincronização na ordem
dos minutos (cadência do Cloud Scheduler), não segundos. Para sincronizar
estado de tickets entre equipas — não para eventos clínicos em tempo real —
isto é adequado. Se o requisito de latência mudar no futuro, a migração
natural seria substituir o Cloud Scheduler por Pub/Sub push, mantendo a
tabela `outbox` como registo de auditoria/replay.

## Decisão 3 — Suporte a N sistemas desde o início, não só A/B

Quando surgiu a pergunta "e se entrar uma terceira aplicação?", a resposta
não foi "adicionar mais colunas" mas sim generalizar o modelo:
- `conversation_participants` é uma tabela associativa (conversa ↔ sistema),
  não colunas fixas `sistema_a_ref` / `sistema_b_ref`.
- O fan-out ao inserir na outbox é "todos os participantes da conversa
  exceto a origem", não "o outro lado do par".
- A configuração de cada sistema (`systems`) inclui tudo o que é específico
  dele (URL, autenticação, mapeamento de estados, template de payload), para
  que adicionar um sistema novo seja uma operação de configuração via
  frontend, não uma alteração de código.

Custo desta decisão: uma tabela extra e uma FK a mais, praticamente nulo.
Benefício: evita reescrever o modelo de dados quando aparecer o 3º ou 4º
sistema — o que, dado o histórico de integrações do autor (Mirth Connect
ligando múltiplos sistemas clínicos), era uma hipótese realista, não
hipotética.

## Decisão 4 — Vocabulário de estado interno + mapeamento por sistema

Cada sistema externo tem o seu próprio vocabulário de estados (ex: "Aberto"
vs "NEW"). Em vez de o bridge conhecer todos os vocabulários de todos os
sistemas em código, cada sistema define um `status_mapping`
(`{interno: externo}`) na sua configuração. O bridge só conhece o
vocabulário interno canónico (`novo`, `em_progresso`, `aguarda_terceiros`,
`resolvido`, `fechado` — ver `status_mapper.py`).

Isto significa que a lógica de tradução nunca cresce com o número de
sistemas: cresce a configuração, não o código.

## Decisão 5 — Prevenção de loop

Cada linha da `outbox` regista explicitamente `origem` (quem gerou o
evento) e `destino` (para quem vai). O fan-out ao processar um evento
recebido exclui sempre a origem da lista de destinos. Isto por si só evita
o eco imediato (A→B→A na mesma operação).

**Nota para evolução futura**: o esqueleto atual não implementa ainda
deduplicação de eventos semanticamente idênticos vindos de fontes diferentes
em janelas curtas de tempo (ex: os dois sistemas atualizam o mesmo campo
quase em simultâneo). Se isso se tornar um problema real em produção, o
sítio natural para resolver é `correlation_service.find_or_create_conversation`,
comparando o `status_local` já guardado com o novo antes de gerar fan-out.

## Decisão 6 — Sem ORM

Interação com a base de dados em SQL explícito via `psycopg3` assíncrono,
não SQLAlchemy nem outro ORM. Com 6 tabelas e queries relativamente simples,
um ORM acrescentaria uma camada de abstração sem benefício real, e a lógica
de concorrência (`FOR UPDATE SKIP LOCKED`) é mais direta de escrever e
raciocinar em SQL puro do que através de abstrações de ORM.

## Decisão 7 — Frontend sem framework

O frontend de configuração/auditoria é HTML + CSS + JavaScript vanilla,
servido como ficheiros estáticos pelo próprio FastAPI (`StaticFiles`). Não
há build step (webpack/vite/etc.). Justificação: este é um painel
administrativo interno de baixo tráfego, não uma aplicação de utilizador
final — o custo de manutenção de um pipeline de build não se justifica face
ao ganho.

## O que este esqueleto assume e deixa por decidir

- **Autenticação humana ao frontend**: o código não implementa login;
  assume-se IAP ou proxy de autenticação à frente do Cloud Run (ver
  README.md secção 5). Decisão adiada deliberadamente — depende de como a
  organização já gere acesso a ferramentas internas.
- **Reconciliação de conflitos de campo** (quem "ganha" quando os dois
  sistemas escrevem o mesmo campo quase em simultâneo): não implementado.
  O esqueleto assume que cada sistema é autoritativo sobre o seu próprio
  `status_local`, e o `status_geral` da conversa é informativo, não uma
  fonte de verdade normativa. Se for necessário um dono de campo explícito
  por direção (como discutido inicialmente), o sítio a estender é
  `correlation_service.py`.
- **Alertas de falha** (ex: Telegram, como usado noutros projetos do autor):
  não implementado neste esqueleto; o sítio natural é dentro de
  `outbox_service.mark_failed`, quando uma entrada atinge `max_tentativas`.

## Convenções do código

- Identificadores (nomes de funções, variáveis, tabelas, colunas) em
  inglês/neutro técnico; comentários, docstrings e texto orientado ao
  utilizador (frontend, mensagens de erro) em português europeu (pt-PT).
- Cada ficheiro `.py` tem um docstring de módulo no topo a explicar a sua
  responsabilidade — ler esses docstrings é a forma mais rápida de navegar
  o projeto pela primeira vez.
- `app/services/` contém lógica de negócio pura, sem dependência de FastAPI;
  `app/api/` contém só orquestração HTTP fina sobre os serviços.
