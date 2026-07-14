#!/usr/bin/env bash
# M5.2 install-doc smoke test: prove the documented install actually works.
#
# Extracts the commands from the "## Install" block of docs/install.md and runs them
# verbatim on a clean directory — only the public clone URL is rewritten to THIS checkout,
# so the test exercises the current tree, and any drift between the docs and the real
# install flow fails CI. Then it asserts the outcomes install.md promises.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${INSTALL_SMOKE_WORK:-/tmp/eeper-install-smoke}"
CLONE="$WORK/eeper"

cleanup() {
  if [ -d "$CLONE/deploy" ]; then
    (cd "$CLONE/deploy" && docker compose --profile core down -v >/dev/null 2>&1 || true)
  fi
}
trap cleanup EXIT

rm -rf "$WORK"
mkdir -p "$WORK"

# 1) Extract the first ```bash block under "## Install".
python3 - "$REPO/docs/install.md" >"$WORK/steps.sh" <<'PY'
import pathlib, re, sys
md = pathlib.Path(sys.argv[1]).read_text()
if "## Install" not in md:
    sys.exit("install.md has no '## Install' section")
body = md.split("## Install", 1)[1]
m = re.search(r"```bash\n(.*?)```", body, re.S)
if not m:
    sys.exit("no ```bash block under '## Install' in install.md")
sys.stdout.write(m.group(1))
PY

# 2) Rewrite the public clone URL to the local checkout so we test THIS tree, and sanity
#    check that the documented flow still runs install.sh.
sed -i.bak "s#https://github.com/calhargis/eeper.git#file://$REPO#g" "$WORK/steps.sh"
grep -q "install.sh" "$WORK/steps.sh" || {
  echo "FAIL: the documented install no longer runs install.sh"
  exit 1
}

echo "== documented install steps =="
cat "$WORK/steps.sh"
echo "==============================="

# 3) Run the documented commands verbatim, from the clean work dir.
(cd "$WORK" && bash -euo pipefail steps.sh)

# 4) Assert the outcomes install.md promises (its numbered list + security posture).
fail() {
  echo "FAIL: $1"
  exit 1
}
test -f "$CLONE/deploy/.env" || fail "deploy/.env was not generated"
grep -q '^POSTGRES_PASSWORD=.\+' "$CLONE/deploy/.env" || fail "no generated POSTGRES_PASSWORD in .env"
grep -q '^EEPER_SECRET_KEY=.\+' "$CLONE/deploy/.env" || fail "no generated EEPER_SECRET_KEY in .env"
test -f "$CLONE/deploy/eeper-local-ca.crt" || fail "local CA cert was not extracted"

# The stack is up and login-gated: first boot required, no default credentials.
status="$(curl -sk --retry 5 --retry-delay 3 https://localhost/api/v1/system/status)"
echo "system/status: $status"
case "$status" in
*'"first_boot_required":true'*) : ;;
*) fail "stack is not first-boot-gated (got: $status)" ;;
esac

echo "OK: the documented install produced a healthy, login-gated stack."
