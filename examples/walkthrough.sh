#!/usr/bin/env bash
# walkthrough.sh
# --------------
# Runs the scenario documented in README.md against a running local
# instance - Ticket Bridge's flagship use case: system_a (a clinical team)
# flags that a patient has no insurance on file; system_b (patient
# registration/insurance) picks it up, validates it, and resolves it;
# system_a confirms and closes the case. All of this happens almost like a
# chat, via the shared conversation_id - including a human-written note at
# each step, carried in 'metadata' alongside any structured data.
#
# Uses the seed data from migrations/002_seed_example.sql as-is (fictional
# example.local base_urls), so the /api/v1/sync calls below will report
# delivery failures - that's expected, and explained inline. To see a real
# delivery happen, run live_delivery_demo.sh instead.
#
# Prerequisites: app running locally with 001+002+003 migrations applied
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

echo "### 1. system_a flags that the patient has no insurance on file (no conversation_id yet)"
RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{
        "external_ref": "CASE-4471",
        "status": "new",
        "subject": "Patient #4471 - no insurance on file",
        "topic_code": "PATIENT_ADMIN",
        "metadata": {"note": "Patient checked in without an insurance card - please verify coverage."}
      }')
echo "$RESPONSE" | json
CONVERSATION_ID=$(echo "$RESPONSE" | field conversation_id)
echo
echo "    -> system_b is subscribed to PATIENT_ADMIN and has no case linked"
echo "       yet, so the outbox now holds a 'ticket.created' payload for it,"
echo "       note included - a person always writes something:"
echo "       {\"event\": \"ticket.created\", \"conversation_id\": \"$CONVERSATION_ID\", \"status\": \"new\", \"source_system\": \"system_a\", \"source_ref\": \"CASE-4471\", \"external_ref\": null, \"conversation_subject\": \"Patient #4471 - no insurance on file\", \"metadata\": {\"note\": \"Patient checked in without an insurance card - please verify coverage.\"}}"
echo

echo "### 2. Trigger sync (the in-process scheduler would do this automatically within SYNC_INTERVAL_SECONDS)"
curl -s -X POST "$BASE_URL/api/v1/sync" -H "X-Scheduler-Secret: $SCHEDULER_SHARED_SECRET" | json
echo "    -> delivery to system_b's example.local URL fails (it's fictional) -"
echo "       run live_delivery_demo.sh to watch a real delivery instead."
echo

echo "### 3. system_b picks it up, validates the patient, reports 'under way'"
curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-b" \
  -d "{
        \"conversation_id\": \"$CONVERSATION_ID\",
        \"external_ref\": \"INSVER-8842\",
        \"status\": \"in_progress\",
        \"metadata\": {\"note\": \"On it - contacting the insurer now to confirm coverage.\"}
      }" | json
echo
echo "    -> this fans out to system_a (already linked as CASE-4471) - the"
echo "       update system_a 'receives' in the chat, note included:"
echo "       {\"event\": \"ticket.updated\", \"conversation_id\": \"$CONVERSATION_ID\", \"status\": \"in_progress\", \"source_system\": \"system_b\", \"source_ref\": \"INSVER-8842\", \"external_ref\": \"CASE-4471\", \"conversation_subject\": \"Patient #4471 - no insurance on file\", \"metadata\": {\"note\": \"On it - contacting the insurer now to confirm coverage.\"}}"
echo

echo "### 4. system_b finds the insurance number and resolves its case"
echo "    (the actual goal of the exchange - the insurance number travels"
echo "     in 'metadata', alongside the note the clerk actually typed)"
curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-b" \
  -d "{
        \"conversation_id\": \"$CONVERSATION_ID\",
        \"external_ref\": \"INSVER-8842\",
        \"status\": \"resolved\",
        \"metadata\": {\"insurance_number\": \"INS-2298104\", \"note\": \"The insurance number is now configured.\"}
      }" | json
echo
echo "    -> system_a gets 'ticket.updated' again, same fixed shape - but"
echo "       this time 'metadata' carries both the structured result and"
echo "       the human note that came with it:"
echo "       {\"event\": \"ticket.updated\", \"conversation_id\": \"$CONVERSATION_ID\", \"status\": \"resolved\", \"source_system\": \"system_b\", \"source_ref\": \"INSVER-8842\", \"external_ref\": \"CASE-4471\", \"conversation_subject\": \"Patient #4471 - no insurance on file\", \"metadata\": {\"insurance_number\": \"INS-2298104\", \"note\": \"The insurance number is now configured.\"}}"
echo

echo "### 5. system_a confirms and closes the case - closing the loop"
curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d "{
        \"conversation_id\": \"$CONVERSATION_ID\",
        \"external_ref\": \"CASE-4471\",
        \"status\": \"closed\",
        \"metadata\": {\"note\": \"Confirmed, patient record updated. Closing the case - thanks!\"}
      }" | json
echo
echo "    -> system_b gets 'ticket.updated' with status 'closed' and the"
echo "       closing note - both sides now agree the case is done:"
echo "       {\"event\": \"ticket.updated\", \"conversation_id\": \"$CONVERSATION_ID\", \"status\": \"closed\", \"source_system\": \"system_a\", \"source_ref\": \"CASE-4471\", \"external_ref\": \"INSVER-8842\", \"conversation_subject\": \"Patient #4471 - no insurance on file\", \"metadata\": {\"note\": \"Confirmed, patient record updated. Closing the case - thanks!\"}}"
echo

echo "### 6. Trigger sync again and inspect the final state"
curl -s -X POST "$BASE_URL/api/v1/sync" -H "X-Scheduler-Secret: $SCHEDULER_SHARED_SECRET" | json
echo
echo "### Conversation state:"
curl -s "$BASE_URL/api/v1/conversations/$CONVERSATION_ID" | json
echo
echo "### Audit trail:"
curl -s "$BASE_URL/api/v1/audit?conversation_id=$CONVERSATION_ID" | json
