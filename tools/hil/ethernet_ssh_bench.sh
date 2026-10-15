#!/usr/bin/env bash
# Explicitly requested, destructive-to-its-own-namespaces-only RF bench.
# Never changes host LAN/interfaces/routes/firewall or the host sshd service.
set -euo pipefail

endpoint_a=''
endpoint_b=''
bench_dir=''
prefix=''
usage() {
    printf '%s\n' 'Usage: sudo bash ethernet_ssh_bench.sh --a tcp://RADIO_A:5003 --b tcp://RADIO_B:5003' \
        '  --bench-dir EXISTING_ABSOLUTE_DIRECTORY --prefix ethssh-UNIQUE' \
        'Both radios must already have matching Ethernet PHY settings and station WiFi.' \
        'Creates only two fresh isolated namespaces, temporary TAPs and a private test sshd.' \
        'Runs ten pings and verifies actual SSH transfers of 4096 bytes in each direction.' \
        'The network test has a shared 180-second deadline. Artifacts stay under bench-dir.'
}
die() { printf '%s\n' "$*" >&2; exit 2; }
while (($#)); do
    case "$1" in
        --a|--b|--bench-dir|--prefix)
            (($# >= 2)) || die 'Missing option value.'
            case "$1" in
                --a) endpoint_a=$2 ;; --b) endpoint_b=$2 ;;
                --bench-dir) bench_dir=$2 ;; --prefix) prefix=$2 ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown option: $1" ;;
    esac
done
[[ "$prefix" =~ ^ethssh-[a-z0-9][a-z0-9-]{0,19}$ ]] \
    || die '--prefix must be ethssh- followed by 1..20 lowercase letters/digits/hyphens.'
[[ "$bench_dir" == /* && -d "$bench_dir" && "$bench_dir" != / ]] \
    || die '--bench-dir must be an existing absolute non-root directory.'
[[ -n "$endpoint_a" && -n "$endpoint_b" && "$endpoint_a" != "$endpoint_b" ]] \
    || die 'Supply two different, explicitly selected TCP radio endpoints.'
[[ ${EUID:-$(id -u)} == 0 ]] || die 'Root is required for isolated network namespaces/TAP.'
for dependency in ip python3 ssh ssh-keygen sha256sum dd timeout mktemp realpath stat runuser sysctl ss; do
    command -v "$dependency" >/dev/null || die "Missing dependency: $dependency"
done
[[ -x /usr/sbin/sshd ]] || die 'Missing /usr/sbin/sshd.'
[[ -c /dev/net/tun ]] || die 'Missing /dev/net/tun.'
id mikec >/dev/null 2>&1 || die 'The local bench account mikec is missing.'
python3 - "$endpoint_a" "$endpoint_b" <<'PY'
import sys
from urllib.parse import urlparse
for endpoint in sys.argv[1:]:
    url = urlparse(endpoint)
    if (url.scheme != 'tcp' or not url.hostname or url.username or url.password
            or url.path not in ('', '/') or url.query or url.fragment
            or url.port not in (None, 5003)):
        raise SystemExit('Endpoints must be tcp://HOST[:5003], without credentials/path/query.')
PY
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
relay="$script_dir/ethernet_tap_relay.py"
[[ -r "$relay" ]] || die 'ethernet_tap_relay.py must be beside this script.'
bench_dir=$(realpath -e -- "$bench_dir")
ns_a="$prefix-a"
ns_b="$prefix-b"
[[ ! -e "/var/run/netns/$ns_a" && ! -e "/var/run/netns/$ns_b" ]] \
    || die 'A requested namespace already exists; refusing to reuse or remove it.'
run_dir=$(mktemp -d -- "$bench_dir/ethernet-ssh.XXXXXXXX")
[[ "$run_dir" == "$bench_dir/ethernet-ssh."* ]] || die 'Unexpected artifact directory.'
# Public-key lookup runs as mikec. Generated private keys remain root-only 0600.
chmod 755 -- "$run_dir"
created_a=0; created_b=0; inode_a=''; inode_b=''
relay_a=''; relay_b=''; sshd_pid=''
deadline=$((SECONDS + 180))
bounded() {
    local remaining=$((deadline - SECONDS))
    ((remaining > 0)) || { echo 'Shared 180-second bench deadline expired.' >&2; return 124; }
    timeout --kill-after=3s "${remaining}s" "$@"
}
namespace_matches() {
    local name=$1 expected=$2 observed
    [[ -n "$expected" ]] || return 1
    observed=$(stat -Lc '%d:%i' -- "/var/run/netns/$name" 2>/dev/null) || return 1
    [[ "$observed" == "$expected" ]]
}
cleanup_namespace() {
    local name=$1 expected=$2 pid
    namespace_matches "$name" "$expected" || return 0
    # Only processes in this exact newly created namespace are ours to stop.
    while read -r pid; do
        [[ "$pid" =~ ^[0-9]+$ ]] && kill -TERM "$pid" 2>/dev/null || true
    done < <(ip netns pids "$name")
    sleep 0.3
    namespace_matches "$name" "$expected" || return 0
    while read -r pid; do
        [[ "$pid" =~ ^[0-9]+$ ]] && kill -KILL "$pid" 2>/dev/null || true
    done < <(ip netns pids "$name")
    namespace_matches "$name" "$expected" && ip netns delete "$name" || true
}
cleanup() {
    local result=$? child parent
    trap - EXIT INT TERM HUP
    set +e
    # A relay can still be in its bounded host-side TCP connect before setns.
    # Check parentage before touching tracked children; never kill a reused PID.
    for child in "$relay_a" "$relay_b" "$sshd_pid"; do
        [[ "$child" =~ ^[0-9]+$ ]] || continue
        parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
        [[ "$parent" == "$$" ]] && kill -TERM "$child" 2>/dev/null || true
    done
    ((created_a)) && cleanup_namespace "$ns_a" "$inode_a"
    ((created_b)) && cleanup_namespace "$ns_b" "$inode_b"
    for child in "$relay_a" "$relay_b" "$sshd_pid"; do
        [[ "$child" =~ ^[0-9]+$ ]] || continue
        parent=$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null)
        [[ "$parent" == "$$" ]] && kill -KILL "$child" 2>/dev/null || true
    done
    wait 2>/dev/null
    # Exact generated files only; evidence and public keys are retained.
    rm -f -- "$run_dir/client_key" "$run_dir/host_key"
    printf 'exit=%s\n' "$result" >> "$run_dir/result.txt"
    printf 'Bench artifacts: %s\n' "$run_dir"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

ssh-keygen -q -t ed25519 -N '' -f "$run_dir/host_key"
ssh-keygen -q -t ed25519 -N '' -f "$run_dir/client_key"
chmod 644 -- "$run_dir/client_key.pub"
runuser -u mikec -- test -r "$run_dir/client_key.pub" \
    || die 'mikec cannot traverse bench-dir and read its generated public key.'
awk '{print "[10.90.5.2]:2222 " $1 " " $2}' "$run_dir/host_key.pub" > "$run_dir/known_hosts"
dd if=/dev/urandom of="$run_dir/a-input.bin" bs=4096 count=1 status=none
dd if=/dev/urandom of="$run_dir/b-input.bin" bs=4096 count=1 status=none
chmod 644 -- "$run_dir/b-input.bin"
sha256sum "$run_dir/a-input.bin" "$run_dir/b-input.bin" > "$run_dir/source.sha256"
sshd_options=(-f /dev/null -h "$run_dir/host_key" -p 2222
    -o ListenAddress=10.90.5.2 -o "PidFile=$run_dir/sshd.pid"
    -o "AuthorizedKeysFile=$run_dir/client_key.pub" -o StrictModes=no
    -o AllowUsers=mikec -o PermitRootLogin=no -o UsePAM=no
    -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
    -o AuthenticationMethods=publickey -o AllowTcpForwarding=no
    -o PermitTunnel=no -o X11Forwarding=no -o PermitTTY=no)
/usr/sbin/sshd -t "${sshd_options[@]}"

ip netns add "$ns_a"; created_a=1
inode_a=$(stat -Lc '%d:%i' -- "/var/run/netns/$ns_a")
ip netns add "$ns_b"; created_b=1
inode_b=$(stat -Lc '%d:%i' -- "/var/run/netns/$ns_b")
for ns in "$ns_a" "$ns_b"; do
    ip -n "$ns" link set lo up
    ip netns exec "$ns" sysctl -qw net.ipv6.conf.all.disable_ipv6=1
    ip netns exec "$ns" sysctl -qw net.ipv6.conf.default.disable_ipv6=1
done
# The relay connects in the host namespace BEFORE entering the isolated one.
# No veth/default route exists, so inner IP traffic can only cross the radios.
python3 "$relay" --endpoint "$endpoint_a" --netns "/var/run/netns/$ns_a" \
    --interface lora0 --duration 180 --status-file "$run_dir/a-relay.json" \
    > "$run_dir/a-relay.log" 2>&1 &
relay_a=$!
python3 "$relay" --endpoint "$endpoint_b" --netns "/var/run/netns/$ns_b" \
    --interface lora0 --duration 180 --status-file "$run_dir/b-relay.json" \
    > "$run_dir/b-relay.log" 2>&1 &
relay_b=$!
for _ in {1..50}; do
    kill -0 "$relay_a" && kill -0 "$relay_b" || die 'A TAP relay exited; inspect its bench log.'
    if ip -n "$ns_a" link show dev lora0 >/dev/null 2>&1 \
            && ip -n "$ns_b" link show dev lora0 >/dev/null 2>&1; then break; fi
    sleep 0.2
done
ip -n "$ns_a" link set lora0 address 02:90:05:00:00:01 mtu 230 up
ip -n "$ns_b" link set lora0 address 02:90:05:00:00:02 mtu 230 up
ip -n "$ns_a" address add 10.90.5.1/24 dev lora0
ip -n "$ns_b" address add 10.90.5.2/24 dev lora0
ip -n "$ns_a" route show > "$run_dir/a-routes.txt"
ip -n "$ns_b" route show > "$run_dir/b-routes.txt"
ip netns exec "$ns_b" /usr/sbin/sshd -D -e "${sshd_options[@]}" > "$run_dir/sshd.log" 2>&1 &
sshd_pid=$!
for _ in {1..30}; do
    kill -0 "$sshd_pid" || die 'The isolated sshd exited; inspect sshd.log.'
    if ip netns exec "$ns_b" ss -H -ltn | grep -q '10\.90\.5\.2:2222'; then break; fi
    sleep 0.1
done
# With both -c and -w, Linux ping may keep sending until it receives count
# replies, building an unrealistic LoRa backlog. Send only ten paced requests.
# Loss is evidence, not a reason to skip the actual reliable-transport test.
if ! bounded ip netns exec "$ns_a" ping -n -c 10 -i 1 -W 3 -s 32 10.90.5.2 \
    > "$run_dir/ping.txt" 2>&1; then
    printf 'Diagnostic ping had loss; continuing to the SSH checksum test.\n' >> "$run_dir/result.txt"
fi
ssh_options=(-F /dev/null -T -p 2222 -i "$run_dir/client_key"
    -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes
    -o "UserKnownHostsFile=$run_dir/known_hosts" -o GlobalKnownHostsFile=/dev/null
    -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
    -o ConnectionAttempts=1 -o ConnectTimeout=60 -o ServerAliveInterval=0
    -o ControlMaster=no -o ClearAllForwardings=yes
    -o Compression=no -o KexAlgorithms=curve25519-sha256
    -o HostKeyAlgorithms=ssh-ed25519 -o Ciphers=chacha20-poly1305@openssh.com)
bounded ip netns exec "$ns_a" ssh "${ssh_options[@]}" mikec@10.90.5.2 sha256sum \
    < "$run_dir/a-input.bin" > "$run_dir/a-to-b.sha256" 2> "$run_dir/a-to-b-ssh.log"
expected_a=$(sha256sum "$run_dir/a-input.bin"); expected_a=${expected_a%% *}
read -r actual_a _ < "$run_dir/a-to-b.sha256"
[[ "$actual_a" == "$expected_a" ]] || die 'SSH A-to-B payload checksum mismatch.'
remote_path=$(python3 -c 'import shlex,sys;print(shlex.quote(sys.argv[1]))' "$run_dir/b-input.bin")
bounded ip netns exec "$ns_a" ssh "${ssh_options[@]}" mikec@10.90.5.2 "exec /bin/cat -- $remote_path" \
    > "$run_dir/b-received.bin" 2> "$run_dir/b-to-a-ssh.log"
[[ $(stat -c %s "$run_dir/b-received.bin") == 4096 ]] || die 'SSH B-to-A payload length mismatch.'
expected_b=$(sha256sum "$run_dir/b-input.bin"); expected_b=${expected_b%% *}
actual_b=$(sha256sum "$run_dir/b-received.bin"); actual_b=${actual_b%% *}
[[ "$actual_b" == "$expected_b" ]] || die 'SSH B-to-A payload checksum mismatch.'
printf 'PASS: real SSH carried 4096 bytes each way; both SHA-256 values match.\na_to_b=%s\nb_to_a=%s\n' \
    "$actual_a" "$actual_b" | tee "$run_dir/result.txt"
