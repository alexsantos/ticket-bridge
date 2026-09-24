# _admin_session.sh
# -----------------
# Sourced (not run) by the example scripts that call admin endpoints
# (/api/v1/systems, /conversations, /audit), which need an admin session
# since 0.6.0 - see CLAUDE.md Decision 11. /api/v1/events (per-system API
# key) and /api/v1/sync (scheduler secret) don't need this.
#
# Credentials: ADMIN_USERNAME (default "admin") and ADMIN_PASSWORD. If
# ADMIN_PASSWORD isn't set, it's prompted for (hidden) when run from a
# terminal.
#
# The session cookie is read from the login response's Set-Cookie header
# and sent back as a plain Cookie header, rather than via a curl cookie
# jar: outside ENVIRONMENT=local the cookie is marked Secure, and curl
# won't store a Secure cookie received over plain http:// - which is
# exactly how these scripts talk to a local or VM instance.
#
# Provides: admin_login, admin_curl (curl -s with the session attached),
# admin_logout. Expects BASE_URL to be set by the caller.

admin_login() {
  local username="${ADMIN_USERNAME:-admin}"
  if [ -z "${ADMIN_PASSWORD:-}" ]; then
    if [ -t 0 ]; then
      read -rsp "Admin password for '$username' at $BASE_URL: " ADMIN_PASSWORD
      echo >&2
    else
      echo "Set ADMIN_PASSWORD (admin endpoints need a session since 0.6.0)." >&2
      exit 1
    fi
  fi

  local headers status
  headers=$(mktemp)
  # Password goes through the environment and stdin, never argv, so it
  # doesn't show up in `ps`.
  status=$(ADMIN_USERNAME="$username" ADMIN_PASSWORD="$ADMIN_PASSWORD" python3 -c \
      'import json, os; print(json.dumps({"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]}))' \
    | curl -s -o /dev/null -D "$headers" -w '%{http_code}' -X POST "$BASE_URL/api/v1/auth/login" \
        -H "Content-Type: application/json" --data @-)
  # `|| true`: a failed login has no Set-Cookie, and under the callers'
  # `set -euo pipefail` grep's non-zero exit would otherwise abort here,
  # before the status check below can say why.
  ADMIN_COOKIE=$(grep -i '^set-cookie:' "$headers" | head -n1 | sed -E 's/^[^:]+:[[:space:]]*([^;]+).*/\1/' | tr -d '\r' || true)
  rm -f "$headers"

  case "$status" in
    200) ;;
    401) echo "Admin login failed: invalid username or password." >&2; exit 1 ;;
    429) echo "Admin login throttled after too many failed attempts - wait LOGIN_LOCKOUT_MINUTES and retry." >&2; exit 1 ;;
    *)   echo "Admin login failed: HTTP $status from $BASE_URL/api/v1/auth/login." >&2; exit 1 ;;
  esac
  if [ -z "$ADMIN_COOKIE" ]; then
    echo "Admin login returned 200 but no session cookie." >&2
    exit 1
  fi
}

admin_curl() {
  curl -s -H "Cookie: $ADMIN_COOKIE" "$@"
}

admin_logout() {
  [ -n "${ADMIN_COOKIE:-}" ] || return 0
  curl -s -o /dev/null -H "Cookie: $ADMIN_COOKIE" -X POST "$BASE_URL/api/v1/auth/logout" || true
  ADMIN_COOKIE=
}
