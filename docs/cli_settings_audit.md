# CLI setting dispatch audit

The 1.17.1.5 source retained `get path.hash.mode` in `CommonCLI` but lost its
setter when radio preferences moved into `CommonRadioPrefs`. Infrastructure
roles call `CommonCLI` directly, so the shared parser's setter was unreachable.
The correction restores `set path.hash.mode 0|1|2` there, validates the value,
and saves accepted changes. Missing/invalid values report a value error and
leave the setting unchanged.

The other radio setters removed by the original refactor had already been
restored. A feature-guarded check found a second mismatch: `get extra.sf` exposed
stored data on non-LR2021 radios while its setter was compiled out. Both
commands now return `Error: extra.sf requires an LR2021 radio` there. Supported
LR2021 set/get behavior is preserved. These are source corrections; existing
release binaries need rebuilding/updating to receive them.

## Coverage

| Surface | Check |
| --- | --- |
| Common infrastructure CLI | 72 literal setter keys and 84 query keys; every query has a setter or an explicitly reviewed read-only/alternate-command exception |
| MQTT/observer CLI | 33 literal setter keys and 39 query keys, including Wi-Fi, timezone, alerts, display and watchdog settings |
| MQTT slots | All nine writable slot subkeys have query/set coverage; `diag` is intentionally read-only |
| Repeater, Room Server, Sensor | Shared CLI delegation and role-specific query/set coverage |
| Companion | Shared-radio allowlist and parser agreement, persistence wiring, terminal-specific setters, and role query exceptions |
| Radio settings moved by the refactor | All 13 shared setters remain present in infrastructure dispatch |
| Literal command comparisons | Checks key lengths so a setter cannot accidentally compare its value against the key's terminating NUL |
| Feature guards | Preprocesses common and observer handlers for minimal, nRF52 GPS/SD, ESP32 WebConfig, ESP32 MQTT, RS232/GPS, ESP-NOW, and LR2021 profiles before comparing query/set coverage |

Read-only exceptions include runtime connection status, diagnostics, firmware
role, bootloader identity and reset information. Alternate-command exceptions
include the password command and `set battery.alert on <region>`. These
exceptions are explicit in the test, rather than silently allowing all missing
setters. Dynamic/delegated command families and hardware-specific features
continue to use their dedicated tests; the inventory is not a claim that every
build has every feature enabled.

## Executed behavior tests

The native C++ fixture compiles the production get/set dispatch branches and
the real setting bodies for 14 radio-related settings: `radio`, `freq`, `af`,
`dutycycle`, `int.thresh`, `cad`, `radio.rxgain`, `tx`, `rxdelay`,
`agc.reset.interval`, `path.hash.mode`, `multi.acks`, `txdelay`, and
`direct.txdelay`. It runs local and authenticated on-air caller timestamps,
checks round-trip values and save calls, preserves AGC interval rounding, and
checks that rejected hardware applies do not save or change preferences.
Hardware callbacks are mocked; these are not live-radio or flash-durability
tests. The fixture also runs with and without `USE_LR2021`, checking supported
`extra.sf` set/query/clear behavior, invalid values, and the unsupported-radio
error.

The focused hash-mode test checks all valid modes, missing values, negative and
out-of-range values, overflow, malformed numbers, whitespace, and similarly
named unknown keys. Existing transport tests exercise Companion local/framed
and on-air dispatch, local-only secret reads, and browser/stream routing under
AddressSanitizer and UndefinedBehaviorSanitizer on Linux.

Run the CI checks locally with a host C++ compiler (Linux is required for the
existing sanitizer-enabled transport suite):

```text
python3 -B test/test_path_hash_cli.py
python3 -B test/test_cli_settings_contract.py
python3 -B test/test_local_cli_access.py
```

All 14 tests passed on the Linux VM. The workflow runs these commands on future
pushes and pull requests. The new inventory and native radio tests also passed
with the Windows host compiler.

`pio run -e heltec_v4_repeater` passed after both corrections, including the
firmware's flash-size and runtime-RAM checks. The initial audit used host tests
and build checks; subsequent hardware checks are recorded in the
[dual-profile validation](radio_profiles_validation.md).
