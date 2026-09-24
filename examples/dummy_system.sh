#!/usr/bin/env bash
# dummy_system.sh
# ---------------
# Plays the "other side" of a conversation when only one real system is
# connected (e.g. a dev environment): registers a dummy system in the
# bridge, receives what the bridge delivers to it, and replies as it.
# Nothing in the bridge distinguishes this from a real system - adding one
# is plain configuration (CLAUDE.md Decision 3).
#
# Usage:
#   ./dummy_system.sh setup <topic> [topic...]   create/reactivate the dummy, subscribe it,
#                                                and generate its inbound API key
#   ./dummy_system.sh receive [--compose]        run the mock receiver the dummy's base_url
#                                                points at (leave it running in its own terminal)
#   ./dummy_system.sh open <topic> <subject> [note]
#                                                open a new ticket as the dummy
#   ./dummy_system.sh reply <conversation_id> <status> [note]
#                                                post an update as the dummy
#   ./dummy_system.sh sync                       trigger delivery now instead of waiting
#                                                for the scheduler (needs SCHEDULER_SHARED_SECRET)
#   ./dummy_system.sh teardown                   deactivate the dummy, drop its topics,
#                                                revoke its API keys
#
# `receive --compose` runs the receiver inside the bridge's own `app`
# container (docker compose), so the default base_url
# (http://localhost:$RECEIVER_PORT/webhook) reaches it without any Docker
# networking. Without --compose it runs on this machine - right when the
# bridge also runs directly here (uv run uvicorn ...).
#
# Environment (all optional):
#   BASE_URL           bridge URL, including any ROOT_PATH prefix (default http://localhost:8080)
#   DUMMY_CODE         system code (default "dummy")
#   RECEIVER_PORT      mock receiver port (default 9000)
#   DUMMY_BASE_URL     override the dummy's delivery URL entirely
#   METADATA           extra JSON object merged into an event's metadata,
#                      e.g. METADATA='{"insurance_number": "INS-1"}' ./dummy_system.sh reply ...
#   COMPOSE_DIR        directory holding docker-compose.yml, for --compose (default ..)
#   COMPOSE_SERVICE    the bridge's compose service name (default app)
#   ADMIN_USERNAME / ADMIN_PASSWORD   for setup/teardown - see _admin_session.sh
#
# State (the dummy's API key, and which external_ref it used for each
# conversation) lives in examples/.dummy-<code>.* - gitignored, removed by
# teardown.

set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="${BASE_URL:-http://localhost:8080}"
DUMMY_CODE="${DUMMY_CODE:-dummy}"
RECEIVER_PORT="${RECEIVER_PORT:-9000}"
DUMMY_BASE_URL="${DUMMY_BASE_URL:-http://localhost:$RECEIVER_PORT/webhook}"
KEY_FILE=".dummy-$DUMMY_CODE.key"
REFS_FILE=".dummy-$DUMMY_CODE.refs"
KEY_DESCRIPTION="dummy_system.sh"

if [ -f ../.env ]; then
  set -a; source ../.env; set +a
fi

source ./_admin_session.sh

# (basename: we've already cd'd into this script's directory above)
usage() { sed -n '/^# Usage:/,/^# `receive/p' "$(basename "$0")" | sed '$d; s/^# \{0,1\}//'; exit 1; }
die() { echo "$*" >&2; exit 1; }

# request METHOD URL [JSON] [curl-fn] - prints the response body, exits on HTTP >= 400.
request() {
  local method="$1" url="$2" data="${3:-}" fn="${4:-admin_curl}" out status
  local args=(-w $'\n%{http_code}' -X "$method" "$url")
  [ -n "$data" ] && args+=(-H "Content-Type: application/json" -d "$data")
  out=$("$fn" "${args[@]}")
  status="${out##*$'\n'}"
  out="${out%$'\n'*}"
  if [ "$status" -ge 400 ]; then
    echo "HTTP $status from $method $url" >&2
    echo "$out" >&2
    exit 1
  fi
  printf '%s' "$out"
}

# Checked by each command up front: from inside request's $(...) subshell
# an exit wouldn't stop the script.
require_key() {
  [ -f "$KEY_FILE" ] || die "No API key for '$DUMMY_CODE' yet - run: ./dummy_system.sh setup <topic>"
}

dummy_curl() {
  curl -s -H "X-API-Key: $(cat "$KEY_FILE")" "$@"
}

# Builds an IncomingEvent body from environment variables (values never
# pass through the shell's own quoting).
event_json() {
  python3 - <<'PY'
import json, os
metadata = json.loads(os.environ.get("METADATA") or "{}")
if os.environ.get("EV_NOTE"):
    metadata["note"] = os.environ["EV_NOTE"]
body = {"external_ref": os.environ["EV_REF"], "status": os.environ["EV_STATUS"]}
for key, env in (("conversation_id", "EV_CONV"), ("topic_code", "EV_TOPIC"), ("subject", "EV_SUBJECT")):
    if os.environ.get(env):
        body[key] = os.environ[env]
if metadata:
    body["metadata"] = metadata
print(json.dumps(body))
PY
}

# Empty input means request already failed and printed why - don't add a
# JSON parse error on top of it.
pretty() { local body; body=$(cat); [ -z "$body" ] || printf '%s' "$body" | python3 -m json.tool; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }

cmd_setup() {
  [ $# -ge 1 ] || die "Usage: ./dummy_system.sh setup <topic> [topic...]"
  admin_login
  trap admin_logout EXIT

  local topics body status
  topics=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")
  status=$(admin_curl -o /dev/null -w '%{http_code}' "$BASE_URL/api/v1/systems/$DUMMY_CODE")
  if [ "$status" = "404" ]; then
    echo "==> Creating system '$DUMMY_CODE' -> $DUMMY_BASE_URL, topics $topics"
    body=$(CODE="$DUMMY_CODE" URL="$DUMMY_BASE_URL" TOPICS="$topics" python3 -c '
import json, os
print(json.dumps({"code": os.environ["CODE"], "name": "Dummy system (dummy_system.sh)",
                  "base_url": os.environ["URL"], "topics": json.loads(os.environ["TOPICS"])}))')
    request POST "$BASE_URL/api/v1/systems" "$body" | pretty
  else
    echo "==> System '$DUMMY_CODE' exists - reactivating it -> $DUMMY_BASE_URL, topics $topics"
    body=$(URL="$DUMMY_BASE_URL" TOPICS="$topics" python3 -c '
import json, os
print(json.dumps({"base_url": os.environ["URL"], "active": True, "topics": json.loads(os.environ["TOPICS"])}))')
    request PATCH "$BASE_URL/api/v1/systems/$DUMMY_CODE" "$body" | pretty
  fi

  revoke_keys
  echo "==> Generating a new inbound API key (saved to examples/$KEY_FILE)"
  request POST "$BASE_URL/api/v1/systems/$DUMMY_CODE/api-keys" "{\"description\": \"$KEY_DESCRIPTION\"}" \
    | field api_key > "$KEY_FILE"
  chmod 600 "$KEY_FILE"

  cat <<EOF

Done. Next:
  1. In another terminal:  ./dummy_system.sh receive
     (add --compose if the bridge runs in docker compose)
  2. Open a ticket from your real system on one of: $*
     (or as the dummy: ./dummy_system.sh open <topic> "<subject>")
  3. When the receiver prints the delivery, reply with its conversation_id:
     ./dummy_system.sh reply <conversation_id> in_progress "Picked up"
EOF
}

# Revokes the dummy's active keys generated by this script, so re-running
# setup doesn't pile up valid keys.
revoke_keys() {
  local ids id
  ids=$(request GET "$BASE_URL/api/v1/systems/$DUMMY_CODE/api-keys" | DESC="$KEY_DESCRIPTION" python3 -c '
import json, os, sys
for k in json.load(sys.stdin):
    if k["active"] and k["description"] == os.environ["DESC"]:
        print(k["id"])')
  for id in $ids; do
    echo "==> Revoking previous dummy key #$id"
    request DELETE "$BASE_URL/api/v1/systems/$DUMMY_CODE/api-keys/$id" > /dev/null
  done
}

cmd_receive() {
  if [ "${1:-}" = "--compose" ]; then
    local service="${COMPOSE_SERVICE:-app}"
    cd "${COMPOSE_DIR:-..}"
    # Copied in and run with a TTY (not piped via stdin with -T) so Ctrl+C
    # actually stops it - without a TTY the signal isn't forwarded, and the
    # receiver would keep running, holding the port, inside the container.
    docker compose cp "$OLDPWD/mock_receiver.py" "$service:/tmp/mock_receiver.py"
    exec docker compose exec "$service" python /tmp/mock_receiver.py "$RECEIVER_PORT"
  fi
  exec python3 mock_receiver.py "$RECEIVER_PORT"
}

cmd_open() {
  [ $# -ge 2 ] || die "Usage: ./dummy_system.sh open <topic> <subject> [note]"
  require_key
  local ref body response conv
  ref="DUMMY-$(date +%Y%m%d%H%M%S)"
  body=$(EV_REF="$ref" EV_STATUS=new EV_TOPIC="$1" EV_SUBJECT="$2" EV_NOTE="${3:-}" event_json)
  response=$(request POST "$BASE_URL/api/v1/events" "$body" dummy_curl)
  echo "$response" | pretty
  conv=$(echo "$response" | field conversation_id)
  echo "$conv $ref" >> "$REFS_FILE"
  echo "==> Opened $ref, conversation $conv"
}

cmd_reply() {
  [ $# -ge 2 ] || die "Usage: ./dummy_system.sh reply <conversation_id> <status> [note]"
  require_key
  local conv="$1" ref body
  # Reuse the dummy's own ticket ref for this conversation, so every reply
  # updates the same participant row instead of looking like a new ticket.
  ref=$(awk -v c="$conv" '$1 == c { print $2; exit }' "$REFS_FILE" 2>/dev/null || true)
  if [ -z "$ref" ]; then
    ref="DUMMY-${conv:0:8}"
    echo "$conv $ref" >> "$REFS_FILE"
  fi
  body=$(EV_REF="$ref" EV_STATUS="$2" EV_CONV="$conv" EV_NOTE="${3:-}" event_json)
  request POST "$BASE_URL/api/v1/events" "$body" dummy_curl | pretty
  echo "==> Sent '$2' as $ref"
}

cmd_sync() {
  : "${SCHEDULER_SHARED_SECRET:?Set SCHEDULER_SHARED_SECRET (e.g. via ../.env) to trigger sync manually.}"
  curl -s -X POST "$BASE_URL/api/v1/sync" -H "X-Scheduler-Secret: $SCHEDULER_SHARED_SECRET" | pretty
}

cmd_teardown() {
  admin_login
  trap admin_logout EXIT
  echo "==> Deactivating '$DUMMY_CODE' and removing its topic subscriptions"
  request PATCH "$BASE_URL/api/v1/systems/$DUMMY_CODE" '{"active": false, "topics": []}' | pretty
  revoke_keys
  rm -f "$KEY_FILE" "$REFS_FILE"
  echo "==> Done. The system row stays (systems are never hard-deleted); re-run setup to reuse it."
}

case "${1:-}" in
  setup)    shift; cmd_setup "$@" ;;
  receive)  shift; cmd_receive "$@" ;;
  open)     shift; cmd_open "$@" ;;
  reply)    shift; cmd_reply "$@" ;;
  sync)     shift; cmd_sync "$@" ;;
  teardown) shift; cmd_teardown "$@" ;;
  *)        usage ;;
esac
