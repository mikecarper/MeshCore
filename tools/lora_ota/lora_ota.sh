#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
python_cmd=${PYTHON:-python3}

exec "$python_cmd" "$script_dir/lora_ota.py" "$@"
