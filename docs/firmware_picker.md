# Firmware picker

Pick the choices in any order. Every selection narrows all the other controls
to firmware combinations that were actually built in the current release set.

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
      <strong>Erase &amp; fresh install (merged .bin)</strong>. Narrower profiles
      remain available when their reduced transport or feature set is intentional.
    </p>
  </div>

  <p class="firmware-picker-order-note">
    Pick in any order. Use <strong>Any</strong> to clear one choice, or clear
    everything with the button below.
  </p>

  <form class="firmware-picker-form" data-role="form">
    <div class="firmware-picker-control firmware-picker-select-control">
      <label for="firmware-picker-hardware-family">Hardware</label>
      <select id="firmware-picker-hardware-family" data-field="hardwareFamily" disabled>
        <option value="">Loading hardware...</option>
      </select>
    </div>

    <div class="firmware-picker-control firmware-picker-select-control" data-role="hardware-variant-control" hidden>
      <label for="firmware-picker-hardware">Hardware variant</label>
      <select id="firmware-picker-hardware" data-field="hardware" disabled>
        <option value="">Choose hardware first</option>
      </select>
    </div>

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

    <fieldset class="firmware-picker-control firmware-picker-radio-control" data-radio-field="ota" disabled>
      <legend>OTA capability</legend>
      <div class="firmware-picker-radio-options" data-field="ota">
        Loading OTA choices...
      </div>
    </fieldset>

    <div class="firmware-picker-control firmware-picker-select-control">
      <label for="firmware-picker-mode">Connection / bridge mode</label>
      <select id="firmware-picker-mode" data-field="mode" disabled>
        <option value="">Loading modes...</option>
      </select>
    </div>

    <fieldset class="firmware-picker-control firmware-picker-radio-control" data-radio-field="feature" disabled>
      <legend>Feature profile</legend>
      <div class="firmware-picker-radio-options" data-field="feature">
        Loading profiles...
      </div>
    </fieldset>

    <div class="firmware-picker-control firmware-picker-select-control">
      <label for="firmware-picker-variant">Firmware variant</label>
      <select id="firmware-picker-variant" data-field="variant" disabled>
        <option value="">Loading variants...</option>
      </select>
    </div>

    <div class="firmware-picker-form-actions firmware-picker-wide">
      <button type="reset" data-action="clear" disabled>Clear all choices</button>
    </div>
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
| LoRa OTA enabled / receiver | Install-capable profile that can stage an exact matching update received over LoRa |
| LoRa OTA source only | Full Companion serving a host-supplied update to another node without self-installing it |

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
| <code>-merged.bin</code> | Erase/fresh install, recovery, role migration, or partition-profile change on ESP32 over USB |
| Non-merged <code>.bin</code> | Update an existing same-board, same-role, same-partition installation |
| <code>.zip</code> | Native nRF52 Serial DFU update package; it is not an extra archive |
| <code>.uf2</code> | UF2 bootloader drag-and-drop install or update |
| <code>.hex</code> | Erase/recovery flash with a supported wired programmer |

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

Hardware families with multiple released targets get a second hardware-variant
menu. It separates revisions, display type, expansion kit, radio/PA layout,
pin map, and other physical differences without crowding the first menu. The
firmware-variant menu separately exposes build choices such as FEM on/off,
power saving, serial port, or no-external-sensors. Do not substitute a
similarly named target.
