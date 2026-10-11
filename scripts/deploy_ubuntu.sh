#!/usr/bin/env bash
#
# Deploy a release tarball onto an Ubuntu host.
#
# The order is chosen so that a failure at any step leaves the *previous* release serving:
#
#   1. extract into /opt/xauusd/releases/<stamp>     (a fresh directory, nothing live)
#   2. uv sync --frozen --no-dev into that directory (nothing live is touched)
#   3. run the startup gates                        (nothing live is touched)
#   4. repoint the /opt/xauusd/app symlink           (atomic: readers see old or new)
#   5. systemctl daemon-reload and restart           (the only step that is not reversible)
#
# So a gate failure at step 3 exits before the symlink moves, and the running engine never
# learns a release happened. That is the whole reason the symlink exists: it is the single
# atomic switch, and `ln -sfn` on Linux is close enough to atomic for a reader that opens
# one path.
#
# Requires root (it writes under /opt and restarts units).

set -euo pipefail

APP_ROOT=/opt/xauusd
RELEASES="${APP_ROOT}/releases"
APP_LINK="${APP_ROOT}/app"
PYTHON_DIR="${APP_ROOT}/python"
ENV_FILE=/etc/xauusd/prod.env

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARBALL="${1:-}"

log() { echo "deploy: $*"; }
die() { echo "deploy: $*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "must run as root (writes under ${APP_ROOT}, restarts units)"
[[ -n "${TARBALL}" ]] || die "usage: deploy_ubuntu.sh <release.tar.gz>"
[[ -f "${TARBALL}" ]] || die "release tarball ${TARBALL} not found"
[[ -d "${RELEASES}" ]] || mkdir -p "${RELEASES}"
[[ -f "${ENV_FILE}" ]] || die "${ENV_FILE} is missing; the units' EnvironmentFile must exist"

# The tarball was cut with libarchive-free tar and carries a single top-level directory
# named xauusd-<stamp>. Both are checked, because extracting into an unexpected layout is
# how a deploy silently ends up pointing at yesterday.
TOP_ENTRY="$(tar -tzf "${TARBALL}" | head -n1 | cut -d/ -f1)"
[[ -n "${TOP_ENTRY}" ]] || die "could not read the archive's top-level entry"
[[ "${TOP_ENTRY}" == xauusd-* ]] || die "unexpected archive layout: ${TOP_ENTRY}"

STAMP="${TOP_ENTRY#xauusd-}"
TARGET="${RELEASES}/${STAMP}"

if [[ -e "${TARGET}" ]]; then
  log "release ${STAMP} already extracted; removing and re-extracting"
  rm -rf "${TARGET}"
fi

# --- 1. extract ---------------------------------------------------------------- #
log "extracting ${TARBALL} into ${TARGET}"
tar -xzf "${TARBALL}" -C "${RELEASES}"
[[ -f "${TARGET}/pyproject.toml" ]] || die "extracted release has no pyproject.toml"
[[ -f "${TARGET}/uv.lock" ]] || die "extracted release has no uv.lock; resolve before cutting a release"
[[ -d "${TARGET}/engine/src/engine" ]] || die "extracted release has no engine/src/engine"
[[ -f "${TARGET}/api/main.py" ]] || die "extracted release has no api/main.py"

# The dashboard is mounted, not built, so a release without it deploys an API that serves
# nothing but JSON. That is a legitimate state (Phase 5 shipped it) but it must be a
# decision, not an accident.
if [[ ! -d "${TARGET}/web/dist" ]]; then
  log "warning: release has no web/dist; the API will mount nothing"
fi

# --- 2. install --------------------------------------------------------------- #
# UV_PYTHON_INSTALL_DIR puts the interpreter under the deployment's own tree rather than
# in root's home, which would be the wrong place for a service that runs as xauusd-web.
# --frozen: CI and deploy must reproduce uv.lock exactly. --no-dev: no test toolchain on a
# trading host.
log "syncing dependencies into ${TARGET}/.venv"
(
  cd "${TARGET}"
  UV_PYTHON_INSTALL_DIR="${PYTHON_DIR}" uv sync --frozen --no-dev
)

[[ -x "${TARGET}/.venv/bin/python" ]] || die "uv sync did not produce a venv python"
[[ -x "${TARGET}/.venv/bin/uvicorn" ]] || die "uv sync did not produce uvicorn (uvicorn is a runtime dependency)"

# --- 3. verify the host, before anything switches ------------------------------ #
# The same gate `python -m engine.runtime.gates` runs and the same exit code (78) it uses,
# so a host that would fail startup fails the deploy instead. Also verifies the deployment
# can actually import its own code: a release that cannot be imported must not be linked.
log "running startup gates against the new release"
(
  cd "${TARGET}"
  UV_PYTHON_INSTALL_DIR="${PYTHON_DIR}" ./.venv/bin/python -m engine.runtime.gates
) || die "startup gates refused this host; refusing to deploy (exit 78)"

log "import-checking the new release"
(
  cd "${TARGET}"
  UV_PYTHON_INSTALL_DIR="${PYTHON_DIR}" ./.venv/bin/python - <<'PY'
import api.main  # noqa: F401  -- the web unit execs this
import engine.runtime.supervisor  # noqa: F401  -- the engine unit execs this
print("deploy: imports resolved")
PY
) || die "the new release cannot import its own modules"

# --- 3b. wire the runtime paths ------------------------------------------------ #
# The engine writes ./journal.db relative to its working directory, and the API reads
# ./projection relative to its own. Neither the engine nor the API takes an environment
# override for those paths in the shipped version, so the deployment supplies them as
# symlinks inside the release directory rather than editing engine code during a deploy.
#
# They are links into /var/lib, which is what makes the units' ProtectSystem=strict and
# StateDirectory work: SQLite follows the link, and the writes land on the writable state
# directories rather than on the read-only /opt tree.
if [[ ! -e "${TARGET}/journal.db" ]]; then
  ln -s /var/lib/xauusd/journal/journal.db "${TARGET}/journal.db"
  log "linked ${TARGET}/journal.db -> /var/lib/xauusd/journal/journal.db"
fi
if [[ ! -e "${TARGET}/projection" ]]; then
  ln -s /var/lib/xauusd/projection "${TARGET}/projection"
  log "linked ${TARGET}/projection -> /var/lib/xauusd/projection"
fi

# Ownership: every unit runs as an account that must be able to read the code. The release
# directory is world-readable, and the two directories the services need to *write* are
# already owned by systemd's StateDirectory= mechanism at /var/lib.
chown -R root:root "${TARGET}"
chmod -R a-s,u+rwX,go+rX "${TARGET}"
find "${TARGET}" -type d -exec chmod 0755 {} +

for unit in xauusd-engine xauusd-publisher xauusd-web; do
  id -u "${unit%%-*}" >/dev/null 2>&1 || die "system account for ${unit} does not exist; see systemd/README.md"
done

# Validate the units this release ships before pointing anything at it.
if command -v systemd-analyze >/dev/null 2>&1; then
  log "verifying the shipped systemd units"
  systemd-analyze verify "${TARGET}/systemd/"xauusd-*.service "${TARGET}/systemd/"xauusd-*.timer \
    || die "systemd-analyze verify rejected the shipped units"
fi

# --- 4. switch the symlink ---------------------------------------------------- #
# Written to a temporary name and renamed, so a concurrent reader sees either the old
# target or the new one, never a missing link.
log "switching ${APP_LINK} -> ${TARGET}"
ln -sfn "${TARGET}" "${APP_LINK}.new"
mv -Tf "${APP_LINK}.new" "${APP_LINK}"

# --- 5. restart ---------------------------------------------------------------- #
log "reloading systemd and restarting the services"
systemctl daemon-reload
systemctl restart xauusd-engine.service || die "xauusd-engine failed to start"
systemctl restart xauusd-publisher.service || die "xauusd-publisher failed to start"
systemctl restart xauusd-web.service || die "xauusd-web failed to start"

# `is-active` after restart, rather than trusting the exit status of the restart command:
# systemctl restart returns once the job is *queued*, not once the service is up, and a
# Type=notify unit that never calls sd_notify(READY=1) fails its start job seconds later.
for unit in xauusd-engine xauusd-publisher xauusd-web; do
  if systemctl is-active --quiet "${unit}.service"; then
    log "${unit} is active"
  else
    log "warning: ${unit} did not come up; 'journalctl -u ${unit} -n 50'"
  fi
done

log "deployed release ${STAMP}"
log "rollback: ln -sfn ${RELEASES}/<previous-stamp> ${APP_LINK} && systemctl restart 'xauusd-*'"
