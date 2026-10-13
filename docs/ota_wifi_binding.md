# Device-bound Wi-Fi OTA

The lightweight ESP32 uploader now exposes a session identity over both the
existing control transport and HTTP. An update tool can prove that the HTTP
endpoint belongs to the selected node before uploading, instead of trusting a
shared access-point name or a familiar IP address. The receiving firmware also
validates the streamed application bytes before activating the update.

This contract applies only to builds with `LIGHTWEIGHT_WIFI_OTA` and either
`COMPANION_RADIO_FULL` or `ADMIN_PASSWORD`. It is not the legacy ElegantOTA
protocol, manifest-driven pull OTA, LoRa OTA, or Nordic Bluetooth DFU.

## Control-transport identity

Read the following command through the selected node's existing control
transport before leaving that transport for the update network:

```text
get ota.identity
```

A successful reply has this exact format, with illustrative values:

```text
OTA1 s=0123456789abcdef0123456789abcdef d=02aabbccdd11 c=9 slot=3145728 hw=heltec_v4
```

| Field | Meaning |
| --- | --- |
| `OTA1` | Version 1 lightweight binding contract. |
| `s` | Random 16-byte session nonce, encoded as 32 lowercase hex characters. |
| `d` | Physical eFuse base MAC, encoded as 12 lowercase hex characters without separators. |
| `c` | ESP application-header chip ID: ESP32 `0`, S2 `2`, S3 `9`, C3 `5`, or C6 `13`. |
| `slot` | Live size in bytes of the next, inactive application partition. |
| `hw` | Canonical hardware ID from the running firmware's valid self identity. |

The device field is not the mesh public key, BLE advertising address, station
IP, or node name. Hardware IDs contain 1 to 32 ASCII letters, digits, `_`, `-`,
or `.`. The token is prepared once and retained across repeated identity reads
and server startup. A successful `stop ota` clears it, including when the
server was never started. Reboot also loses this RAM-only session. A new
prepared session gets a new nonce; clients must not reuse a previous token.

Identity preparation fails closed if the running or next partition is missing,
both refer to the same partition, the next slot is empty, the hardware identity
is unavailable, or the physical MAC cannot be read. A reply beginning with
`ERR:` is not a token and must not enable an upload. Unsupported uploaders do
not return a synthetic `OTA1` identity.

## HTTP handshake and upload

Start the existing uploader with `start ota` or `start ota ap`, and use the URL
returned by the firmware. Full Companion uses port `8080`; infrastructure
builds use port `80`. The normal command can reuse a joined Wi-Fi/LAN; `ap`
requests the temporary `MeshCore-OTA` access point. The access-point name and
IP address alone do not identify a radio.

The running lightweight server exposes `GET /ota/info` with a no-cache JSON
response:

```json
{
  "version": 1,
  "protocol": "lightweight",
  "session": "0123456789abcdef0123456789abcdef",
  "device": "02aabbccdd11",
  "chip": 9,
  "slot": 3145728,
  "hardware": "heltec_v4"
}
```

An external update tool must compare all five identity values with the control
reply, require the supported version and protocol, and refuse missing,
malformed, or changed fields. Fetch identity again immediately before POST and
repeat the comparison. If switching an exclusive BLE/Wi-Fi build requires a
reboot, obtain a fresh identity after that transition; the pre-reboot token is
no longer valid.

Send the raw application-only `.bin` to `POST /update` with:

```http
Content-Type: application/octet-stream
Content-Length: <exact complete file size in decimal bytes>
X-MeshCore-OTA-Session: <matching 32-character lowercase session>
```

The complete size includes the final EndF trailer and must fit `slot`. This is
not a multipart, ZIP, UF2, merged flash, compressed, or chunked upload. The
bounded parser rejects duplicate headers, malformed or overflowing lengths,
missing/mismatched sessions, non-binary content types, `Transfer-Encoding`,
`Content-Encoding`, and `Expect` before `Update.begin()` or a flash write.
Detected excess body bytes also cause rejection.

The built-in browser page fetches identity when opened and immediately before
upload, compares the session/device/hardware/chip/slot, and supplies the session
header. It detects an endpoint changing during that browser visit. It does not
independently compare a control-transport reply; external tools must perform
that selected-node binding themselves.

## Exact streamed image checks before activation

The firmware parses the image as it streams, retaining only the final 56-byte
EndF trailer rather than buffering the entire image in RAM. It requires:

- One ESP application image with magic `0xE9`, 1 to 16 segments, valid header
  fields, and the exact chip ID bound to this session.
- Bounded segment lengths, the expected zero padding, and the ESP image XOR
  checksum. If the header declares an appended ESP SHA-256 digest, that complete
  digest must match too.
- No extra image, partition table, or arbitrary suffix after the application
  body. The sole remaining suffix must be the EndF trailer.
- Exactly the declared number of bytes, with no incomplete upload or detected
  trailing data. The body reader has a 15-second inactivity limit.
- An EndF marker of `EndF`, a little-endian body length equal to the actual
  parsed application body, the matching first eight SHA-256 bytes of that
  body, and the exact 32-byte padded canonical hardware field.

Only after all these checks succeed does the server call `Update.end()` and
then return `OK - firmware installed; rebooting` and restart. Bad framing is
rejected before writing. A body or footer failure can occur after some bytes
have been staged in the inactive partition; it aborts the update without
activating that partition or rebooting. This is validation before activation,
not a promise that every rejected image causes zero flash writes.

Binding and these integrity checks are not authentication or signed-release
authorization. `/ota/info` is readable by HTTP clients, the temporary AP is
open, and an on-network client can obtain its token. The server does not compare
the EndF target-ID or version fields. Update tools must still validate trusted
release provenance, the exact board variant and role, and any migration policy
before POST. Do not use the session nonce as a password or claim that the
hardware check alone authorizes a role change. Use a controlled network and
stop the uploader when it is no longer needed.

## Legacy firmware and partition migration

If `get ota.identity` or `/ota/info` is unavailable, returns the legacy page,
or cannot be matched, a safety-enforcing client must refuse the upload. Do not
silently retry an old `/update` or `/update?mode=firmware` endpoint without the
binding contract. Old firmware cannot acquire this protection merely because
the phone app was upgraded.

Install contract-capable firmware using the exact board's USB route, or a
separately reviewed and explicitly authorized migration route. A legacy
uploader might still be manually usable, but that is not a bound update and
must not be represented as one.

The reported slot is measured from the current partition table. This endpoint
does not expand partitions, install a merged flash image, or turn a single-app
layout into dual OTA. An oversized Full image requires the exact board/role
migration package or the matching merged USB image. A two-step migration must
use its supplied bridge and Full application, re-read identity and slot after
the bridge reboots, and establish a new session for stage two. There is no
generic bypass of a failed identity or slot check. See the
[firmware picker](firmware_picker.md#hardware-and-variant-names) and
[ESP32 installation guidance](releases/1.17.1.9.md#installation).

Treat a lost HTTP response or post-upload disconnect as an uncertain outcome,
not permission for an automatic second upload. Reconnect and verify the running
firmware before retrying. This contract does not by itself prove post-boot
health or provide an application-health rollback policy.

## Verification status

The implementation's host `test_wifi_ota*.py` suites passed 29 tests. They cover
production identity and HTTP methods with peripheral fakes for ESP32, S2, S3,
C3, and C6; malformed requests; mismatched/stale sessions; lifecycle cleanup;
corrupt and wrong-chip/hardware images; arbitrary chunk boundaries; and browser
identity changes before POST. These are host tests, not Wi-Fi radio tests.

Build checks reported for this change:

- R8 Full passed the compiler and app-slot checks: `2211096 / 6553600` bytes.
- Tower BLE passed its build. This is not an ESP32 Wi-Fi qualification.
- `Heltec_v3_repeater` compiled, but the portable `1310720`-byte slot check
  rejected its `1323680`-byte image, which is `12960` bytes too large. It is not
  qualified for that slot and must not be offered as a fitting OTA image.

No hardware Wi-Fi OTA qualification has been completed for this change. Do not
infer a successful on-radio upload, reboot, migration, or recovery from a host
test or compiler result.

Implementation references:

- [Session identity and HTTP header parser](../src/helpers/esp32/WiFiOtaSession.h)
- [Streaming application and EndF validator](../src/helpers/esp32/WiFiOtaStreamValidator.h)
- [ESP32 identity, server, activation, and stop lifecycle](../src/helpers/ESP32Board.cpp)
- [Companion control commands](../examples/companion_radio/MyMesh.cpp)
- [Infrastructure control commands](../src/helpers/CommonCLI.cpp)
- [Browser uploader](../webui/pages/lightweight_ota.html)
- [Host binding tests](../test/test_wifi_ota_binding.py)
