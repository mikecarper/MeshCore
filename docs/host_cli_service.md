# LoRa CLI Host Service

The repeater `host <text>` command lets an authenticated LoRa administrator
request a small, explicitly allowed operation from a USB-connected computer.
The included Raspberry Pi endpoint supports these exact requests:

```text
cmd host help
cmd host cpu-temp
cmd host hostname
cmd host uptime
cmd host load
cmd host memory
cmd host disk-free
cmd host clock status
cmd host clock sync
cmd host clock set <unix_epoch>
cmd host network restart
cmd host reboot
cmd host action status <operation_id>
cmd host run <alias> [arguments]
```

The first seven actions after `help` are read-only. Clock changes, network
restart, and reboot are opt-in actions and are disabled unless explicitly
enabled.
Text such as `reboot now`, `cpu-temp; reboot`, and embedded newlines is not a
command: the endpoint accepts only an exact allowlist match.

The bridge is included in normal repeater firmware except on Wio-E5. Its
specialized RS232 bridge image omits the host service because the combined
application exceeds the fixed 240 KiB partition; use the normal Wio-E5
repeater image when the USB/MQTT host service is needed.

## Run alongside meshcoretomqtt

`meshcoretomqtt` must remain the only process that opens the repeater USB serial
port. The endpoint communicates with it through the same MQTT broker, which can
be local to the Raspberry Pi:

```text
LoRa -> repeater -> USB -> meshcoretomqtt -> MQTT -> host endpoint
LoRa <- repeater <- USB <- meshcoretomqtt <- MQTT <- host endpoint
```

Clock-recovery deployments must run the loopback broker, `meshcoretomqtt`, and
endpoint on the same Pi. A wrong Pi clock can prevent TLS validation against a
remote broker, while split host clocks can reject the signed live claim before
the correction arrives. Remote brokers remain suitable for ordinary commands
when the connection and both clocks are already healthy.

Launch `meshcoretomqtt` with its existing arguments plus `--debug`; its current
debug-topic parser needs that flag to publish the repeater request record. Do
not open the serial TTY from the host endpoint too.

This feature does not compare the repeater wall clock with the Pi clock.
Minutes of drift, an unset repeater clock, and later clock corrections are safe:
request freshness comes from a live one-time challenge. The normal
`meshcoretomqtt` `sync_time` setting may remain enabled for its other uses, but
host-command authorization does not depend on it.

Generate a dedicated service key using the Python environment installed by
`meshcoretomqtt`:

```sh
sudo /opt/mctomqtt/venv/bin/python3 host_cli_service.py \
  --generate-key /etc/mctomqtt/host-cli-key.json
sudo chown mctomqtt:mctomqtt /etc/mctomqtt/host-cli-key.json
sudo chmod 600 /etc/mctomqtt/host-cli-key.json
```

Add the printed public key to the existing `meshcoretomqtt` configuration:

```toml
[remote_serial]
enabled = true
allowed_companions = [
  "SERVICE_PUBLIC_KEY_PRINTED_ABOVE"
]
nonce_ttl = 120
command_timeout = 10
```

Restart `meshcoretomqtt`, then run the endpoint. Replace `USA` with the exact
three-character IATA namespace configured in the broker and supply the
repeater's complete 64-character public key:

```sh
sudo -u mctomqtt /opt/mctomqtt/venv/bin/python3 host_cli_service.py \
  --broker 127.0.0.1 \
  --iata USA \
  --repeater-key REPEATER_PUBLIC_KEY \
  --service-key /etc/mctomqtt/host-cli-key.json
```

Add `--username NAME --password-file FILE` for broker authentication. Add
`--tls`, and optionally `--ca-cert FILE`, for TLS. Custom installations can use
`--request-topic` and `--command-topic` to match their broker namespace. The
endpoint source and its systemd/configuration details are in
[`examples/host_cli_service`](https://github.com/mikecarper/MeshCore/tree/keymindCascade/examples/host_cli_service).

## Allowlisted programs and arguments

Add `--programs-file FILE` to expose locally selected programs as
`host run <alias> [arguments]`. The example JSON file fixes each executable,
fixed leading arguments, maximum 1-5 second runtime, and the exact remote
argument schema. For example:

```text
cmd host run fan on 15
```

The example maps that request to the fixed process argument vector:

```text
/usr/local/bin/mesh-fan-control --source lora on 15
```

The supported argument rules are an explicit non-option `choice`, a bounded
nonnegative `integer`, or a short `token` using a restricted ASCII character
set. Alias lookup and every argument validation happen before process creation.
Execution uses an absolute configured path, no stdin, `shell=False`, a minimal
environment, and `/` as its working directory. Extra arguments, leading-option
injection, shell syntax, control characters, and invalid quoting are rejected.
The allowlist file and executable must not be group- or world-writable.

Copy and edit
[`programs.example.json`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/examples/host_cli_service/programs.example.json),
then give the service account only the operating-system permissions needed by
those trusted programs.

## Opt-in network and reboot recovery

These actions use their own socket-activated root broker, independently of the
clock-control broker. Installing or enabling clock control does not grant host
recovery actions. Install the following as root-owned files:

```sh
sudo install -o root -g root -m 0755 meshcore_host_actions.py \
  /usr/local/sbin/meshcore-host-actions
sudo install -o root -g root -m 0644 meshcore-host-actions.socket \
  /etc/systemd/system/meshcore-host-actions.socket
sudo install -o root -g root -m 0644 meshcore-host-actions.service \
  /etc/systemd/system/meshcore-host-actions.service
sudo install -o root -g root -m 0644 meshcore-networkmanager-restart.service \
  /etc/systemd/system/meshcore-networkmanager-restart.service
sudo install -o root -g root -m 0644 meshcore-host-reboot.timer \
  /etc/systemd/system/meshcore-host-reboot.timer
sudo install -o root -g root -m 0644 meshcore-host-reboot.service \
  /etc/systemd/system/meshcore-host-reboot.service
```

The root-owned broker policy enables nothing by default. Create a systemd
drop-in and select `network-restart`, `reboot`, or both as an exact
comma-separated list:

```sh
sudo systemctl edit meshcore-host-actions.service
```

```ini
[Service]
Environment=MESHCORE_HOST_ACTIONS=network-restart,reboot
```

Reload systemd and enable only the broker socket:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-host-actions.socket
```

After changing the policy on an already active installation, restart
`meshcore-host-actions.service` so the broker reads the new value.

Finally add `--allow-network-restart`, `--allow-reboot`, or both to the
unprivileged endpoint. An action must pass both gates: the endpoint flag and
the root-owned broker policy. The endpoint never invokes `sudo`; remove legacy
`wifi-restart` program aliases and host-action sudoers entries because sudo
aliases are not a fallback for this hardened service. Use the exact
`host network restart` action instead.

Systemd creates `/run/meshcore-host-actions.sock` as `root:mctomqtt` mode
`0660`. The root broker authenticates both the endpoint UID and primary GID
with `SO_PEERCRED`, while the endpoint authenticates the root-created listener.
The protocol is one bounded ASCII line. Neither the caller nor request text can
choose an executable, unit, path, or argument. The broker can start only the
fixed static NetworkManager restart service or the fixed reboot timer; the
broker and action units have empty capability sets and systemd sandboxing.
The configured `mctomqtt` UID and its effective primary GID are the delegated
local trust boundary; supplementary group membership alone is rejected.

Side effects follow a fail-closed two-phase sequence. The endpoint reserves a
canonical 128-bit operation ID with `PREPARE`, publishes its reply with MQTT
QoS 1, and requires `wait_for_publish` plus `is_published` confirmation before
sending one `COMMIT`. It does not block the Paho network callback while waiting.
No confirmation means no commit. Reboot uses a fixed approximately 10-second
timer that starts only after commit. The MQTT confirmation proves acceptance
by the local broker, not delivery over USB and LoRa; there is no correlated
serial delivery acknowledgement, so the physical reply is best effort.

`network restart` deliberately drops Wi-Fi and Tailscale management while
NetworkManager restarts. Use a loopback MQTT broker and keep the broker,
`meshcoretomqtt`, host endpoint, and USB device services independent of
NetworkManager: they must not have `Requires=`, `BindsTo=`, or ordering
dependencies on it. This preserves the local reply/commit path while remote
management temporarily disappears.

The operation ID is derived from the authenticated repeater key, request ID,
and nonce. The reply displays it, and its state can be queried later:

```text
cmd host action status 0123456789ABCDEF0123456789ABCDEF
```

The root broker retains at most 64 records for the current boot. It persists
`prepared` before replying and `in-progress` before scheduling, schedules a
committed operation at most once, and never automatically retries an ambiguous
outcome. A broker restart converts recovered `in-progress` to `ambiguous`.
Only an old uncommitted `prepared` record may be evicted; otherwise a full
store rejects new work. Reusing an operation ID for a different action is
rejected. `scheduled` means PID 1 accepted the fixed unit job; it does not claim
that the subsequent network restart or reboot completed successfully.

A distinct reboot is rejected while another reboot is committed or ambiguous,
so a second request cannot falsely promise a fresh 10-second timer while the
first timer is already running. A new reboot reservation may supersede only an
older, uncommitted reboot reservation; the superseded operation can no longer
be committed.

## Opt-in clock recovery

`clock status` reports the Pi epoch and NTP synchronization state without root
access. To enable the exact `clock sync` and `clock set <unix_epoch>` actions,
install
[`meshcore_clock_control.py`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/examples/host_cli_service/meshcore_clock_control.py)
as `/usr/local/sbin/meshcore-clock-control`, owned by root and mode `0755`,
then install the accompanying
[`meshcore-clock-control.socket`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/examples/host_cli_service/meshcore-clock-control.socket)
and
[`meshcore-clock-control.service`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/examples/host_cli_service/meshcore-clock-control.service)
and static
[`meshcore-chrony-step.service`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/examples/host_cli_service/meshcore-chrony-step.service)
files in `/etc/systemd/system` as root-owned mode-`0644` files:

```sh
sudo install -o root -g root -m 0755 meshcore_clock_control.py \
  /usr/local/sbin/meshcore-clock-control
sudo install -o root -g root -m 0644 meshcore-clock-control.socket \
  /etc/systemd/system/meshcore-clock-control.socket
sudo install -o root -g root -m 0644 meshcore-clock-control.service \
  /etc/systemd/system/meshcore-clock-control.service
sudo install -o root -g root -m 0644 meshcore-chrony-step.service \
  /etc/systemd/system/meshcore-chrony-step.service
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-clock-control.socket
```

The socket is fixed at `/run/meshcore-clock-control.sock`; systemd creates it
as `root:mctomqtt` with mode `0660`. Run the endpoint as user and primary group
`mctomqtt`, then add `--allow-clock-control`. The endpoint validates the socket
metadata and authenticates the connected server as root with Linux
`SO_PEERCRED`. The root service independently requires the peer's UID and
primary GID to match `mctomqtt:mctomqtt` before it reads or executes a request.
No clock-control sudo rule is used; remove old clock-helper sudoers lines when
upgrading.

Both layers require one canonical unsigned decimal argument with no sign,
leading zero, whitespace, or trailing text. The accepted epoch range is 2020
through 2099. The private protocol accepts only the complete bounded ASCII
lines `sync` and `set <epoch>`. Every child process uses a fixed absolute argv,
`shell=False`, and a timeout no longer than 1.5 seconds.

`clock set` changes `CLOCK_REALTIME` without disabling NTP, then requests an NTP
step. If the NTP request fails, the response honestly reports that the clock
changed and which synchronization step remains incomplete. If `clock sync`
enables NTP but the explicit step/restart fails, that partial outcome is also
reported. When chrony is available, the root broker starts only the fixed
static `meshcore-chrony-step.service`. That hardened one-shot runs
`/usr/bin/chronyc -a makestep` as `_chrony:_chrony`, with no capabilities,
`AF_UNIX` only, and a one-second start timeout; it can therefore reach chrony's
private runtime socket without broadening the root broker. The `_chrony`
account comes from the Debian/Raspberry Pi chrony package. Without chrony, the
broker restarts systemd-timesyncd. Clock drift cannot authorize a captured command:
the signed request still needs the repeater's live one-time claim, whose
deadline is monotonic rather than wall-clock based.

## Trust and injection controls

The MQTT broker transports records but does not establish their authenticity.
The endpoint checks the configured repeater identity and verifies its Ed25519
signature over the complete request ID, random nonce, and Base64URL request. It
also validates framing, UTF-8, and byte limits. It does not execute that first
record.

Instead, the endpoint stores the request in memory, creates a random 64-bit
challenge, and sends `@claim=<random>` through `meshcoretomqtt`'s signed serial
channel. The repeater accepts it only from physical USB while the exact ID and
nonce are pending, then signs a `CLAIMED` proof containing the challenge. Only
a matching live proof lets the endpoint perform allowlist matching and execute
the action. It consumes the proof before execution, so MQTT redelivery cannot
execute an action twice.

A captured request only causes a new challenge that an idle repeater refuses.
A captured proof does not match a new challenge, and restarting the endpoint
forgets pending challenges. Repeater/Pi clock drift therefore cannot turn an
old `reboot` or `run` record into a valid action. The reply and claim commands
still use short-lived JWTs; `meshcoretomqtt` checks their signer allowlist,
target, expiration, signature, and separate replay nonce. Wall-clock expiry and
its in-memory nonce cache are not sufficient across a backward clock jump plus
restart; the firmware's live one-time claim is what protects these host
actions. Keep recovery-mode `allowed_companions` limited to the dedicated
service key, with the endpoint and `meshcoretomqtt` on the same Pi.

Newlines and other reply control characters are converted to spaces and are
also independently rejected by the firmware parser.

Protect the service private key and use broker ACLs that allow it to subscribe
only to the selected repeater debug topic and publish only to that repeater
serial-command topic. A private key listed in `allowed_companions` is trusted to
sign serial requests.

## Limits

- The request is at most 155 UTF-8 bytes after `host `, or 152 bytes with the
  legacy three-byte companion correlation prefix.
- The complete LoRa reply is at most 162 UTF-8 bytes.
- Only one host request can be pending per repeater.
- Only one privileged action or action-status query can be active; overlapping
  work is rejected before reservation.
- The live claim must complete within 4 seconds. Allowlisted programs have a
  configured 1-5 second child-process timeout. Privileged actions and status
  queries share one 5-second broker/reply-confirmation deadline.
- `host.reply` is accepted only through physical USB, not LoRa or Ethernet.

Use `cmd get host` to report the bridge state and limits.
