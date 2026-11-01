# W12 WiFi status and diagnostic validation — 2026-10-31

Baseline: `a85d282144b979ea97f97a1854c4049d5e710c16` on `keymindCascade`.
`git pull --ff-only` reported already up to date. The reported
`8a8c80ee6` build has the same first-join status defect.

## Reproduced defect and change

Starting WebConfig with saved credentials enters `MODE_CONNECTING` before an
HTTP server exists. Previously `get wifi.status` inspected only the pointer
for attached HTTP routes, so it could report `off` while the role correctly
reported WebConfig already running.

Repeater and Room now pass their actual session into the shared formatter.
First joins report `connecting`; request routing still uses the existing
attach/detach gate. Legacy formatter callers and Companion fallback behavior
are retained. Authentication, protocol masks, station ownership and the
15-second setup-AP fallback have not been changed.

The MQTT disconnect-reason wrapper now uses the existing shared label table
instead of a stale duplicate. Nineteen compile-time assertions check that
table against the installed SDK. In particular, SDK reason 201 is SSID not
found, not security mismatch; 202 is authentication failed and 203 is
association failed. Unknown reasons retain the raw-code fallback.

## Verification

Thirty-seven host tests passed:

- 17 status, station-ownership, outage-policy and Companion status tests,
  including a first-join regression that rejects the old implementation and
  a caller that omits its session.
- 18 setup-AP, recovery authentication, login, password-session and WiFi
  compatibility tests.
- Two production MQTT reason-label and diagnostic tests.

`git diff --check` passed.

The W12 MQTT observer Full image built successfully using:

```text
bash build.sh build-firmware meshnology_w12_repeater_observer_mqtt --full-exact --profile default --radio-preset target --firmware-version v1.18.0.2-alpha2-wifi-status
```

Artifacts and the complete build log are in
`/var/tmp/meshcore-w12-wifi-status-20261031/`.
The app uses 2,202,549 bytes of flash and 130,356 bytes of static RAM.
The firmware's runtime RAM qualification passed. Its EndF body fingerprint is
`26a4ca896519a2d1`, hardware identity `meshnology_w12`, target `0x8d88f33c`.
This is an expanded-partition build for verification, not confirmation that it
matches the reporter's currently installed partition layout.

## Limits and connection guidance

The reporter's old `get webui` prefix `off` described saved automatic startup,
while the rest of that response described its temporary connecting/setup session.
A setup AP after the join deadline does not identify the reason the station
failed to connect.

The board uses ESP32-S3 2.4 GHz b/g/n. Its bundled Arduino 2.0.17 ESP32-S3 SDK
enables WPA3 SAE and optional station PMF. WPA2-only is not enforced by this
firmware. A separate visible 2.4 GHz WPA2-Personal/AES, 20 MHz SSID is a useful
comparison when diagnosing an access point.

No device was flashed and no connection to the reporter's access point was
tested. The association failure remains unconfirmed pending access-point
settings or a WiFi disconnect log. See [WiFi troubleshooting](../WiFi.md#troubleshooting).

## Human-readable output follow-up

WiFi responses now name the live state and network in plain language, including
the recovery hotspot URL when a saved-network join fails. They distinguish an
idle saved configuration from a disabled radio. A recovery hotspot that remains
up briefly after the station connects reports the successful connection too.
Replies are bounded even when a caller supplies a one-byte buffer.

Repeater, Room and Companion share a Web UI formatter. It names the current
session before its separate `auto-start: on|off` setting. Stopped, starting,
stopping, connecting, waiting, hotspot and running states are distinct; a
disconnected or zero-IP station is not shown as a working web address.
Repeated temporary starts show current status instead of an already-running
error. WiFi enable acknowledgements explicitly say they apply to this boot
and direct the operator to `get wifi.status` to check the connection.

Twenty-nine targeted host tests passed for the follow-up: the actual WiFi and
Web UI formatters, all three Web UI role paths, runtime radio control,
station/portal ownership, Full power lifecycle and reviewed CLI text contracts.
These checks include failed-join/hotspot transitions, preserved HTTP route
admission, temporary/saved setting separation, and bounded replies.

Both updated W12 firmware builds passed:

- MQTT observer Full: version `v1.18.0.2-alpha2-wifi-readable`, flash use
  2,203,441 bytes, EndF body fingerprint `fc5bfd8fcf2f8b93`. Artifacts are in
  `/var/tmp/meshcore-w12-wifi-readable-20261031/`.
- Full Companion: the same version, app image 2,299,544 bytes, EndF body
  fingerprint `fc383f3ed031a6ef`. Artifacts are in
  `/var/tmp/meshcore-w12-wifi-readable-companion-20261031/`.

Both builds passed runtime RAM checks; the Companion build also verified all
26 declared capability markers. No image was installed during this follow-up.

## Shared connection diagnostics follow-up

`get wifi.diag` now reports the connection stage, application attempt count and
elapsed time, plus the most recent retained driver disconnect code, label and
age. The event listener starts before the first shared station attempt and
works without MQTT. NetworkLink's two direct station starts now use the same
station policy and diagnostic entry point. The common ESP32 source filter
includes the adapter for infrastructure profiles as well as Companions.

The listener copies fixed metadata under a critical section. It never logs,
allocates, accesses credentials, invokes a wireless driver or reconnects.
Formatting uses a copied snapshot outside the critical section. Association
and IP readiness are separate stages. The displayed connected state also
checks the current driver state to avoid reporting a stale connection.

WebConfig and setup-portal deadlines record timeouts before deliberate
disconnects. Explicit stops preserve the prior failure; reason 8 is excluded
from failure history because it also represents firmware-requested leaves.
Specific preflight results survive a later deadline, including radio blocking,
scan deferral, policy failure and an SSID absent on the fixed mesh channel.
The normal infrastructure status appends a complete failure hint when it fits,
or points to `get wifi.diag`, preserving the setup hotspot address.

History is scoped to the last attempted SSID, cleared on a different target,
and volatile across reboots. Driver-managed retries are not counted as new
application attempts. Historical errors remain explicitly labeled `Last drop`
after a retry or successful connection. Queued events from rapid stop/restart
operations cannot always be attributed to an individual same-SSID attempt;
the output does not claim a definitive root cause from such an event.

The final combined regression passed all 137 tests in 60.7 seconds. Coverage
includes the production event adapter and station policy without MQTT, bounded
formatting under ASan/UBSan, stale-network and local-disconnect events, timeout
history through the real WebConfig fallback, both CLI routes, radio ownership,
power suspension, setup authentication, OTA handoffs and reviewed reply/help
contracts. Test log: `/var/tmp/meshcore-wifi-diagnostics-final-tests-20261031.log`.
`git diff --check` passed.

Both diagnostic firmware builds passed, built serially:

- W12 MQTT observer Full, `v1.18.0.2-alpha2-wifi-diag`: app image 2,206,632
  bytes; static RAM 130,420 bytes; EndF body fingerprint `2ed7e4ffa0e8b28c`.
  Runtime RAM qualification: 235,624 available / 234,636 required; startup
  admission only, not qualification of maximum TLS/WSS/outbox load. Artifacts
  and build log: `/var/tmp/meshcore-w12-wifi-diagnostics-r2-20261031/`.
- W12 Full Companion, same version: app image 2,301,912 bytes; static RAM
  126,808 bytes; EndF body fingerprint `d2e95609b0898464`. Runtime RAM
  qualification passed; all 26 capability markers verified. Artifacts:
  `/var/tmp/meshcore-w12-wifi-diagnostics-companion-20261031/`.

These remain expanded-partition verification builds. No device was flashed,
and the reporter's actual network failure has not been reproduced on hardware.
