"""
REST API routers, one file per functional area:

  - events.py: receiving inbound events from external systems.
  - sync.py: outbox processing (called by Cloud Scheduler).
  - systems.py: CRUD for federated system configuration and topic subscriptions.
  - topics.py: CRUD for ticket topics/categories (e.g. INFRA, SPM, SALES).
  - conversations.py: querying conversations and their participants.
  - audit.py: querying the audit trail.
"""
