#!/usr/bin/env bash
#
# Bundle the code and the compiled dashboard into one timestamped release tarball.
#
# The release is what deploy_ubuntu.sh extracts, so it has to be self-contained: the
# Python sources, the API, the systemd units, the config documents, the scripts that run
# them, and the *built* `web/dist` — not `web/src`. A deploy target has no Node toolchain
# and no reason to install one, so shipping sources it cannot build would ship a bundle
# that only builds on a developer's laptop.
#
# The archive carries the commit it was cut from, so every deployed directory can be traced
# back to a source tree without a registry.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/dist/releases}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

cd "${REPO_ROOT}"

# Everything the running system needs and nothing else. `web/node_modules` and `web/dist`
# are handled explicitly below; the rest is excluded by path.
PATHS=(
  engine/src
  api
  scripts
  systemd
  config
  pyproject.toml
  uv.lock
  .python-version
  AGENTS.md
)

for path in "${PATHS[@]}"; do
  if [[ ! -e "${path}" ]]; then
    echo "make_release: required path '${path}' is missing from the repository" >&2
    exit 1
  fi
done

if [[ ! -d web/dist ]]; then
  echo "make_release: web/dist is missing; run 'npm ci && npm run build' in web/ first" >&2
  exit 1
fi
# The dashboard's mandatory disclosure must survive into the bundle: the API mounts
# web/dist, and a release built without the disclosure is not a releasable dashboard.
if [[ ! -s web/dist/index.html ]]; then
  echo "make_release: web/dist/index.html is missing or empty" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
TARBALL="${OUT_DIR}/xauusd-${STAMP}.tar.gz"

# `--sort=name` and a fixed mtime make the archive reproducible: two runs over the same
# tree produce the same bytes, so a checksum can be compared across builds.
# `web/node_modules` is excluded on a path prefix so a host that ran `npm install` in place
# does not ship a gigabyte of dependencies.
tar --sort=name \
    --owner=0 --group=0 --numeric-owner \
    --mtime="@${RELEASE_EPOCH:-0}" \
    --exclude='web/node_modules' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='*.db' \
    --exclude='*.db-wal' \
    --exclude='*.db-shm' \
    --exclude='.hypothesis' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --transform "s,^,xauusd-${STAMP}/," \
    -czf "${TARBALL}" \
    "${PATHS[@]}" web/dist

SHA_OUT="${TARBALL}.sha256"
sha256sum "${TARBALL}" > "${SHA_OUT}"

echo "make_release: ${TARBALL}"
echo "make_release: $(cat "${SHA_OUT}")"
echo "make_release: commit $(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
