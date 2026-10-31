<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/cli_build_matrix/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

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

## Automatic radio detection

Choose the board family and role; these targets detect the fitted radio at
startup and select its existing driver. Ordinary releases and the firmware
picker use one shared image per role instead of separate chip choices.

| Target prefix | Detected radios |
| --- | --- |
| `Tbeam_*` | SX1262, SX1276/78 |
| `LilyGo_T3S3_*` | SX1262, SX1276/78 |
| `Generic_E22_*` | SX1262, SX1268 |
| `Meshadventurer_*` | SX1262, SX1268 |
| `Tenstar_C3_*` | SX1262, SX1268 |
| `muzi_base_*` | SX1262 (Uno), LR1121 (Duo) |

For example:

```bash
bash build.sh build-firmware Tbeam_companion_radio_full
bash build.sh build-firmware LilyGo_T3S3_repeater
bash build.sh build-firmware Generic_E22_repeater
bash build.sh build-firmware muzi_base_companion_radio_full
```

Selection happens after board power is enabled and before saved radio regulator
and switching settings are applied. The `board` command reports the detected
family. SX126x retains RX power saving and buffered SPI switching; SX127x retains
its own interrupt pin, modulation limits, and power control. The shared ESP32
factory TX power is 20 dBm; SX1262 also supports an explicit 22 dBm setting.
The universal T-Beam Companion uses 160 contacts and 40 channels to retain Full
wireless and OTA support within classic ESP32 RAM. Existing chip-specific
Companion capacities are unchanged.

These images cover the classic T-Beam and T3 S3 SX1262/SX1276/78 variants.
T-Beam Supreme, T-Beam 1W, SX1272, SX1280, and other LR-series boards require
their own targets. An unrecognized or missing radio cannot fall back to a guessed
driver or start transmitting. Detection reads chip identification registers
and normally takes a few tens of milliseconds, with a bounded timeout.
SX1268 keeps its 410–810 MHz limit and its own power controls. When a compiled
default is outside that range, an SX1268 starts at 434 MHz; valid saved radio
settings take precedence. Detection does not identify antenna bands or external
power amplifiers, so boards with different PA wiring remain separate. Tenstar
supports its unwired reset pin; a real hardware reset still requires that wire.

Muzi Base selects the Uno or Duo IRQ pin, TCXO voltage and RF switching only
after identifying the radio. The same Companion detects the optional SuperIO
display/GPS accessories and the fitted QSPI flash's actual JEDEC capacity.
It retains 350 contacts, 40 channels and 256 queued messages. The fixed Uno/Duo
LoRa OTA recipes keep their historical identities and remain hardware
qualification builds; the shared infrastructure recipes do not add an OTA
receiver or bypass the release qualification requirement. For now the shared
Repeater and Room recipes are explicit `pio run` qualification builds, excluded
from ordinary releases. The shared Full Companion replaces the ordinary
Uno/Duo USB, BLE and SuperIO choices once its packaged capabilities pass.

The existing `Tbeam_SX1262_*`, `Tbeam_SX1276_*`, and
`LilyGo_T3S3_sx1262_*`/`sx1276_*` images keep their names and OTA identities.
A new universal image has its own target identity; it does not bypass existing
cross-board OTA checks. Chip-specific targets remain available for explicit
builds and exact historical LoRa OTA updates. To move to a shared target, install
its matching image locally over USB. Duplicate ordinary images are omitted only
when the shared recipe and packaged detection capability are verified.
Use the Full profile for universal T3 S3 infrastructure;
both radio drivers exceed its older 1.3 MB standard application slot. Full uses
the board's existing expanded dual-OTA layout; install its matching merged
image when moving from the smaller layout. The universal T3 S3 image reserves
GPIO21/10 as radio DIO pins instead of driving them as RF switches, and uses no
OLED reset GPIO.

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
| `auto` | nRF52 repeater, room-server, and sensor builds publish the required pair: Full supported sensors + LoRa OTA and Reduced sensors + LoRa OTA. Both must pass flash, RAM, packaged-application OTA, and layout checks; a reduced image cannot substitute for a failing full image. The same policy applies to canonical bulk/release builds and external-QSPI/SD recipes. ESP32 boards with a qualified expanded profile use it; other standalone ESP32 repeaters and room servers attempt the complete recipe in their current application region, with a measured flash/partition-overflow fallback to their reduced LoRa OTA recipe. Other roles keep every capability declared by their resolved PlatformIO recipe and fail instead of silently removing one that does not fit. Compiler errors and missing-capability checks never trigger or conceal a reduced build. |
| `standard` | Preserves the deployed/portable partition contract and documented reductions. nRF52 infrastructure still publishes both OTA sensor profiles in that same exact layout; this is not an override to remove OTA or skip the full profile. |
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

For a single ESP32 repeater or room-server target, `auto` selects its matching
combined Full MQTT/USB/WiFi image when that image covers the plain Full recipe.
The direct observer and Full-matrix paths use the same `full-usb-wifi-ota`
artifact and runtime output controls. The ordinary bulk release publishes one
Full OTA identity per exact ESP32 board and role. Audited plain targets keep
their identity while compiling the combined observer/ESP-NOW sources; G2 keeps
its deployed observer identity. T-Beam SX1262/SX1276 choose the observer in
the ordinary release. TLora V2 retains two Full images because UART, MQTT
and ESP-NOW together exceed its internal RAM budget: the normal repeater
provides UART plus ESP-NOW, and the observer provides MQTT plus ESP-NOW.
T-Beam plain Full recipes remain available
as explicit alternatives with different routing capacities. Build one with
`bash build.sh build-firmware <target> --full-exact`, then deliberately install
it over LoRa with `ota pull <MID8> flash` and `ota install` on compatible
hardware and partition layouts.

The ordinary ESP32 release publishes Full images. Standard and reduced OTA
recipes, including those that fit a 1.25 MiB portable application slot, remain
available through explicit builds for recovery and migration. Select
`--build-profile standard` to retain an exact target's original partition
contract. They are omitted from the ordinary release and interactive board
menu. That menu also omits transport/default aliases covered by Full and
development utilities; it retains distinct hardware, roles, storage, and
measured capacity exceptions. Exact legacy names remain accepted by
`build-firmware` and `build-matching-firmwares`.

For the normal-role partition migrations, the release publishes a canonical
Full image under the normal target identity. Use its qualified wireless
expansion package when one exists, or install its matching merged image over
USB. mOTA rejects an incompatible partition signature until that migration
is complete, then later Full updates use the normal target ID. Moving from a
plain target to a differently named sibling can also change the LoRa OTA
identity; partition expansion does not by itself authorize that transition.

Legacy supported ESP32 Wi-Fi repeaters can use the
[Wi-Fi partition migration](esp32_wifi_partition_migration.md) bridge to
preserve their identity and install an expanded Full image without a USB flash.
The default `cascade` settings profile enables device and LoRa RX power saving;
saved preferences take precedence, and active USB/network services can prevent
device sleep. `--profile default` instead uses the upstream power-saving defaults.

Cascade Companions default to **2-byte path hashes per hop** (`path.hash.mode 1`)
on a fresh install or factory reset. Saved path settings take precedence.

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

Repeater profiles use up to 254 neighbor entries. Measured internal-DRAM
limits reduce selected Generic E22, Heltec V2, Meshadventurer, T-Beam OTA/MQTT,
and TLora MQTT profiles to 50. Check the artifact capability manifest and
[ESP32 memory budget](research/esp32_memory_budget.md) for the exact target.

See [local maintenance commands](terminal_chat_cli.md#local-maintenance-commands)
for USB, BLE/binary Companion, TCP/Ethernet, and browser CLI privileges.
A direct connection can access local diagnostics and explicit credential
getters; relaying a command over LoRa keeps its remote restrictions.

## Profile matrix

| Build/profile | Command availability |
|---|---|
| Standard non-MQTT repeater or room server | Keeps the normal role CLI and, where USB is a safe plaintext console, embeds packet logging behind persistent `get/set usb.logging` and separately saved `get/set usb.debug` verbosity. USB debug defaults off and requires compiled diagnostic support. The explicitly selected portable policy can omit WebConfig and browser WiFi OTA, so those commands are unavailable and the omission is recorded in the capability manifest. |
| Legacy standard logging | No longer emitted separately. Its behavior is compiled into the ordinary artifact. Size-constrained STM32 targets embed packet logging without verbose `MESH_DEBUG`. |
| LoRa-OTA (`-ota-`) | LoRa OTA adds the `ota ...` commands; it does not otherwise reduce the role CLI. ESP32 `no_external_sensors` artifacts retain the compact browser WiFi uploader, the complete CLI, and up to 254 neighbors, subject to recorded internal-DRAM reductions. |
| nRF52 repeater/room-server/sensor release pair | `full-ota` retains the complete supported sensor recipe; `reduced-ota` omits declared optional sensor drivers. Both include verified LoRa OTA, use the same logical target identity and exact storage/layout contract within the pair, and retain the role's normal commands. Repeaters/room servers also verify `retry.preset`. RAK3401 and RAK4631 reduced builds retain GPS where compatible and INA219/INA226/INA260/INA3221 I2C voltage/current monitors. External QSPI/SD targets also publish both choices. Companion/Terminal Chat and KISS roles do not enter this policy. |
| ESP32 MQTT observer or ESP-NOW bridge | Always uses the expanded FULL partition profile. The build never substitutes a reduced CLI to fit the legacy application slot. |
| FULL ESP32 USB + WiFi | Uses the matching MQTT target with packet logging, USB debug defaulting off, and the complete command surface supported by that role and hardware. Debug verbosity can be enabled only for diagnostics compiled into that image. `get/set logging.output off\|usb\|wifi\|both` selects and persists the active output paths; `get/set usb.debug` separately saves USB verbosity. |
| FULL ESP32 logging fallback | Uses the matching non-MQTT target when no compatible MQTT sibling exists, preserving its hardware and role CLI. Full LoRa repeaters also include runtime ESP-NOW with `set espnow.enabled on\|off` and `get espnow.running`; newly combined modes default off. Dedicated images remain only for measured capacity exceptions. USB debug defaults off; `get/set usb.debug` selects verbose output independently of packet capture. |
| nRF52 dual-CDC Full Companion | Fresh installs expose only interface `00`; it starts as an ASCII terminal and automatically hands a complete `<` frame to framed Companion. The same interface also carries exclusive serial mOTA traffic. `set usb.logging on reboot` adds output-only interface `02` for ASCII RAW/RX/TX records and optional debug. Alternatively, `set usb.logging stream reboot` keeps CLI replies and packet records on interface `00` for unmodified one-port serial bridges; Binary Companion and serial mOTA require logging off on that port. BLE and source-only LoRa OTA remain available. `get/set usb.logging` persistently controls whether the logging interface is present; `get/set usb.debug` changes verbosity without a reboot. Send CLI/control queries to interface `00`, not the log endpoint. |
| ESP32 single-TTY Full Companion | Every ESP32 Full image starts with the ASCII terminal on its one USB TTY and automatically hands a complete `<` frame to framed Companion. On 1.17.1.5, run `set powersaving off` first. `set usb.logging on` switches that TTY to an input-capable plaintext RAW/RX/TX logging terminal and makes framed Companion unavailable on USB; `set usb.logging off` stops logging but leaves the TTY in normal ASCII mode. `get/set usb.debug` separately controls saved verbose diagnostics, defaulting off. The terminal stop token or a valid incoming framed probe then performs the ordinary switch to Binary Companion. A saved logging-on setting starts directly in that logging terminal and disables automatic frame detection. BLE, WiFi, and source-only LoRa OTA remain available. ESP32 Full uses Arduino-ESP32 2.x where supported; RC32 and ESP32-C6 keep their board-required Arduino 3.x platform but still expose only one TTY. |
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
USB packet capture and debug verbosity share the canonical artifact, not a new
build/profile name. `usb.logging` remains the master; verbose diagnostics need
both that switch and saved `usb.debug` on. Fresh and legacy preferences default
debug to off. Setting debug on cannot restore code compiled out of an image.

## Canonical bulk-build policy

Bulk and release-matrix commands omit legacy names whose behavior is already
available from a canonical image:

- Every canonical nRF52 repeater, room-server, and sensor recipe emits two
  separately named, qualified OTA artifacts: `full-ota` and `reduced-ota`.
  The capability sidecar records `sensor.profile.full` or
  `sensor.profile.reduced` so the picker can identify the actual profile.
  Boards without optional sensor drivers still receive the two named choices;
  the size saving may be small or zero. A missing or unqualified member blocks
  publication of an incomplete pair. This does not make an unsupported
  bootloader or a different physical storage layout compatible.

- Companion `_ps` names are replaced by the ordinary Companion image plus the
  persisted `set powersaving on|off` setting.
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
  `MKE_s3_repeater` includes the bridge on UART 2, with RX on GPIO16 and TX on
  GPIO17. Fresh installs leave it disabled at 115200 baud; upgrades from normal
  repeaters keep it disabled, while existing saved RS-232 settings are retained.
  Use `set bridge.baud 115200` and `set bridge.enabled on` to enable it, or
  `set bridge.enabled off` to stop it. `set bridge.uart 2` selects its fixed
  UART; other UART numbers are rejected. Successful changes take effect
  immediately and persist across reboots. The historical
  `MKE_s3_repeater_bridge_rs232` name remains directly buildable with its
  bridge-on default for compatibility but is omitted from bulk releases.
  The same MKE image includes ESP-NOW, controlled independently with
  `set espnow.enabled on|off` and `get espnow.running`. Both bridges can run
  together and share `bridge.source` and `bridge.delay`; `bridge.channel`,
  `bridge.secret`, and `bridge.format` apply only to ESP-NOW. Its first boot
  after an upgrade leaves ESP-NOW off, including upgrades from the dedicated
  ESP-NOW image: run `set espnow.enabled on` to enable it. Subsequent reboots
  retain the saved setting. The historical `MKE_s3_repeater_bridge_espnow`
  target remains directly buildable with its bridge-on default but is omitted
  from bulk releases. Browser OTA pauses ESP-NOW while leaving RS-232 running;
  `stop ota` restores ESP-NOW when its saved setting is on.
- Full Heltec V3, Wireless Stick Lite V3 and RAK3112 MQTT repeaters retain
  RS-232 alongside MQTT and ESP-NOW. They use UART2 with RX on GPIO5 and
  TX on GPIO6. Use `set rs232.enabled on|off` and
  `get rs232.running` independently of `mqtt.enabled` and `espnow.enabled`.
  `bridge.baud` and `bridge.uart` configure the UART. Old MQTT preferences
  do not enable this newly added UART; its saved intent needs a validated
  UART profile marker. Historical `bridge.enabled` keeps its ESP-NOW meaning
  in MQTT images and its UART meaning in non-MQTT images. Full qualification
  verifies the linked UART driver even when compiling observer sources for a
  normal repeater, and resumed release selection rejects Full images without
  that proof.
- LilyGo T-LoRa V2.1-1.6 retains a normal Full UART/ESP-NOW repeater and a
  separate Full MQTT/ESP-NOW observer. Real triple-transport qualification
  fell 6,496 bytes short of the required internal heap, including the UART
  driver allowance. The normal UART remains on RX GPIO34 and TX GPIO25;
  use `bridge.enabled` or `rs232.enabled` to control it. Both Full identities
  remain in bulk releases instead of reducing routing capacity or RAM margins.
- MeshTower V2 repeaters use
  `Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors` as the one
  canonical target. Its SD OTA target `0A9DBBF0`, hardware ID
  `Heltec_tower_v2`, layout, and filenames stay unchanged, with separate
  required Full and Reduced sensor profiles. Both
  `Heltec_tower_v2_repeater` and
  `Heltec_tower_v2_repeater_lora_ota_no_external_sensors` resolve to this primary
  in `build-firmware` and matching builds. Old internal-only bootloader/OTA
  identities require one local USB/BLE DFU or SWD migration; the build aliases
  do not make the new image a same-target LoRa upgrade for those installed nodes.
  Existing SD users need no configuration change. The application selects SD
  first and falls back automatically for a missing or unmountable card only
  when the matching published combined OTAFIX profile declares exact
  privileged storage `09` and optional application storage `02` in its separate
  `MOTASTOR` record. Internal fallback accepts flash-staged signed application
  in-place deltas from trusted signers that fit its smaller workspace;
  SD-generated large deltas may be rejected. Full images, bootloader updates,
  and archive capture remain SD-only. Storage stays pinned through a transfer:
  use `ota cancel` before
  selecting a different backend. The application does not use an `APPHYBRID`
  retained-RAM staging arena. See [MeshTower SD OTA](ota_meshtower_v2_sdcard.md).

The old aliases still work with `build-firmware` and
`build-matching-firmwares`. Dedicated LoRa OTA repeater images are not
collapsed outside the MeshTower consolidation above; they retain their exact
storage, bootloader, role, and target identity contracts. Companion boards
keep transport-specific canonical images only when no exact Full recipe has
passed the combined flash/RAM qualification.
ESP32 deliberately uses one TTY: Binary Companion and plaintext USB logging
are mutually exclusive there. nRF52 retains its optional second CDC port.

The [OTAFIX 2.4.12 release](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/v0.11.0-OTAFIX2.4.12)
is the bootloader reference for [firmware 1.18.0.1](releases/1.18.0.1.md),
not an all-board qualification claim.
Consult its exact-board manifest and unavailable-profile reasons. Internal
application storage accepts exact-base in-place deltas that fit the workspace;
full application packages need the matching external NOR or microSD profile.
The retired internal-only MeshTower V2 bootloader requires one local
installation of the combined SD/internal profile. Its changed identity/layout
must not be crossed by an ordinary LoRa application or bootloader update.

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
  protocol. All ESP32 WiFi Companions with WebConfig also expose the browser
  CLI tab and `wifi.cli` switch, enabled by default in station/LAN mode. Older
  Companion firmware needs an update to support it. Full Companion additionally
  exposes its complete chat and streaming text terminal on TCP port 5002.
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
