# CLI Availability by Firmware Build

MeshCore command availability is determined in three layers:

1. **Role** - repeater, room server, sensor, companion, bridge, or KISS modem.
2. **Build profile** - standard, portable, logging, OTA, or FULL.
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
| Standard logging | Logging does not remove commands by itself. It has the same CLI as the selected role/profile and adds the compiled logging behavior. |
| LoRa-OTA (`-ota-`) | LoRa OTA adds the `ota ...` commands; it does not otherwise reduce the role CLI. ESP32 `no_external_sensors` artifacts retain the compact browser WiFi uploader and use a 254-entry neighbor table. A portable OTA artifact can still have the other portable restrictions described below. |
| Portable MQTT observer | Keeps MQTT/WiFi commissioning, bridge control, radio essentials, update commands, basic identity/status commands, `neighbors`, `discover.neighbors`, `outpath`, and `altpath`. The large repeater administration tree is omitted to fit the legacy ESP32 application slot. |
| Portable ESP-NOW bridge | Keeps the repeater's role-specific handlers and a reduced common configuration surface containing radio and bridge essentials, including `rxdelay`, `txdelay`, `outpath`, and `altpath`. |
| FULL ESP32 | Uses the matching MQTT target with logging off, removes size-based CLI cuts, and restores the complete command surface supported by that role and hardware. |
| FULL ESP32 logging | Uses the matching non-MQTT target with debug and packet logging enabled and the complete command surface supported by that role and hardware. |
| `no_external_sensors` | Removes optional external-sensor drivers and their settings; it does not remove core repeater discovery or routing commands. |

`logging`, `OTA`, and `FULL` describe independent build features. Do not infer
that a command is missing merely because `logging` appears in the filename.

## Portable MQTT observer retained surface

The portable MQTT observer keeps these command groups:

- lifecycle and identity: `reboot`, `poweroff`, `shutdown`, `ver`, `board`,
  `password`, and `erase` on the local console;
- radio operation: `advert`, `advert.zerohop`, `clock`, `clock sync`, `time`,
  `memory`, `neighbors`, `discover.neighbors`, and the remote-client routing
  controls `outpath` and `altpath`;
- browser/update control when compiled: `start ota`, `stop ota`, `ota check`,
  and `ota update`;
- radio essentials through `get`/`set`: radio parameters, TX power, CAD,
  interference threshold, AGC reset interval, RX gain, `rxdelay`, `txdelay`,
  repeat state, and applicable FEM controls;
- MQTT, WiFi, NTP, bridge, and alert commands implemented by the observer
  feature set;
- region and onboard-GPS commands that fit and are compiled into the selected
  target.

Everything in the repeater-only administration tree that is not listed above is
intentionally cut from this portable profile. The main omissions include ACL
editing, flood filter/moderation/scope administration, advanced mesh-clock
controls, recent-repeater/path administration, battery-alert/RX-watchdog
controls, stored-log management, and external-sensor administration. Use the
matching FULL ESP32 build when those commands are required.

Some observer commands have their own hardware limit:

- MQTT neighbor-table publishing and `discover.scopes` require the compiled
  `WITH_MQTT_NEIGHBORS` feature. PSRAM boards enable it automatically; selected
  non-PSRAM variants opt in with `MQTT_NEIGHBORS_WITHOUT_PSRAM`. The commands
  can therefore be present in either portable or FULL MQTT profiles.
- `discover.neighbors` does **not** require MQTT or PSRAM.
- full NTP connectivity diagnostics are omitted from the portable profile.

## Discovery invariant

`discover.neighbors` sends the zero-hop node-discovery request used to refresh
the repeater neighbor table. It is available in every repeater build profile,
including portable MQTT, standard, logging, OTA, FULL, and FULL logging builds.

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
  commands.
- MQTT commands require an MQTT observer target.
- `discover.scopes` requires an MQTT observer with compiled neighbor support;
  it does not independently require PSRAM or the FULL parser.
- GPS and external-sensor commands require their drivers and pins.
- Ethernet and bridge commands require the corresponding transport.
- LoRa OTA commands require an artifact with OTA enabled.
- `uf2reset` applies only to nRF52.

When diagnosing an unavailable command, check the role first, then the filename
profile, and finally the target's compiled hardware features.
