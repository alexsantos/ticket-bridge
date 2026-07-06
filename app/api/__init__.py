"""
Routers da API REST, um ficheiro por área funcional:

  - events.py: receção de eventos de entrada dos sistemas externos.
  - sync.py: processamento da outbox (chamado pelo Cloud Scheduler).
  - systems.py: CRUD de configuração dos sistemas federados.
  - conversations.py: consulta de conversas e respetivos participantes.
  - audit.py: consulta do registo de auditoria.
"""
