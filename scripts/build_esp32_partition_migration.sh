#!/bin/sh
# Interactive front end for the verified ESP32 in-place migration recipe.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
recipe=$script_dir/build_esp32_partition_migration.py

usage() {
    cat <<'EOF'
Usage: scripts/build_esp32_partition_migration.sh [options]

With no arguments, choose one ESP32 board/role or the complete configured
set from a menu. The complete set creates one ZIP per board/role plus a release ZIP.

  --board KEY           Build one board (repeatable)
  --role ROLE           Build every repeater, room-server, or sensor board
  --all                 Build all configured repeater, room-server and sensor roles
  --version VERSION     Firmware version (required without the menu)
  --radio-preset NAME   Radio preset (required without the menu)
  --profile NAME        default or cascade (default: cascade)
  --jobs N              PlatformIO workers within each serial build
  --output-root PATH    Release output folder
  --dry-run             Print commands without building
  --list-boards         Show configured board/role keys
  --help                Show this help

Boards with unrelated source layout plans are not offered. A name appearing
in an old firmware release is not proof that OTA migration is safe on that
hardware. The bridge checks the live layout, and no release ZIP is published
unless every selected package passes the recipe's checks.
EOF
}

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required" >&2
    exit 1
fi

version=
radio_preset=
profile=cascade
jobs=
output_root=
boards=
role=
all=0
dry_run=0
interactive=1

while [ "$#" -gt 0 ]; do
    interactive=0
    case "$1" in
        --board|--role|--version|--radio-preset|--profile|--jobs|--output-root)
            if [ "$#" -lt 2 ]; then
                echo "Missing value for $1" >&2
                exit 2
            fi
            case "$1" in
                --board) boards="${boards}${boards:+ }$2" ;;
                --role) role=$2 ;;
                --version) version=$2 ;;
                --radio-preset) radio_preset=$2 ;;
                --profile) profile=$2 ;;
                --jobs) jobs=$2 ;;
                --output-root) output_root=$2 ;;
            esac
            shift 2 ;;
        --all) all=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        --list-boards) exec python3 -B "$recipe" --list-boards ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

board_keys=$(python3 -B "$recipe" --list-boards)
if [ -z "$board_keys" ]; then
    echo "No boards are configured" >&2
    exit 1
fi

if [ "$interactive" -eq 1 ]; then
    echo "ESP32 in-place partition migration"
    echo "Repeater, room-server and sensor recipes are listed."
    index=1
    for board in $board_keys; do
        echo "  $index) $board"
        index=$((index + 1))
    done
    echo "  a) Build all configured board/role recipes and create a release ZIP"
    echo "  r) Build all repeaters"
    echo "  m) Build all room servers"
    echo "  s) Build all sensors"
    echo "  q) Quit"
    printf 'Choose: '
    IFS= read -r choice
    case "$choice" in
        a|A|all) all=1 ;;
        r|R) role=repeater ;;
        m|M) role=room-server ;;
        s|S) role=sensor ;;
        q|Q|quit) exit 0 ;;
        *)
            index=1
            for board in $board_keys; do
                if [ "$choice" = "$index" ]; then
                    boards=$board
                    break
                fi
                index=$((index + 1))
            done
            if [ -z "$boards" ]; then
                echo "Invalid selection" >&2
                exit 2
            fi ;;
    esac
    printf 'Firmware version: '
    IFS= read -r version
    printf 'Radio preset [usa-cascadia]: '
    IFS= read -r radio_preset
    radio_preset=${radio_preset:-usa-cascadia}
    printf 'Profile [cascade]: '
    IFS= read -r selected_profile
    profile=${selected_profile:-cascade}
fi

if { [ "$all" -eq 1 ] && { [ -n "$boards" ] || [ -n "$role" ]; }; } \
    || { [ -n "$boards" ] && [ -n "$role" ]; }; then
    echo "Choose only one of --all, --board, or --role" >&2
    exit 2
fi
if [ -z "$version" ] || [ -z "$radio_preset" ]; then
    echo "Firmware version and radio preset are required" >&2
    exit 2
fi
if [ "$profile" != default ] && [ "$profile" != cascade ]; then
    echo "Profile must be default or cascade" >&2
    exit 2
fi
if [ -n "$role" ] && [ "$role" != repeater ] \
    && [ "$role" != room-server ] && [ "$role" != sensor ]; then
    echo "Role must be repeater, room-server, or sensor" >&2
    exit 2
fi

set -- "$recipe" --version "$version" --radio-preset "$radio_preset" --profile "$profile"
if [ -n "$boards" ]; then
    for board in $boards; do
        found=0
        for known in $board_keys; do
            if [ "$board" = "$known" ]; then found=1; break; fi
        done
        if [ "$found" -ne 1 ]; then
            echo "Board/role is not configured for this recipe: $board" >&2
            exit 2
        fi
        set -- "$@" --board "$board"
    done
fi
if [ -n "$role" ]; then set -- "$@" --role "$role"; fi
if [ -n "$jobs" ]; then set -- "$@" --jobs "$jobs"; fi
if [ -n "$output_root" ]; then set -- "$@" --output-root "$output_root"; fi
if [ "$dry_run" -eq 1 ]; then set -- "$@" --dry-run; fi
exec python3 -B "$@"
