#!/usr/bin/env bash
# Matched RAW156 / full KISS230 USB-LoRa-USB SFTP client. No host routes.
set -euo pipefail
mode=''; endpoint=''; windows_python=''; expect_serial=''; expect_vid=''; expect_pid=''
bench_dir=''; workspace=''; dart=''; config=''; prefix=''; account=''; baud=115200; dtr=off
die() { printf '%s\n' "$*" >&2; exit 2; }
while (($#)); do
    (($# >= 2)) || die 'Every option requires a value.'
    case "$1" in
        --mode) mode=$2 ;; --endpoint) endpoint=$2 ;;
        --windows-python) windows_python=$2 ;; --expect-serial) expect_serial=$2 ;;
        --expect-vid) expect_vid=$2 ;; --expect-pid) expect_pid=$2 ;;
        --bench-dir) bench_dir=$2 ;; --workspace) workspace=$2 ;; --dart) dart=$2 ;;
        --config) config=$2 ;; --prefix) prefix=$2 ;; --account) account=$2 ;;
        --baud) baud=$2 ;; --serial-dtr) dtr=$2 ;;
        *) die "Unknown option: $1" ;;
    esac
    shift 2
done
[[ ${EUID:-$(id -u)} == 0 ]] || die 'Root is required only for the isolated namespace/TUN.'
[[ "$mode" == raw || "$mode" == kiss ]] || die 'Choose --mode raw or kiss explicitly.'
[[ "$endpoint" =~ ^winusb://COM[1-9][0-9]*$ ]] || die 'Choose one explicit winusb://COMn endpoint.'
[[ "$prefix" =~ ^mcusb-[a-z0-9][a-z0-9-]{0,19}$ ]] || die 'Use a unique mcusb- prefix.'
[[ "$account" =~ ^[a-z_][a-z0-9_-]*$ && $(id -u "$account") -ne 0 ]] || die 'Choose an unprivileged existing account.'
[[ "$baud" =~ ^[0-9]+$ && "$baud" -ge 1200 && "$baud" -le 2000000 ]] || die 'Invalid baud.'
[[ "$dtr" == off || "$dtr" == on ]] || die 'Choose DTR off or on explicitly.'
[[ "$expect_serial" =~ ^[A-Za-z0-9_-]{1,128}$ ]] || die 'An exact USB serial is required.'
[[ "$expect_vid" =~ ^0x[0-9A-Fa-f]{4}$ && "$expect_pid" =~ ^0x[0-9A-Fa-f]{4}$ ]] || die 'Exact hexadecimal USB VID/PID are required.'
for directory in "$bench_dir" "$workspace"; do
    [[ "$directory" == /* && "$directory" != / && -d "$directory" ]] || die 'Use existing absolute directories.'
done
[[ "$windows_python" == /* && -x "$windows_python" && "$dart" == /* && -x "$dart" ]] || die 'Choose absolute Python.exe and Dart executable paths.'
[[ "$config" == /* && -f "$config" && ! -L "$config" ]] || die 'Choose the private regular configuration file.'
[[ -f "$workspace/tool/hil/lora_sftp_bench.dart" && -f "$workspace/.dart_tool/package_config.json" ]] || die 'The prepared Dart workspace is required.'
[[ ! -e "/var/run/netns/$prefix" ]] || die 'Refusing to reuse a namespace.'
mtu=156; interface=mcip0
if [[ "$mode" == kiss ]]; then mtu=230; interface=lora0; fi
python3 - "$config" "$mtu" "$account" <<'PY'
import json, os, pwd, stat, sys
path, mtu, account = sys.argv[1:]
info = os.lstat(path)
if not stat.S_ISREG(info.st_mode) or info.st_uid != pwd.getpwnam(account).pw_uid or info.st_mode & 0o077:
    raise SystemExit('Configuration must be owner-only and owned by the selected account.')
with open(path, encoding='utf-8') as source:
    config = json.load(source)
if config.get('mtu') != int(mtu) or config.get('enabled') is not True:
    raise SystemExit('Configuration MTU must match the explicitly selected transport.')
PY
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
run_dir=$(mktemp -d -- "$bench_dir/usb-sftp.XXXXXXXX")
chown "$account": "$run_dir"; chmod 700 -- "$run_dir"
created=0; inode=''; relay_pid=''; client_pid=''; monitor_pid=''; tcp_started=0
capture_tcp() {
    local phase=$1 observed=''
    observed=$(stat -Lc '%d:%i' -- "/var/run/netns/$prefix" 2>/dev/null) || return 1
    [[ -n "$inode" && "$observed" == "$inode" ]] || return 1
    date -u +%Y-%m-%dT%H:%M:%SZ > "$run_dir/tcp-$phase.utc"
    ip netns exec "$prefix" cat /proc/net/snmp > "$run_dir/tcp-$phase.snmp"
}
cleanup() {
    local result=$? observed='' child parent
    trap - EXIT INT TERM HUP
    set +e
    if ((tcp_started)); then
        capture_tcp after
        python3 "$script_dir/ethernet_tcp_stats.py" --directory "$run_dir" > "$run_dir/tcp-stats.log" 2>&1
    fi
    for child in "$client_pid" "$monitor_pid" "$relay_pid"; do
        [[ "$child" =~ ^[0-9]+$ ]] || continue
        parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
        [[ "$parent" == "$$" ]] && kill -TERM "$child" 2>/dev/null
    done
    if ((created)); then
        observed=$(stat -Lc '%d:%i' -- "/var/run/netns/$prefix" 2>/dev/null)
        if [[ -n "$inode" && "$observed" == "$inode" ]]; then
            while read -r child; do
                [[ "$child" =~ ^[0-9]+$ ]] && kill -TERM "$child" 2>/dev/null
            done < <(ip netns pids "$prefix")
            sleep 0.3
            observed=$(stat -Lc '%d:%i' -- "/var/run/netns/$prefix" 2>/dev/null)
            if [[ "$observed" == "$inode" ]]; then
                while read -r child; do
                    [[ "$child" =~ ^[0-9]+$ ]] && kill -KILL "$child" 2>/dev/null
                done < <(ip netns pids "$prefix")
                ip netns delete "$prefix"
            fi
        fi
    fi
    for child in "$client_pid" "$monitor_pid" "$relay_pid"; do
        [[ "$child" =~ ^[0-9]+$ ]] || continue
        for _ in {1..30}; do
            kill -0 "$child" 2>/dev/null || break
            sleep 0.1
        done
        parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
        [[ "$parent" == "$$" ]] && kill -KILL "$child" 2>/dev/null
        wait "$child" 2>/dev/null
    done
    printf 'mode=%s\nmtu=%s\nexit=%s\n' "$mode" "$mtu" "$result" >> "$run_dir/result.txt"
    for evidence in relay.json relay.log result.txt client.log interface.json routes.txt \
        tcp-before.utc tcp-before.snmp tcp-after.utc tcp-after.snmp tcp-stats.log tcp-stats.json tcp-ss.txt; do
        [[ -f "$run_dir/$evidence" && ! -L "$run_dir/$evidence" ]] && chown "$account": "$run_dir/$evidence"
    done
    printf 'USB SFTP artifacts: %s\n' "$run_dir"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
ip netns add "$prefix"; created=1
inode=$(stat -Lc '%d:%i' -- "/var/run/netns/$prefix")
ip -n "$prefix" link set lo up
ip netns exec "$prefix" sysctl -qw net.ipv6.conf.all.disable_ipv6=1
ip netns exec "$prefix" sysctl -qw net.ipv6.conf.default.disable_ipv6=1
usb_args=(--endpoint "$endpoint" --windows-python "$windows_python" --expect-serial "$expect_serial"
    --expect-vid "$expect_vid" --expect-pid "$expect_pid" --baud "$baud" --serial-dtr "$dtr"
    --netns "/var/run/netns/$prefix" --interface "$interface" --duration 660 --status-file "$run_dir/relay.json")
if [[ "$mode" == raw ]]; then
    python3 "$script_dir/meshcore_ip_relay.py" "${usb_args[@]}" --local-address 10.90.5.1 \
        --remote-address 10.90.5.2 --path-hex '' --path-hash-width 1 --return-path-byte-count 0 \
        --mtu 156 --ready-after-handshake > "$run_dir/relay.log" 2>&1 &
else
    python3 "$script_dir/ethernet_tap_relay.py" "${usb_args[@]}" --kiss-port 1 --kiss-modem \
        > "$run_dir/relay.log" 2>&1 &
fi
relay_pid=$!
ready=0
for _ in {1..150}; do
    kill -0 "$relay_pid" 2>/dev/null || die 'USB relay exited; inspect its bounded log.'
    if python3 - "$run_dir/relay.json" "$mode" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as source:
        value = json.load(source)
    ready = value.get('state') == 'ready'
except (OSError, ValueError):
    ready = False
raise SystemExit(0 if ready else 1)
PY
    then ready=1; break; fi
    sleep 0.2
done
((ready)) || die 'USB relay handshake did not become ready.'
if [[ "$mode" == kiss ]]; then
    ip -n "$prefix" link set "$interface" address 02:90:05:00:00:01 mtu 230 up
    ip -n "$prefix" address add 10.90.5.1/24 dev "$interface"
fi
actual_mtu=$(ip -n "$prefix" -j link show dev "$interface" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["mtu"])')
[[ "$actual_mtu" == "$mtu" ]] || die 'The actual isolated interface MTU differs.'
ip -n "$prefix" -j address show dev "$interface" > "$run_dir/interface.json"
ip -n "$prefix" route show > "$run_dir/routes.txt"
capture_tcp before; tcp_started=1
printf 'READY: mode=%s MTU=%s namespace=%s\nArtifacts: %s\n' "$mode" "$mtu" "$prefix" "$run_dir"
timeout --kill-after=5s 600s ip netns exec "$prefix" runuser -u "$account" -- \
    "$dart" "--packages=$workspace/.dart_tool/package_config.json" "$workspace/tool/hil/lora_sftp_bench.dart" \
    --config "$config" --report "$run_dir/sftp.json" > "$run_dir/client.log" 2>&1 &
client_pid=$!
(
    for _ in {1..100}; do
        kill -0 "$client_pid" 2>/dev/null || break
        date -u +%Y-%m-%dT%H:%M:%SZ
        timeout --kill-after=1s 2s ip netns exec "$prefix" ss -tin dst 10.90.5.2 | head -n 8
        sleep 2
    done
) > "$run_dir/tcp-ss.txt" 2>&1 &
monitor_pid=$!
client_result=0
wait "$client_pid" || client_result=$?
client_pid=''
exit "$client_result"
