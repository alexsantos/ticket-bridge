#!/usr/bin/env bash
# live_delivery_demo.sh
# ----------------------
# Temporarily points system_b's base_url at a local mock receiver
# (mock_receiver.py) so you can watch a REAL delivery happen end-to-end,
# instead of it failing against the fictional example.local URL from the
# seed data. Restores system_b's original base_url automatically on exit,
# whether the script succeeds or fails.
#
# If Ticket Bridge itself runs inside a container, replace "localhost"
# below with host.docker.internal (Mac/Windows) or the container's
# gateway address (Linux).
#
# Prerequisites: app running locally with 001+002 migrations applied
# (see README.md section 3). Changing system_b's base_url needs an admin
# session: set ADMIN_PASSWORD (and ADMIN_USERNAME if not "admin"), or
# you'll be prompted - see _admin_session.sh.

set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="${BASE_URL:-http://localhost:8080}"
RECEIVER_PORT="${RECEIVER_PORT:-9000}"

if [ -f ../.env ]; then
  set -a; source ../.env; set +a
fi

json() { python3 -m json.tool; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }

source ./_admin_session.sh
admin_login

echo "==> Starting mock receiver on port $RECEIVER_PORT"
python3 mock_receiver.py "$RECEIVER_PORT" &
RECEIVER_PID=$!
sleep 1

echo "==> Reading system_b's current base_url (to restore afterwards)"
ORIGINAL_BASE_URL=$(admin_curl "$BASE_URL/api/v1/systems/system_b" | field base_url)
echo "    was: $ORIGINAL_BASE_URL"

cleanup() {
  echo
  echo "==> Restoring system_b's base_url and stopping the mock receiver"
  admin_curl -X PATCH "$BASE_URL/api/v1/systems/system_b" \
    -H "Content-Type: application/json" \
    -d "{\"base_url\": \"$ORIGINAL_BASE_URL\"}" > /dev/null
  kill "$RECEIVER_PID" 2>/dev/null || true
  admin_logout
}
trap cleanup EXIT

echo "==> Pointing system_b at the mock receiver instead"
admin_curl -X PATCH "$BASE_URL/api/v1/systems/system_b" \
  -H "Content-Type: application/json" \
  -d "{\"base_url\": \"http://localhost:$RECEIVER_PORT/webhook\"}" | json

echo
echo "==> Flagging a patient with no insurance on file, as system_a"
RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{"external_ref": "CASE-LIVE-1", "status": "new", "subject": "Patient #9001 - no insurance on file", "topic_code": "PATIENT_ADMIN", "metadata": {"note": "Patient checked in without an insurance card - please verify coverage."}}')
echo "$RESPONSE" | json

echo
echo "==> Triggering sync now - watch for the real payload printed below by the mock receiver"
admin_curl -X POST "$BASE_URL/api/v1/sync" | json

sleep 1
echo
echo "==> Done - system_b's base_url is being restored now."
