#!/usr/bin/env bash
# Apply the same bounded download retry to direct CI PlatformIO calls.
set -o pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)/build_legacy.sh"
run_pio_with_size_detection "$@"
