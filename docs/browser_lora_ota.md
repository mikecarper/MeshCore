<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/browser_lora_ota/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Update remote firmware over LoRa

Connect a USB radio, choose the remote device, then prepare and start its update.
Keep the browser open until the new firmware is verified. This updater is experimental.
Use Chrome or Edge on a computer with a USB Companion or compatible Repeater.

<div data-lora-ota>
  <p data-role="workflow-status" class="lora-ota-notice" role="status" aria-live="polite">Connect your USB radio to get started.</p>
  <p data-role="browser-support" class="lora-ota-notice" role="alert" hidden>USB access is unavailable in this browser. Open this page in Chrome or Edge on a computer. Firefox does not provide the Web Serial connection this updater needs.</p>
  <section class="lora-ota-step">
    <h2>1. Connect and choose the remote device</h2>
    <details class="lora-ota-advanced"><summary>USB connection options</summary>
    <label>USB device
      <select data-role="source"><option value="auto">Detect automatically</option><option value="companion">Companion</option><option value="repeater">Repeater</option></select>
    </label>
    </details>
    <p>The USB radio sends the update over LoRa. Choose the remote device and enter its admin password if needed.</p>
    <p class="lora-ota-actions"><button type="button" data-role="connect">Connect USB source</button> <button type="button" data-role="reconnect" hidden disabled>Reconnect USB source</button> <button type="button" data-role="disconnect" class="lora-ota-secondary" disabled>Disconnect</button></p>
    <p data-role="source-info" role="status" hidden></p>
    <p data-role="ota-availability" class="lora-ota-availability" role="status" aria-live="polite" hidden></p>
    <p data-role="source-update-help" role="status" hidden>This source needs updated firmware for this OTA workflow. If it is still connected here, disconnect it before opening the flasher. <a data-role="source-update-link" href="https://mikecarper.github.io/meshfirmware/" target="_blank" rel="noopener noreferrer">Update source firmware</a></p>
    <p data-role="contact-status" role="status">Connect your USB radio to choose the remote device.</p>
    <section data-role="target-controls" hidden>
    <label>Search contacts by name or public-key prefix<input data-role="contact-search" autocomplete="off" spellcheck="false" disabled></label>
    <p><strong>Device to update</strong></p>
    <section data-role="contact" class="lora-ota-contacts" role="group" aria-label="Devices to update" hidden></section>
    <label>Admin password (optional)<input data-role="password" type="password" autocomplete="off" aria-describedby="lora-ota-password-help"></label>
    <small id="lora-ota-password-help">Leave blank to use existing ACL access or an active session.</small>
    <details class="lora-ota-advanced">
      <summary>Enter a public key manually</summary>
      <label>Target public key (64 hex characters)<input data-role="key" size="64" autocomplete="off" spellcheck="false"></label>
      <p>For a USB Repeater source, enter the remote device key here.</p>
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
    </details>
    <p>Prepare update checks the firmware, sets up a temporary channel and tests the link. Your normal radio channels stay unchanged.</p>
    <p class="lora-ota-actions"><button type="button" data-role="inspect" disabled>Prepare update</button></p>
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
    <p>Prepare update configures both radios and tests the link automatically. It checks temporary radio2 first on each device, using the full window when available. Primary-channel fallback starts with a two-minute trial and extends only after the target responds on the new channel. Saved normal channels stay unchanged.</p>
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
      <label>Preamble<input data-role="radio-preamble" type="number" min="1" max="65535" value="128"></label>
      <label>Temporary window (minutes)<input data-role="radio-minutes" type="number" min="1" max="10080" value="120"></label>
      <p class="lora-ota-actions"><button type="button" data-role="radio-prepare" disabled>Prepare now</button> <button type="button" class="lora-ota-secondary" data-role="radio-check" disabled>Check channel</button></p>
    </details>
    <p>Setup turns automatic OTA fetch/install off and sets OTA hops to 0. These OTA preferences are saved; any changes appear in the diagnostic log. RX power saving stays unchanged.</p>
    <p data-role="radio-setup-status" role="status" aria-live="polite">Connect and choose the remote device first.</p>
    <details class="lora-ota-advanced">
      <summary>Manual setup and older firmware</summary>
      <p>No manual channel commands are needed for direct updates with USB radio controls, including older Companions. Firmware without those controls needs a Companion update for automatic setup.</p>
      <p>For manual setup, apply the command on the target first, then the USB source. Every relay also needs matching settings; automatic route preparation is not supported.</p>
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
  <details class="lora-ota-advanced" data-role="diagnostics"><summary>Diagnostic log</summary>
    <pre data-role="status"></pre>
  </details>
  </details>
</div>

<details markdown="1">
<summary>Compatibility and advanced update details</summary>

## About guarded OTA

Guarded OTA keeps update replies separate from normal messages and checks the
source radio during the update. The updater selects it automatically when the
Companion supports it. Both modes verify the target's public key before updating.

Older Companions use their shared message queue. Unrelated pending messages may
be saved in this browser and cannot be restored to the Companion. Keep other
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

## Setup and advanced details

<details id="regional-update-defaults">
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
experimental; Wi-Fi support is still under development.

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

The source needs a compatible USB Companion with raw packet support, or a
Repeater with `ota.peer` and USB folder support. Older Companions use their
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
can select temporary radio2, the page keeps its original primary channel and
probes the requested update channel on radio2. Probe timeouts account for radio
airtime. If the target still answers on the original channel without a pending
switch, setup can send another switch request, with a pause and a bounded number
of attempts. It continues only after verifying the target on the new channel.
Other setup paths wait up to 35 seconds for matching temporary-radio readbacks;
errors show the requested and reported settings, including preamble.
Firmware without
the required USB radio readbacks cannot be configured or checked automatically;
the page identifies the missing command and links to a Companion update.
Some firmware accepts raw OTA packets from USB but
suppresses their transmission without an active temporary radio.
After a USB timeout, select **Reconnect USB source**, then **Prepare update**.
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
<script src="../_javascript/lora_ota_region_profiles.js"></script>
<script src="../_javascript/lora_ota_radio_setup.js"></script>
<script src="../_javascript/lora_ota_recovery.js"></script>
<script src="../_javascript/lora_ota_lease.js"></script>
<script src="../_javascript/lora_ota_catalog.js"></script>
<script src="../_javascript/lora_ota_browser.js"></script>
