"""
Service layer: business logic isolated from the HTTP endpoints (api/).

  - correlation_service: management of conversations and participants.
  - status_mapper: translation of status vocabularies between systems.
  - outbox_service: table-based transactional queue (outbox pattern).
  - dispatcher: actual HTTP delivery to each external system.
  - secrets: secret resolution (Secret Manager / local env).
  - audit_service: append-only event log for diagnostics.
"""
