"""
Ticket Bridge
=============

Lightweight ticket correlation service across N support applications,
designed to replace the "central hub" role that OSTicket used to play
implicitly.

Architecture (see CLAUDE.md for the full rationale):
    - Stateless FastAPI, runs on Cloud Run (scales to zero).
    - PostgreSQL (Cloud SQL) as the single source of truth and transactional
      queue (outbox pattern) - no RabbitMQ/Pub-Sub.
    - Cloud Scheduler periodically triggers the sync endpoint that
      processes the outbox and delivers pending events to each system.
    - Supports fan-out to N systems per conversation (not just an A/B pair).
"""
