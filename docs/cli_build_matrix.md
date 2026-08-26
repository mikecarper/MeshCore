# CLI Availability by Firmware Build

MeshCore command availability is determined in three layers:

1. **Role** - repeater, room server, sensor, companion, bridge, or KISS modem.
2. **Build profile** - standard, logging, OTA, or FULL.
3. **Compiled hardware features** - WiFi, MQTT, GPS, external sensors, PSRAM,
   Ethernet, and similar optional support.

The complete command descriptions are in [CLI Commands](cli_commands.md). This
page describes the commands intentionally omitted or limited by build profile.
The [CLI Command Availability Matrix](cli_command_availability.md) expands this
summary into separate nRF52 and ESP32 command tables.

## Role comes first

| Role | Text administration CLI |
|---|---|
| Repeater | Full repeater administration surface, subject to the profile differences below |
| Room server | Room-server administration surface, subject to the profile differences below |
| Sensor | Sensor command surface; it does not acquire the repeater administration tree |
| Serial, USB, BLE, or WiFi companion | Uses the companion protocol; any serial diagnostics are target-specific |
| KISS modem | Uses the KISS/TNC frame interface, not the repeater text CLI |
| Bridge | Uses its base role plus commands for the bridge transport compiled into that target |

A command belonging to a different role is not considered a profile cut. For
example, adding FULL features to a sensor does not turn it into a repeater
administrator.

Repeater profiles use 254 neighbor entries across supported platforms. The
classic T-Beam SX1262 and SX1276 MQTT observer repeaters are the exception and
retain 50 because their MQTT discovery tables are constrained by internal DRAM.

## Profile matrix

| Build/profile | Command availability |
|---|---|
| Standard non-MQTT repeater or room server | Keeps the normal role CLI. Size-constrained ESP32 artifacts can omit WebConfig and browser WiFi OTA, so their WebConfig/WiFi commands are unavailable. |
| Standard logging | Logging does not remove commands by itself. It has the same CLI as the selected role/profile and adds compiled logging behavior. CommonCLI roles persist `get/set usb.logging`. ESP32 roles covered by unified FULL and nRF52 Companions covered by dual-CDC Full Companion are not duplicated here. |
| LoRa-OTA (`-ota-`) | LoRa OTA adds the `ota ...` commands; it does not otherwise reduce the role CLI. ESP32 `no_external_sensors` artifacts retain the compact browser WiFi uploader, the complete CLI, and a 254-entry neighbor table. |
| ESP32 MQTT observer or ESP-NOW bridge | Always uses the expanded FULL partition profile. The build never substitutes a reduced CLI to fit the legacy application slot. |
| FULL ESP32 USB + WiFi | Uses the matching MQTT target with packet logging on, verbose debug off, and the complete command surface supported by that role and hardware. `get/set logging.output off\|usb\|wifi\|both` selects and persists the active output paths. |
| FULL ESP32 logging fallback | Uses the matching non-MQTT target only when no WiFi MQTT sibling exists, with debug and packet logging enabled and the complete command surface supported by that role and hardware. Its persistent USB gate also covers output-off operation, avoiding a second FULL ESP-NOW image. |
| Dual-CDC Full Companion | nRF52 and qualified native-USB ESP32-S3 Full images use one physical USB connection. Fresh installs expose only interface `00` for framed Companion/terminal/mOTA traffic. Enabling logging and rebooting adds interface `02` for plaintext logs. They also provide BLE and source-only LoRa OTA; ESP32 additionally provides WiFi. `get/set usb.logging` persistently controls whether the logging interface is present. |
| `no_external_sensors` | Removes optional external-sensor drivers and their settings; it does not remove core repeater discovery or routing commands. GPS-preserving RAK nRF52 OTA profiles retain their GPS commands and provider. The RAK4631 Serial1 RS232 bridge remains GPS-off because both features require Serial1. |

`logging`, `OTA`, and `FULL` describe independent build features. Do not infer
that a command is missing merely because `logging` appears in the filename.

## Canonical bulk-build policy

Bulk and release-matrix commands omit legacy names whose behavior is already
available from a canonical image:

- Companion `_ps` names are replaced by the ordinary Companion image plus the
  persisted `powersaving on|off` setting.
- Companion `_femoff` names are replaced by the matching controllable-FEM
  image plus `radio.fem.rxgain on|off`. The old names remain explicit build
  targets for compatibility.
- `Station_G2_logging_*` and `Station_G3_ESP32_logging_*` hardware names are
  replaced by their ordinary Station target. G2 boosted receive gain is the
  persisted `radio.rxgain on|off` setting; the G3 alias changed only the
  advertised default name.
- When a board has a dual-CDC Full Companion, that one artifact replaces its
  separate USB, BLE, ordinary WiFi, and USB packet-logging Companion artifacts.
  Current support includes nRF52 Full Companion plus qualified native-USB
  ESP32-S3 Full targets: Heltec V4, T-Beam 1W, Station G2/G3, XIAO S3 WIO,
  Heltec Tracker V2, Meshnology W12, and Nibble Screen/Zero Connect. RAK3112
  and Heltec RC32 keep separate transport and logging artifacts pending live
  hardware validation.
  It provides BLE plus an always-present interface `00` for framed
  Companion/terminal/mOTA traffic. Enabling logging and rebooting adds
  interface `02` for plaintext logs.
  Its LoRa OTA support is source-only: it can serve a host file to another node
  but has no staging store and cannot update itself over LoRa.

The old aliases still work with `build-firmware` and
`build-matching-firmwares`. Dedicated repeater LoRa OTA receiver images are not
collapsed; they retain their exact storage, bootloader, role, and target
identity contracts. ESP32 boards without the tested dual-CDC Full profile keep
USB, BLE, WiFi, and Full Companion images separate because Full changes
partitions, RAM use, active transports, and power behavior.

## Complete CLI policy

The compact ESP32 CLI has been removed. MQTT observers and ESP-NOW bridges are
automatically promoted to FULL builds with expanded partitions rather than
dropping administration commands. This keeps `tempradio`, LoRa OTA, power
saving, RXPS, logging, statistics, sensor, ACL, routing, and advanced radio
commands whenever the role and compiled hardware support them.

Some commands still have hardware or feature limits. MQTT neighbor-table
publishing and `discover.scopes` require the compiled `WITH_MQTT_NEIGHBORS`
feature. PSRAM boards enable it automatically, and selected non-PSRAM variants
opt in with `MQTT_NEIGHBORS_WITHOUT_PSRAM`. `discover.neighbors` does not
require MQTT or PSRAM.

## Discovery invariant

`discover.neighbors` sends the zero-hop node-discovery request used to refresh
the repeater neighbor table. It is available in every repeater build profile,
including standard, logging, OTA, unified FULL, and FULL logging-fallback builds.

The exact command is:

```text
discover.neighbors
```

It accepts no options and returns:

```text
OK - Discover sent
```

Some MQTT room-server targets also expose the command as part of their compiled
neighbor-table feature. It is not a cross-role guarantee: companion, KISS,
sensor, and ordinary room-server firmware use different interfaces or do not
maintain the repeater administrator neighbor table.

## Feature-dependent commands

Even in a FULL build, a command can be unavailable when its underlying feature
does not exist on that target:

- WebConfig and the `wifi.ssid`, `wifi.status`, `wifi.powersave`, and `wifi.cli` command
  family require an ESP32 WebConfig build. FULL standalone repeater and
  room-server builds support the corresponding WiFi setters and status
  commands. ESP32 WiFi Companions with WebConfig expose WiFi credentials,
  connection status, WebConfig, CLI-tab, and power-save controls from their USB
  text terminal as well as power saving through WebConfig and the binary
  protocol. Full Companion additionally exposes its complete role-specific
  text terminal on TCP port 5002.
- MQTT commands require an MQTT observer target.
- `discover.scopes` requires an MQTT observer with compiled neighbor support;
  it does not independently require PSRAM or the FULL parser.
- GPS and external-sensor commands require their drivers and pins.
- Ethernet and bridge commands require the corresponding transport.
- LoRa OTA commands require an artifact with OTA enabled.
- `uf2reset` applies only to nRF52.

When diagnosing an unavailable command, check the role first, then the filename
profile, and finally the target's compiled hardware features.
