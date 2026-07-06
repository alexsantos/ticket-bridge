#!/usr/bin/env bash
# walkthrough.sh
# --------------
# Runs the scenario documented in README.md against a running local
# instance: system_a opens an INFRA ticket, system_b (subscribed to INFRA)
# gets notified and links its own ticket, system_a updates the status, and
# system_b gets notified again - this time as an update to its own known
# ticket rather than a "please open one" notification. Then system_b
# resolves it, fanning back out to system_a.
#
# Uses the seed data from migrations/002_seed_example.sql as-is (fictional
# example.local base_urls), so the /api/v1/sync calls below will report
# delivery failures - that's expected, and explained inline. To see a real
# delivery happen, run live_delivery_demo.sh instead.
#
# Prerequisites: app running locally with 001+002 migrations applied
# (see README.md section 3).

set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="${BASE_URL:-http://localhost:8080}"

if [ -f ../.env ]; then
  set -a; source ../.env; set +a
fi
: "${SCHEDULER_SHARED_SECRET:?Set SCHEDULER_SHARED_SECRET (e.g. via .env) before running this script.}"

json() { python3 -m json.tool; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }

echo "### 1. system_a creates an INFRA ticket (no conversation_id yet)"
RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{"external_ref": "TICKET-1001", "status": "new", "subject": "Print queue stuck on floor 3", "topic_code": "INFRA"}')
echo "$RESPONSE" | json
CONVERSATION_ID=$(echo "$RESPONSE" | field conversation_id)
echo
echo "    -> system_b is subscribed to INFRA and has no ticket linked yet,"
echo "       so the outbox now holds an 'on_create' payload for it:"
echo "       {\"action\": \"CREATE\", \"source_ref\": \"TICKET-1001\", \"source_system\": \"system_a\", \"status\": \"NEW\", \"correlation\": \"$CONVERSATION_ID\"}"
echo

echo "### 2. Trigger sync (the in-process scheduler would do this automatically within SYNC_INTERVAL_SECONDS)"
curl -s -X POST "$BASE_URL/api/v1/sync" -H "X-Scheduler-Secret: $SCHEDULER_SHARED_SECRET" | json
echo "    -> delivery to system_b's example.local URL fails (it's fictional) -"
echo "       run live_delivery_demo.sh to watch a real delivery instead."
echo

echo "### 3. system_b links its own ticket to the same conversation"
curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-b" \
  -d "{\"conversation_id\": \"$CONVERSATION_ID\", \"external_ref\": \"INC-2001\", \"status\": \"new\"}" | json
echo
echo "    -> this ALSO fans out to system_a (already linked as TICKET-1001),"
echo "       since fan-out excludes only the event's source, not other"
echo "       already-linked participants - system_a gets its own on_update:"
echo "       {\"action\": \"update_ticket\", \"ticket_ref\": \"TICKET-1001\", \"status\": \"Open\", \"conversation_id\": \"$CONVERSATION_ID\"}"
echo

echo "### 4. system_a updates the ticket to in_progress"
curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d "{\"conversation_id\": \"$CONVERSATION_ID\", \"external_ref\": \"TICKET-1001\", \"status\": \"in_progress\"}" | json
echo
echo "    -> system_b now HAS a ticket linked (INC-2001), so this time the"
echo "       outbox holds an 'on_update' payload instead:"
echo "       {\"action\": \"UPDATE\", \"incident_id\": \"INC-2001\", \"status\": \"IN_PROGRESS\", \"correlation\": \"$CONVERSATION_ID\"}"
echo

echo "### 5. system_b resolves its ticket - fans back out to system_a"
curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-b" \
  -d "{\"conversation_id\": \"$CONVERSATION_ID\", \"external_ref\": \"INC-2001\", \"status\": \"resolved\"}" | json
echo
echo "    -> system_a already has a ticket linked (TICKET-1001), so it gets"
echo "       an 'on_update' payload in its own vocabulary/shape:"
echo "       {\"action\": \"update_ticket\", \"ticket_ref\": \"TICKET-1001\", \"status\": \"Resolved\", \"conversation_id\": \"$CONVERSATION_ID\"}"
echo

echo "### 6. Trigger sync again and inspect the final state"
curl -s -X POST "$BASE_URL/api/v1/sync" -H "X-Scheduler-Secret: $SCHEDULER_SHARED_SECRET" | json
echo
echo "### Conversation state:"
curl -s "$BASE_URL/api/v1/conversations/$CONVERSATION_ID" | json
echo
echo "### Audit trail:"
curl -s "$BASE_URL/api/v1/audit?conversation_id=$CONVERSATION_ID" | json
