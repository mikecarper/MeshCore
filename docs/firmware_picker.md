<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/firmware_picker/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Firmware picker

Each result now includes **Restore your settings after flashing**. Its commands
follow the selected logging mode and the exact image's verified role/hardware
controls. Click On, Off, or Check to view and copy the commands; Companion
MQTT and GPS show their app/WebConfig steps instead. See
[feature switches by role](role_feature_switches.md) for the full reference.
The [USB web console](https://flasher.meshcore.io/console) works with the
default ASCII terminal on USB Companion and infrastructure roles.

Pick the choices in any order. The controls stay limited to compatible
firmware combinations that were actually built in the current release set.
The result count stays visible while you scroll through the filters on a phone.
Once the exact board and role are selected, the picker skips logging,
connection, and profile choices that all select the same firmware. Logging
settings move into **Restore your settings after flashing** beneath the files.
Choices that select different images remain visible.
The optional chip-family filter (ESP32, nRF52, RP2040, or STM32) can narrow the
hardware list first. You can skip it: picking hardware fills it in automatically.
Chip family and hardware are the only dropdowns. All remaining choices use
buttons. **Firmware profile** combines OTA support, Full/standard, and sensor
or storage differences when a choice between different images is needed.

RAK3401 and RAK4631 repeaters use one unified image per sensor policy for
supported internal and external OTA storage. **Full** and **Reduced** choose
optional sensor drivers; they do not select a storage module. The unified
RAK4631 image also includes runtime RS-232 UART selection. Ethernet remains
a separate RAK4631 hardware image. LoRa OTA still requires the matching
OTAFIX bootloader and a package compatible with the installed target.

The picker reads public release metadata from GitHub. It does not upload device
information. Hardware names, target names, and download links come directly
from the published firmware assets.

<div class="firmware-picker" data-firmware-picker data-release-repo="mikecarper/MeshCore" data-controls-url="../_data/firmware_controls.json?v=1.17.1.9-88c85108" data-bootloaders-url="../_data/bootloader_manifest.json?v=20261002-1" data-share-url="https://mikecarper.github.io/MeshCore/firmware_picker/">
  <div class="firmware-picker-intro" role="note">
    <strong>Current release set</strong>
    <p data-role="release-set">Loading release information...</p>
    <p>
      For a new installation, choose the exact board and role, and select
      <strong>Full install / layout migration (merged .bin)</strong>. Current ESP32
      releases combine the supported features in Full; use runtime settings to
      choose the active transports. Where separate profiles remain, prefer
      <strong>FULL / complete</strong> when it meets your update and hardware
      requirements. Other chip families retain their qualified profiles.
    </p>
    <p>
      Upgrading an existing ESP32 infrastructure node from a smaller partition
      layout? The selected Full result links directly to its exact board and
      role partition-expansion ZIP when published. Follow its included README
      to check the installed OTA target and source layout for a supported Wi-Fi
      or LoRa route. If no unique matching ZIP is available, the picker links
      to the utility release to check availability. USB installation uses the matching
      merged image instead. An app-only .bin cannot change the partition table.
      If the installed firmware supports it, run <code>get storage.layout</code>
      to see the live flash size and OTA slots. Firmware version alone does not
      prove the installed layout.
    </p>
  </div>

  <p class="firmware-picker-order-note">
    Pick in any order. Use <strong>Any</strong> to clear one choice, or clear
    everything with the button below. The address bar updates with your choices;
    copy its URL to reopen or share the same selection.
  </p>

  <form class="firmware-picker-form" data-role="form">
    <div class="firmware-picker-feedback firmware-picker-wide" data-role="filter-feedback" hidden>
      <div aria-live="polite" aria-atomic="true">
        <strong data-role="filter-count"></strong>
        <p data-role="filter-note"></p>
      </div>
      <a data-role="view-results" hidden>View files</a>
    </div>

    <details class="firmware-picker-chip-family firmware-picker-wide">
      <summary data-role="chip-family-summary">Optional: chip family</summary>
      <p id="firmware-picker-chip-help">Skip this if you know your board. Picking hardware selects its chip family automatically. Choose Any to clear this filter.</p>
      <div class="firmware-picker-control firmware-picker-select-control">
        <label for="firmware-picker-chip-family">Chip family</label>
        <select id="firmware-picker-chip-family" data-field="chipFamily" aria-describedby="firmware-picker-chip-help" disabled>
          <option value="">Any chip family - skip this filter</option>
        </select>
      </div>
    </details>

    <div class="firmware-picker-control firmware-picker-select-control">
      <label for="firmware-picker-hardware-family">Hardware</label>
      <select id="firmware-picker-hardware-family" data-field="hardwareFamily" disabled>
        <option value="">Loading hardware...</option>
      </select>
    </div>

    <fieldset class="firmware-picker-control firmware-picker-radio-control firmware-picker-wide" data-radio-field="hardware" data-role="hardware-variant-control" disabled hidden>
      <legend>Hardware variant</legend>
      <div class="firmware-picker-radio-options" data-field="hardware">
        Choose hardware first
      </div>
    </fieldset>

    <fieldset class="firmware-picker-control firmware-picker-radio-control firmware-picker-wide" data-radio-field="install" disabled>
      <legend>Install operation</legend>
      <div class="firmware-picker-radio-options" data-field="install">
        Loading install choices...
      </div>
    </fieldset>

    <fieldset class="firmware-picker-control firmware-picker-radio-control" data-radio-field="role" disabled>
      <legend>Firmware role</legend>
      <div class="firmware-picker-radio-options" data-field="role">
        Loading roles...
      </div>
    </fieldset>

    <fieldset class="firmware-picker-control firmware-picker-radio-control" data-radio-field="logging" disabled>
      <legend>Logging / MQTT</legend>
      <div class="firmware-picker-radio-options" data-field="logging">
        Loading logging choices...
      </div>
    </fieldset>

    <fieldset class="firmware-picker-control firmware-picker-radio-control firmware-picker-wide" data-radio-field="firmwareProfile" aria-describedby="firmware-picker-profile-help" disabled>
      <legend>Firmware profile</legend>
      <p id="firmware-picker-profile-help" class="firmware-picker-control-help">Each button combines OTA support with the feature, sensor, and storage choices for that image. Reduced optional sensors means fewer environmental/ranging drivers; I2C and supported board peripherals remain available.</p>
      <div class="firmware-picker-radio-options" data-field="firmwareProfile">
        Loading firmware profiles...
      </div>
    </fieldset>

    <fieldset class="firmware-picker-control firmware-picker-radio-control firmware-picker-wide" data-radio-field="mode" disabled>
      <legend>Connection / bridge mode</legend>
      <div class="firmware-picker-radio-options" data-field="mode">
        Loading modes...
      </div>
    </fieldset>

    <div class="firmware-picker-form-actions firmware-picker-wide">
      <button type="reset" data-action="clear" disabled>Clear all choices</button>
      <button type="button" data-action="copy-link" disabled>Copy link to settings</button>
      <a data-role="share-link" hidden>Link to these settings</a>
      <p data-role="link-status" aria-live="polite"></p>
    </div>
  </form>

  <section class="firmware-picker-result" data-role="bootloader" aria-live="polite" hidden>
    <h2 data-role="bootloader-title">nRF52 bootloader</h2>
    <div data-role="bootloader-content"></div>
  </section>

  <div class="firmware-picker-status" data-role="status" aria-live="polite">
    Loading the current firmware catalog...
  </div>

  <section class="firmware-picker-result" data-role="result" aria-live="polite" hidden>
    <p class="firmware-picker-eyebrow" data-role="result-eyebrow">Exact firmware match</p>
    <h2 data-role="result-title">Recommended download</h2>
    <p data-role="result-note" hidden></p>
    <div data-role="result-list"></div>
  </section>

  <section class="firmware-picker-missing" data-role="missing" aria-live="polite" hidden>
    <h2>No exact firmware matched</h2>
    <p data-role="missing-text"></p>
    <a href="https://github.com/mikecarper/MeshCore/releases">Browse all firmware releases</a>
  </section>

  <details class="firmware-asset-browser">
    <summary>Advanced: search current release filenames</summary>
    <p>
      Use this for uncommon board suffixes or expert recovery. A filename match
      is not a board-identity check.
    </p>
    <label>
      Filename contains
      <input data-field="asset-search" placeholder="Station_G2, heltec_v4, RAK_4631, ...">
    </label>
    <div data-role="asset-results"></div>
  </details>
</div>

## What the choices mean

| Choice | Use |
| --- | --- |
| Companion | A phone, computer, or host application controls the radio |
| Repeater | Standalone mesh relay |
| Room Server | Hosts room conversations and history |
| Sensor / telemetry | Publishes supported sensor data |
| Terminal Chat | Standalone serial-terminal interface |
| USB logging / USB-connected MQTT | Node remains attached to a computer over a data-capable USB cable |
| Wi-Fi MQTT observer | Firmware connects directly to MQTT over Wi-Fi; this is not USB logging |
| USB logging + Wi-Fi MQTT | Unified FULL image sends to both paths; avoid two publishers aimed at the same broker unless messages are deduplicated |
| No logging | Normal standalone operation with external logging output disabled |
| Receives LoRa OTA | Repeater, room server, or sensor image that can stage an exact matching update received over LoRa; any sensor/storage tradeoff appears on the same Firmware profile button |
| Receives LoRa OTA - Reduced optional sensors | Compact OTA image that omits selected optional environmental/ranging drivers while retaining generic I2C and supported board peripherals |
| Reduced sensors + LoRa OTA | Qualified nRF52 repeater, room server, or sensor build with the reduced optional sensor recipe and LoRa OTA retained |
| Full supported sensors + LoRa OTA | Qualified nRF52 build with all sensor drivers supported by that board's full recipe and LoRa OTA retained; does not add support for every possible attached sensor |
| LoRa OTA source only | Full Companion serving a host-supplied update to another node without self-installing it |

Connection and bridge choices depend on the selected role. Companion firmware
may offer Full, combined USB + Bluetooth, Bluetooth, USB, Wi-Fi, serial, or
Ethernet transports.
Normal repeater firmware includes runtime-controlled RS-232 support where the
board has room; use `set bridge.enabled on` after configuring `bridge.uart` and
`bridge.baud`. The Wio-E5 remains the capacity exception and offers a separate
RS-232 image. Choose Wi-Fi MQTT in the result's saved logging settings when
it shares the Full image; separate MQTT images retain a **Logging / MQTT**
filter. Full ESP32 LoRa repeaters include runtime
ESP-NOW when capacity permits; dedicated ESP-NOW images remain for measured
capacity exceptions. Ethernet still needs its exact hardware recipe.

For commands and option explanations, follow the
[ESP-NOW bridge setup guide](espnow_bridge_setup.md).

An ESP-NOW bridge target keeps LoRa as its primary mesh radio. Expanded ESP32
Full LoRa repeaters include ESP-NOW, including boards without an MQTT
recipe. Existing MQTT-backed Full repeater and room-server images combine
both transports. Use `set espnow.enabled on|off` for ESP-NOW and, where
supported, `set mqtt.enabled on|off` for MQTT. Newly combined ordinary
repeaters start with ESP-NOW off until explicitly enabled, including the first
upgrade from unmarked legacy preferences. Later reboots preserve that setting.
On combined RS-232 boards without MQTT, `bridge.enabled` controls the UART.
Full Heltec V3, WSL3 and RAK3112 MQTT images also retain RS-232;
use `set rs232.enabled on|off` and `get rs232.running` for their independent
UART. T-LoRa V2.1 keeps two Full choices due to its internal RAM limit:
the normal repeater has UART plus ESP-NOW, and the observer has MQTT plus
ESP-NOW. MQTT and ESP-NOW use one 2.4 GHz radio, so when both are enabled the ESP-NOW bridge channel must
match the connected WiFi access point's fixed channel. ESP-NOW-only mode does
not require WiFi credentials. Its runtime
`bridge.format` setting chooses the peer protocol: `wrapped` (the
backward-compatible bridge-to-bridge default using `bridge.secret`) or `raw`
(direct MeshCore ESP-NOW LR frames for `Generic_ESPNOW`,
`SenseCapIndicator-ESPNow`, and other primary-ESP-NOW nodes). This is one
firmware choice, not two board images. Match `bridge.channel` to the primary
nodes' `espnow.channel` before selecting `set bridge.format raw`.
For Heltec V4 specifically, `companion_radio_full` is still a LoRa-primary
Companion; choose the combined `heltec_v4_repeater` Full firmware and enable
ESP-NOW to make that board the LoRa/ESP-NOW gateway. Use its exact merged artifact when
changing roles or partition layouts.

## Share a selection

Select any combination of choices, then copy the address bar or use **Copy link
to settings**. Opening the link restores those choices after the release
catalog loads. Partial selections work too, so you can share a board and role
while leaving other choices open. **Clear all choices** removes the picker
parameters from the URL. Changes update the current browser-history entry
without reloading the page or adding a Back-button entry for every click.

For example, [RAK3401 repeater with internal storage](?hardware=RAK_3401&role=repeater&ota=lora-receiver&variant=no-external-sensors)
preselects that board, role, OTA capability, and storage profile.

The query parameters are `chipFamily`, `hardwareFamily`, `hardware`, `role`,
`logging`, `ota`, `mode`, `feature`, `variant`, and `install`. Values use the
picker's internal identifiers rather than the displayed labels. One Firmware
profile button sets `ota`, `feature`, and `variant` together. Existing links
with only some of those choices still work and show their partial selection.
The generated
link also records `chipAuto` so automatic chip-family selection or an explicit
**Any** choice behaves the same after reopening. Existing section anchors and
unrelated query parameters are preserved.

Links use the current release catalog. If a linked choice is no longer available
or conflicts with another choice, the picker identifies it and asks you to
review the remaining selections. The downloadable HTML's **Copy link to
settings** button creates a public website link that other people can open.

## FULL versus standard

Current ESP32 ordinary releases use one Full / complete image per exact board
and role. Full keeps the supported feature set and CLI; runtime switches select
USB, Bluetooth, Wi-Fi, logging, and other qualified features. Separate portable,
minimal, and single-transport ESP32 images are installation/recovery recipes,
not additional ordinary release choices. The documented T-LoRa UART/MQTT RAM
split remains two Full images. Other chip families retain their qualified
capacity and sensor-profile choices.

Moving an older ESP32 installation to an expanded Full layout requires a
partition migration.
Use the exact-board merged image over USB, or a supported exact board and role
staged migration ZIP from the [utility release](https://github.com/mikecarper/MeshCore/releases/tag/utility-v1.17.1.9-halo-keymind-cascade-dev-88c85108).
Read that ZIP's README before choosing the Wi-Fi or LoRa route. Do not send a
loose app-only Full `.bin` directly to an older layout: the running application
cannot move its own active and inactive partitions. Run
[`get storage.layout`](cli_commands.md#show-the-storage-layout) on the installed
firmware if available. It reports the live partition table; a version-based
guess cannot confirm the installed layout. See the
[partition lookup guide](esp32_partition_catalog.md) when the old firmware lacks
that command.

Current `full-usb-wifi` profiles use one binary for no external output, USB
packet logging/USB-connected MQTT, direct WiFi MQTT, or both. The picker skips
the logging filter when these modes use the same file; select the saved
runtime mode with `set logging.output off|usb|wifi|both`. A FULL logging-fallback
profile is listed only when no WiFi MQTT sibling exists; it supports both
the no-output and USB settings because `set usb.logging off|on` is persistent.
On a fresh unified FULL install with no saved SSID, the setup AP and WiFi radio
remain available for 30 minutes per boot, then turn off automatically until the
next reboot or power cycle. An explicit administrator `start webconfig` remains
available as an override. A saved SSID switches to the normal indefinite
reconnect behavior instead.

Fresh ESP32 Full infrastructure defaults to USB logging off. Existing saved
logging and device power-saving settings survive an update. Automatic
unconfigured boot setup also pauses network-bridge retries until an explicit
bridge start, allowing its radio to turn off when the AP closes. Manual
WebConfig/WiFi starts preserve an explicitly enabled ESP-NOW bridge, including
when the master radio switch restores the selected services. Compact OTA
images scan before raising a new AP and verify live driver readiness before
reporting the update URL.

Full Companion profiles use one binary for USB, BLE, ordinary Wi-Fi on ESP32,
source-only LoRa OTA, Terminal Chat, optional USB packet logging, and any
board-qualified serial or Ethernet Companion transport. Bulk builds therefore
omit separate attached-transport, Terminal Chat, and USB-logging artifacts
whenever the exact Full recipe exists. RAK4631 repeater and room-server
Ethernet images remain separate roles. Fresh installs default to logging off.
Heltec V3 and base OLED V4 Full images also contain the former direct Wi-Fi
MQTT Companion capability, configured at runtime through WebConfig, so their
separate `companion_radio_wifi_mqtt` artifacts are omitted from canonical
builds as well.

When Full Companion does not fit but a matching USB Companion does, that USB
artifact also supplies Terminal Chat and replaces its standalone release image.
Heltec E290 and T190 now publish one Full USB + BLE + WiFi Companion; their old
combined and single-transport names are explicit-build compatibility aliases.
SSD1306 Full Companion builds use `set display.rotation 90|180|270`; `0`
restores the board default, so a separate rotated release image is not
recommended.

Ordinary non-OTA roles also use one artifact for normal operation and USB
logging. On ESP32 1.17.1.5, run `set powersaving off` before `set usb.logging on`.
Select the saved mode with `set usb.logging off|on`; no `-logging-`
artifact is emitted. KISS, BLE-only Companion, and constrained LoRa OTA
repeater images retain their protocol/partition contracts and do not inherit
plaintext USB logging.

nRF52 Full Companion keeps the multi-role primary interface on `00`; it starts
as an ASCII terminal and automatically hands a complete `<` frame to Binary
Companion. `set usb.logging on reboot` adds its plaintext interface `02`.

Every ESP32 Full Companion instead exposes one USB TTY. Logging is off by
default, so the TTY serves the ASCII/Binary Companion switcher. On 1.17.1.5,
run these two text commands to enable USB logging:

```text
set powersaving off
set usb.logging on
```

The second command turns that same TTY into an input-capable plaintext
CLI/logging stream; framed Binary Companion is unavailable on USB while
logging owns it. `set usb.logging off` stops the logs and leaves the TTY in
the normal ASCII terminal, matching a fresh Full installation. Send
`+++MESHCORE-TERM-STOP`, or let a Companion app send a valid framed probe, to
switch from there to Binary Companion. BLE and Wi-Fi Companion remain usable
while the USB TTY is logging. ESP32 Full builds use the repository's
Arduino-ESP32 2.x base where the board supports it; RC32 and ESP32-C6 retain
their board-required Arduino 3.x platform but still expose only one USB TTY.
A second ESP32 CDC interface is not part of the release profile.

The picker includes the power-saving workaround when selecting **USB** or
**USB + WiFi** logging on ESP32 1.17.1.5. WiFi/MQTT-only logging does not need
it while the Repeater/Room Server bridge is running; check `get mqtt.running`.
The workaround is not added to nRF52 directions. See [logging by role](role_feature_switches.md)
for the saved settings and the original firmware's USB sleep issue.

## Companion USB terminal on Linux

Full, USB and USB + Bluetooth Companion results include **Companion USB terminal
(Linux / Bash)** under **Restore your settings after flashing**, with copyable
connection and recovery commands.

Updated 1.17.1.6 USB Companions default to ASCII at boot and after an observable
USB session reset. `picocom -b 115200 /dev/ttyACM0` is sufficient for a fresh
session. A valid framed app command automatically selects Binary Companion.
The command below also works with older firmware or a port left in Binary mode.

Run this in Bash on the Linux computer connected to your radio:

```bash
picocom -b 115200 --imap spchex \
  --initstring $'\r+++MESHCORE-TERM-START\r' \
  /dev/ttyACM0
```

Replace `/dev/ttyACM0` with your radio's port, such as `/dev/ttyACM1` or
`/dev/ttyUSB0`. Use the primary USB data interface (`00` when multiple interfaces
appear), and close other apps using it. The init string requests the ASCII CLI;
its leading carriage return clears an unfinished input line. `--imap spchex`
displays binary control bytes safely during the transition.

If the terminal display is scrambled, exit picocom with **Ctrl+A, then Ctrl+X**,
run `reset` in your Linux shell, then reconnect with the command above. At the
radio prompt, run `version` and `board` to identify the node. See the
[Companion USB mode guide](terminal_chat_cli.md#companion-usb-mode).

## Companion tempradio2 transmit settings

LoRa Companion results for 1.17.1.6 and newer include **Companion tempradio2:
transmit on both** under **Restore your settings after flashing**. After
configuring `tempradio2` in `rxtx` mode, choose **Transmit on both** to copy:

```text
set radio2.cross on
```

This copies new messages to both `radio` and `tempradio2`. The setting is saved,
outlasts the temporary session and reboot, and also allows OTA traffic to cross
profiles. **Default isolation** copies `set radio2.cross auto`; **Check** shows
the temporary profile, crossing policy and per-profile transmit counters.
See the [two-profile setup guide](radio_profiles.md#companion-messages-on-both-profiles).

## Installation methods

| File | Use |
| --- | --- |
| <code>-merged.bin</code> | Erase/fresh install, recovery, role migration, or partition-profile change on ESP32 over USB |
| Non-merged <code>.bin</code> | Update an existing same-board, same-role, same-partition installation |
| <code>*-migration.zip</code> | Exact ESP32 board and role staged partition migration; follow its included README for Wi-Fi or LoRa steps |
| Other <code>.zip</code> firmware shown by the picker | Native nRF52 Serial DFU update package; it is not an extra archive |
| <code>.uf2</code> | UF2 bootloader drag-and-drop install or update |
| <code>.hex</code> | Erase/recovery flash with a supported wired programmer |

Never send a merged ESP32 image through browser OTA or LoRa OTA. Back up the
node identity, configuration, keys, and radio settings, and verify every
filename suffix before flashing. A staged migration writes the partition table;
power loss during that write can require cable recovery. The package README
states what each route preserves and which older layouts it accepts.

## LoRa OTA and OTAFIX

A LoRa OTA capable build can receive and stage updates for its role. A later
LoRa update still needs an exact target identity,
compatible partition signature, matching radio settings, and the correct
update package.

Selecting an nRF52 board shows its direct **Bootloader UF2**, **Bootloader DFU
ZIP**, and **Bootloader HEX (SWD)** downloads, when an exact published profile
is available. ESP32 and other chip families do not show this section. The web
picker checks the latest stable OTAFIX release; the downloadable picker
includes the bootloader release named in its saved catalog. If a board has no
verified mapping or a file is missing, the picker explains that instead of
offering another board's bootloader. OTAFIX 2.4.11 publishes a combined
MeshTower V2 SD/internal image, mapped only to `Heltec_tower_v2_sdcard`.
The historical `Heltec_tower_v2` internal-only profile is explicitly retired:
move to the normal combined image with a one-time local USB/BLE DFU or SWD
installation, not a cross-profile LoRa bootloader update. The picker does not
silently substitute its bootloader. RAK3401 and RAK4631 each use their
board-specific adaptive storage bootloader.
Ikoka Stick, Nano and Handheld plus SolarXiao use the XIAO bootloader;
Wio Tracker L1 1W and E-Ink use the L1 bootloader. See the
[board coverage audit](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/blob/feature/ota-delta-apply/docs/meshcore-hardware-coverage.md)
for the source checks and boards still awaiting a dedicated, tested release.

Board mappings and file names, URLs, sizes and SHA-256 hashes come from
`bootloader-manifest.json` in the
[OTAFIX release](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/latest).
Application UF2 and DFU files do not install a bootloader. Follow the bootloader
release's migration instructions if the installed version needs a recovery bridge.

nRF52 LoRa OTA requires an OTAFIX bootloader built for the exact board and
storage layout. Select the hardware-matched HEX, Serial DFU ZIP, or
bootloader-update UF2 for the application image being installed.

## Hardware and variant names

Hardware families with multiple released targets get **Hardware variant**
buttons. They separate revisions, display type, expansion kit, radio/PA layout,
pin map, and other physical differences without crowding the Hardware dropdown.
**Firmware profile** buttons combine OTA support, Full/standard, and choices
that need a different image or wiring, such as serial port or external storage.
The picker skips this step when all available profile choices lead to the
same firmware file.
The 1.17.1.9 nRF52 repeater, room-server and sensor releases publish two separately
qualified choices: **Reduced sensors + LoRa OTA** and
**Full supported sensors + LoRa OTA**, with `-reduced-ota` and `-full-ota` artifact suffixes. Each keeps
its exact board/role OTA identity and storage layout; the suffix is not a new
radio or partition identity. Labels come from the release's qualified
`sensor.profile.reduced` / `sensor.profile.full` metadata and verified LoRa
receiver, not from the filename alone. Both profiles remain separate in local
and published releases, including boards whose optional sensor set is small
enough that the images have little or no size difference. Packaging requires
both profiles with matching packaged OTA identity and flash geometry, even
when a matrix is resumed or partial publication is requested. A failed build
is reported, never presented as a supported download. Companion is unchanged.
The legacy `no_external_sensors` profile is labeled **Receives LoRa OTA -
Reduced optional sensors** on the same button. On RAK3401 and RAK4631 this
compact profile uses internal storage without an external storage board. The
reduction does not disable generic I2C or unrelated board-integrated peripherals.
Reduced RAK3401 and RAK4631 targets retain INA219/INA226/INA260/INA3221 as
voltage/current entries in the optional sensor table. They are not the only I2C
users: the SSD1306 OLED, supported autodiscovered RTCs, and RAK12500 GPS remain
separate I2C peripherals in compatible recipes. RAK12501/L76K GPS uses Serial1
instead. The explicit RAK4631 Serial1 bridge omits the combined GPS provider
because its bridge owns the RAK12501 UART, so that legacy image does not expose
RAK12500 either. The firmware-configured INA3221 and RAK12500 addresses are both
`0x42`; to install both, keep RAK12500 at `0x42`, strap INA3221 A0 to SCL for
`0x43`, and use a build with `-DTELEM_INA3221_ADDRESS=0x43`. Companion power saving,
controllable FEM receive gain, and radio-chip receive gain are saved settings
rather than separate recommended firmware files. Do not substitute a similarly
named physical target.

The header-wired RAK19007 W25Q16 LoRa-OTA recipes are also exact hardware
variants, labeled **External storage board (W25Q16)** in the picker. W25Q16 is
the flash-memory part on the added storage board; RAK13302 identifies the radio
module, not the storage board. Choose this variant for RAK4631 or for RAK3401 + RAK13302 only
when that core/radio combination, the `EF4015` flash wiring, and its matching
OTAFIX bootloader are all present. The common base-board wiring does not make
the two firmware or bootloader identities interchangeable.

The picker recommends one Full Companion image instead of separate USB, BLE,
ordinary WiFi, and USB-logging images. On ESP32, logging is off by default so
the one USB TTY starts in ASCII and automatically changes to framed Companion
when a complete `<` frame arrives. Enabling logging gives that TTY to the
plaintext CLI/logger and disables framed USB Companion until logging is turned
off and the normal ASCII-to-binary mode switch occurs. nRF52 Full Companion
retains its optional dedicated interface `02`.
Exact filename search finds compatibility images included in the selected
release family. For older releases, open their GitHub release pages.

The current public matrix keeps one ESP32 Full image per board and role.
Some richer images, including Station G2 Full, retain an observer-named OTA
identity. Exact older identities needed by a partition migration are packaged
inside that board and role's migration ZIP instead of adding a second ordinary
Full download. A deliberate target-ID override does not make a different
partition layout or physical board compatible.

Some normal ESP32 roles begin in the legacy 1.25 MiB dual-OTA layout but have
a Full image with larger slots. The utility release lists the exact
board and role migration ZIPs available for that release. Each package checks its source layout and target
identity, contains the bridge and Full application files for its supported
routes, and documents what saved data it preserves. Use the package's own Full
application after the bridge; do not substitute a loose Full image with a
different target ID. If no exact package exists, use the matching merged USB
image. Once migrated, routine app-only updates still need the same target and
partition signature.

## Maintaining the runtime directions

The online picker and downloadable HTML use the same command renderer.
Hardware-specific controls are enabled only when `_data/firmware_controls.json`
matches the selected release family and exact target. If that metadata is
missing or belongs to another release, the picker retains basic role/logging
directions and links the complete guide without inventing hardware support.
For 1.17.1.6, that JSON also records the verified LoRa OTA role of each image
and USB logging modes where the packaged application was checked. Logging
metadata is tied to the source hash of its firmware file. The normal iKoka
30 dBm repeater has USB logging and LoRa OTA; its compact no-external-sensors
image has LoRa OTA without logging.
Capacity directions come from each qualified profile's recorded reductions and
are tied to the source hash of the selected download. The older 1.17.1.5 USB
sleep workaround stays limited to that release.

After qualifying a new release, resolve its PlatformIO configuration with no
other PlatformIO process running, then generate the controls from that source
revision and its staged manifests:

```bash
pio project config --json-output > /tmp/meshcore-picker-pio-config.json
python3 scripts/generate_picker_controls.py \
  --stage /path/to/staged-release \
  --pio-config /tmp/meshcore-picker-pio-config.json
```

Refresh the `data-controls-url` version query when publishing the generated
metadata so browsers fetch the new release's controls.

The generated `partitionMigrations` lookup comes from `BOARDS` in
`scripts/package_esp32_partition_migration.py`, the same exact board/role table
used to build the migration packages. Expanded ESP32 Full results use this
lookup (including canonical board/role recipes for Observer profiles), then
require one matching published ZIP in the same release family's utility page.
Hardware variants and roles remain distinct. Unlisted targets, missing or
ambiguous assets, and stale metadata never produce a guessed download link.

For the downloadable version, save the release family's public GitHub release
objects as a JSON array, then package the same picker UI and controls:

```bash
python3 scripts/package_firmware_picker.py \
  --releases-json /path/to/releases.json \
  --output /path/to/FIRMWARE-PICKER.html
```

This HTML embeds its catalog and directions, so selections work without an
internet connection. Firmware downloads and the USB web console still need
network access. When replacing the downloadable picker on release pages,
update its entry in each page's `SHA256SUMS.txt` as well.


## Memory-corrected 1.17.1.5 downloads

Corrected downloads retain the 1.17.1.5 release page and use source suffix
`aa20e927`, with `1e4d1e16` for Wireless Paper Full's 350-contact follow-up.
The picker accepts replacement source hashes within that exact
release version. Its installation directions identify the nRF52 queue-sharing
behavior, Wireless Paper Full's 350 contacts with a shared 256/128-slot queue,
and the 150-contact limit on the other six affected ESP32 Full profiles.
Those notices apply only to the corrected files. See the
[memory correction details](old-releases/1.17.1.5.md#memory-corrections-in-aa20e927)
and each replacement's `.memory.json` report before updating.
