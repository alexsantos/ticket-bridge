"""
Camada de serviços: lógica de negócio isolada dos endpoints HTTP (api/).

  - correlation_service: gestão de conversas e participantes.
  - status_mapper: tradução de vocabulários de estado entre sistemas.
  - outbox_service: fila transacional baseada em tabela (outbox pattern).
  - dispatcher: entrega HTTP efetiva a cada sistema externo.
  - secrets: resolução de segredos (Secret Manager / env local).
  - audit_service: registo append-only de eventos para diagnóstico.
"""
