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
cmd host reboot
cmd host run <alias> [arguments]
```

The first six actions after `help` are read-only. `reboot` is an opt-in action
example and is disabled unless the endpoint is started with `--allow-reboot`.
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

## Opt-in reboot example

To enable only the exact `host reboot` action, give the service account narrow
permission in `/etc/sudoers.d/meshcore-host-cli`:

```text
mctomqtt ALL=(root) NOPASSWD: /usr/bin/systemctl reboot
```

Set that file to mode `440`, test it locally, and add `--allow-reboot` to the
endpoint command. Its default response is `OK - host reboot scheduled in 5s`.
The delay can be set from 3 through 60 seconds with `--reboot-delay`.

The implementation always invokes the fixed argument vector
`/usr/bin/sudo -n /usr/bin/systemctl reboot`; no LoRa text is placed in a shell,
path, or process argument. Do not grant the service account a wildcard sudo
rule.

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
target, expiration, signature, and separate replay nonce. If the endpoint and
`meshcoretomqtt` are on different computers, those two host clocks must be
compatible for JWT validation. They naturally share a clock when both run on
the same Pi.

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
- The live claim must complete within 4 seconds; after it is accepted, the
  program and reply have 6 seconds.
- `host.reply` is accepted only through physical USB, not LoRa or Ethernet.

Use `cmd get host` to report the bridge state and limits.
