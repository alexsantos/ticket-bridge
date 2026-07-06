# Ticket Bridge

Serviço leve de correlação de tickets entre múltiplas aplicações de suporte,
desenhado para substituir a função de "hub central" que o OSTicket
desempenhava implicitamente quando duas (ou mais) equipas usavam a mesma
ferramenta para acompanhar processos entre si.

Cada equipa continua a usar a sua aplicação de suporte à operação. O Ticket
Bridge fica no meio: recebe eventos de criação/atualização de um sistema,
mantém a correlação (`conversation_id`) e distribui ("fan-out") a mudança de
estado a todos os outros sistemas envolvidos nessa conversa.

Ver **`CLAUDE.md`** para o racional completo das decisões de arquitetura.

---

## 1. Arquitetura em resumo

```
Sistema A ──POST /api/v1/events──▶ ┌────────────────────┐
                                    │   Ticket Bridge     │
Sistema B ──POST /api/v1/events──▶ │  (Cloud Run, FastAPI)│
                                    │                      │
Sistema C ──POST /api/v1/events──▶ │  PostgreSQL:         │
                                    │   - conversations     │
                                    │   - participants       │
            ◀── outbox (HTTP) ──── │   - outbox (fila)       │
                                    │   - audit_log            │
                                    └──────────┬───────────┘
                                               │
                                     Cloud Scheduler
                                     (dispara /api/v1/sync
                                      a cada 1-2 min)
```

- **Sem broker externo** (RabbitMQ/Pub-Sub): a fila é uma tabela Postgres
  (`outbox`), processada com `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Stateless**: qualquer instância Cloud Run pode processar qualquer
  pedido; o estado vive todo na base de dados.
- **N sistemas, não apenas 2**: `conversation_participants` permite associar
  quantos sistemas forem necessários à mesma conversa.

---

## 2. Estrutura do projeto

```
ticket-bridge/
├── app/
│   ├── main.py                  # arranque da app, routers, health check
│   ├── config.py                # configuração via variáveis de ambiente
│   ├── database.py              # pool de ligações Postgres (psycopg3)
│   ├── models.py                # modelos de domínio internos
│   ├── schemas.py                # contratos de request/response da API
│   ├── security.py              # autenticação (API keys, segredo do scheduler)
│   ├── api/
│   │   ├── events.py            # POST /api/v1/events   (entrada)
│   │   ├── sync.py              # POST /api/v1/sync     (processa outbox)
│   │   ├── systems.py           # CRUD /api/v1/systems  (configuração)
│   │   ├── conversations.py     # GET  /api/v1/conversations
│   │   └── audit.py             # GET  /api/v1/audit
│   ├── services/
│   │   ├── correlation_service.py  # gestão de conversas/participantes
│   │   ├── status_mapper.py        # tradução de vocabulários de estado
│   │   ├── outbox_service.py       # fila transacional (outbox pattern)
│   │   ├── dispatcher.py           # entrega HTTP a cada sistema
│   │   ├── secrets.py              # resolução de segredos (Secret Manager / env)
│   │   └── audit_service.py        # escrita/leitura de audit_log
│   └── frontend/
│       ├── index.html           # painel de configuração/auditoria
│       ├── style.css
│       └── app.js
├── migrations/
│   ├── 001_initial_schema.sql   # schema completo (correr primeiro)
│   └── 002_seed_example.sql     # dados de exemplo (só desenvolvimento)
├── tests/
│   └── test_status_mapper.py
├── requirements.txt
├── Dockerfile
├── .env.example
├── CLAUDE.md                    # racional de arquitetura
└── README.md                    # este ficheiro
```

Cada ficheiro `.py` tem um docstring no topo a explicar a sua
responsabilidade — comece por aí ao explorar o código no PyCharm.

---

## 3. Correr localmente (desenvolvimento)

### 3.1. Pré-requisitos
- Python 3.12+
- PostgreSQL 15+ local (ou via Docker)
- PyCharm (abrir a pasta `ticket-bridge/` como projeto; marcar `app` como
  "Sources Root" se necessário)

### 3.2. Passos

```bash
# 1. Criar ambiente virtual
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Criar base de dados local
createdb ticketbridge

# 4. Correr as migrations
psql "postgresql://localhost/ticketbridge" -f migrations/001_initial_schema.sql
psql "postgresql://localhost/ticketbridge" -f migrations/002_seed_example.sql   # opcional, dados de exemplo

# 5. Configurar variáveis de ambiente
cp .env.example .env
# editar .env com as credenciais da BD local

# 6. Arrancar o servidor
uvicorn app.main:app --reload --port 8080
```

Aceder a:
- **Frontend**: http://localhost:8080/
- **Documentação interativa da API (Swagger)**: http://localhost:8080/docs
- **Health check**: http://localhost:8080/health

### 3.3. Testar o fluxo de ponta a ponta localmente

```bash
# Criar uma conversa a partir do "sistema_a" (usa a chave de exemplo do seed)
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-sistema-a" \
  -d '{"ref_externa": "TICKET-123", "status": "novo", "assunto": "Impressora do laboratório com falha"}'

# Nota: como "sistema_a" ainda não tem outros participantes na conversa,
# nada é enviado à outbox nesta primeira chamada - é preciso um segundo
# sistema associar-se à mesma conversation_id devolvida na resposta acima.
```

Para os testes automatizados:
```bash
pytest tests/ -v
```

---

## 4. Instalação em GCP (produção)

Esta secção assume um projeto GCP já existente e `gcloud` autenticado
(`gcloud auth login`, `gcloud config set project SEU_PROJETO_ID`).

### 4.1. Ativar APIs necessárias

```bash
gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    cloudscheduler.googleapis.com \
    artifactregistry.googleapis.com
```

### 4.2. Base de dados (Cloud SQL - PostgreSQL)

```bash
# Criar a instância (ajustar tier conforme carga esperada - db-f1-micro
# chega perfeitamente para este volume de tráfego)
gcloud sql instances create ticket-bridge-db \
    --database-version=POSTGRES_16 \
    --tier=db-f1-micro \
    --region=europe-west1 \
    --storage-auto-increase

# Criar a base de dados e o utilizador
gcloud sql databases create ticketbridge --instance=ticket-bridge-db
gcloud sql users create ticketbridge \
    --instance=ticket-bridge-db \
    --password="DEFINA_UMA_PASSWORD_FORTE_AQUI"

# Correr as migrations via Cloud SQL Auth Proxy
cloud-sql-proxy SEU_PROJETO_ID:europe-west1:ticket-bridge-db &
psql "postgresql://ticketbridge:PASSWORD@localhost:5432/ticketbridge" \
    -f migrations/001_initial_schema.sql
# (002_seed_example.sql é só para desenvolvimento - não correr em produção)
```

### 4.3. Segredos (Secret Manager)

Cada sistema externo tem uma referência de segredo (`secret_ref`) na sua
configuração — é o valor que o dispatcher usa para autenticar chamadas de
saída. Também o segredo partilhado do Cloud Scheduler e a password da BD
devem viver aqui.

```bash
echo -n "PASSWORD_DA_BD" | gcloud secrets create db-password --data-file=-
echo -n "$(openssl rand -base64 32)" | gcloud secrets create scheduler-shared-secret --data-file=-

# Um segredo por sistema externo, nome igual ao 'secret_ref' configurado
# no frontend para esse sistema:
echo -n "CHAVE_DE_SAIDA_SISTEMA_A" | gcloud secrets create sistema_a_outbound_key --data-file=-
echo -n "TOKEN_DE_SAIDA_SISTEMA_B" | gcloud secrets create sistema_b_outbound_token --data-file=-
```

### 4.4. Service Account dedicada

```bash
gcloud iam service-accounts create ticket-bridge-sa \
    --display-name="Ticket Bridge Cloud Run"

# Acesso ao Cloud SQL
gcloud projects add-iam-policy-binding SEU_PROJETO_ID \
    --member="serviceAccount:ticket-bridge-sa@SEU_PROJETO_ID.iam.gserviceaccount.com" \
    --role="roles/cloudsql.client"

# Acesso aos segredos
gcloud projects add-iam-policy-binding SEU_PROJETO_ID \
    --member="serviceAccount:ticket-bridge-sa@SEU_PROJETO_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

### 4.5. Build e deploy no Cloud Run

```bash
# Build da imagem (Artifact Registry)
gcloud artifacts repositories create ticket-bridge \
    --repository-format=docker --location=europe-west1

gcloud builds submit --tag \
    europe-west1-docker.pkg.dev/SEU_PROJETO_ID/ticket-bridge/app:latest

# Deploy
gcloud run deploy ticket-bridge \
    --image=europe-west1-docker.pkg.dev/SEU_PROJETO_ID/ticket-bridge/app:latest \
    --region=europe-west1 \
    --service-account=ticket-bridge-sa@SEU_PROJETO_ID.iam.gserviceaccount.com \
    --add-cloudsql-instances=SEU_PROJETO_ID:europe-west1:ticket-bridge-db \
    --set-env-vars="ENVIRONMENT=production,GOOGLE_CLOUD_PROJECT=SEU_PROJETO_ID" \
    --set-env-vars="DATABASE_URL=postgresql://ticketbridge:PASSWORD@/ticketbridge?host=/cloudsql/SEU_PROJETO_ID:europe-west1:ticket-bridge-db" \
    --set-secrets="SCHEDULER_SHARED_SECRET=scheduler-shared-secret:latest" \
    --no-allow-unauthenticated \
    --min-instances=0 \
    --max-instances=3
```

> **Nota sobre a password da BD na `DATABASE_URL`**: para produção real,
> prefira montar a `DATABASE_URL` completa também via `--set-secrets` em vez
> de a passar em `--set-env-vars`, ou usar o
> [Cloud SQL Python Connector](https://cloud.google.com/sql/docs/postgres/connect-connectors)
> em vez de uma connection string com password embutida.

### 4.6. Cloud Scheduler (dispara o `/sync`)

```bash
# Dar à Scheduler uma identidade que possa invocar o serviço Cloud Run
gcloud iam service-accounts create ticket-bridge-scheduler \
    --display-name="Ticket Bridge Scheduler Invoker"

gcloud run services add-iam-policy-binding ticket-bridge \
    --region=europe-west1 \
    --member="serviceAccount:ticket-bridge-scheduler@SEU_PROJETO_ID.iam.gserviceaccount.com" \
    --role="roles/run.invoker"

SERVICE_URL=$(gcloud run services describe ticket-bridge --region=europe-west1 --format='value(status.url)')

gcloud scheduler jobs create http ticket-bridge-sync \
    --location=europe-west1 \
    --schedule="*/2 * * * *" \
    --uri="${SERVICE_URL}/api/v1/sync" \
    --http-method=POST \
    --oidc-service-account-email="ticket-bridge-scheduler@SEU_PROJETO_ID.iam.gserviceaccount.com" \
    --headers="X-Scheduler-Secret=VALOR_DO_SEGREDO_scheduler-shared-secret"
```

> **Segurança do `/sync`**: o exemplo acima combina OIDC nativo do Cloud
> Scheduler (`--no-allow-unauthenticated` no Cloud Run + `--oidc-service-account-email`)
> com o segredo partilhado no header, como defesa em profundidade. Em muitos
> casos o OIDC sozinho já é suficiente; o segredo partilhado é uma camada
> extra simples de manter.

### 4.7. Registar os sistemas reais

Depois do deploy, aceder ao frontend em `${SERVICE_URL}/` (autenticado via
IAM — ver secção seguinte) e criar os sistemas reais no separador
"Sistemas", com os `secret_ref` a apontar para os segredos criados em 4.3.

---

## 5. Segurança do frontend de configuração

Os endpoints de configuração (`/api/v1/systems`) e auditoria
(`/api/v1/conversations`, `/api/v1/audit`) **não têm autenticação própria**
no código — o Cloud Run é implantado com `--no-allow-unauthenticated`, pelo
que só quem tiver o papel `roles/run.invoker` consegue chamar o serviço.

Para acesso humano ao frontend, as opções mais simples são:
- **Identity-Aware Proxy (IAP)** à frente do Cloud Run — recomendado, dá
  login com conta Google corporativa sem código adicional.
- Túnel autenticado via `gcloud run services proxy ticket-bridge` para
  acesso administrativo pontual sem expor o serviço publicamente.

Os endpoints `/api/v1/events` (chamados pelos sistemas externos) e
`/api/v1/sync` (chamado pelo Scheduler) têm a sua própria autenticação
(API key por sistema / segredo do scheduler), independente do IAM do Cloud
Run — por isso mesmo com `--no-allow-unauthenticated` pode ser necessário
avaliar caso a caso se esses sistemas conseguem autenticar-se via IAM
também, ou se precisam de `--allow-unauthenticated` com a autenticação
aplicativa (API key) como única barreira.

---

## 6. Operação do dia-a-dia

- **Adicionar um novo sistema (ex: 3ª equipa)**: separador "Sistemas" no
  frontend → "Novo sistema". Não requer deploy de código.
- **Diagnosticar uma entrega falhada**: separador "Auditoria", filtrar por
  sistema; entradas `entrega_falha` mostram o erro HTTP/rede. A linha
  correspondente na tabela `outbox` mantém-se `pending` até
  `max_tentativas`, altura em que passa a `failed` para intervenção manual.
- **Reprocessar uma entrega falhada manualmente**:
  ```sql
  UPDATE outbox SET status = 'pending', tentativas = 0
  WHERE id = <id_da_linha>;
  ```
  A próxima execução do `/sync` volta a tentar.

---

## 7. Próximos passos sugeridos (fora do âmbito deste esqueleto)

- Autenticação humana ao frontend (IAP).
- Alertas (ex: Telegram, à semelhança de outros projetos internos) quando
  entradas da outbox atingem `status = 'failed'`.
- Paginação nos endpoints de listagem (`conversations`, `audit`) para
  volumes de produção elevados.
- Validação de assinatura de webhook (HMAC) em vez de apenas API key,
  se algum sistema externo suportar.
