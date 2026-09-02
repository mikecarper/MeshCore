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

## Selecting a build profile

The runtime settings profile and the feature profile are separate switches:

```bash
bash build.sh build-firmware RAK_3401_repeater \
  --profile cascade \
  --build-profile auto
```

`--profile default|cascade` selects saved-setting defaults. It does not select
which code is linked. `--build-profile auto|standard|full` controls features
and partition policy:

| Selection | Behavior |
| --- | --- |
| `auto` | For one explicit target, pass 1 builds the complete supported LoRa-OTA-capable recipe. ESP32 boards with a qualified expanded profile use it; other repeaters attempt the complete recipe in their current application region. A non-repeater keeps every capability declared by its resolved PlatformIO recipe, including LoRa OTA, and fails instead of silently removing one that does not fit. A measured repeater flash/partition overflow starts the standard `no_external_sensors` LoRa OTA pass. Internal-flash nRF52 repeaters publish that reduced pass even when the complete image fits, because the smaller running image leaves more room to stage a delta; matched QSPI/SD repeaters do not need the redundant artifact. Compiler errors and missing-capability checks never trigger or conceal a reduced build. Canonical bulk commands keep their established standard partition contract. |
| `standard` | Immediately uses the deployed/portable partition contract and its documented reductions. This is useful when the operator already knows the expanded or complete image is unsuitable. |
| `full` | Requires a qualified ESP32 expanded-partition target (or an explicitly named Full Companion). Install a matching merged image when this changes the partition table. |

Successful builds also emit
`<firmware>.capabilities.json`. The sidecar records the effective profile,
logical OTA target, actual PlatformIO base, artifact name target, promised
capabilities, and every reduction selected by the script. The build
fails if a promised linked marker is absent. Current invariants include
`retry.preset` for repeater/room-server roles, WebConfig for the Indicator and
ESP32 Full Companion, TempRadio/OTA controls for install-capable Companions,
host-mOTA controls for Full Companion, and the
advanced flood-rule engine for Full room servers. Resume mode will not accept
an old or failed artifact without a verified sidecar.

## Role comes first

| Role | Text administration CLI |
|---|---|
| Repeater | Full repeater administration surface, subject to the profile differences below |
| Room server | Room-server administration surface, subject to the profile differences below |
| Sensor | Sensor command surface; it does not acquire the repeater administration tree |
| Full, serial, Ethernet, USB, BLE, or WiFi companion | Uses the companion protocol; Full combines every qualified transport for that exact board |
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
| Standard non-MQTT repeater or room server | Keeps the normal role CLI and, where USB is a safe plaintext console, embeds debug/packet logging behind persistent `get/set usb.logging`. The explicitly selected portable policy can omit WebConfig and browser WiFi OTA, so those commands are unavailable and the omission is recorded in the capability manifest. |
| Legacy standard logging | No longer emitted separately. Its behavior is compiled into the ordinary artifact. Size-constrained STM32 targets embed packet logging without verbose `MESH_DEBUG`. |
| LoRa-OTA (`-ota-`) | LoRa OTA adds the `ota ...` commands; it does not otherwise reduce the role CLI. ESP32 `no_external_sensors` artifacts retain the compact browser WiFi uploader, the complete CLI, and a 254-entry neighbor table. |
| Internal-flash nRF52 repeater auto pair | `full-ota` retains the board's external-sensor drivers; `reduced-ota` omits the declared optional sensors to leave additional internal-flash staging room. RAK3401 and RAK4631 reduced builds retain INA219, INA226, INA260, and INA3221 I2C voltage/current monitors at a measured cost below 5 KiB. Both artifacts carry the same stable OTA target identity and are checked for `ota ...` and `retry.preset`; RAK artifacts also verify the retained monitor drivers. |
| ESP32 MQTT observer or ESP-NOW bridge | Always uses the expanded FULL partition profile. The build never substitutes a reduced CLI to fit the legacy application slot. |
| FULL ESP32 USB + WiFi | Uses the matching MQTT target with packet logging on, verbose debug off, and the complete command surface supported by that role and hardware. `get/set logging.output off\|usb\|wifi\|both` selects and persists the active output paths. |
| FULL ESP32 logging fallback | Uses the matching non-MQTT target only when no WiFi MQTT sibling exists, with debug and packet logging enabled and the complete command surface supported by that role and hardware. Its persistent USB gate also covers output-off operation, avoiding a second FULL ESP-NOW image. |
| nRF52 dual-CDC Full Companion | Fresh installs expose only interface `00`; it starts as an ASCII terminal and automatically hands a complete `<` frame to framed Companion. The same interface also carries exclusive serial mOTA traffic. Enabling logging and rebooting adds interface `02` for plaintext logs. BLE and source-only LoRa OTA remain available. `get/set usb.logging` persistently controls whether the logging interface is present. |
| ESP32 single-TTY Full Companion | Every ESP32 Full image starts with the ASCII terminal on its one USB TTY and automatically hands a complete `<` frame to framed Companion. `set usb.logging on` switches that TTY to an input-capable plaintext logging terminal and makes framed Companion unavailable on USB; `set usb.logging off` stops logging but leaves the TTY in normal ASCII mode. The terminal stop token or a valid incoming framed probe then performs the ordinary switch to Binary Companion. A saved logging-on setting starts directly in that logging terminal and disables automatic frame detection. BLE, WiFi, and source-only LoRa OTA remain available. ESP32 Full uses Arduino-ESP32 2.x where supported; RC32 and ESP32-C6 keep their board-required Arduino 3.x platform but still expose only one TTY. |
| `no_external_sensors` | Trims selected optional environmental/ranging drivers and their settings; it does not remove generic I2C, core repeater discovery, routing, or runtime RS-232 commands. RAK3401 and RAK4631 profiles retain the four common INA I2C voltage/current monitors. GPS-preserving RAK nRF52 OTA profiles retain their GPS commands and provider; RAK4631 defaults the bridge to UART 2 because RAK12501/L76K GPS uses UART 1. Legacy target suffixes remain stable for OTA identity compatibility. |

The four retained INA drivers are entries in the optional environmental-sensor
table, not the complete set of RAK I2C consumers. Compatible reduced profiles
also retain the SSD1306 OLED, supported autodiscovered RTCs, and RAK12500 I2C
GPS as separate board peripherals. RAK12501/L76K GPS uses UART Serial1. The
explicit RAK4631 Serial1 bridge omits the combined GPS provider, including the
otherwise non-UART RAK12500 path.

The firmware-configured INA3221 and RAK12500 addresses are both `0x42`. To use
both devices on one bus, leave RAK12500 at `0x42`, strap INA3221 A0 to SCL for
`0x43`, and build with `-DTELEM_INA3221_ADDRESS=0x43`.

`logging`, `OTA`, and `FULL` describe independent build features in historical
filenames. Current standard artifacts use no `-logging-` infix because their
USB logging is runtime controlled.

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
- When a board has a Full Companion, that one artifact replaces its
  separate USB, BLE, ordinary WiFi, hardware-serial, Ethernet Companion, and
  USB packet-logging Companion artifacts.
  It provides BLE and source-only LoRa OTA; ESP32 also provides ordinary WiFi.
  nRF52 keeps framed traffic on interface `00` and can add logging on interface
  `02` after a reboot. Every ESP32 Full build instead switches its one USB TTY
  into an input-capable logging terminal. Turning logging off restores the
  normal ASCII terminal; the usual stop token or a valid framed probe then
  switches it to Binary Companion.
  Its LoRa OTA support is source-only: it can serve a host file to another node
  but has no staging store and cannot update itself over LoRa.
  Installing an ESP32 Full Companion may require one merged-image erase/flash
  to adopt its expanded partition table. Once migrated, later Full Companion
  releases use normal app updates. The transport-specific non-Full images stay
  available as explicit build targets, but are not canonical release artifacts
  when the qualified Full image fits.

- Standalone Terminal Chat is omitted when the same exact hardware has either
  Full Companion or a USB Companion, because their local text terminal supplies
  the same role. Heltec E290 and T190 similarly publish one combined USB + BLE
  Companion. RAK4631 repeater and room-server Ethernet builds stay separate;
  only the RAK4631 Ethernet Companion is folded into Full Companion.
- Matching RS-232 bridge roles are compiled into the normal repeater and
  selected at runtime with `bridge.enabled`, `bridge.baud`, and `bridge.uart`.
  Historical bridge names remain directly buildable but are omitted from bulk
  releases. Wio-E5 remains separate because its normal image has only 916
  bytes free, while the combined image exceeds the fixed 240 KiB application
  partition by 2,192 bytes.

The old aliases still work with `build-firmware` and
`build-matching-firmwares`. Dedicated LoRa OTA repeater images are not
collapsed; they retain their exact storage, bootloader, role, and target
identity contracts. Companion boards keep transport-specific canonical images
only when no exact Full recipe has passed the combined flash/RAM qualification.
ESP32 deliberately uses one TTY: Binary Companion and plaintext USB logging
are mutually exclusive there. nRF52 retains its optional second CDC port.

## Complete CLI policy

The compact ESP32 CLI has been removed. `retry.preset` is a checked invariant
for every repeater and room-server artifact, not a FULL-only command. MQTT
observers and ESP-NOW bridges are
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

- WebConfig and the `wifi.ssid`, `wifi.status`, and `wifi.powersave` command
  family require an ESP32 WebConfig build. FULL standalone repeater and
  room-server builds support the corresponding WiFi setters and status
  commands. ESP32 WiFi Companions with WebConfig expose WiFi credentials,
  connection status, WebConfig, and power-save controls from their USB
  text terminal as well as power saving through WebConfig and the binary
  protocol. The browser CLI tab and `wifi.cli` setting are repeater/room-server
  features. Full Companion instead exposes its complete role-specific text
  terminal on TCP port 5002.
- MQTT commands require an MQTT observer target.
- `discover.scopes` requires an MQTT observer with compiled neighbor support;
  it does not independently require PSRAM or the FULL parser.
- GPS and external-sensor commands require their drivers and pins.
- Ethernet and bridge commands require the corresponding transport.
- LoRa OTA commands require an artifact with OTA enabled.
- `uf2reset` applies only to nRF52.

When diagnosing an unavailable command, check the role first, then the filename
profile, then the target's compiled hardware features and its adjacent
`.capabilities.json` file. A current artifact that promises the command but
does not contain its linked marker is rejected during the build.
