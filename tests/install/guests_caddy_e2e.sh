#!/usr/bin/env bash
# End-to-end: guests' web addresses through the script install's Caddy (no nginx at all).
#
#   tests/install/guests_caddy_e2e.sh [ubuntu-24.04 | ubuntu-22.04 | debian-12]
#
# On a fresh "server" (a privileged container with systemd as PID 1, tests/install/
# Dockerfile.<distro>, Docker running INSIDE it on a volume of its own):
#   1. install.sh --guests the curl | bash way, with Caddy's own CA on 443
#      (AGENTKENNEL_TLS_INTERNAL=1, as SCENARIO=internal of run.sh); set the password
#   2. guests/.env for the test domain guests.test (every name -> 127.0.0.1 in /etc/hosts,
#      no Cloudflare: DNS is "a wildcard record"), guests/install.sh --check says Caddy,
#      guests/install.sh --yes builds the images in the inner Docker and starts the services
#   3. guestctl add anna (no disk of its own: that would take a loop device of the host's
#      kernel) + guestctl web anna -> https://sb-anna.guests.test answers through Caddy with
#      the guest's own dashboard; the owner's Open signs the owner in there
#   4. a site published from inside the sandbox (the site gate, as the guest's agent does)
#      and the shared files address sbx.guests.test (a file, a planted symlink, no listing)
#   5. web-off anna: the 404 stub, then the name is gone
#   6. the main dashboard still works (in the container and from the host: smoke.py)
# Always tears down its container, image and the inner Docker's volumes; `docker ps -a`
# must be unchanged after. Env: KEEP=1 leaves the container running for a look.
set -Eeuo pipefail

DISTRO="${1:-ubuntu-24.04}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DOCKERFILE="$HERE/Dockerfile.$DISTRO"
[ -f "$DOCKERFILE" ] || { echo "no $DOCKERFILE" >&2; exit 2; }

NAME="agentkennel-install-test-guests-caddy-$$"
IMAGE="agentkennel-install-test-guests-caddy:$$"
VOL="agentkennel-install-test-guests-docker-$$"
VOLC="agentkennel-install-test-guests-containerd-$$"   # Docker 29 keeps images in containerd
WORK="$(mktemp -d)"
PW="guests-e2e-pass-$$"
IP=203.0.113.7
MAIN_HOST="203-0-113-7.sslip.io"
MAIN="https://$MAIN_HOST"
DOMAIN=guests.test
DIR=/home/ubuntu/agentkennel
FAILS=0

say()  { printf '\n=== %s\n' "$*"; }
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }
expect() {
  local name=$1; shift
  if "$@"; then pass "$name"; else fail "$name"; fi
}

docker ps -a --format '{{.Names}}' | { grep -v "^agentkennel-install-test-" || true; } | sort > "$WORK/ps-before"

cleanup() {
  local rc=$?
  if [ "${KEEP:-}" = 1 ]; then
    echo "KEEP=1: container $NAME left running (volume $VOL)"
  else
    docker rm -f -v "$NAME" >/dev/null 2>&1 || true
    docker volume rm -f "$VOL" "$VOLC" >/dev/null 2>&1 || true
    docker rmi -f "$IMAGE" >/dev/null 2>&1 || true
  fi
  docker ps -a --format '{{.Names}}' | sort > "$WORK/ps-after"
  gone="$(comm -23 "$WORK/ps-before" "$WORK/ps-after")"
  if [ -n "$gone" ]; then
    echo "FAIL  containers disappeared during the test: $gone"; rc=1
  elif [ "${KEEP:-}" != 1 ] && grep -qx "$NAME" "$WORK/ps-after"; then
    echo "FAIL  test container $NAME was not removed"; rc=1
  elif [ "${KEEP:-}" != 1 ] && docker volume ls -q | grep -Eqx "$VOL|$VOLC"; then
    echo "FAIL  volume $VOL or $VOLC was not removed"; rc=1
  else
    echo "PASS  other containers untouched ($(wc -l < "$WORK/ps-before") before, all still there); test container, image and volume removed"
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap cleanup EXIT

as_ubuntu() { docker exec -u ubuntu -w /home/ubuntu -e HOME=/home/ubuntu "$NAME" bash -c "$1"; }
gctl() { as_ubuntu "python3 $DIR/guests/guestctl.py $*"; }
chk() { as_ubuntu "python3 /tmp/guests_caddy_check.py $*"; }

say "build $IMAGE"
docker build -q -t "$IMAGE" -f "$DOCKERFILE" "$HERE" >/dev/null

# the working tree as it is now, as the bare repo the installer clones
(cd "$REPO" && { git ls-files; echo install.sh; git ls-files -o --exclude-standard tests/install guests; } \
   | sort -u | while read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done > "$WORK/files")
tar -C "$REPO" -cf "$WORK/tree.tar" -T "$WORK/files"
(
  mkdir -p "$WORK/src" && tar -C "$WORK/src" -xf "$WORK/tree.tar" && cd "$WORK/src" \
  && git init -q -b master && git add -A && git -c user.name=t -c user.email=t@t commit -qm tree \
  && git clone -q --bare "$WORK/src" "$WORK/agentkennel.git"
)

freeport() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
H443="$(freeport)"
say "boot $NAME (systemd PID 1, inner Docker on volume $VOL)"
docker volume create "$VOL" >/dev/null
docker volume create "$VOLC" >/dev/null
# the inner Docker's storage on real volumes: overlay on the container's own overlay root fails
docker run -d --name "$NAME" --hostname agentkennel-test --privileged --cgroupns=private \
  --tmpfs /run --tmpfs /run/lock -v "$VOL:/var/lib/docker" -v "$VOLC:/var/lib/containerd" \
  -p "127.0.0.1:$H443:443" "$IMAGE" >/dev/null
wait_systemd() {
  local st=""
  for _ in $(seq 1 60); do
    st="$(docker exec "$NAME" systemctl is-system-running 2>/dev/null || true)"
    case "$st" in running|degraded) return 0 ;; esac
    sleep 1
  done
  echo "systemd state: $st"; return 1
}
expect "systemd is up" wait_systemd
docker cp "$WORK/agentkennel.git" "$NAME:/opt/agentkennel.git"
docker exec "$NAME" chown -R ubuntu:ubuntu /opt/agentkennel.git
docker cp "$HERE/guests_caddy_check.py" "$NAME:/tmp/guests_caddy_check.py"
# every name of the test on this machine (no DNS: like a wildcard record)
docker exec "$NAME" bash -c "echo '127.0.0.1 $MAIN_HOST sb-anna.$DOMAIN blog-anna.$DOMAIN sbx.$DOMAIN' >> /etc/hosts"

say "install.sh --guests (bash < install.sh), Caddy's own CA on 443"
set +e
docker exec -i -u ubuntu -w /home/ubuntu -e AGENTKENNEL_REPO_URL=/opt/agentkennel.git \
  -e AGENTKENNEL_PUBLIC_IP=$IP -e AGENTKENNEL_TLS_INTERNAL=1 "$NAME" \
  bash -s -- --guests < "$REPO/install.sh" > "$WORK/install.log" 2>&1
rc=$?
set -e
tail -n 25 "$WORK/install.log"
expect "install.sh --guests exit 0" test "$rc" = 0
[ "$rc" = 0 ] || exit 1
expect "HTTPS on 443 with Caddy's CA" grep -q "Dashboard: $MAIN — HTTPS is on" "$WORK/install.log"
expect "no nginx on the server" bash -c "! docker exec $NAME bash -c 'command -v nginx || test -x /usr/sbin/nginx'"
expect "Caddy imports the guests' sites folder" docker exec "$NAME" bash -c \
  "systemctl show -p Environment agentkennel-caddy | grep -q 'AGENTKENNEL_CADDY_SITES=$DIR/.sessions/caddy-sites/\*.caddy'"
docker exec "$NAME" systemctl is-active --quiet docker || docker exec "$NAME" systemctl start docker
expect "Docker runs inside" docker exec "$NAME" systemctl is-active --quiet docker
# the Guests block of the owner's panel: inside a container status_server hides it unless
# GUESTS_DIR says so (a guest's own sandbox must not show it) — this container is the server
as_ubuntu "echo GUESTS_DIR=$DIR/guests >> $DIR/.env"
docker exec "$NAME" systemctl restart agentkennel-status
docker exec "$NAME" bash -c 'for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:3046/login && exit 0; sleep 1; done; exit 1'
SETUP="$(grep -o '/login?setup=[A-Za-z0-9_-]*' "$WORK/install.log" | tail -1)"
expect "main dashboard: set the password, log in" chk login "$MAIN" "$PW" "'$SETUP'"

say "guests/.env + guests/install.sh"
as_ubuntu "umask 077; cat > $DIR/guests/.env <<EOF
GUESTS_DOMAIN=$DOMAIN
GUEST_BOT_TOKEN=123456:e2e-not-a-real-token
OWNER_TG_ID=42
SERVER_IP=$IP
GUESTS_LANG=en
GUESTS_CERT_WAIT=60
EOF"
as_ubuntu "$DIR/guests/install.sh --check" > "$WORK/check.log" 2>&1 || true
cat "$WORK/check.log"
expect "--check: Caddy carries the web addresses, and why" grep -q "web addresses: caddy: agentkennel-caddy.service serves port 443" "$WORK/check.log"
expect "--check: no 'nginx and certbot are needed'" bash -c "! grep -q 'nginx and certbot' '$WORK/check.log'"
set +e
as_ubuntu "$DIR/guests/install.sh --yes" > "$WORK/guests-install.log" 2>&1
rc=$?
set -e
tail -n 15 "$WORK/guests-install.log"
expect "guests/install.sh exit 0" test "$rc" = 0
[ "$rc" = 0 ] || exit 1
for u in agentkennel-guest-turnstile agentkennel-guest-sitegate; do
  expect "$u active" docker exec "$NAME" systemctl is-active --quiet "$u"
done
# the Telegram relay runs on a made-up bot token here: Telegram refuses it and the unit may
# be between restarts — running or restarting is what can be checked, not failed
tgrelay_up() {
  local st; st="$(docker exec "$NAME" systemctl is-active agentkennel-guest-tgrelay || true)"
  echo "  agentkennel-guest-tgrelay: $st"
  case "$st" in active|activating) return 0 ;; esac
  docker exec "$NAME" journalctl -u agentkennel-guest-tgrelay --no-pager -o cat -n 15 || true
  return 1
}
expect "agentkennel-guest-tgrelay running or restarting (fake bot token)" tgrelay_up
gctl doctor > "$WORK/doctor.log" 2>&1 || true
cat "$WORK/doctor.log"
expect "doctor: Web addresses OK on caddy" grep -q "^OK    Web addresses: caddy: " "$WORK/doctor.log"

say "a guest and its address"
expect "guestctl add anna" gctl add anna --tg 111 --disk-gb 0
web_ok() { gctl web anna > "$WORK/web.log" 2>&1 || { cat "$WORK/web.log"; return 1; }; cat "$WORK/web.log"; }
expect "guestctl web anna (no sudo, no certbot)" web_ok
expect "the address is a Caddy block" as_ubuntu "test -f $DIR/.sessions/caddy-sites/sb-anna.$DOMAIN.caddy && ! ls $DIR/guests/nginx/sites/*.conf 2>/dev/null"
if ! chk guest "$MAIN" "sb-anna.$DOMAIN"; then
  fail "guest dashboard + Open through Caddy"
  docker exec "$NAME" bash -c 'docker ps -a; curl -sS -o /dev/null -w "sandbox port: %{http_code}\n" http://127.0.0.1:18101/login; docker logs --tail 40 sbx-anna 2>&1; journalctl -u agentkennel-caddy --no-pager -o cat -n 15' || true
else
  pass "guest dashboard + Open through Caddy"
fi
gctl list --json > "$WORK/list.json" || true
expect "guestctl list shows the address" grep -q "\"url\": \"https://sb-anna.$DOMAIN\"" "$WORK/list.json"

say "a site published from the sandbox"
docker exec "$NAME" docker exec -d sbx-anna sh -c \
  'mkdir -p /work/blog && echo BLOG-OK > /work/blog/index.html && cd /work/blog && exec python3 -m http.server 3000'
sleep 2
docker exec "$NAME" docker exec sbx-anna sh -c \
  'curl -s --max-time 120 -X POST http://172.31.250.1:8794/publish -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" -d "{\"site\":\"blog\",\"port\":3000}"' \
  | tee "$WORK/publish.json"; echo
expect "the site gate published blog-anna" grep -q "\"url\": \"https://blog-anna.$DOMAIN\"" "$WORK/publish.json"
expect "blog-anna answers through Caddy" chk site "https://blog-anna.$DOMAIN/" BLOG-OK

say "the shared files address"
expect "guestctl files-web" gctl files-web
KEY="$(as_ubuntu "python3 -c 'import json;print(json.load(open(\"$DIR/guests/turnstile/data/sandboxes.json\"))[\"anna\"][\"public_key\"])'")"
docker exec "$NAME" docker exec sbx-anna sh -c \
  'echo FILES-OK > /work/public/hello.txt && ln -sf /etc/passwd /work/public/pw.txt'
expect "sbx.$DOMAIN serves the files safely" chk files "https://sbx.$DOMAIN/f/$KEY"

say "web off"
docker exec -d -u ubuntu -e HOME=/home/ubuntu "$NAME" bash -c "python3 $DIR/guests/guestctl.py web-off anna > /tmp/web-off.log 2>&1; echo \$? > /tmp/web-off.rc"
expect "the stub answers 404" chk stub "sb-anna.$DOMAIN"
web_off_done() {
  for _ in $(seq 1 75); do docker exec "$NAME" test -f /tmp/web-off.rc && break; sleep 2; done
  docker exec "$NAME" bash -c 'cat /tmp/web-off.log; test "$(cat /tmp/web-off.rc)" = 0'
}
expect "web-off finished (stub, DNS, then the stub removed)" web_off_done
expect "sb-anna is gone" chk gone "sb-anna.$DOMAIN"
expect "the published site still works" chk site "https://blog-anna.$DOMAIN/" BLOG-OK

say "the main dashboard"
expect "main dashboard in the container" chk main "$MAIN"
expect "main dashboard from the host (smoke.py)" python3 "$HERE/smoke.py" "$MAIN" --password "$PW" \
  --insecure --connect "127.0.0.1:$H443"
expect "one caddy process, still the unit's" docker exec "$NAME" bash -c '[ "$(pgrep -xc caddy)" -ge 1 ] && systemctl is-active --quiet agentkennel-caddy'
expect "nothing of this used sudo for the web (no sudo in Caddy ops' journal)" bash -c \
  "! docker exec $NAME journalctl --no-pager -o cat _COMM=sudo 2>/dev/null | grep -Eq 'COMMAND=.*(nginx|certbot|sites-enabled)'"

say "RESULT guests on Caddy ($DISTRO): $([ $FAILS = 0 ] && echo PASS || echo "FAIL ($FAILS)")"
[ "$FAILS" = 0 ]
