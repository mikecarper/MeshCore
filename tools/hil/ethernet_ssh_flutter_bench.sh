#!/usr/bin/env bash
# Isolated Linux client for the explicitly opt-in Flutter SSH-over-radio test.
set -euo pipefail
endpoint=''; bench_dir=''; workspace=''; flutter=''; private_key=''; fingerprint=''
prefix=''; account=''; test_name=rf; test_timeout=210; phase_timeout=45
die() { printf '%s\n' "$*" >&2; exit 2; }
while (($#)); do
    (($# >= 2)) || die 'Every option requires a value.'
    case "$1" in
        --endpoint) endpoint=$2 ;; --bench-dir) bench_dir=$2 ;;
        --workspace) workspace=$2 ;; --flutter) flutter=$2 ;;
        --key) private_key=$2 ;; --fingerprint) fingerprint=$2 ;;
        --prefix) prefix=$2 ;; --account) account=$2 ;;
        --test-name) test_name=$2 ;;
        --phase-timeout)
            [[ "$2" =~ ^[0-9]{1,2}$ ]] && ((10#$2 >= 5 && 10#$2 <= 60)) \
                || die '--phase-timeout must be 5..60 seconds.'
            phase_timeout=$((10#$2)) ;;
        --test-timeout)
            [[ "$2" =~ ^[0-9]{1,3}$ ]] && ((10#$2 >= 1 && 10#$2 <= 180)) \
                || die '--test-timeout must be 1..180 seconds.'
            test_timeout=$((10#$2)) ;;
        *) die "Unknown option: $1" ;;
    esac
    shift 2
done
[[ "$test_name" == rf || "$test_name" == speed ]] || die '--test-name must be rf or speed.'
test_file="test/services/lora_ssh_${test_name}_test.dart"
[[ ${EUID:-$(id -u)} == 0 ]] || die 'Root is required for the isolated namespace/TAP.'
[[ "$prefix" =~ ^ethflutter-[a-z0-9][a-z0-9-]{0,19}$ ]] || die 'Use a unique ethflutter- prefix.'
[[ "$account" =~ ^[a-z_][a-z0-9_-]*$ ]] || die 'Specify an unprivileged test account.'
[[ $(id -u "$account") -ne 0 ]] || die 'The Flutter test account must not be root.'
for directory in "$bench_dir" "$workspace"; do
    [[ "$directory" == /* && "$directory" != / && -d "$directory" ]] || die 'Use existing absolute directories.'
done
[[ "$flutter" == /* && -x "$flutter" && "$private_key" == /* && -f "$private_key" ]] || die 'Use explicit Flutter/key paths.'
[[ -f "$workspace/$test_file" ]] || die 'The selected opt-in SSH backend test is missing.'
[[ "$fingerprint" =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] || die 'Use the expected OpenSSH SHA-256 fingerprint.'
runuser -u "$account" -- test -r "$private_key" || die 'The test account cannot read its selected key.'
[[ ! -e "/var/run/netns/$prefix" ]] || die 'Refusing to reuse an existing namespace.'
python3 - "$endpoint" <<'PY'
import ipaddress, sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
try:
    host = ipaddress.IPv4Address(u.hostname)
    valid = (u.scheme == 'tcp' and u.port == 5003 and not u.username and not u.password
             and u.path in ('', '/') and not u.query and not u.fragment
             and not host.is_multicast and not host.is_unspecified)
except ValueError:
    valid = False
if not valid:
    raise SystemExit('Use an explicit numeric IPv4 tcp://RADIO:5003 endpoint.')
PY
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
run_dir=$(mktemp -d -- "$bench_dir/ethernet-flutter.XXXXXXXX")
chown "$account": "$run_dir"
chmod 700 -- "$run_dir"
# Keep evidence readable by the selected account even if sudo inherited 077.
# The account-owned parent remains private (0700); no keys are copied here.
umask 022
created=0; inode=''; relay_pid=''; test_pid=''; monitor_pid=''; tcp_started=0; tcp_finished=0
capture_tcp() {
    local phase=$1 observed=''
    observed=$(stat -Lc '%d:%i' -- "/var/run/netns/$prefix" 2>/dev/null) || return 1
    [[ -n "$inode" && "$observed" == "$inode" ]] || return 1
    date -u +%Y-%m-%dT%H:%M:%SZ > "$run_dir/tcp-$phase.utc"
    ip netns exec "$prefix" cat /proc/net/snmp > "$run_dir/tcp-$phase.snmp" \
        2> "$run_dir/tcp-$phase.error"
}
finish_tcp() {
    ((tcp_started && !tcp_finished)) || return 0
    tcp_finished=1
    capture_tcp after || true
    python3 "$script_dir/ethernet_tcp_stats.py" --directory "$run_dir" \
        > "$run_dir/tcp-stats.error" 2>&1 || true
}
sample_tcp() {
    local remaining=300 sample lines observed
    while ((remaining > 0)) && kill -0 "$test_pid" 2>/dev/null; do
        observed=$(stat -Lc '%d:%i' -- "/var/run/netns/$prefix" 2>/dev/null) || break
        [[ "$observed" == "$inode" ]] || break
        sample=$(printf '\nUTC %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            timeout --kill-after=1s 2s ip netns exec "$prefix" ss -tin 2>&1 | head -n 12 || true)
        printf '%s\n' "$sample" | head -n "$remaining" >> "$run_dir/tcp-ss.txt"
        lines=$(printf '%s\n' "$sample" | wc -l)
        remaining=$((remaining - lines))
        sleep 1
    done
}
cleanup() {
    local result=$? observed='' child parent
    trap - EXIT INT TERM HUP
    set +e
    finish_tcp
    for child in "$test_pid" "$monitor_pid" "$relay_pid"; do
        if [[ "$child" =~ ^[0-9]+$ ]]; then
            parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
            [[ "$parent" == "$$" ]] && kill -TERM "$child" 2>/dev/null
        fi
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
    # Reap only our explicit children. A relay still connecting outside the
    # namespace must not make cleanup wait forever after ignoring SIGTERM.
    for child in "$test_pid" "$monitor_pid" "$relay_pid"; do
        [[ "$child" =~ ^[0-9]+$ ]] || continue
        parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
        if [[ "$parent" == "$$" ]]; then
            for _ in {1..20}; do
                kill -0 "$child" 2>/dev/null || break
                sleep 0.1
            done
            parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
            [[ "$parent" == "$$" ]] && kill -KILL "$child" 2>/dev/null
        fi
        wait "$child" 2>/dev/null
    done
    printf 'exit=%s\n' "$result" >> "$run_dir/result.txt"
    printf 'Flutter RF artifacts: %s\n' "$run_dir"
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
python3 "$script_dir/ethernet_tap_relay.py" --endpoint "$endpoint" \
    --netns "/var/run/netns/$prefix" --interface lora0 --duration 240 \
    --status-file "$run_dir/relay.json" > "$run_dir/relay.log" 2>&1 &
relay_pid=$!
for _ in {1..60}; do
    kill -0 "$relay_pid" || die 'TAP relay exited; inspect relay.log.'
    ip -n "$prefix" link show dev lora0 >/dev/null 2>&1 && break
    sleep 0.2
done
ip -n "$prefix" link set lora0 address 02:90:05:00:00:01 mtu 230 up
ip -n "$prefix" address add 10.90.5.1/24 dev lora0
ip -n "$prefix" route show > "$run_dir/routes.txt"
cd -- "$workspace"
tcp_started=1
capture_tcp before || true
timeout --kill-after=5s "${test_timeout}s" ip netns exec "$prefix" runuser -u "$account" -- env \
    LORA_SSH_RF_KEY="$private_key" EXPECTED_HOST_FINGERPRINT_SHA256="$fingerprint" \
    LORA_SSH_RF_PORT=2222 LORA_SSH_RF_USER=mikec \
    LORA_SSH_SPEED_REPORT="$run_dir/speed.json" LORA_SSH_SPEED_BYTES=8192 \
    LORA_SSH_SPEED_TIMEOUT_SECONDS="$phase_timeout" LORA_SSH_SPEED_TIMEOUT="$phase_timeout" \
    "$flutter" test --no-pub --reporter expanded "$test_file" \
    > "$run_dir/flutter.txt" 2>&1 &
test_pid=$!
sample_tcp &
monitor_pid=$!
test_result=0
wait "$test_pid" || test_result=$?
test_pid=''
if [[ "$monitor_pid" =~ ^[0-9]+$ ]]; then
    kill -TERM "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
fi
monitor_pid=''
finish_tcp
printf 'scenario=%s\n' "$test_name" > "$run_dir/result.txt"
if ((test_result == 0)); then
    printf 'PASS: real Flutter SSH backend test completed through the isolated radio TAP.\n' >> "$run_dir/result.txt"
fi
exit "$test_result"
