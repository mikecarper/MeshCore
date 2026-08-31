# LoRa CLI host service example

This example lets an authenticated remote administrator ask a computer attached
to a repeater to perform a small, explicitly allowed operation. The included
service implements this fixed allowlist:

```text
host help
host cpu-temp
host hostname
host uptime
host load
host memory
host disk-free
host clock status
host clock sync
host clock set <unix_epoch>
host network restart
host reboot
host action status <operation_id>
host run <alias> [arguments]
```

The status commands read bounded operating-system APIs or virtual files. The
service never invokes a shell and does not accept arbitrary commands,
file paths, or executable names. Locally configured program aliases can accept
strictly validated arguments. Network restart, host reboot, and clock changes
are disabled by default; clock status remains read-only.

Normal repeater firmware includes the host bridge. The specialized Wio-E5
RS232 bridge image omits it to stay within that board's fixed 240 KiB
application partition; use the normal Wio-E5 repeater image for this service.

## Using it with meshcoretomqtt

`meshcoretomqtt` remains the only process that opens the repeater's USB serial
port. The data path is:

```text
LoRa admin -> repeater -> USB -> meshcoretomqtt -> MQTT broker
MQTT broker -> host endpoint -> signed remote-serial MQTT -> meshcoretomqtt
meshcoretomqtt -> USB -> repeater -> LoRa reply
```

Two processes must not read the same serial TTY. The MQTT endpoint avoids that
race and can run on the same Pi as the broker and `meshcoretomqtt`, or on a
different host.

For **clock recovery**, keep the broker, `meshcoretomqtt`, and this endpoint on
the same Pi and use the loopback broker address. A wrong Pi clock can break TLS
to a remote broker, and split hosts can reject the signed claim before the
clock is repaired. Other read-only host commands may use a remote broker when
the clocks and TLS connection are already healthy.

The current `meshcoretomqtt` release publishes repeater `DEBUG` records only
when it is launched with `--debug`. Add `--debug` to its existing systemd
`ExecStart` command and restart it. Keep any existing `--config` arguments.
This publishes other firmware debug records too, so use broker ACLs and do not
expose the debug topic publicly.

The host-command authorization does not compare the repeater clock with the Pi
clock. Several minutes of drift, an unset repeater wall clock, or a later clock
correction cannot make an old request executable. Freshness comes from the
live one-time challenge described below. The normal `meshcoretomqtt`
`sync_time` setting can remain enabled for its other uses, but this feature does
not depend on it.

Generate a dedicated service signing key with the Python environment already
installed by `meshcoretomqtt`:

```sh
sudo /opt/mctomqtt/venv/bin/python3 host_cli_service.py \
  --generate-key /etc/mctomqtt/host-cli-key.json
sudo chown mctomqtt:mctomqtt /etc/mctomqtt/host-cli-key.json
sudo chmod 600 /etc/mctomqtt/host-cli-key.json
```

The command prints the public key. Add only that public key to the existing
`meshcoretomqtt` configuration:

```toml
[remote_serial]
enabled = true
allowed_companions = [
  "SERVICE_PUBLIC_KEY_PRINTED_ABOVE"
]
nonce_ttl = 120
command_timeout = 10
```

The name `allowed_companions` comes from `meshcoretomqtt`; a dedicated service
identity works because it uses the same Ed25519 token format. Restart
`mctomqtt` after changing the file.

Run the endpoint against the same broker and topic namespace. Replace `USA`
with the exact three-character IATA value used by that broker and replace the
repeater key with the repeater's complete 64-hex-character public key:

```sh
sudo -u mctomqtt /opt/mctomqtt/venv/bin/python3 host_cli_service.py \
  --broker 127.0.0.1 \
  --iata USA \
  --repeater-key REPEATER_PUBLIC_KEY \
  --service-key /etc/mctomqtt/host-cli-key.json
```

For a password-protected broker, add `--username NAME --password-file FILE`.
For TLS, add `--tls`, and optionally `--ca-cert FILE`. If the debug or serial
command topic is customized in `meshcoretomqtt`, pass the matching
`--request-topic` or `--command-topic` value.

## Opt-in host recovery actions

Network recovery and reboot use a separate socket-activated root broker. They
do not use `sudo`, and installing the clock-control broker does not grant these
actions. Install the broker and its fixed, static action units as root-owned
files:

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

The broker's root-owned policy is empty by default. Opt in to either action or
both with a systemd drop-in; only these two exact values are valid:

```sh
sudo systemctl edit meshcore-host-actions.service
```

```ini
[Service]
Environment=MESHCORE_HOST_ACTIONS=network-restart,reboot
```

Then activate the socket:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-host-actions.socket
```

After changing the policy on an already active installation, restart
`meshcore-host-actions.service` so the root broker reads the new value.

Add `--allow-network-restart`, `--allow-reboot`, or both to the unprivileged
endpoint. Both the endpoint flag and the root-owned broker policy must allow an
action. The socket is fixed at `/run/meshcore-host-actions.sock`, owned by
`root:mctomqtt`, and mode `0660`. The broker requires the endpoint's exact UID
and primary GID through `SO_PEERCRED`; the endpoint requires a root-created
listener. It accepts one bounded ASCII line and can start only the fixed
`meshcore-networkmanager-restart.service` or
`meshcore-host-reboot.timer`. All action units have empty capability sets and
systemd sandboxing. No request text becomes an executable, unit, path, or
argument.

The configured `mctomqtt` UID with `mctomqtt` as its effective primary group
is the delegated local trust boundary. Merely having `mctomqtt` as a
supplementary group is rejected.

For a new operation the endpoint sends `PREPARE` first, publishes the LoRa
reply through MQTT QoS 1, waits for local MQTT publish confirmation, and only
then sends one `COMMIT`. The reboot commit starts a fixed approximately
10-second timer. If publish confirmation is absent, the action is not
committed. The MQTT acknowledgement confirms acceptance by the local broker;
there is no correlated serial/LoRa delivery acknowledgement, so physical reply
delivery remains best effort.

`network restart` intentionally interrupts Wi-Fi and Tailscale management while
NetworkManager restarts. Keep the MQTT broker on loopback, and ensure the
broker, `meshcoretomqtt`, host endpoint, and USB device services do not use
`Requires=`, `BindsTo=`, or ordering dependencies on NetworkManager. That local
USB/MQTT path must remain up long enough to accept the reply and commit, even
though remote management temporarily disappears.

Each reply names a canonical 32-hex operation ID derived from the authenticated
repeater identity plus request ID and nonce. Check a result with:

```text
cmd host action status 0123456789ABCDEF0123456789ABCDEF
```

The root broker keeps at most 64 per-boot operation records. A committed action
is scheduled at most once. `scheduled` means PID 1 accepted the fixed unit job,
not that the later network restart or reboot completed. A service restart
converts a recovered
`in-progress` record to `ambiguous`; an ambiguous or timed-out operation is
never automatically retried. While a reboot is committed or ambiguous, a
different reboot is rejected instead of starting or promising a fresh timer.
A new reboot reservation may atomically supersede an older, uncommitted reboot
reservation; the older operation can no longer be committed. Only old,
uncommitted `prepared` records may be evicted. If no safe record can be
evicted, new actions fail closed as `state full`.

Remove the legacy `wifi-restart` program alias and every legacy host-action
entry from `sudoers`. Use the exact `host network restart` action instead. This
endpoint never invokes `sudo`, and sudo command aliases do not provide a
fallback under its hardened service configuration.

## Clock status and recovery

`host clock status` reports the Pi epoch and NTP synchronization state without
requiring root. To opt in to clock changes, install the independently
validating socket service and its systemd units. The executable must remain
root-owned, and the endpoint must run with `mctomqtt` as both its user and
primary group:

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

The socket unit creates exactly
`/run/meshcore-clock-control.sock` as `root:mctomqtt` with mode `0660`. The
root service starts on the first request and accepts only a process whose
Linux `SO_PEERCRED` UID and primary GID both resolve to the configured
`mctomqtt:mctomqtt` identity. The endpoint independently checks the socket's
type, owner, group, and mode, then requires the connected server's
`SO_PEERCRED` UID to be root. It does not use `sudo` for clock control. Remove
any older clock-helper entries from `/etc/sudoers.d/meshcore-host-cli`.

Add `--allow-clock-control` to the endpoint. The available requests are:

```text
cmd host clock status
cmd host clock sync
cmd host clock set 1788147000
```

The endpoint and root service both require a canonical unsigned decimal epoch,
with no sign, leading zero, whitespace, or trailing argument. Values are
limited to 2020 through 2099. The private protocol permits only one bounded
ASCII line: `sync` or `set <epoch>`. Extra lines, control characters, partial
lines, oversized messages, unrecognized replies, and a reply for the wrong
epoch all fail closed. Every child operation uses a fixed absolute argv,
`shell=False`, and a timeout of at most 1.5 seconds.

`clock set` never disables NTP: it sets the epoch, then requests an NTP step.
If setting the clock succeeds but NTP recovery fails, the service reports a
partial result rather than incorrectly reporting total failure. Likewise, if
`clock sync` enables NTP but its explicit step/restart fails, the reply says
that NTP was enabled and names the remaining failure. When chrony is installed,
the root broker starts only the fixed, static
`meshcore-chrony-step.service`. That hardened one-shot runs only
`/usr/bin/chronyc -a makestep` as `_chrony:_chrony`, has no capabilities,
permits only `AF_UNIX`, and has its own one-second start timeout. The `_chrony`
account is supplied by the Debian/Raspberry Pi chrony package. Without chrony,
the broker restarts systemd-timesyncd.

Clock commands still require the signed LoRa request and live one-time claim.
Their replay protection uses random nonces and monotonic claim deadlines, not a
tight comparison between the repeater clock and Pi clock. The final JWT is
created and checked by processes on the same Pi.

## Allowlisted programs with arguments

Pass `--programs-file FILE` to expose selected local programs as
`host run <alias> [arguments]`. Start with `programs.example.json`, replace its
example executable paths with installed programs, and keep the file owned by a
trusted local account. The service rejects a group- or world-writable allowlist
or executable.

Each entry fixes the executable and any leading arguments in `argv`. Its remote
arguments have an exact count and one of three validators:

- `choice` accepts only one explicitly listed value;
- `integer` accepts a canonical nonnegative decimal value inside `min` and
  `max`;
- `token` accepts a bounded ASCII token that starts with a letter or digit and
  contains only letters, digits, `_`, `.`, `:`, `@`, `+`, `,`, or `-`.

For the example `fan` entry, the remote command is:

```text
cmd host run fan on 15
```

It always executes this argument vector, without a shell:

```text
/usr/local/bin/mesh-fan-control --source lora on 15
```

An alias cannot select another executable. Extra arguments, option-shaped
tokens such as `--help`, invalid choices, out-of-range integers, shell syntax,
control characters, and malformed quoting are rejected before a process is
started. Program timeouts are limited to 1-5 seconds so the reply fits inside
the repeater's 10-second host-service deadline. The selected local program is
still trusted code; give its service account only the operating-system
permissions that program needs.

From a companion's authenticated remote CLI, select and log into the repeater,
then run:

```text
cmd host help
cmd host cpu-temp
```

Use `cmd get host` to view the bridge limits and whether a request is waiting.

## Injection and replay protection

The broker is transport, not the trust boundary. The endpoint accepts a request
only when all of these checks pass:

- the LoRa caller was authenticated by the repeater and has administrator
  permission;
- the MQTT `origin_id` is the configured repeater identity;
- the complete protocol record has a valid Ed25519 signature from that
  repeater;
- the request contains the pending 32-bit ID and a fresh random 64-bit nonce;
- Base64URL framing, UTF-8, and byte limits are valid;
- a new random 64-bit service challenge is returned through the signed serial
  channel and the repeater signs proof that the exact request is still pending;
- only after that live proof does the decoded request reach the fixed built-in
  or locally configured program allowlist.

For `run`, the request must additionally match a locally configured alias and
every argument validator. Execution uses a fixed absolute executable,
`shell=False`, a fixed minimal environment, no stdin, and `/` as its working
directory.

The initial signed request never executes an action. The endpoint first stores
it in memory and sends `@claim=<random>` with the same ID and nonce. The
firmware accepts that claim only through physical USB while that exact LoRa
request is pending, then emits a signed `CLAIMED` proof containing the random
value. The endpoint removes the proof before running the action, making action
execution at most once even if MQTT redelivers a record.

A captured request can at most make the endpoint issue a new challenge; a
repeater with no matching live request will not sign it. A captured `CLAIMED`
record cannot match the endpoint's new random challenge, and a service restart
forgets all outstanding challenges. These replay properties do not use wall
clock time. The final response still travels in a short-lived JWT whose
signature, signer allowlist, target, expiration, and independent nonce are
checked by `meshcoretomqtt`. Its wall-clock expiry and in-memory nonce cache are
not by themselves safe across a backward clock jump plus restart. The
firmware's live one-time claim protects these host actions from that rollback.
For recovery deployments, keep `allowed_companions` limited to this dedicated
service key, and run the endpoint and `meshcoretomqtt` on the same Pi so the
claim JWT uses one clock. Repeater-to-Pi clock drift remains irrelevant.

Request text is Base64URL encoded before it enters the line-oriented USB stream.
Reply controls such as carriage return and newline are converted to spaces, and
the firmware independently rejects control characters and malformed UTF-8.
This prevents text from becoming a second serial command.

Protect `host-cli-key.json`: any private key placed in
`allowed_companions` is trusted by `meshcoretomqtt` to sign serial requests.
Use a dedicated broker account whose ACL can only subscribe to this repeater's
debug topic and publish to this repeater's `serial/commands` topic.

## Limits

- LoRa `host` command: 155 UTF-8 bytes after `host `, or 152 bytes when the
  companion uses its legacy three-byte correlation prefix.
- LoRa reply: 162 UTF-8 bytes total, including that correlation prefix.
- One request can be pending per repeater.
- Only one privileged action or action-status query can be active; overlapping
  work is rejected before it is reserved.
- Live-claim deadline: 4 seconds. Allowlisted programs have a configured 1-5
  second child-process timeout. Privileged actions and status queries share
  one 5-second broker/reply-confirmation deadline.
- `host.reply` is accepted only from the physical USB serial command path, not
  from LoRa or the Ethernet CLI.

Run the dependency-free logic tests with:

```sh
cd examples/host_cli_service
python3 -m unittest -v
```

The live service needs Python 3.11 or later, `paho-mqtt`, and
`ed25519-orlp`; the standard `/opt/mctomqtt/venv` already contains them.
