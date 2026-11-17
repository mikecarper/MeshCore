<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/browser_lora_ota/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Update remote firmware over LoRa

Connect a source radio by USB, Wi-Fi, or Bluetooth, choose the remote device, then prepare and start its update.
Keep the browser open until the new firmware is verified. This updater is experimental.
USB requires Chrome or Edge on a computer with a Companion or compatible Repeater.
Wi-Fi requires a compatible Companion on the same local network as your phone or computer.
Bluetooth requires a compatible Companion with Bluetooth OTA support. On Android, use Chrome.

<div data-lora-ota>
  <p data-role="workflow-status" class="lora-ota-notice" role="status" aria-live="polite">Connect your source radio to get started.</p>
  <label class="lora-ota-verbose-toggle"><input type="checkbox" data-role="verbose-errors" aria-controls="lora-ota-error-details"> Show detailed diagnostics</label>
  <aside data-role="verbose-error-panel" hidden role="region" aria-label="Detailed error diagnostics">
    <pre id="lora-ota-error-details" data-role="error-details">Detailed errors will appear here. Passwords and secret command arguments are hidden.</pre>
  </aside>
  <p data-role="browser-support" class="lora-ota-notice" role="alert" hidden></p>
  <section id="ota-connect" class="lora-ota-step" style="scroll-margin-top: 6rem;">
    <aside data-role="saved-update" class="lora-ota-notice" hidden>
      <p data-role="saved-update-status">An interrupted update is saved in this browser.</p>
      <small>Forgetting these files does not cancel the remote update.</small>
      <button type="button" data-role="saved-update-resume">Resume saved update</button>
      <button type="button" class="lora-ota-secondary" data-role="saved-update-forget">Forget saved update</button>
    </aside>
    <p data-role="resume-storage-warning" class="lora-ota-notice" role="status" hidden></p>
    <h2>1. Connect and choose the remote device</h2>
    <label>Connection
      <select data-role="connection"><option value="usb">USB</option><option value="wifi">Wi-Fi Companion</option><option value="bluetooth">Bluetooth Companion</option></select>
    </label>
    <section data-role="bluetooth-options" hidden>
      <p>Turn on Bluetooth, disconnect other apps from the Companion, and select <strong>Connect Bluetooth Companion</strong> to choose your device. The Companion needs firmware with Bluetooth OTA support.</p>
      <p data-role="bluefy-help" hidden>On iPhone or iPad, <a href="https://apps.apple.com/us/app/bluefy-web-ble-browser/id1492822055">install Bluefy</a>, then <a href="bluefy://open?url=https%3A%2F%2Fmikecarper.github.io%2FMeshCore%2Fbrowser_lora_ota%2F%3Fconnection%3Dbluetooth%23ota-connect">open this updater in Bluefy</a>. If the link does not open it, copy <code>https://mikecarper.github.io/MeshCore/browser_lora_ota/?connection=bluetooth#ota-connect</code> into Bluefy's address bar.</p>
    </section>
    <section data-role="wifi-options" hidden>
      <p>Scan your local network, or enter the Companion's Wi-Fi address. <a href="#wi-fi-companion-connection">Wi-Fi setup instructions</a>.</p>
      <details class="lora-ota-advanced"><summary>Find a Wi-Fi Companion</summary>
        <p>The browser will try to suggest your private network. If it cannot, enter your phone's Wi-Fi IPv4 address, such as 192.168.1.20, or its /24 prefix, such as 192.168.1.0/24. Browsers may hide the address for privacy.</p>
        <label>How to find your network address <select data-role="wifi-os"><option value="unknown">Choose your device</option><option value="apple">iPhone or iPad</option><option value="android">Android</option><option value="mac">macOS</option><option value="windows">Windows</option><option value="linux">Linux</option></select></label>
        <p data-role="wifi-os-help" aria-live="polite"></p>
        <p>Use this phone or computer's Wi-Fi IPv4 address to choose the network prefix; it is not the Companion's IP address. The scan covers one /24 network.</p>
        <label>Your Wi-Fi IPv4 address or /24 network <input data-role="wifi-subnet" type="text" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="192.168.1.20 or 192.168.1.0/24"></label>
        <p>Example networks: choose one that matches the first three numbers of your Wi-Fi address.</p>
        <p class="lora-ota-examples" aria-label="Example network addresses">
          <button type="button" class="lora-ota-secondary" data-wifi-example="192.168.1.1">192.168.1.1</button>
          <button type="button" class="lora-ota-secondary" data-wifi-example="192.168.0.1">192.168.0.1</button>
          <button type="button" class="lora-ota-secondary" data-wifi-example="10.0.0.1">10.0.0.1</button>
        </p>
        <p>Hotspot examples (the address can vary by phone):</p>
        <p class="lora-ota-examples" aria-label="Example hotspot addresses">
          <button type="button" class="lora-ota-secondary" data-wifi-example="172.20.10.1">iPhone: 172.20.10.1</button>
          <button type="button" class="lora-ota-secondary" data-wifi-example="192.168.43.1">Android: 192.168.43.1</button>
        </p>
        <label data-role="wifi-networks-wrap" hidden>Suggested network <select data-role="wifi-networks"><option value="">Select a network</option></select></label>
        <p class="lora-ota-actions"><button type="button" data-role="wifi-scan">Scan network</button> <button type="button" data-role="wifi-scan-stop" hidden>Stop scan</button></p>
        <p data-role="wifi-scan-status" role="status" aria-live="polite"></p>
        <label data-role="wifi-results-wrap" hidden>Companion found <select data-role="wifi-results"><option value="">Select a device</option></select></label>
      </details>
      <label>Companion IP address <input data-role="wifi-url" type="text" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="192.168.1.50"></label>
    </section>
    <details data-role="usb-options" class="lora-ota-advanced"><summary>USB connection options</summary>
    <label>USB device
      <select data-role="source"><option value="auto">Detect automatically</option><option value="companion">Companion</option><option value="repeater">Repeater</option></select>
    </label>
    </details>
    <p>The source radio sends the update over LoRa. Choose the remote device and enter its admin password if needed.</p>
    <p class="lora-ota-actions"><button type="button" data-role="connect">Connect USB source</button> <button type="button" data-role="reconnect" hidden disabled>Reconnect USB source</button> <button type="button" data-role="disconnect" class="lora-ota-secondary" disabled>Disconnect</button></p>
    <p data-role="source-info" role="status" hidden></p>
    <p data-role="ota-availability" class="lora-ota-availability" role="status" aria-live="polite" hidden></p>
    <p data-role="source-update-help" role="status" hidden>This source needs updated firmware for this OTA workflow. If it is still connected here, disconnect it before opening the flasher. <a data-role="source-update-link" href="https://mikecarper.github.io/meshfirmware/" target="_blank" rel="noopener noreferrer">Update source firmware</a></p>
    <p data-role="contact-status" role="status">Connect your source radio to choose the remote device.</p>
    <section data-role="target-controls" hidden>
    <label>Search contacts by name or public-key prefix<input data-role="contact-search" autocomplete="off" spellcheck="false" disabled></label>
    <p><strong>Device to update</strong></p>
    <p data-role="contact-route-help" hidden>Saved paths can be out of date. Incoming advert paths show how a recent advertisement reached this Companion; they are not verified return routes. The recent-advert cache is limited and clears on restart. Neither path confirms current reachability. Wi-Fi and Bluetooth Companion updates need a direct (zero-hop) connection; select Find matching firmware to contact the device and check compatibility.</p>
    <p data-role="advert-path-progress" role="status" hidden></p>
    <p><button type="button" class="lora-ota-secondary" data-role="advert-path-stop" hidden>Stop checking advert paths</button></p>
    <section data-role="contact" class="lora-ota-contacts" role="group" aria-label="Devices to update" hidden></section>
    <label>Admin password (optional)<input data-role="password" type="password" autocomplete="off" aria-describedby="lora-ota-password-help"></label>
    <small id="lora-ota-password-help">Leave blank to use existing ACL access or an active session.</small>
    <details class="lora-ota-advanced">
      <summary>Enter a public key manually</summary>
      <label>Target public key (64 hex characters)<input data-role="key" size="64" autocomplete="off" spellcheck="false"></label>
      <p>For a USB Repeater source, enter the remote device key here.</p>
      <p data-role="manual-contact-help" hidden>This Companion requires the remote device to be saved as a contact. Discover or add it in the MeshCore app, then reconnect here.</p>
    </details>
    </section>
    <section data-role="legacy-warning" hidden>
      <p>Checking the device reads this Companion's shared message queue. Pending unrelated messages will move from the Companion to this browser and cannot be restored to the Companion. They are stored unencrypted in this browser profile and can be downloaded below. Closing the browser while a message is being received can lose that message.</p>
      <p>Keep other apps disconnected while using this Companion.</p>
    </section>
    <p class="lora-ota-actions"><button type="button" data-role="investigate" disabled>Find matching firmware</button></p>
    <p data-role="investigation-progress" hidden><span data-role="investigation-progress-label" role="status" aria-live="polite"></span> <span data-role="investigation-progress-elapsed" aria-live="off"></span></p>
    <p><button type="button" class="lora-ota-secondary" data-role="investigation-stop" hidden>Stop investigation</button></p>
    <details data-role="investigation-details" hidden><summary>Device check details</summary><pre data-role="investigation" hidden></pre></details>
    <section data-role="legacy-inbox" hidden>
      <p data-role="legacy-inbox-status" role="status"></p>
      <details><summary>Messages saved in this browser</summary><pre data-role="legacy-inbox-messages"></pre></details>
      <button type="button" class="lora-ota-secondary" data-role="legacy-inbox-export">Download saved messages</button>
      <p>After downloading, you can clear these browser copies, including interrupted or possibly lost message records. They cannot be restored to the Companion.</p>
      <button type="button" class="lora-ota-secondary" data-role="legacy-inbox-clear" disabled>Clear saved browser copies</button>
    </section>
  </section>
  <section class="lora-ota-step">
    <h2>2. Prepare firmware</h2>
    <p data-role="legacy-source-block" hidden>This Companion uses its shared message queue and raw packets.</p>
    <p data-role="firmware-catalog-status" role="status">Select the remote device, then choose Find matching firmware.</p>
    <section data-role="firmware-role-wrap" hidden><label>Target type
      <select data-role="firmware-role"><option value="">Choose a type</option><option value="repeater">Repeater</option><option value="room">Room</option><option value="sensor">Sensor</option></select>
    </label></section>
    <section data-role="firmware-choice-wrap" hidden><label>Matching firmware
      <select data-role="firmware-choice"></select>
    </label></section>
    <p><button type="button" data-role="firmware-download" hidden disabled>Load selected firmware</button></p>
    <details class="lora-ota-advanced" data-role="manual-firmware"><summary>Use a local file or an installed firmware image</summary>
    <label>Choose a firmware file (.bin, .zip, or .mota)<input data-role="file" type="file" accept=".bin,.zip,.mota"></label>
    <p><a href="https://github.com/mikecarper/MeshCore/releases">Get firmware from GitHub releases</a></p>
    <p>To make a smaller differential update, also select the exact firmware currently installed on the target.</p>
    <label>Installed firmware (optional)<input data-role="base" type="file" accept=".bin,.zip"></label>
    <p data-role="firmware-base-status" role="status"></p>
    <label><input data-role="full-rebuild" type="checkbox"> Prepare a new full package instead of resuming</label>
    <p>A staged update is discarded only if you choose Replace and install update.</p>
    </details>
    <p>Prepare update checks the firmware and link, and finds a fitting update path. Intermediate updates, when needed, run automatically.</p>
    <p data-role="compression-summary">Transfer compression: Zopfli (1,000 iterations). Preparing the transfer can take a long time; choose fast compression under Update path options if preferred.</p>
    <label><input type="checkbox" data-role="quiet-forwarding" checked> Pause normal forwarding during the update, then restore it</label>
    <p>After radio2 is verified, radio1 floods pause. Older firmware pauses forwarding on both profiles. Keep this page open until forwarding is restored.</p>
    <p data-role="quiet-forwarding-status" role="status" hidden></p>
    <button type="button" data-role="restore-forwarding" class="lora-ota-secondary" hidden>Restore forwarding</button>
    <details class="lora-ota-advanced"><summary>Update path options</summary>
      <label>Transfer compression<select data-role="compression"><option value="zopfli" selected>Zopfli (1,000 iterations; slower preparation)</option><option value="native">Fast browser compression</option></select></label>
      <p>Zopfli prepares every block before the transfer and may take a long time for large updates, especially on older phones. It is used when the target supports compressed blocks. Path estimates use fast compression and are approximate.</p>
      <label><input type="checkbox" data-role="auto-search" checked> Compare compatible intermediate updates automatically</label>
      <label>Prefer<select data-role="auto-objective"><option value="steps">Fewest installs, then least data</option><option value="data">Least radio data, then fewest installs</option></select></label>
      <label>Search<select data-role="auto-depth"><option value="standard">Automatic</option><option value="deep">Deep search (takes longer)</option></select></label>
      <p>Deep search compares every page-aligned rewrite workspace and published bridge candidate for these exact images. Every patch must fit and reconstruct its complete firmware. Estimates exclude retries.</p>
    </details>
    <p class="lora-ota-actions"><button type="button" data-role="inspect" disabled>Prepare update</button> <button type="button" data-role="prepare-stop" class="lora-ota-secondary" hidden>Stop preparing</button></p>
  </section>
  <section class="lora-ota-step">
    <h2>3. Update the remote device</h2>
    <p data-role="update-summary">The selected firmware will be sent over LoRa, installed and verified automatically.</p>
    <p data-role="update-progress" role="status" aria-live="polite" hidden></p>
    <p class="lora-ota-actions"><button type="button" data-role="update" disabled>Update remote firmware</button> <button type="button" data-role="transfer-stop" class="lora-ota-secondary" hidden disabled>Stop transfer</button></p>
    <details class="lora-ota-advanced"><summary>Separate transfer, resume and install controls</summary>
    <p class="lora-ota-actions"><button type="button" data-role="transfer" disabled>Transfer</button> <button type="button" data-role="resume" class="lora-ota-secondary" disabled>Resume</button> <button type="button" data-role="install" disabled>Install and verify</button></p>
    </details>
  </section>
  <details class="lora-ota-advanced" data-role="advanced-tools"><summary>Advanced settings and diagnostics</summary>
  <details class="lora-ota-advanced" data-role="radio-setup-panel">
    <summary>Update channel and manual radio setup</summary>
    <p>Prepare update configures both radios and tests the link. The target's normal radio2 may pause during the update; its saved settings return after reboot or temporary-window expiry. Primary-channel fallback starts with a two-minute trial. Saved radio settings stay unchanged.</p>
    <p>Recognized community radio settings select a regional update profile automatically. An active temporary channel is kept unless you choose a region or edit the radio settings. Unrecognized settings keep the current channel. <a href="#regional-update-defaults">Regional defaults and limits</a>.</p>
    <details class="lora-ota-advanced">
      <summary>Update channel options</summary>
      <label>Region<select data-role="radio-region"><option value="auto">Auto (recognized radio settings)</option></select></label>
      <p data-role="radio-region-status">Detected from recognized radio settings. Choose a region to override.</p>
      <button type="button" data-role="radio-reset" class="lora-ota-secondary" hidden>Reset manual overrides</button>
      <label>Update frequency (MHz)<input data-role="radio-frequency" type="number" min="150" max="2500" step="0.001" placeholder="Choose a frequency"></label>
      <label>Bandwidth (kHz)<select data-role="radio-bandwidth"><option value="7.8">7.8</option><option value="10.4">10.4</option><option value="15.6">15.6</option><option value="20.8">20.8</option><option value="31.25">31.25</option><option value="41.7">41.7</option><option value="62.5">62.5</option><option value="125">125</option><option value="250" selected>250</option><option value="500">500</option></select></label>
      <label>Spreading factor<select data-role="radio-sf"><option value="5">SF5</option><option value="6">SF6</option><option value="7" selected>SF7</option><option value="8">SF8</option><option value="9">SF9</option><option value="10">SF10</option><option value="11">SF11</option><option value="12">SF12</option></select></label>
      <label>Coding rate<select data-role="radio-cr"><option value="4">4/4</option><option value="5" selected>4/5</option><option value="6">4/6</option><option value="7">4/7</option><option value="8">4/8</option></select></label>
      <label>Primary-only preamble<input data-role="radio-preamble" type="number" min="1" max="65535" value="128"></label>
      <label>Radio2 OTA preamble<input data-role="radio2-preamble" type="number" min="1" max="65535" value="368"></label>
      <label>Temporary window (minutes)<input data-role="radio-minutes" type="number" min="1" max="10080" value="120"></label>
      <p class="lora-ota-actions"><button type="button" data-role="radio-prepare" disabled>Prepare now</button> <button type="button" class="lora-ota-secondary" data-role="radio-check" disabled>Check channel</button></p>
    </details>
    <p>Setup turns automatic OTA fetch/install off and sets OTA hops to 0. These OTA preferences are saved; any changes appear in the diagnostic log. RX power saving stays unchanged.</p>
    <p data-role="radio-setup-status" role="status" aria-live="polite">Connect and choose the remote device first.</p>
    <details class="lora-ota-advanced">
      <summary>Manual setup and older firmware</summary>
      <p>No manual channel commands are needed for direct updates with supported source radio controls. Firmware without those controls needs a Companion update for automatic setup.</p>
      <p>For manual setup, apply the command on the target first, then the source radio. Every relay also needs matching settings; automatic route preparation is not supported.</p>
      <pre data-role="radio-commands">Choose the update frequency to show the temporary command.</pre>
    </details>
  </details>

  <details class="lora-ota-advanced">
    <summary>Advanced connection and firmware options</summary>
    <p><label><input type="checkbox" data-role="acl"> Use existing ACL access only (ignore the password)</label></p>
    <p><label>Admin-route relay public keys, nearest first (optional; one per line)<textarea data-role="relays" rows="3" cols="64" spellcheck="false"></textarea></label></p>
    <p><label>Candidate relay public keys for route search (optional; one per line)<textarea data-role="candidates" rows="3" cols="64" spellcheck="false"></textarea></label></p>
    <p><label>Minimum SNR (dB) <input data-role="snr" type="number" value="-5" step="0.25"></label> <label>Maximum observation age (hours) <input data-role="age" type="number" value="24" min="1" max="168"></label></p>
    <p><button type="button" data-role="find-route" class="lora-ota-secondary" disabled>Find admin route</button></p>
    <p><label>New firmware (required for a prebuilt differential package)<input data-role="image" type="file" accept=".bin,.zip"></label></p>
    <p><label>Trusted signer public key (optional)<input data-role="signer" size="64" autocomplete="off" spellcheck="false"></label></p>
  </details>
  <details class="lora-ota-advanced">
    <summary>Repeater forwarding</summary>
    <p>Optionally change forwarding on the source or target. The radio flood settings leave direct-route messages enabled; all forwarding controls both. These saved settings need firmware that supports them. A relay used by the selected route must keep forwarding on. To manage a different repeater, connect with that repeater as the target first. Restore the setting afterward if needed.</p>
    <label>Repeater
      <select data-role="repeat-node"><option value="source">Source radio</option><option value="target">Update target</option><option value="other">Selected route relay (check or turn on)</option></select>
    </label>
    <label>Route relay's full public key (when selected)<input data-role="repeat-key" autocomplete="off" spellcheck="false"></label>
    <label>Setting
      <select data-role="repeat-kind"><option value="radio.flood">Radio1 floods</option><option value="radio2.flood">Radio2 floods</option><option value="repeat">All forwarding</option></select>
    </label>
    <p class="lora-ota-actions">
      <button type="button" class="lora-ota-secondary" data-role="repeat-read" disabled>Check forwarding</button>
      <button type="button" class="lora-ota-secondary" data-role="repeat-off" disabled>Turn forwarding off</button>
      <button type="button" class="lora-ota-secondary" data-role="repeat-on" disabled>Turn forwarding on</button>
    </p>
  </details>
  <details class="lora-ota-advanced">
    <summary>Multi-version nRF52 update</summary>
    <p>If you reopen this page during a multi-version update, connect the same source and select <strong>Resume saved update</strong>. If browser storage was unavailable, reselect the exact original firmware files and planning settings, then choose <strong>Compare paths</strong> and <strong>Resume saved path</strong>.</p>
    <p>For nRF52 internal staging, choose the exact installed firmware above, the final firmware above, and optionally up to six intermediate images below. Each intermediate must contain OTA receiver evidence; the actual node is checked after every reboot. Prepared packages are unsigned. Use the single-update controls above for ESP32 or nRF52 with external storage.</p>
    <p><label>Intermediate firmware images (.bin or .zip)<br><input data-role="chain-files" type="file" accept=".bin,.zip" multiple></label></p>
    <p><label><input data-role="chain-attest" type="checkbox"> I have tested every selected intermediate on this exact hardware and confirmed it retains working LoRa OTA.</label></p>
    <p>For supported RAK3401 and RAK4631 internal-storage OTAFIX builds, the browser simulates every selected differential using the identified bootloader. The simulation does not replace testing intermediates on the actual hardware. Full-package intermediate steps are not yet modeled.</p>
    <p><label>Optimize <select data-role="chain-objective"><option value="packets">Fewer packets</option><option value="airtime">Estimated airtime and reboots</option></select></label>
    <label>Estimated preamble <input data-role="chain-preamble" type="number" min="1" max="65535" value="128"></label>
    <label>Minutes per new temporary window <input data-role="chain-minutes" type="number" min="1" max="10080" value="240"></label></p>
    <p><button type="button" data-role="chain-plan" disabled>Compare paths</button> <button type="button" data-role="chain-run" disabled>Run selected path</button> <button type="button" data-role="chain-resume" disabled>Resume saved path</button> <button type="button" data-role="chain-stop" class="lora-ota-secondary" hidden>Stop update</button></p>
    <p>Estimates exclude retries, status polls and congestion. Use the single-update controls for an actual update.</p>
  </details>
  <details class="lora-ota-advanced" data-role="diagnostics"><summary>Diagnostic log</summary>
    <pre data-role="status"></pre>
  </details>
  </details>
</div>

## Resume an interrupted update

The browser remembers your connection, target and update choices. When you start a transfer, it also tries to save the selected firmware files in this browser. After reopening the page, connect the same source and select **Resume saved update**. The updater checks the target and saved package before offering the next step. It does not save your admin password; leave it blank when the source already has access through the target's ACL.

For a manually assembled multi-version path, **Resume saved update** restores the saved plan and offers **Resume saved path**. If the browser could not save the plan, reselect the original firmware files and planning settings, then use **Compare paths** and **Resume saved path**.

Browser storage can be unavailable, cleared, or full. If the firmware cannot be saved, the updater warns you to keep the original files so you can select them again. Saved choices and firmware stay on this device and are not synced to another browser or phone. Forgetting saved firmware does not cancel the target's update. A shared automatic-path cache may remain in this browser.

## Bluetooth Companion connection

Turn on Bluetooth and disconnect the Companion from other apps. Select **Bluetooth Companion**, then **Connect Bluetooth Companion** and choose the radio. On Android, open this page in Chrome. Allow Bluetooth access if prompted.

The Companion must have firmware that supports Bluetooth OTA. Bluetooth updates require a direct, zero-hop LoRa connection between the Companion and the remote device. Keep the phone near the Companion and leave the browser open during the update.

## Wi-Fi Companion connection

Connect your phone and the Companion to the same trusted Wi-Fi network.
Select **Wi-Fi Companion** above, enter the Companion's private IPv4 address,
and connect. No computer or local bridge is required. You can also scan one
private /24 network using **Find a Wi-Fi Companion**. Your browser may ask
for local-network access; allow it to connect or scan. If the prompt appears
during a scan, scan again after allowing access. A scan sends a read-only
discovery request and does not change device settings. Disconnect other apps
from the Companion for the duration of the update.

Direct Wi-Fi requires a Companion built with the browser Wi-Fi OTA endpoint;
older USB-only or bridge-only builds need a firmware update first. Repeater
sources use USB. To enable Wi-Fi and find the Companion's address, see
[Wi-Fi Companion setup](WiFi.md#wifi-companion-setup). The local connection is
not encrypted; use a trusted network. Browser support for local network access
varies; on Android, try current Chrome if your browser blocks local access.
iPhone browser support has not yet been verified. A failed scan can also mean that the network prefix
or device address is wrong, or the browser blocked local network access.

If the connection drops, reconnect the same source and check its state before
continuing. The update is not automatically restarted or installed again.

<details markdown="1">
<summary>Compatibility and advanced update details</summary>

### Automatic intermediate updates

Prepare update compares compatible published application bridges automatically.
Update remote firmware transfers, installs and verifies each selected step,
then prepares the next radio window. Progress shows the current step. After an
interruption, use Prepare update and Continue firmware update; the exact plan
and completed steps are saved in this browser.

A connection failure before step 1 creates its transfer record can still be
recovered. The browser rechecks the exact starting image and package, confirms
the target has no staged download, and then starts that same first step. A
missing record never authorizes replacing staged data or repeating an uncertain
install. Existing mismatched or unreadable recovery records are retained and
reported as local saved-state errors.

If an explicitly requested replacement was cancelled before a connection
failure, the same saved step can continue once fresh checks prove the original
firmware and an idle target. Cancellation is not sent again to guess its result.

Under Update path options, choose fewer installs or less radio data. Deep search
also checks every page-aligned workspace that could fit. It takes longer and
can be stopped before uploading firmware. Estimates exclude retries and other
mesh traffic. The comparison covers the published candidates for the two exact
images; it does not guarantee the best possible route through all firmware ever
built.

The initial published bridge bank covers the GAT562 30S Mesh Kit repeater from
the original 1.18.0.1 Full image to 1.18.0.2 Full. Its hybrid staging requires
confirmed retained-RAM support. These are application updates; the bootloader
and saved settings are retained.

### Bootloader RAM staging cutoff

[OTAFIX 2.4.6](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/0.11.0-OTAFIX2.4.6)
introduced the extra **64 KiB of retained RAM** for normal internal-storage
nRF52840 bootloaders. It holds part of the update package across a software
reset; removing power loses that part of the transfer.

| Installed bootloader | Retained-RAM staging |
| --- | --- |
| Stock Adafruit or OTAFIX 2.4.5 and earlier | Not available from the version alone. |
| Official stable OTAFIX 2.4.6 through 2.4.12, normal internal-storage profile | Available; the browser checks the version and live internal-storage identity. |
| External-only QSPI or SD profile | Uses its storage backend; the version does not establish a RAM arena. |
| Custom, preview, unknown or another release family | Requires an explicit `ota ram` capability or an exact verified bootloader identity. |

The browser reads `ota ram` first. When older application firmware does not
provide it, the browser uses `get bootloader.ver` with `ota bootloader status`.
The version fallback requires a reviewed stable 2.4.6-2.4.12 version, ABI 3,
internal profile `caps=0A`, and a hybrid application with expanded `ED000`
staging. Normal adaptive RAK3401/RAK4631 2.4.10 and later also report that
internal profile. SD/internal combined profiles still require the explicit
RAM capability or exact identity check.

An explicit `hybrid=0` is never overridden by the version. A bare
`0.11.0 (base)`, missing reply, or timeout does not establish support. The
exact published GAT562 OTAFIX 2.4.12 identity and CRC remains a fallback when
the older version reader cannot identify it. A larger version in another
OTAFIX lineage is not assumed compatible.

Preparation failures include the installed and selected filenames, versions,
full firmware hashes, retained-RAM check, search result and flash budget in
the diagnostic log. Passwords and local file paths are omitted.

## About guarded OTA

Guarded OTA keeps update replies separate from normal messages and checks the
source radio during the update. The updater selects it automatically when the
Companion supports it. Both modes verify the target's public key before updating.

Older USB Companions use their shared message queue. Unrelated pending messages
may be saved in this browser and cannot be restored to the Companion. Wi-Fi
requires guarded OTA and cannot use this older shared-queue path. Keep other
apps disconnected and avoid changing radio settings during the update.

Older Keymind Cascade Full Companions can use their existing USB mOTA
firmware-source mode. The page selects it automatically when the older
176-byte command limit prevents raw-packet updates, then restores Binary
Companion mode for status checks and when finished. No manual mode change or
Companion firmware update is needed when that capability is available.

Newer Companions can send complete OTA packets directly when `get usb.raw.max`
reports `188`. A Companion without either capability needs updated firmware;
the page checks before changing the target. Older remote devices remain
supported when their firmware includes compatible LoRa OTA.

For generated nRF52 hybrid internal-storage updates larger than one flash page,
the updater checks the target's bootloader RAM capability before starting and
checks it again for each intermediate update. If the running firmware cannot
confirm that capability, those updates are unavailable; smaller updates are not
affected by this check.

## Setup and advanced details

<details id="regional-update-defaults" markdown="1">
<summary>Regional update defaults and limits</summary>

Fast profiles use **SF5, CR7**; known SX127x radios use **SF7**. Auto matches the saved frequency, bandwidth,
spreading factor and coding rate against a checked snapshot of the
[MeshCore community suggestions](https://api.meshcore.nz/api/v1/config).
These identify suggested profiles, not a device's country or certification.
Choose **Region** under **Update channel options** to override the suggestion.

| Area | Update MHz / BW kHz | Operating limits and reference |
| --- | --- | --- |
| EU / UK, including the matching Swiss profile | **869.525 / 250** | 500 mW ERP; 10% duty cycle or the applicable compliant channel-access alternative. [EU decision](https://eur-lex.europa.eu/eli/dec_impl/2025/105/oj/eng), [UK IR 2030](https://www.ofcom.org.uk/siteassets/resources/documents/spectrum/interface-requirements/ir-2030.pdf), [Swiss RIR1008-09](https://www.ofcomnet.ch/api/RIR/1008/09). |
| EU / UK 433 MHz | **433.925 / 250** | 10 mW ERP and 10% duty cycle for this operating category. [EU decision](https://eur-lex.europa.eu/eli/dec_impl/2025/105/oj/eng), [UK IR 2030](https://www.ofcom.org.uk/siteassets/resources/documents/spectrum/interface-requirements/ir-2030.pdf). |
| US / Canada | **909.950 / 500** | The fixed-channel DTS category requires measured 6 dB bandwidth of at least 500 kHz; nominal BW500 alone does not establish equipment compliance. Power, spectral density and antenna limits also apply. [FCC §15.247](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-A/part-15/subpart-C/section-15.247), [ISED RSS-247](https://ised-isde.canada.ca/site/spectrum-management-telecommunications/en/devices-and-equipment/radio-equipment-standards/radio-standards-specifications-rss/rss-247-digital-transmission-systems-dtss-frequency-hopping-systems-fhss-and-licence-exempt-local). |
| Costa Rica | **909.950 / 250** | This segment permits up to 24 dBm conducted / 30 dBm EIRP, subject to equipment approval. [2026 decree, Table 22](https://micitt.go.cr/sites/default/files/marco_juridico_legal/Decreto%20n.%C2%B0%2045769-MICITT%20MODIFICACI%C3%93N%20PARCIAL%20A%20LOS%20ART%C3%8DCULOS%204%2C%2014%2C%2015%20Y%2016%20DEL%20DECRETO.pdf). |
| Australia | **921.500 / 250** | Digital modulation: up to 1 W EIRP and 25 mW per 3 kHz radiated peak power density. [LIPD 2025, Table 8 / clause 43](https://www.legislation.gov.au/F2025L01047/asmade/2025-09-05/text/original/pdf). |
| Brazil | **921.500 / 500** | New fixed-channel digital certifications require measured 6 dB bandwidth of at least 500 kHz, plus power and spectral-density limits. Previously approved equipment can retain its original requirements. [Ato 14448](https://informacoes.anatel.gov.br/legislacao/atos-de-requisitos-tecnicos-de-gestao-do-espectro/2017/1139-ato-14448), [2026 transition](https://informacoes.anatel.gov.br/legislacao/component/content/article/174-atos-de-certificacao-de-produtos/2026/2132-ato-4746). |
| Thailand | **921.500 / 250** | Fixed-channel duty limits depend on EIRP: 1% below 50 mW, 10% above 50 mW; higher powers can require an operating licence. [NBTC standard](https://standard.nbtc.go.th/getattachment/Standards/มาตรฐานทางเทคนิคของเครื่องโทรคมนาคม/1033-2565.pdf.aspx?lang=th-TH), [authorization rules](https://standard.nbtc.go.th/getattachment/Standards/กฎระเบียบที่เกี่ยวข้อง/หลักเกณฑ์-920-925-MHz.pdf.aspx?lang=th-TH). |
| Vietnam | **921.500 / 250** | Licence-exempt LPWAN: up to 25 mW ERP, 1% endpoint / 10% qualifying access-station duty cycle. A repeater is not automatically a qualifying gateway. [2025 consolidated rules, Appendix 19](https://congbaocdn.chinhphu.vn/CongBaoCP/VanBan/2025/6/45035/56681-1-2025761-7621-vbhn-bkhcn.pdf). |

The shared Australia/Brazil community preset selects **921.500 / 500**;
choosing Australia explicitly selects BW250. New Zealand and unknown custom
profiles retain their saved settings: the full conditions of the current
[New Zealand SRD licence](https://www.rsm.govt.nz/about/publications/gazette-notices/general-user-radio-licence-gurl-notices)
were not verified for a new fast profile.

Profiles select the update channel. Existing power and airtime settings are
retained; the browser does not raise them or certify antenna gain, actual
emissions or equipment approval. Both radios must already satisfy the applicable
power and duty-cycle limits. Limits checked **10 November 2026**.

</details>

Direct updates were tested over LoRa with a released 1.18.0.1 Full Companion:
transfer recovery, automatic window extension, stopping, replacing firmware,
installation and post-reboot verification passed. See the
[Mercerwood test results](test-results/browser_lora_ota_mercerwood_2026-11-10.safe.json).
Repeater-source and multi-hop hardware tests remain unverified. This updater is
experimental.

Guarded mode requires a custom Keymind Cascade Full Companion built from commit
`bede4d85f` or later with both guarded features enabled. The 1.18.0.1 release
and published 1.18.0.2 previews do not have both features.

Investigation retries unanswered checks. Download, install and cancel commands
are not automatically repeated after an uncertain reply. Older targets validate the staged image with `ota install`,
but cannot bind the full container hash before installation. The updater checks
the reported firmware identity and body hash after reboot.
For direct updates, it reads the target's saved channel before installing and
automatically reconnects there after reboot. Any source retune is temporary;
saved radio channels are unchanged.

The source needs a compatible Companion with raw packet support, or a USB
Repeater with `ota.peer` and USB folder support. Wi-Fi also requires the
guarded OTA support described above. Older USB Companions use their
shared message queue: unrelated messages moved into the browser can be
downloaded, but cannot be restored to the Companion. The page prepares the
update channel and required OTA preferences automatically on
compatible direct connections. Once data transfer starts, long updates can extend
temporary windows that this browser set up and can still verify. Existing or
unverified windows retain their original deadline. The required
OTA policy changes remain saved and are listed in the diagnostic log. RX power saving is not changed or required to be off; radio2 handles its own receive mode. Missing setting acknowledgements are checked automatically before setup continues.
On older Companions with USB radio controls, the page automatically aligns both
temporary channels with the Companion's saved settings. It tests primary
channels with a two-minute window before extending them; temporary radio2
uses the full window and keeps the normal channel available. A missing USB
reply during setup stops the operation and requires reconnecting. With a Companion that
can select temporary radio2, the page first sends the target's temporary-channel
request over the already-proven primary link. Only afterward does it enable
source radio2 and test the requested channel. This avoids changing the source's
receive scanning and automatic preamble before the target is ready. A lost
request reply is checked on the requested channel, not blindly sent again.
An adopted matching target window is checked on its current update channel;
the browser does not require it to answer on an old channel it has left.
Probe timeouts account for radio
airtime and allow at least five seconds for a reply. Read-only Companion CLI
checks use up to seven attempts before a target response and eleven after one
is confirmed. The channel handoff still has one 90-second deadline, which can
stop those reads sooner. Only
read-only identity and state checks are retried; a target switch still requires
a fresh full-key proof on the original channel. Failures retain the command,
stage and selected radio, rather than hiding the underlying timeout. The log
shows the cached saved and temporary target settings and labels each CLI check
as original primary or temporary OTA radio2. A temporary primary window can make
a target unreachable on its saved channel until the window expires; remote
silence alone does not prove the processor has hung. The browser checks the
requested channel first, then checks the original settings after missed replies.
If the target still answers on the original channel without a pending
switch, setup can send another switch request, with a pause and a bounded number
of attempts. It continues only after verifying the target on the new channel.
Other setup paths wait up to 35 seconds for matching temporary-radio readbacks;
errors show the requested and reported settings, including preamble.
New windows use a 368-symbol preamble at both ends when either endpoint uses
radio2. Primary-only setup uses its separate preamble setting. Recovery keeps
the verified preamble of an existing window until that window is replaced.

When the target already has a saved radio2 profile, setup can temporarily use
that profile for the update instead of changing the saved tuple or disabling it
permanently. Its normal radio2 traffic pauses while the temporary update profile
is active. The saved profile returns when the target reboots after installation
or when the temporary window expires. The browser verifies restoration before
reporting the update complete and keeps the original profile in the update's
recovery record. Existing temporary radio2 sessions, radio2 schedules and
automatically owned runtime profiles are not replaced. This is a temporary
frequency override on the same transceiver, not a second physical radio.

During an active download, an unanswered temporary-window getter leaves file
serving active and retries after a pause while the last verified window remains
valid. Identity changes, USB failures and uncertain renewal writes still stop
the session. Stopping does not cancel the target's staged download; prepare the
same update again to resume its retained blocks.
If the window has expired, setup can prepare a new one after matching both
devices, the saved package's full hash and the target's staged download ID.
Automatic update steps keep their own recovery records, which are also used
after refreshing the page or reconnecting USB.

Installer capability is checked on the working channel before radio setup.
An explicit earlier result is reused only on the same connection, source,
authenticated target and installed firmware identity. Changing hardware,
version or the installed body hash requires another check. A timeout never
authorizes a weaker installer. The final install command and post-reboot image
verification are still required.

**Pause normal forwarding** is enabled by default for direct updates once
radio2 has been verified. It pauses primary-profile floods when `radio.flood`
is supported; an explicit unsupported reply permits the older global `repeat`
control, which pauses forwarding on both profiles. Locally addressed admin
commands and the target's own OTA traffic remain enabled. An originally off
setting is never turned on. This option does not change multi-hop relays.
This is optional: if preparation cannot establish the original setting and
safe recovery storage, the updater skips the pause, warns, and continues the
firmware checks. Older sources without bounded optional checks skip this
optimization. A pause whose outcome is uncertain retains its recovery record.

Forwarding settings persist across reboot. Before changing one, the browser
saves its original value and full source/target identities in a local recovery
record, without a password. It checks restoration after the final update and
tries restoration after Stop, an error or Disconnect when the source is healthy
and idle. Keep the page open until restoration is confirmed. If interrupted,
reconnect the same source, select the same target and use **Restore forwarding**.
Firmware **DONE** means the running target identity and image were verified.
An optional forwarding or normal-radio2 check cannot turn that success into a
failed firmware update. Pending cleanup stays visible as a warning; its recovery
record remains until verification succeeds. An unconfirmed setting write is not
silently repeated. Keep the page open if cleanup is still pending.

The source's five-minute admin lease is checked before reusing cached access.
When this page's exact session has expired, it reacquires access and proves the
target again before continuing; an active unknown session is not taken over.
Read retries are not multiplied after the transport exhausts its 7/11 attempts.

If a Companion USB command times out, the page tries reopening that same USB
port once and confirms the source's complete identity. This is a per-command
acknowledgement timeout, not a USB inactivity limit. It does not restart the
update or replay an unanswered install, pull or settings command. Select
**Prepare update** afterward to read the target's current state.

On that same-page reconnect, the browser can close its own abandoned local
reply mailbox without waiting for its five-minute lease to expire. It cannot
take over an unrecognized session. A surviving radio2 update channel is
reselected only after its exact local temporary settings are confirmed; the
target must still prove its full identity before forwarding is restored. An
unconfirmed mailbox close is checked by reads, never automatically repeated.

Firmware without
the required USB radio readbacks cannot be configured or checked automatically;
the page identifies the missing command and links to a Companion update.
Some firmware accepts raw OTA packets from USB but
suppresses their transmission without an active temporary radio.
After a USB timeout, select **Reconnect USB source**, then **Prepare update**.
After a Wi-Fi interruption, select **Reconnect Wi-Fi Companion**, then
**Prepare update**; the page checks the same source identity before continuing.
The selected target and firmware are kept. A matching interrupted download can
be resumed or restarted from this browser's saved update record. To abandon it,
choose different firmware and prepare again. **Replace and install update**
discards the unfinished download and starts the selected image; preparation
alone does not discard anything. Replacement rechecks the staged download
before clearing it. An unanswered cancel is not sent again automatically.
Source folders must be cleared first. If the source
clock is wrong, set it before connecting. Multi-version updates require a
supported identified RAK bootloader and successful simulation of every step.

For multi-hop, enter each relay's full public key in order from the source to
the destination. The source must already have ACL access to every relay and
the destination. This selects the **administrative route**; the firmware still
floods OTA data and does not restrict bulk traffic to these relays. Every participant must use the same saved and temporary
radio settings, with radio2 and crossing off; relays must have repeat on. Set
OTA hops high enough for the route on every participant. These checks are
experimental and have not yet been verified over LoRa.

Alternatively, connect with the admin-route field empty, enter the full keys
of candidate repeaters, and select **Find admin route**. The search checks
cached neighbor reports from reachable ACL-authorized nodes. It favors fewer
relays among paths meeting the selected SNR floor, then the strongest weakest
link. Reports can be stale and do not identify which radio profile heard them.
This is a hint for the admin route; it does not select the OTA data flood path.

Choose a firmware `.bin` or Nordic DFU `.zip` to create a full package in the browser, or a
prebuilt `.mota` package. Differential packages also require the exact new
firmware `.bin`; the target's existing firmware must match the package's base
hash. A signed package requires its full trusted signing key, which must also
already be trusted by the target. The target makes the final full-key check.

To create a differential package in the browser, select the new
`.bin` and the exact old `.bin` currently installed on the target. The browser
reconstructs the generated patch and checks it against the new image; the target
also checks it during installation. nRF52 generation requires a supported
embedded layout and confirmed internal or QSPI staging; unsupported nRF52 layouts
can use a compatible prebuilt `.mota`.
Preparing a differential for a large image can use substantial browser memory;
if preparation fails, remove the old image to use a full package instead.


</details>

<script src="../_javascript/lora_ota_checks.js?v=initial-step-recovery-1"></script>
<script src="../_javascript/lora_ota_package.js"></script>
<script src="../_javascript/lora_ota_zip.js"></script>
<script src="../_javascript/lora_ota_delta.js?v=auto-paths-3"></script>
<script src="../_javascript/lora_ota_port.js"></script>
<script src="../_javascript/lora_ota_wifi_port.js?v=direct-wifi-1"></script>
<script src="../_javascript/lora_ota_ble_port.js?v=bluetooth-1"></script>
<script src="../_javascript/lora_ota_wifi_scan.js?v=direct-wifi-1"></script>
<script src="../_javascript/lora_ota_serial.js?v=zopfli-1"></script>
<script src="../_javascript/lora_ota_companion_source.js?v=zopfli-1"></script>
<script src="../_javascript/lora_ota_companion.js?v=bluetooth-1"></script>
<script src="../_javascript/lora_ota_contacts.js?v=advert-path-1"></script>
<script src="../_javascript/lora_ota_session.js?v=zopfli-1"></script>
<script src="../_javascript/lora_ota_route.js"></script>
<script src="../_javascript/lora_ota_repeat.js"></script>
<script src="../_javascript/lora_ota_quiet.js?v=workflow-first-1"></script>
<script src="../_javascript/lora_ota_discovery.js"></script>
<script src="../_javascript/lora_ota_collect.js"></script>
<script src="../_javascript/lora_ota_routed_session.js?v=ota-quiet-1"></script>
<script src="../_javascript/lora_ota_plan.js"></script>
<script src="../_javascript/lora_ota_candidates.js?v=auto-paths-1"></script>
<script src="../_javascript/lora_ota_sim.js"></script>
<script src="../_javascript/lora_ota_chain.js?v=workflow-first-1"></script>
<script src="../_javascript/lora_ota_chain_ui.js?v=auto-paths-3"></script>
<script src="../_javascript/lora_ota_auto_chain.js?v=resume-auto-paths-2"></script>
<script src="../_javascript/lora_ota_region_profiles.js"></script>
<script src="../_javascript/lora_ota_radio_setup.js?v=radio2-preamble-368-1"></script>
<script src="../_javascript/lora_ota_recovery.js?v=usb-retry-1"></script>
<script src="../_javascript/lora_ota_lease.js?v=active-lease-retry-1"></script>
<script src="../_javascript/lora_ota_catalog.js?v=4aeb7bf86"></script>
<script src="../_javascript/lora_ota_preferences.js?v=zopfli-1"></script>
<script src="../_javascript/lora_ota_resume_store.js?v=zopfli-1"></script>
<script src="../_javascript/lora_ota_zopfli.js?v=1"></script>
<script src="../_javascript/lora_ota_compression.js?v=1"></script>
<script src="../_javascript/lora_ota_browser.js?v=zopfli-1"></script>
