# Full USB logging repair — 1.17.1.6

Audit date: 2026-09-13. Release firmware source: `306feebe6d648a247f925894f876be9d747fc8d2`.

The original Station G2 Full Repeater and Full Room Server images omitted USB
packet logging. Both inherited `-UMESH_PACKET_LOGGING` from their ordinary MQTT
observer recipes. Full added `-DMESH_PACKET_LOGGING=1`, but PlatformIO's
`ProcessFlags` moves undefines after defines in the compiler command. The
resulting image compiled successfully with its packet logger removed.

The previous release gate checked OTA and other features without requiring
USB logging in Full infrastructure. An old passing manifest could also be
reused by `--resume` without the new logging evidence.

## Published-image audit

Each downloaded/cached artifact was matched to GitHub's published SHA-256.
The check inspected application bytes, including the application inside nRF52
DFU ZIPs. It required the packet-log formatter and USB logging controls.

| Release group | Application images | Missing USB packet logging |
| --- | ---: | ---: |
| ESP32 Full infrastructure/utility | 157 | 2 |
| ESP32 Full Companion | 52 | 0 |
| nRF52 Full Companion | 43 | 0 |
| Total | 252 | 2 |

Affected targets:

- `Station_G2_repeater_observer_mqtt`
- `Station_G2_room_server_observer_mqtt`

V4/R8 Full builds and Station G2's Full ESP-NOW bridge passed. This audit does
not count ordinary MQTT profiles that intentionally omit USB logging as
broken. Artifact inspection proves compiled support, not hardware operation
on every board.

## Fix and regression coverage

- Full builds that require logging remove inherited disables of
  `MESH_PACKET_LOGGING` before PlatformIO parses flags. Other macros and
  explicit logging-off profiles retain their policy.
- Full capability checks require the packet logger and USB controls in the
  packaged application. Combined USB/WiFi profiles additionally require
  the `logging.output` setter. ELF symbols or debug text cannot satisfy these
  checks.
- Resumed builds must have passing application evidence for the current
  logging contract, along with the existing artifact hashes and RAM checks.
- CI runs the flag, real PlatformIO/compiler, artifact, resume, and build-profile
  regression suites.

Focused verification (after installing PlatformIO and its tool-scons package):

```sh
python3 -B test/test_full_build_flag_dedup.py
python3 -B test/test_full_logging_compile.py
python3 -B test/test_firmware_capabilities.py
python3 -B test/test_full_logging_resume.py
bash test/test_build_profiles.sh
python3 -B test/test_cascade_release_package.py
```

Both Station G2 profiles were rebuilt through `build.sh --full` with the
original observer recipes, including their inherited logging undefines. They
passed the new application checks, runtime RAM budgets, and both OTA slot
size checks. The new checker rejects both original published images.

On the Bellevue Station G2, the corrected Full repeater implementation also
passed serial CLI and passive traffic checks: four RX and two TX packet log
lines were captured. Its active temporary profile retained preamble 120 and
the original schedule end time; the Pi relay services were restored.

Corrected application SHA-256:

```text
Repeater:    81a0a2926d4e567c069d0b10f316047881bcedce062afaa42119dddb2baadf23
Room Server: 7ee81f1cb65fc274bd363397c3c80ce9006d575efae50fe952c8ca246613a18c
```
