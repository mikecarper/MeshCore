# Management reports (MGR1)

Available on repeater (including observer), room-server and sensor firmware.
Companions/terminal-chat nodes and KISS modems do not originate these reports.
Off by default. A management password and explicit enable are both required.
No radio settings, existing preferences layout, or OTA authorization policy is
changed by enabling reporting. This protocol does **not** authorize updates.

## CLI

Run through the existing local CLI or an authenticated administrator session:

```
get data.tx
set data.tx path 1:12ab77
set data.tx region auto
set mgmt.password <12-to-96-byte password>
set mgmt.direct 5
set mgmt.flood 21
set mgmt.enabled on
get mgmt
set mgmt.enabled off
```

Use a long randomly generated password. There is no password getter. Firmware
stores the derived 32-byte key, not the plaintext password. That stored key is
equivalent authority to decrypt reports and must also be protected. Passwords
entered into terminal programs may still be recorded by those programs.

`data.tx` is the single shared route for management reports, telemetry history,
and future scheduled data producers. Its fresh-install defaults are `path=direct`
(zero hops) and `region=auto`; configuring it never enables a producer. Paths
use `1:`, `2:` or `3:` followed by complete hop hashes without separators, or
the comma-separated form accepted by `set outpath`. `none` removes the path.
`get/set mgmt.path` remain compatibility aliases for `get/set data.tx path`.

`region=auto` uses the radio's configured default region when it is usable;
otherwise it resolves the unique deepest flood-enabled entry in the region
hierarchy. If equally deep candidates make that choice ambiguous, it resolves
to nothing and no fallback is transmitted. `default` requires and follows
`region default`; a region name pins that named scope. `none` disables scoped fallback. The
resolved transport key is looked up when a report starts, so edits to the region
definition take effect without rewriting `/data_tx`.

The direct path leads to the receiver/uplink's vicinity; this broadcast-radio
datagram has no private destination identity. The radio ID in the payload
identifies the reporter.

Direct and flood schedules are independently configurable and may each be
turned off. Fresh settings are `direct=5d` and `flood=21d`; global reporting is
still off until `mgmt.enabled on`. Direct accepts 5–90 days and requires the
shared path. Flood accepts 21–90 days and requires a resolvable shared region.
At least one route must remain active while reporting is enabled. The legacy
`set mgmt.interval N` shorthand enables both, setting direct to `N` and flood to
`max(21,N)`. No transmission is sent merely by configuring a password. Initial
reporting waits for the configured schedules. Reports have deterministic
per-radio/per-sequence jitter of up to an hour; pages are spaced at least a
minute apart.

The radio cannot know that an observer uploaded a packet to MQTT, so the flood
schedule is deliberately independent of direct transmission. A region-scoped
`TRANSPORT_FLOOD` is sent at its independently configured interval. An
unresolved or ambiguous data region blocks that transmission
rather than sending an unscoped flood. If direct and flood become due together,
the flood is sent and replaces the redundant direct copy.
Ordinary reports are never retried in a tight loop; a partial report gives up
after an hour. Existing relay filters, hop limits and duty constraints still apply.

Schedule state is atomically reserved **before** transmission, and checkpointed
hourly. Timers use elapsed powered-on time rather than the RTC: clock corrections
cannot create floods, and reboot does not clear the budget. Downtime is not
credited; each reboot can delay a report by up to an additional hour. This is a
deliberately conservative tradeoff for nodes with unreliable clocks. Off/on and
password changes do not reset the flood limit. Corrupt/unreadable state or failed
writes stop reporting; `get mgmt` shows `FAULT(no TX)` until storage is repaired
and the radio restarted. `/management` and the shared `/data_tx` are versioned,
CRC-protected, and replaced transactionally.

Weekly history is collected once a minute while enabled. Hour-bucket extrema
cover 7 days to 7 days + 1 hour (conservative boundary bucket). The first report
after enable/reboot is marked partial where appropriate. Since-report extrema
reset after all pages have been queued, retaining readings taken since the
snapshot for the next report. These statistics are not durable;
reboot loses the history, not the flood countdown. Temperature is MCU temperature,
not ambient. Reporting does not wake GPS, start Wi-Fi, or initialize external OTA
media. Unknown capabilities/readiness are explicitly distinguishable from false.
History/snapshot working memory is allocated only when reporting is enabled and
is bounded to 1.5 KiB, plus a small configuration object and temporary stack use.

## Routing and MQTT

Both direct/path and flood reports use **`PAYLOAD_TYPE_GRP_DATA` (`0x06`)**.
The route bits independently select direct or flood. `MGR1` is an application
extension with **literally plaintext public fields**, not a call to the ordinary
encrypted `createGroupDatagram()` builder. A fixed public marker is not an owner
or password-derived channel ID. There is no outer channel encryption.

The body is padded with zeroes to a group-compatible length `3 + 16*n` (maximum
179 bytes). Existing repeaters in the checked upstream implementation route group
data without requiring a successful channel decryption. This fork recognizes the
management envelope before ordinary channel processing. Reception of a management
packet never exempts it from forwarding policy. Some third-party firmware may
apply additional channel/layout policies; interoperability with every fork is
not guaranteed. Packet logging/uplinks can capture it without the password.

`RAW_CUSTOM` (`0x0F`) is **not used for reports** because stock upstream does
not flood-route it.

The observer's existing MQTT `PACKET` JSON supplies the complete frame in `raw`.
No broker configuration or password changes are necessary to capture a report.
The decoder understands all four route forms, 1–3-byte hashes, scope transport
codes, and duplicate copies heard by several uplinks:

```
python -m pip install -r tools/management/requirements.txt
python tools/management/report.py --mqtt capture.jsonl
```

The password is prompted, not supplied as a process argument. Input may be JSONL
or a JSON array of MQTT messages. Alternatively omit `--mqtt` for a JSON array
of canonical payload hex strings. `--match-admin FULL_PUBLIC_KEY` in canonical
payload mode matches a known administrator against the encrypted fingerprints.
This is an offline capture decoder, not a broker subscriber or downlink service.

## Canonical payload (little endian)

| Offset | Bytes | Field |
|---:|---:|---|
| 0 | 4 | `MGR1` |
| 4 | 16 | First 16 bytes of reporter public key |
| 20 | 4 | Persisted report sequence (never wraps; exhaustion stops TX) |
| 24 | 4 | RTC Unix timestamp; advisory, may be wrong |
| 28 | 4 | Firmware major/minor/patch/pre packed as mOTA version |
| 32 | 4 | Bootloader packed version, or unknown |
| 36 | 4 | EndF target ID |
| 40 | 8 | Complete mOTA delta-base body hash |
| 48 | 4 | EndF image length |
| 52 | 4 | Staging capacity; planning still checks actual package geometry |
| 56 | 4 | OTA capability bits |
| 60 | 2 | Uptime hours, saturated at 65535 |
| 62 | 4 | Weekly minimum mV, minimum °C, maximum °C |
| 66 | 4 | Since-report extrema in the same format |
| 70 | 1 | History coverage hours, capped at 168 |
| 71 | 1 | Interval days for this report's direct or flood schedule |
| 72 | 1 | Role: 1 repeater, 2 room server, 3 sensor |
| 73 | 1 | Compiled/detected capability bits |
| 74 | 1 | Active status bits |
| 75 | 1 | Mask of status bits whose state is known |
| 76 | 2 | Validity/partial-history flags |
| 78 | 1 | Zero-based page index |
| 79 | 1 | Total pages (1–6) |
| 80 | 1 | Unique ACL count (0–36) |
| 81 | 1 | First ACL index in this page |
| 82 | 1 | ACL entries on this page (0–6) |
| 83 | 13 × count | AES-SIV encrypted ACL entries |
| after ACL | 16 | Full AES-SIV authentication tag |
| after tag | 0–15 | Zero padding to group-compatible length; not part of canonical payload |

Each private entry is a per-radio 12-byte keyed fingerprint followed by flags:
bit 0 administrator, bit 1 trusted OTA signer. Duplicate entries combine flags.
An oversized ACL fails closed rather than silently truncating. The reported
allowlist includes all current full administrators and all four possible trusted
OTA signing keys; region/filter managers and ordinary clients are excluded.

Feature bits: 0 Wi-Fi, 1 GPS, 2 NTP, 3 USB data, 4 LoRa OTA. Wi-Fi active means
connected, GPS means receiver enabled (not necessarily a fix), NTP means an
actual accepted NTP response this boot, USB means observable native USB data
connection (not power or an unobservable external UART bridge). OTA capability
means compiled support; active means an established usable apply/store path.

Validity bits: 0 firmware version, 1 bootloader version, 2 EndF/base identity,
3 staging capacity, 4 partial week, 5 partial since-report period, 6 MCU temperature.
Voltage is unsigned millivolts, zero missing. Temperatures: zero missing,
1–251 represent −50…200 °C, 252 below range, 253 above range, 254–255 reserved.
OTA bits: 0 protocol compiled, 1 transfer DEFLATE, 2 2-KiB app transfer blocks;
bits 8–23 are apply codec bits (full/sequential/in-place). Transfer DEFLATE is
not compressed bootloader apply. There is **no manifest ID**. Exact old binaries
are still needed on the computer to generate a differential update; a hash alone
cannot reconstruct them. Unknown metadata must not be treated as OTA readiness.

## Cryptography

Password root: `SHA256("#" || literal UTF-8 password)` (hashtag-style derivation).
Subkeys: `HMAC-SHA256(root, ASCII_domain || radio_id16)`.
Domains: `MeshCore-MGR1-SIV`, `MeshCore-MGR1-ACL`.
Fingerprint: `HMAC-SHA256(ACL_subkey, radio_id16 || full_admin_key32)[0:12]`.
The same owner has different fingerprints on different radios. A collector
needs a candidate administrator's full key to identify a fingerprint.

Encryption is [RFC 5297 AES-SIV-CMAC-256](https://www.rfc-editor.org/rfc/rfc5297),
using the existing rweather AES primitive. The single associated-data string is
the complete 83-byte clear header. Ciphertext is exactly the ACL byte length.
Full 16-byte tag, no truncation. This deterministic misuse-resistant mode avoids
reliance on the firmware's noncryptographic general-purpose RNG or RTC nonces.
An identical restored snapshot may repeat ciphertext, but does not expose XORs
of different ACL plaintexts as nonce-reused stream encryption would.

All public fields are readable without a password but are authenticated only to
password holders. A shared password authenticates knowledge of that password,
**not** a unique individual radio identity; another holder can forge reports.
There is no per-device signature. Collectors must enforce their own persisted
sequence/replay policy and explicitly handle factory resets/restored backups.
The decoder verifies snapshot consistency but does not maintain a database.
This fast password derivation permits offline guessing, so twelve characters
is only a minimum length, not a guarantee of password strength. Packet timing,
public metadata and entry/page counts remain visible.
