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
host reboot
host run <alias> [arguments]
```

The status commands read bounded operating-system APIs or virtual files. The
service never invokes a shell and does not accept arbitrary commands,
file paths, or executable names. Locally configured program aliases can accept
strictly validated arguments. Host reboot is disabled by default.

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

To enable the exact `host reboot` request, give only `systemctl reboot` to the
service account in `/etc/sudoers.d/meshcore-host-cli`:

```text
mctomqtt ALL=(root) NOPASSWD: /usr/bin/systemctl reboot
```

Set that file to mode `440`, then add `--allow-reboot` to the endpoint command.
The endpoint publishes `OK - host reboot scheduled in 5s`, waits for the MQTT
publish, and then runs the fixed argv
`/usr/bin/sudo -n /usr/bin/systemctl reboot` after five seconds. Change the
delay from 3 through 60 seconds with `--reboot-delay`; no LoRa text is ever
inserted into the process invocation. Do not add a broad sudo wildcard.

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
checked by `meshcoretomqtt`. When both processes run on the same Pi, that JWT
check naturally uses the same host clock; if they run on different computers,
those two computers need compatible clocks. Repeater-to-Pi clock drift remains
irrelevant.

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
- Live-claim deadline: 4 seconds, followed by up to 6 seconds for execution and
  the reply.
- `host.reply` is accepted only from the physical USB serial command path, not
  from LoRa or the Ethernet CLI.

Run the dependency-free logic tests with:

```sh
cd examples/host_cli_service
python3 -m unittest -v
```

The live service needs Python 3.11 or later, `paho-mqtt`, and
`ed25519-orlp`; the standard `/opt/mctomqtt/venv` already contains them.
