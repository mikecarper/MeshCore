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
| Standard logging | Logging does not remove commands by itself. It has the same CLI as the selected role/profile, adds the compiled logging behavior, and provides session-only `get/set usb.logging` control. |
| LoRa-OTA (`-ota-`) | LoRa OTA adds the `ota ...` commands; it does not otherwise reduce the role CLI. ESP32 `no_external_sensors` artifacts retain the compact browser WiFi uploader, the complete CLI, and a 254-entry neighbor table. |
| ESP32 MQTT observer or ESP-NOW bridge | Always uses the expanded FULL partition profile. The build never substitutes a reduced CLI to fit the legacy application slot. |
| FULL ESP32 | Uses the matching MQTT target with logging off and keeps the complete command surface supported by that role and hardware. |
| FULL ESP32 logging | Uses the matching non-MQTT target with debug and packet logging enabled, session-only `get/set usb.logging` control, and the complete command surface supported by that role and hardware. |
| `no_external_sensors` | Removes optional external-sensor drivers and their settings; it does not remove core repeater discovery or routing commands. |

`logging`, `OTA`, and `FULL` describe independent build features. Do not infer
that a command is missing merely because `logging` appears in the filename.

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
including standard, logging, OTA, FULL, and FULL logging builds.

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
  commands. ESP32 WiFi Companion exposes `wifi.powersave` in its WebConfig
  form and binary protocol; Full Companion also exposes the text getter and
  setter on its USB terminal and TCP port 5002.
- MQTT commands require an MQTT observer target.
- `discover.scopes` requires an MQTT observer with compiled neighbor support;
  it does not independently require PSRAM or the FULL parser.
- GPS and external-sensor commands require their drivers and pins.
- Ethernet and bridge commands require the corresponding transport.
- LoRa OTA commands require an artifact with OTA enabled.
- `uf2reset` applies only to nRF52.

When diagnosing an unavailable command, check the role first, then the filename
profile, and finally the target's compiled hardware features.
