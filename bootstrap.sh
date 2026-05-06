#!/usr/bin/env bash
# bootstrap.sh
#
# One-shot host bootstrap for the idea4rc-capsule Python package. Installs
# the apt prerequisites that pipx itself needs, makes sure pipx is
# present (per-user), and `pipx install`s the package directly from the
# scripts/capsule/ directory so the `idea4rc-capsule` console script ends
# up on the invoking user's PATH.
#
# It does NOT install Vault, helm, microk8s, or other capsule
# prerequisites — those are handled by `idea4rc-capsule install --auto`,
# which can be run after this script.
#
# Usage:
#   sudo ./bootstrap.sh
#   # or, if the user is already root:
#   ./bootstrap.sh
#
# Re-runs are safe: pipx install becomes pipx upgrade, apt install becomes
# a no-op when packages are present.

set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()   { printf '[capsule-bootstrap] %s\n' "$*"; }
fatal() { printf '[capsule-bootstrap] FATAL: %s\n' "$*" >&2; exit 1; }

# Determine the "real" user. When run under sudo, install pipx + the
# package into the invoking user's home so the binary lands on their PATH.
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
[[ -n "${TARGET_HOME}" ]] || fatal "could not resolve home for user '${TARGET_USER}'"

run_as_target() {
  if [[ "$(id -un)" == "${TARGET_USER}" ]]; then
    "$@"
  else
    sudo -u "${TARGET_USER}" --preserve-env=HOME -H "$@"
  fi
}

# ---- 1. apt prerequisites for pipx ------------------------------------
APT_PKGS=(python3 python3-venv python3-pip pipx)
MISSING=()
for p in "${APT_PKGS[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
done
if (( ${#MISSING[@]} > 0 )); then
  if [[ "$(id -u)" -ne 0 ]]; then
    fatal "Missing apt packages: ${MISSING[*]}. Re-run with sudo."
  fi
  log "Installing apt prerequisites: ${MISSING[*]}"
  apt-get update -y
  apt-get install -y "${MISSING[@]}"
fi

# ---- 2. pipx itself (per-user) ---------------------------------------
if ! run_as_target bash -lc 'command -v pipx >/dev/null 2>&1'; then
  log "Configuring pipx PATH for ${TARGET_USER}"
  run_as_target bash -lc 'pipx ensurepath >/dev/null 2>&1 || true'
fi

# ---- 3. install / upgrade idea4rc-capsule ----------------------------
log "Installing idea4rc-capsule from ${SCRIPT_DIR} via pipx (user: ${TARGET_USER})"
if run_as_target bash -lc 'pipx list 2>/dev/null | grep -q "package idea4rc-capsule"'; then
  run_as_target bash -lc "pipx upgrade --pip-args='--upgrade' idea4rc-capsule || pipx reinstall idea4rc-capsule"
else
  run_as_target bash -lc "pipx install '${SCRIPT_DIR}'"
fi

log "Done."
log "Next:"
log "  idea4rc-capsule --help"
log "  sudo idea4rc-capsule install --auto      # OS prerequisites"
log "  idea4rc-capsule init-config > ~/capsule.toml"
