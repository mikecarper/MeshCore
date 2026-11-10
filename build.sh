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
  bash build.sh release-build [--firmware-version VERSION] [--resume] [--workers 1|2] [--pio-jobs N]

build-firmware builds one exact target, selecting its complete supported
profile by default. The mature target options remain available for that build.
release-build builds the canonical firmware matrix, ESP32 partition migration
images, supported nRF52 Repeater/Room variants with embedded OTAFIX 2.4.12,
standalone exact-board bootloader files, and a verified local release bundle.
It defaults to v1.18.0.1-halo-keymind-cascade-dev and USA Cascade settings.
Use --workers 2 for disjoint targets in isolated checkouts and package folders;
--pio-jobs controls compiler jobs per worker. Packaging runs once after both finish.
It never publishes to GitHub.
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
