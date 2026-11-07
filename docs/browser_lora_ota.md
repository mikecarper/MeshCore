<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/browser_lora_ota/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Browser LoRa updater (experimental)

This prototype runs in the browser using a USB-connected
Companion or Repeater as the source. Direct and multi-hop updates have not yet been qualified
on hardware. Wi-Fi support is still under development. Keep this page open during the
transfer; closing it disconnects the file source.

Use Chrome or Edge on a computer with Web Serial support. To start, connect the
Companion by USB and choose **Connect USB source**. Choose a contact, then
choose **Investigate** to check its OTA capabilities. Select the firmware and
choose **Check firmware and target**. After
checking the result, choose **Transfer**, then **Install and verify**.

Investigation tries the initial identity check up to seven times. Once the
target has replied, it tries unanswered read-only checks up to eleven times,
waiting up to 7 seconds per attempt for a confirmed zero-hop route, or 20 seconds
for routed or unknown paths. Unanswered checks are marked incomplete.
Login and update operations are not automatically repeated. The target's
radio settings are checked again when needed to safely set up an update.

Guarded OTA is available in custom Keymind Cascade Companion Full builds based
on commit `bede4d85f` or later when guarded CLI and radio support are enabled.
The 1.18.0.1 release and published 1.18.0.2 previews do not have both features.
Older compatible Companions can use raw packets and their shared message queue.
Keep other apps disconnected and avoid changing radio settings during a transfer.

Older targets use `ota install`. They validate the staged image, but cannot
bind the entire staged container to the browser's hash before installation.
The updater checks the reported firmware identity and body hash after reboot.

<div data-lora-ota>
  <section class="lora-ota-step">
    <h2>1. Connect your radio</h2>
    <label>USB device
      <select data-role="source"><option value="companion">Companion</option><option value="repeater">Repeater</option></select>
    </label>
    <p>Plug in your USB radio, then connect it and choose the device to update.</p>
    <p class="lora-ota-actions"><button type="button" data-role="connect">Connect USB source</button> <button type="button" data-role="disconnect" class="lora-ota-secondary">Disconnect</button></p>
    <p data-role="source-info" role="status" hidden></p>
    <label>Search contacts by name or public-key prefix<input data-role="contact-search" autocomplete="off" spellcheck="false" disabled></label>
    <p><strong>Device to update</strong></p>
    <p data-role="contact-status" role="status">Connect a Companion to load contacts.</p>
    <section data-role="contact" class="lora-ota-contacts" role="group" aria-label="Devices to update" hidden></section>
    <label>Admin password (optional)<input data-role="password" type="password" autocomplete="off" aria-describedby="lora-ota-password-help"></label>
    <small id="lora-ota-password-help">Leave blank to use existing ACL access or an active session.</small>
    <details class="lora-ota-advanced">
      <summary>Enter a public key manually</summary>
      <label>Target public key (64 hex characters)<input data-role="key" size="64" autocomplete="off" spellcheck="false"></label>
      <p>For a USB Repeater source, enter the target key here before connecting.</p>
    </details>
    <section data-role="legacy-warning" hidden>
      <p>Investigate reads this Companion’s shared message queue. Pending unrelated messages will move from the Companion to this browser and cannot be restored to the Companion. They are stored unencrypted in this browser profile and can be downloaded below. Closing the browser while a message is being received can lose that message.</p>
      <p>Keep other apps disconnected while using this Companion.</p>
    </section>
    <p class="lora-ota-actions"><button type="button" data-role="investigate" disabled>Investigate</button></p>
    <p data-role="investigation-progress" hidden><span data-role="investigation-progress-label" role="status" aria-live="polite"></span> <span data-role="investigation-progress-elapsed" aria-live="off"></span></p>
    <p><button type="button" class="lora-ota-secondary" data-role="investigation-stop" hidden>Stop investigation</button></p>
    <pre data-role="investigation" role="status" aria-live="polite" hidden></pre>
    <section data-role="legacy-inbox" hidden>
      <p data-role="legacy-inbox-status" role="status"></p>
      <details><summary>Messages saved in this browser</summary><pre data-role="legacy-inbox-messages"></pre></details>
      <button type="button" class="lora-ota-secondary" data-role="legacy-inbox-export">Download saved messages</button>
      <p>After downloading, you can clear these browser copies, including interrupted or possibly lost message records. They cannot be restored to the Companion.</p>
      <button type="button" class="lora-ota-secondary" data-role="legacy-inbox-clear" disabled>Clear saved browser copies</button>
    </section>
  </section>
  <section class="lora-ota-step">
    <h2>2. Choose firmware</h2>
    <p data-role="legacy-source-block" hidden>This Companion uses its shared message queue and raw packets.</p>
    <p data-role="firmware-catalog-status" role="status">Investigate the target to find matching firmware.</p>
    <section data-role="firmware-role-wrap" hidden><label>Target type
      <select data-role="firmware-role"><option value="">Choose a type</option><option value="repeater">Repeater</option><option value="room">Room</option><option value="sensor">Sensor</option></select>
    </label></section>
    <section data-role="firmware-choice-wrap" hidden><label>Matching firmware
      <select data-role="firmware-choice"></select>
    </label></section>
    <p><button type="button" data-role="firmware-download" hidden disabled>Download selected firmware</button></p>
    <label>Or choose a firmware file (.bin, .zip, or .mota)<input data-role="file" type="file" accept=".bin,.zip,.mota"></label>
    <p><a href="https://github.com/mikecarper/MeshCore/releases">Get firmware from GitHub releases</a></p>
    <p>To make a smaller differential update, also select the exact firmware currently installed on the target.</p>
    <label>Installed firmware (optional)<input data-role="base" type="file" accept=".bin,.zip"></label>
    <p data-role="firmware-base-status" role="status"></p>
    <p class="lora-ota-actions"><button type="button" data-role="inspect" disabled>Check firmware and target</button></p>
  </section>
  <section class="lora-ota-step">
    <h2>3. Send and install</h2>
    <p>Keep this page open during the transfer. Check the status below before installing.</p>
    <p class="lora-ota-actions"><button type="button" data-role="transfer" disabled>Transfer</button> <button type="button" data-role="resume" class="lora-ota-secondary" disabled>Resume</button> <button type="button" data-role="install" disabled>Install and verify</button></p>
  </section>
  <details class="lora-ota-advanced">
    <summary>Advanced connection and firmware options</summary>
    <p><label><input type="checkbox" data-role="acl" checked> Source is already in the target ACL</label></p>
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
      <select data-role="repeat-node"><option value="source">USB source</option><option value="target">Update target</option><option value="other">Selected route relay (check or turn on)</option></select>
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
    <p>For nRF52 internal staging, choose the exact installed firmware above, the final firmware above, and optionally up to six intermediate images below. Each intermediate must contain OTA receiver evidence; the actual node is checked after every reboot. Prepared packages are unsigned. Use the single-update controls above for ESP32 or nRF52 with external storage.</p>
    <p><label>Intermediate firmware images (.bin or .zip)<br><input data-role="chain-files" type="file" accept=".bin,.zip" multiple></label></p>
    <p><label><input data-role="chain-attest" type="checkbox"> I have tested every selected intermediate on this exact hardware and confirmed it retains working LoRa OTA.</label></p>
    <p>For supported RAK3401 and RAK4631 internal-storage OTAFIX builds, the browser simulates every selected differential using the identified bootloader. The simulation does not replace testing intermediates on the actual hardware. Full-package intermediate steps are not yet modeled.</p>
    <p><label>Optimize <select data-role="chain-objective"><option value="packets">Fewer packets</option><option value="airtime">Estimated airtime and reboots</option></select></label>
    <label>Estimated preamble <input data-role="chain-preamble" type="number" min="1" max="65535" value="128"></label>
    <label>Minutes per new temporary window <input data-role="chain-minutes" type="number" min="1" max="10080" value="240"></label></p>
    <p><button type="button" data-role="chain-plan" disabled>Compare paths</button> <button type="button" data-role="chain-run" disabled>Run selected path</button> <button type="button" data-role="chain-resume" disabled>Resume saved path</button></p>
    <p>Estimates exclude retries, status polls and congestion. Use the single-update controls for an actual update.</p>
  </details>
  <section class="lora-ota-step">
    <h2>Status</h2>
    <pre data-role="status" role="status" aria-live="polite"></pre>
  </section>
</div>

## Setup and advanced details

The source needs a compatible USB Companion with raw packet support, or a
Repeater with `ota.peer` and USB folder support. Older Companions use their
shared message queue: unrelated messages moved into the browser can be
downloaded, but cannot be restored to the Companion. Before starting, set the
target's TempRadio to match the source radio, turn RX power saving off, and
turn automatic OTA fetch and install off. For a direct update, set OTA hops
to 0. On older Companions, the browser can read the saved radio settings but
cannot confirm active temporary settings, RX power saving or preamble; check
these on the device. Some firmware accepts raw OTA packets from USB but
suppresses their transmission without an active temporary radio.
Existing downloads and source folders must be cleared first. If the source
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


<script src="../_javascript/lora_ota_checks.js"></script>
<script src="../_javascript/lora_ota_package.js"></script>
<script src="../_javascript/lora_ota_zip.js"></script>
<script src="../_javascript/lora_ota_delta.js"></script>
<script src="../_javascript/lora_ota_port.js"></script>
<script src="../_javascript/lora_ota_serial.js"></script>
<script src="../_javascript/lora_ota_companion_source.js"></script>
<script src="../_javascript/lora_ota_companion.js"></script>
<script src="../_javascript/lora_ota_contacts.js"></script>
<script src="../_javascript/lora_ota_session.js"></script>
<script src="../_javascript/lora_ota_route.js"></script>
<script src="../_javascript/lora_ota_repeat.js"></script>
<script src="../_javascript/lora_ota_discovery.js"></script>
<script src="../_javascript/lora_ota_collect.js"></script>
<script src="../_javascript/lora_ota_routed_session.js"></script>
<script src="../_javascript/lora_ota_plan.js"></script>
<script src="../_javascript/lora_ota_candidates.js"></script>
<script src="../_javascript/lora_ota_sim.js"></script>
<script src="../_javascript/lora_ota_chain.js"></script>
<script src="../_javascript/lora_ota_chain_ui.js"></script>
<script src="../_javascript/lora_ota_catalog.js"></script>
<script src="../_javascript/lora_ota_browser.js"></script>
