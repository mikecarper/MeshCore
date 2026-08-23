# Firmware picker

Choose the exact hardware first. The remaining menus then show only firmware
roles and features that were actually built for that hardware in the current
release set.

The picker reads public release metadata from GitHub. It does not upload device
information. Hardware names, target names, and download links come directly
from the published firmware assets.

<div class="firmware-picker" data-firmware-picker data-release-repo="mikecarper/MeshCore">
  <div class="firmware-picker-intro" role="note">
    <strong>Current release set</strong>
    <p data-role="release-set">Loading release information...</p>
    <p>
      For a new installation, choose the exact board and role, prefer a
      <strong>FULL / complete profile</strong> when it is available, and select
      <strong>USB first install / recovery</strong>. Narrower profiles remain
      available when their reduced transport or feature set is intentional.
    </p>
  </div>

  <form class="firmware-picker-form" data-role="form">
    <label>
      1. Hardware
      <select data-field="hardware" disabled>
        <option value="">Loading hardware...</option>
      </select>
    </label>

    <label>
      2. Firmware role
      <select data-field="role" disabled>
        <option value="">Choose hardware first</option>
      </select>
    </label>

    <label>
      3. Logging / MQTT
      <select data-field="logging" disabled>
        <option value="">Choose role first</option>
      </select>
    </label>

    <label>
      4. OTA capability
      <select data-field="ota" disabled>
        <option value="">Choose earlier options first</option>
      </select>
    </label>

    <label>
      5. Connection / bridge mode
      <select data-field="mode" disabled>
        <option value="">Choose earlier options first</option>
      </select>
    </label>

    <label>
      6. Feature profile
      <select data-field="feature" disabled>
        <option value="">Choose earlier options first</option>
      </select>
    </label>

    <label>
      7. Hardware/profile variant
      <select data-field="variant" disabled>
        <option value="">Choose earlier options first</option>
      </select>
    </label>

    <label>
      8. Installation method
      <select data-field="install" disabled>
        <option value="">Choose firmware options first</option>
      </select>
    </label>
  </form>

  <div class="firmware-picker-status" data-role="status" aria-live="polite">
    Loading the current firmware catalog...
  </div>

  <section class="firmware-picker-result" data-role="result" aria-live="polite" hidden>
    <p class="firmware-picker-eyebrow">Exact firmware match</p>
    <h2>Recommended download</h2>
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
| No logging | Normal standalone operation without the dedicated logging/MQTT profile |
| LoRa OTA receiver | Repeater profile that can stage an exact matching update received over LoRa |
| LoRa OTA source | Full Companion serving a host-supplied update to another node |
| OTA-enabled profile | Build uses an OTA-capable application/partition profile, but is not necessarily an explicit LoRa receiver |

Connection and bridge choices depend on the selected role. Companion firmware
may offer Full, Bluetooth, USB, Wi-Fi, serial, or Ethernet transports.
Repeaters may offer standard, ESP-NOW bridge, RS-232 bridge, Ethernet, or MQTT
observer modes.

## FULL versus standard

For a new installation, use the FULL / complete profile when it exists and the
board has enough flash. FULL profiles keep the complete supported feature set
and CLI. Standard profiles remain useful for boards without a FULL build, for
an intentionally narrower transport, or when retaining an existing compatible
partition layout.

Changing between standard and FULL ESP32 layouts requires the exact-board
merged image over USB. A running application cannot safely move its own active
and inactive partitions.

## Installation methods

| File | Use |
| --- | --- |
| <code>-merged.bin</code> | ESP32 first install, recovery, role migration, or partition-profile change over USB |
| Non-merged <code>.bin</code> | Same-board, same-role, same-partition application update |
| <code>.zip</code> | Native nRF52 Serial DFU package; it is not an extra archive |
| <code>.uf2</code> | UF2 bootloader drag-and-drop installation |
| <code>.hex</code> | Full wired programmer or recovery flash |

Never send a merged ESP32 image through browser OTA or LoRa OTA. Back up the
node configuration and verify every filename suffix before flashing.

## LoRa OTA and OTAFIX

An OTA receiver build installs the receiving/staging firmware. A later LoRa
update still needs an exact target identity, compatible partition signature,
matching radio settings, and the correct update package.

nRF52 LoRa OTA requires an OTAFIX bootloader built for the exact board. There
is no universal bootloader file. Use the
[latest stable OTAFIX release](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/latest)
and select the hardware-matched HEX, Serial DFU ZIP, or bootloader-update UF2.

## Hardware and variant names

The hardware menu preserves meaningful board suffixes such as display type,
radio chip, PA/FEM layout, pin map, and external-flash variant. Later menus
expose build variants such as FEM on/off, power saving, serial port, or
no-external-sensors when those choices exist. Do not substitute a similarly
named target.
