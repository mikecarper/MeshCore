# Firmware picker

Choose what the board will do, then let the picker reduce the release assets
to one recommended firmware. The first guided board is Station G2 because its
standard, logging, FULL, LoRa-OTA, MQTT, bridge, room-server, and Companion
profiles are easy to confuse.

The picker reads public release metadata from GitHub. It does not upload any
device information.

<div class="firmware-picker" data-firmware-picker data-release-repo="mikecarper/MeshCore">
  <div class="firmware-picker-intro" role="note">
    <strong>Station G2 shortcut</strong>
    <p>
      If the G2 normally stays connected to a computer and you want USB packet
      logs, choose <strong>USB logging repeater</strong>. The recommended image
      is the expanded <strong>FULL logging</strong> profile. The smaller legacy
      logging image is mainly a compatibility fallback, not the normal choice
      for a computer-powered logger.
    </p>
  </div>

  <form class="firmware-picker-form" data-role="form">
    <label>
      Release channel
      <select data-field="channel">
        <option value="development" selected>Current development and pre-releases</option>
        <option value="stable">Stable releases</option>
      </select>
    </label>

    <label>
      What should the Station G2 do?
      <select data-field="purpose">
        <option value="usb-logging">USB logging repeater - recommended when connected to a computer</option>
        <option value="field">Simple standalone repeater</option>
        <option value="field-ota">Standalone repeater with LoRa/WiFi OTA, no external sensors</option>
        <option value="mqtt">Full MQTT observer/repeater</option>
        <option value="espnow">ESP-NOW bridge repeater</option>
        <option value="room">Room server</option>
        <option value="room-mqtt">Full MQTT room observer</option>
        <option value="companion-usb">USB Companion</option>
        <option value="companion-ble">Bluetooth Companion</option>
        <option value="companion-wifi">WiFi Companion</option>
        <option value="companion-full">Full Companion and LoRa-OTA source</option>
      </select>
    </label>

    <label>
      How will this firmware be installed?
      <select data-field="install">
        <option value="first">First install, recovery, or changing partition/profile - USB cable</option>
        <option value="wifi">Same profile and partition - browser/WiFi OTA</option>
        <option value="lora">Same profile and partition - LoRa OTA</option>
      </select>
    </label>
  </form>

  <div class="firmware-picker-status" data-role="status" aria-live="polite">
    Loading release catalog...
  </div>

  <section class="firmware-picker-result" data-role="result" aria-live="polite" hidden>
    <p class="firmware-picker-eyebrow">Recommended firmware</p>
    <h2 data-role="result-title"></h2>
    <p data-role="result-summary"></p>
    <dl class="firmware-picker-facts" data-role="result-facts"></dl>
    <div class="firmware-picker-actions">
      <a class="firmware-picker-primary" data-role="download" href="#">Download recommended file</a>
      <a data-role="release" href="#">Open its release</a>
    </div>
    <div class="firmware-picker-steps" data-role="steps"></div>
  </section>

  <section class="firmware-picker-missing" data-role="missing" aria-live="polite" hidden>
    <h2>The matching asset is not in this release channel yet</h2>
    <p data-role="missing-text"></p>
    <a href="https://github.com/mikecarper/MeshCore/releases">Browse all firmware releases</a>
  </section>

  <details class="firmware-asset-browser">
    <summary>Advanced: search all recent release filenames</summary>
    <p>
      This search is for uncommon hardware variants and expert recovery. A
      filename match is not a board-identity check.
    </p>
    <label>
      Filename contains
      <input data-field="asset-search" placeholder="Station_G2, heltec_v4, repeater, ...">
    </label>
    <div data-role="asset-results"></div>
  </details>
</div>

## What FULL means for a USB logger

A Station G2 that is powered from and monitored by a computer normally should
use the FULL logging profile:

1. Save its name, radio settings, keys, and other configuration.
2. Download the exact Station G2 `full-logging-ota` merged image recommended
   above.
3. Flash that `-merged.bin` over USB once. It installs the expanded partition
   table as well as the application.
4. Reconnect the serial terminal at 115200 baud and restore any settings that
   were not retained.
5. Leave the data-capable USB connection attached to collect logs. Use
   `set usb.logging off` temporarily if the runtime output needs to be quiet.

Choose the standalone or lean OTA repeater instead when the board is normally
battery/solar powered, has no computer collecting USB output, or must retain a
known legacy partition layout.

## Can a Heltec V4 partition be expanded by OTA?

No. WiFi OTA and LoRa OTA write an application into the inactive application
partition. They do not replace the partition table at flash offset `0x8000`.
Allowing a running application to move its own active/inactive partitions
would risk overwriting the running image, staged image, NVS, or filesystem.

The current Heltec V4 definition already uses its 16 MiB flash efficiently:

| Region | Size |
|---|---:|
| Application slot A | `0x640000` (6.25 MiB) |
| Application slot B | `0x640000` (6.25 MiB) |
| SPIFFS | `0x360000` (3.375 MiB) |

That is much larger than current MeshCore V4 applications. A different layout
is possible only as a custom build, and it requires a one-time exact-board
merged-image flash over USB. Expanding the app slots further would shrink or
remove SPIFFS, or remove the second slot and therefore remove safe OTA.

After installing a new partition table over USB, later WiFi or LoRa updates
must use a non-merged application built for the same board, role, and partition
signature. Never use an app-only FULL image to try to migrate a device that is
still running another partition layout.

## Merged versus non-merged files

| File | Use |
|---|---|
| `-merged.bin` | First install, recovery, role/profile migration, or partition-table change over USB |
| `.bin` without `-merged` | Browser/WiFi application update when the installed partition layout already matches |
| `.mota` | LoRa OTA package for the exact target identity and installed partition layout |

When uncertain, back up the configuration and use the exact-board merged image
over USB. Do not send a merged image through browser OTA or LoRa OTA.
