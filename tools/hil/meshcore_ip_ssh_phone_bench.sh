#!/usr/bin/env bash
# Explicit RAW Companion endpoint -> one isolated SSH/SFTP server for Android.
# No host routes, firewall, accounts, system sshd or firmware settings change.
set -euo pipefail
umask 077
endpoint=''; bench_dir=''; prefix=''; account=''; ssh_user=''; duration=1200
baud=115200; serial_dtr=off; expect_serial=''
die() { printf '%s\n' "$*" >&2; exit 2; }
while (($#)); do
    (($# >= 2)) || die 'Every option requires a value.'
    case "$1" in
        --endpoint) endpoint=$2 ;; --bench-dir) bench_dir=$2 ;;
        --prefix) prefix=$2 ;; --duration) duration=$2 ;;
        --account) account=$2 ;; --ssh-user) ssh_user=$2 ;;
        --baud) baud=$2 ;; --serial-dtr) serial_dtr=$2 ;; --expect-serial) expect_serial=$2 ;;
        *) die "Unknown option: $1" ;;
    esac
    shift 2
done
[[ ${EUID:-$(id -u)} == 0 ]] || die 'Run as root for the isolated namespace/TUN.'
[[ "$prefix" =~ ^mcphone-[a-z0-9][a-z0-9-]{0,19}$ ]] || die 'Use a unique mcphone- prefix.'
[[ "$duration" =~ ^[0-9]+$ && "$duration" -ge 60 && "$duration" -le 1800 ]] || die 'Duration must be 60..1800 seconds.'
[[ "$baud" =~ ^[0-9]+$ && "$baud" -ge 1200 && "$baud" -le 2000000 ]] || die 'Baud must be 1200..2000000.'
[[ "$serial_dtr" == off || "$serial_dtr" == on ]] || die 'Serial DTR must be off or explicitly on.'
[[ -z "$expect_serial" || "$expect_serial" =~ ^[A-Za-z0-9_-]{1,128}$ ]] || die 'Invalid expected USB serial.'
[[ "$account" =~ ^[a-z_][a-z0-9_-]*$ ]] || die 'Specify an existing unprivileged --account.'
[[ $(id -u "$account") -ne 0 ]] || die 'The test account must not be root.'
ssh_user=${ssh_user:-$account}
[[ "$ssh_user" == "$account" ]] || die 'SSH user must match the private artifact/key owner account.'
[[ "$bench_dir" == /* && -d "$bench_dir" && "$bench_dir" != / ]] || die 'Use an existing absolute bench directory.'
[[ -x /usr/sbin/sshd && -c /dev/net/tun ]] || die 'sshd and /dev/net/tun are required.'
for dependency in ip python3 ssh-keygen stat realpath runuser sysctl ss mktemp; do
    command -v "$dependency" >/dev/null || die "Missing dependency: $dependency"
done
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
python3 - "$endpoint" "$script_dir" "$expect_serial" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from meshcore_ip_relay import validate_companion_endpoint
try:
    validate_companion_endpoint(sys.argv[1], expect_serial=(sys.argv[3] or None) if len(sys.argv) > 3 else None)
except ValueError as error:
    raise SystemExit(str(error))
PY
relay="$script_dir/meshcore_ip_relay.py"
[[ -r "$relay" ]] || die 'The RAW IP relay must be beside this script.'
bench_dir=$(realpath -e -- "$bench_dir")
namespace="$prefix"
[[ ! -e "/var/run/netns/$namespace" ]] || die 'Namespace already exists; refusing to reuse it.'
run_dir=$(mktemp -d -- "$bench_dir/meshcore-phone.XXXXXXXX")
[[ "$run_dir" == "$bench_dir/meshcore-phone."* ]] || die 'Unexpected artifact directory.'
created=0; inode=''; relay_pid=''; sshd_pid=''
cleanup() {
    local result=$? observed='' child parent
    trap - EXIT INT TERM HUP
    set +e
    for child in "$relay_pid" "$sshd_pid"; do
        [[ "$child" =~ ^[0-9]+$ ]] || continue
        parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
        [[ "$parent" == "$$" ]] && kill -TERM "$child" 2>/dev/null
    done
    if ((created)); then
        observed=$(stat -Lc '%d:%i' -- "/var/run/netns/$namespace" 2>/dev/null)
        if [[ -n "$inode" && "$observed" == "$inode" ]]; then
            while read -r child; do
                [[ "$child" =~ ^[0-9]+$ ]] && kill -TERM "$child" 2>/dev/null
            done < <(ip netns pids "$namespace")
            sleep 0.3
            observed=$(stat -Lc '%d:%i' -- "/var/run/netns/$namespace" 2>/dev/null)
            if [[ "$observed" == "$inode" ]]; then
                while read -r child; do
                    [[ "$child" =~ ^[0-9]+$ ]] && kill -KILL "$child" 2>/dev/null
                done < <(ip netns pids "$namespace")
                ip netns delete "$namespace"
            fi
        fi
    fi
    # A connecting relay still in the host namespace must not strand cleanup.
    # Reap only our explicit children, never search/kill another test's process.
    for child in "$relay_pid" "$sshd_pid"; do
        [[ "$child" =~ ^[0-9]+$ ]] || continue
        for _ in {1..20}; do
            kill -0 "$child" 2>/dev/null || break
            sleep 0.1
        done
        parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
        [[ "$parent" == "$$" ]] && kill -KILL "$child" 2>/dev/null
        wait "$child" 2>/dev/null
    done
    rm -f -- "$run_dir/host_key" "$run_dir/client_key"
    printf 'exit=%s\n' "$result" >> "$run_dir/result.txt"
    chown "$account": "$run_dir/result.txt" 2>/dev/null
    printf 'RAW phone bench stopped; ephemeral keys removed. Evidence: %s\n' "$run_dir"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
chown "$account": "$run_dir"
chmod 700 -- "$run_dir"
ssh-keygen -q -t ed25519 -N '' -f "$run_dir/host_key"
runuser -u "$account" -- ssh-keygen -q -t ed25519 -N '' -f "$run_dir/client_key"
ip netns add "$namespace"; created=1
inode=$(stat -Lc '%d:%i' -- "/var/run/netns/$namespace")
ip -n "$namespace" link set lo up
ip netns exec "$namespace" sysctl -qw net.ipv6.conf.all.disable_ipv6=1
ip netns exec "$namespace" sysctl -qw net.ipv6.conf.default.disable_ipv6=1
deadline=$((SECONDS + duration))
serial_options=(--baud "$baud" --serial-dtr "$serial_dtr")
[[ -z "$expect_serial" ]] || serial_options+=(--expect-serial "$expect_serial")
python3 "$relay" --endpoint "$endpoint" --netns "/var/run/netns/$namespace" \
    "${serial_options[@]}" \
    --interface mcip0 --local-address 10.90.5.2 --remote-address 10.90.5.1 \
    --path-hex '' --path-hash-width 1 --return-path-byte-count 0 --mtu 156 \
    --duration "$duration" --ready-after-handshake --status-file "$run_dir/relay.json" \
    > "$run_dir/relay.log" 2>&1 &
relay_pid=$!
ready=0
for _ in {1..100}; do
    kill -0 "$relay_pid" 2>/dev/null || die 'RAW relay exited; inspect its bounded evidence.'
    if python3 - "$run_dir/relay.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as source:
        state = json.load(source)
    valid = (state.get('state') == 'ready' and state.get('transport') == 'meshcore-raw-ip'
             and state.get('interface') == 'mcip0' and state.get('mtu') == 156
             and state.get('forward_path_bytes') == 0 and state.get('return_path_bytes') == 0)
except (OSError, ValueError):
    valid = False
raise SystemExit(0 if valid else 1)
PY
    then ready=1; break; fi
    sleep 0.2
done
((ready)) || die 'Companion APP_START/SelfInfo handshake did not become ready.'
ip -n "$namespace" link show dev mcip0 >/dev/null
ip -n "$namespace" route show > "$run_dir/routes.txt"
options=(-f /dev/null -h "$run_dir/host_key" -p 2222
    -o ListenAddress=10.90.5.2 -o "PidFile=$run_dir/sshd.pid"
    -o "AuthorizedKeysFile=$run_dir/client_key.pub" -o StrictModes=no
    -o "AllowUsers=$ssh_user" -o PermitRootLogin=no -o UsePAM=no
    -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
    -o AuthenticationMethods=publickey -o DisableForwarding=yes
    -o PermitUserEnvironment=no -o X11Forwarding=no -o PermitTTY=yes
    -o 'Subsystem=sftp internal-sftp')
/usr/sbin/sshd -t "${options[@]}"
ip netns exec "$namespace" /usr/sbin/sshd -D -e "${options[@]}" > "$run_dir/sshd.log" 2>&1 &
sshd_pid=$!
listening=0
for _ in {1..30}; do
    kill -0 "$relay_pid" && kill -0 "$sshd_pid" || die 'An owned fixture child exited.'
    if ip netns exec "$namespace" ss -H -ltn | grep -q '10\.90\.5\.2:2222'; then listening=1; break; fi
    sleep 0.1
done
((listening)) || die 'The isolated SSH/SFTP listener did not become ready.'
printf 'READY: transport=raw gateway=%s local=10.90.5.1 remote=10.90.5.2 MTU=156 SSH=2222 user=%s\n' "$endpoint" "$ssh_user"
printf 'Ephemeral client private key file (never print contents): %s/client_key\n' "$run_dir"
ssh-keygen -lf "$run_dir/host_key.pub"
printf 'Artifacts: %s\n' "$run_dir"
printf 'Server helper PID: %s\n' "$$"
while ((SECONDS < deadline)); do
    kill -0 "$relay_pid" && kill -0 "$sshd_pid" || die 'An owned fixture child exited.'
    sleep 1
done
