"""
Service layer: business logic isolated from the HTTP endpoints (api/).

  - correlation_service: management of conversations, participants, and
    topic-subscription-based fan-out destination resolution.
  - payload_builder: builds the fixed outbound payload sent to a
    destination system (see schemas.OutboundTicketEvent).
  - outbox_service: table-based transactional queue (outbox pattern).
  - sync_service: outbox batch processing (called by scheduler.py and
    api/sync.py).
  - dispatcher: actual HTTP delivery to each external system.
  - secrets: secret resolution (Secret Manager / local env).
  - audit_service: append-only event log for diagnostics.
"""
