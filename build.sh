#!/usr/bin/env bash
# MeshCore's two build entry points. The mature target/profile implementation
# lives in build_legacy.sh until its callers can be migrated independently.

build_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  # CI and shell contract tests source the historical helper functions.
  source "${build_root}/build_legacy.sh"
  return
fi

set -euo pipefail
cd -- "$build_root"

usage() {
  cat <<'EOF'
Usage:
  bash build.sh build-firmware TARGET [build options]
  bash build.sh release-build [--firmware-version VERSION] [--resume]

build-firmware builds one exact target, selecting its complete supported
profile by default. The mature target options remain available for that build.
release-build builds the canonical firmware matrix, ESP32 partition migration
images, and a verified local release bundle. It never publishes to GitHub.
EOF
}

case "${1:-}" in
  build-firmware)
    exec bash "${build_root}/build_legacy.sh" "$@"
    ;;
  release-build)
    shift
    exec python3 "${build_root}/scripts/build_local_release.py" "$@"
    ;;
  help|usage|-h|--help)
    usage
    ;;
  # Read-only CI queries retain their existing interface.
  list|-l|get-companion-firmwares-to-build|get-repeater-firmwares-to-build|get-room-server-firmwares-to-build)
    exec bash "${build_root}/build_legacy.sh" "$@"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
