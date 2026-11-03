#!/usr/bin/env bash
# The Docker way of installing, as the README tells it, end to end:
#   docker compose up -d → the one-time link from `docker compose logs | grep setup` →
#   set the password, log in, open a new terminal (tests/install/smoke.py) → claude runs
#   inside → `docker compose restart` → the same password still logs in → down -v.
#
#   tests/install/docker_e2e.sh build      the checkout (`up -d --build`)
#   tests/install/docker_e2e.sh 2.2.1      the published image ghcr.io/yanhs/agentkennel:2.2.1
#
# Runs under its own compose project on a free port; leaves no container or volume behind.
set -Eeuo pipefail

WHAT="${1:?usage: docker_e2e.sh build | <version>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PROJECT="agentkennel-e2e-$$"
export AGENTKENNEL_PORT="${AGENTKENNEL_PORT:-$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')}"
PW="docker-e2e-$$-pass"
URL="https://127.0.0.1:$AGENTKENNEL_PORT"

dc() { docker compose -p "$PROJECT" -f "$REPO/docker-compose.yml" --project-directory "$REPO" "$@"; }
finish() {
  local rc=$?
  [ $rc -eq 0 ] || dc logs --tail 120 agentkennel || true
  dc down -v --remove-orphans >/dev/null 2>&1 || true
  [ -z "$BUILT" ] || docker rmi "$BUILT" >/dev/null 2>&1 || true
  [ $rc -eq 0 ] && echo "PASS  docker install ($WHAT)" || echo "FAIL  docker install ($WHAT)"
  exit $rc
}
trap finish EXIT

BUILT=""
if [ "$WHAT" = build ]; then
  # a tag of its own: under :latest the build would hide the published image from the
  # next plain `docker compose up -d` on this machine
  export AGENTKENNEL_VERSION="e2e-build-$$"
  BUILT="ghcr.io/yanhs/agentkennel:$AGENTKENNEL_VERSION"
  dc up -d --build --wait
else
  export AGENTKENNEL_VERSION="$WHAT"
  dc pull agentkennel
  dc up -d --no-build --wait
fi

# the README's way: right after `up -d --wait`, one `logs | grep setup`, no waiting loop;
# the line is the whole link, the server's address in front
line="$(dc logs agentkennel 2>&1 | grep setup | tail -1 || true)"
echo "log line: ${line//setup=*/setup=<token>}"
case "$line" in *"://"*":${AGENTKENNEL_PORT}/login?setup="*) ;; *) echo "the setup line is not a whole link"; exit 1 ;; esac
link="$(printf '%s' "$line" | grep -o '/login?setup=[A-Za-z0-9_-]*' | tail -1)"
[ -n "$link" ] || { echo "no setup link in the logs"; exit 1; }
echo "setup link found"

python3 "$REPO/tests/install/smoke.py" "$URL" --password "$PW" --first-run --setup "$link" \
  --new-terminal --wait 90 --insecure
dc exec -T agentkennel claude --version
dc exec -T agentkennel tmux ls

dc restart agentkennel
python3 "$REPO/tests/install/smoke.py" "$URL" --password "$PW" --wait 90 --insecure
echo "the password survived the restart"

# forgot the password: one command in the container, a new link; the old password is out
reset="$(dc exec -T agentkennel python3 status_server.py --reset-password)"
case "$reset" in /login\?setup=*) ;; *) echo "reset printed no link: $reset"; exit 1 ;; esac
if python3 "$REPO/tests/install/smoke.py" "$URL" --password "$PW" --wait 20 --insecure >/dev/null 2>&1; then
  echo "the old password still works after --reset-password"; exit 1
fi
python3 "$REPO/tests/install/smoke.py" "$URL" --password "$PW-new" --first-run --setup "$reset" \
  --wait 30 --insecure
echo "reset: the old password is out, the new link set a new one"
