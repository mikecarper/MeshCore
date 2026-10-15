#!/usr/bin/env bash
# One explicitly selected radio, one isolated SSH server for the phone test.
# No host routes, firewall, accounts, system sshd or other radios are changed.
set -euo pipefail
endpoint=''; bench_dir=''; prefix=''; duration=1200; kiss_port=0; baud=115200; serial_dtr=off; kiss_modem=off
die() { printf '%s\n' "$*" >&2; exit 2; }
while (($#)); do
    (($# >= 2)) || die 'Every option requires a value.'
    case "$1" in
        --endpoint) endpoint=$2 ;; --bench-dir) bench_dir=$2 ;;
        --prefix) prefix=$2 ;; --duration) duration=$2 ;;
        --kiss-port) kiss_port=$2 ;; --baud) baud=$2 ;; --serial-dtr) serial_dtr=$2 ;;
        --kiss-modem) kiss_modem=$2 ;;
        *) die "Unknown option: $1" ;;
    esac
    shift 2
done
[[ ${EUID:-$(id -u)} == 0 ]] || die 'Run with sudo for the isolated namespace/TAP.'
[[ "$prefix" =~ ^ethphone-[a-z0-9][a-z0-9-]{0,19}$ ]] || die 'Use a unique ethphone- prefix.'
[[ "$duration" =~ ^[0-9]+$ && "$duration" -ge 60 && "$duration" -le 1800 ]] || die 'Duration must be 60..1800 seconds.'
[[ "$bench_dir" == /* && -d "$bench_dir" && "$bench_dir" != / ]] || die 'Use an existing absolute bench directory.'
[[ "$kiss_port" == 0 || "$kiss_port" == 1 ]] || die 'KISS port must be 0 or 1.'
[[ "$baud" =~ ^[0-9]{4,7}$ && "$baud" -ge 1200 && "$baud" -le 2000000 ]] || die 'Baud must be 1200..2000000.'
[[ "$serial_dtr" == on || "$serial_dtr" == off ]] || die 'Serial DTR must be on or off.'
[[ "$kiss_modem" == on || "$kiss_modem" == off ]] || die 'Full KISS modem mode must be on or off.'
[[ "$kiss_modem" == off || "$endpoint" == serial:///* ]] || die 'Full KISS modem mode requires an explicit serial endpoint.'
[[ -x /usr/sbin/sshd && -c /dev/net/tun ]] || die 'sshd and /dev/net/tun are required.'
for dependency in ip python3 ssh-keygen stat realpath runuser sysctl ss mktemp; do
    command -v "$dependency" >/dev/null || die "Missing dependency: $dependency"
done
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
python3 - "$endpoint" "$script_dir" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from ethernet_tap_relay import validate_endpoint
try:
    validate_endpoint(sys.argv[1], strict_tcp=True)
except ValueError:
    raise SystemExit('Use numeric IPv4 tcp://RADIO:5003 or an explicit serial:///dev/serial/by-id/DEVICE endpoint.')
PY
relay="$script_dir/ethernet_tap_relay.py"
[[ -r "$relay" ]] || die 'The TAP relay must be beside this script.'
bench_dir=$(realpath -e -- "$bench_dir")
namespace="$prefix"
[[ ! -e "/var/run/netns/$namespace" ]] || die 'Namespace already exists; refusing to reuse it.'
run_dir=$(mktemp -d -- "$bench_dir/ethernet-phone.XXXXXXXX")
[[ "$run_dir" == "$bench_dir/ethernet-phone."* ]] || die 'Unexpected artifact directory.'
# Only the explicitly selected test account can retrieve its ephemeral key.
chown mikec: "${run_dir}"
chmod 700 -- "$run_dir"
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
    wait 2>/dev/null
    rm -f -- "$run_dir/host_key" "$run_dir/client_key"
    printf 'exit=%s\n' "$result" >> "$run_dir/result.txt"
    chmod 644 -- "$run_dir/result.txt"
    printf 'Phone bench stopped; ephemeral keys removed. Evidence: %s\n' "$run_dir"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
ssh-keygen -q -t ed25519 -N '' -f "$run_dir/host_key"
runuser -u mikec -- ssh-keygen -q -t ed25519 -N '' -f "$run_dir/client_key"
ip netns add "$namespace"; created=1
inode=$(stat -Lc '%d:%i' -- "/var/run/netns/$namespace")
ip -n "$namespace" link set lo up
ip netns exec "$namespace" sysctl -qw net.ipv6.conf.all.disable_ipv6=1
ip netns exec "$namespace" sysctl -qw net.ipv6.conf.default.disable_ipv6=1
deadline=$((SECONDS + duration))
modem_args=()
if [[ "$kiss_modem" == on ]]; then modem_args+=(--kiss-modem); fi
python3 "$relay" --endpoint "$endpoint" --netns "/var/run/netns/$namespace" \
    --baud "$baud" --kiss-port "$kiss_port" --serial-dtr "$serial_dtr" \
    "${modem_args[@]}" \
    --interface lora0 --duration "$duration" --status-file "$run_dir/relay.json" \
    > "$run_dir/relay.log" 2>&1 &
relay_pid=$!
for _ in {1..60}; do
    kill -0 "$relay_pid" || die 'TAP relay exited; inspect relay.log.'
    ip -n "$namespace" link show dev lora0 >/dev/null 2>&1 && break
    sleep 0.2
done
ip -n "$namespace" link set lora0 address 02:90:05:00:00:02 mtu 230 up
ip -n "$namespace" address add 10.90.5.2/24 dev lora0
ip -n "$namespace" route show > "$run_dir/routes.txt"
options=(-f /dev/null -h "$run_dir/host_key" -p 2222
    -o ListenAddress=10.90.5.2 -o "PidFile=$run_dir/sshd.pid"
    -o "AuthorizedKeysFile=$run_dir/client_key.pub" -o StrictModes=no
    -o AllowUsers=mikec -o PermitRootLogin=no -o UsePAM=no
    -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
    -o AuthenticationMethods=publickey -o DisableForwarding=yes
    -o PermitUserEnvironment=no -o X11Forwarding=no -o PermitTTY=yes
    -o 'Subsystem=sftp internal-sftp')
/usr/sbin/sshd -t "${options[@]}"
ip netns exec "$namespace" /usr/sbin/sshd -D -e "${options[@]}" > "$run_dir/sshd.log" 2>&1 &
sshd_pid=$!
listening=0
for _ in {1..30}; do
    kill -0 "$sshd_pid" || die 'Isolated sshd exited; inspect sshd.log.'
    if ip netns exec "$namespace" ss -H -ltn | grep -q '10\.90\.5\.2:2222'; then listening=1; break; fi
    sleep 0.1
done
((listening)) || die 'The isolated SSH listener did not become ready.'
printf 'READY: gateway=%s local=10.90.5.1 remote=10.90.5.2 SSH=2222 user=mikec\n' "$endpoint"
printf 'Ephemeral client private key file (never print contents): %s/client_key\n' "$run_dir"
ssh-keygen -lf "$run_dir/host_key.pub"
printf 'Artifacts: %s\n' "$run_dir"
printf 'Server helper PID: %s\n' "$$"
while ((SECONDS < deadline)); do
    kill -0 "$relay_pid" && kill -0 "$sshd_pid" || die 'A bench child exited.'
    sleep 1
done
