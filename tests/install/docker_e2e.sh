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
URL="http://127.0.0.1:$AGENTKENNEL_PORT"

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
  dc up -d --build
else
  export AGENTKENNEL_VERSION="$WHAT"
  dc pull agentkennel
  dc up -d --no-build
fi

link=""
for _ in $(seq 90); do
  link="$(dc logs agentkennel 2>&1 | grep -o '/login?setup=[A-Za-z0-9_-]*' | tail -1 || true)"
  [ -n "$link" ] && break
  sleep 2
done
[ -n "$link" ] || { echo "no setup link in the logs"; exit 1; }
echo "setup link found"

python3 "$REPO/tests/install/smoke.py" "$URL" --password "$PW" --first-run --setup "$link" \
  --new-terminal --wait 90
dc exec -T agentkennel claude --version
dc exec -T agentkennel tmux ls

dc restart agentkennel
python3 "$REPO/tests/install/smoke.py" "$URL" --password "$PW" --wait 90
echo "the password survived the restart"
