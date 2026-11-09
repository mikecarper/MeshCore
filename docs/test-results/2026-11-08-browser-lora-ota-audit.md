# Browser LoRa updater audit - 8 November 2026

This audit covers the shared browser updater, its USB transports and its update
sessions. It does not establish a T096-specific firmware defect. No radios were
flashed or physically tested during this audit.

## Fixes

- Disable conflicting update controls while another action is running. A second
  action cannot replace the active session or its cleanup operation.
- Stop before sending the next pull, install or reconnect retune after a
  disconnect request. Disconnecting while a firmware file loads also prevents
  that preparation from going on to change radio settings.
- Stop polling immediately when the USB transport fails during reboot checks.
  Target reboot delays still allow retries of read-only verification.
- Show the reconnect action after a canceled Companion investigation. Preserve
  an inbox message already removed by an in-flight read before closing.
- Require a fresh USB connection after an unanswered repeater CLI command, so a
  late reply cannot satisfy a later command. An uncertain write is not resent.
- Validate the selected firmware before automatic radio or OTA-policy changes.
  Reject corrupt images and images for another target without those changes.
- Recognize the same completed installation on another Prepare action. For a
  differential update, compare the selected new image with the live target
  before requiring the old base image to match the running firmware.
- Repair the multi-version test fixture that caused the previous GitHub browser
  job to fail before exercising its four scenarios.

## Validation

The regression tests run the actual page scripts, package parsing, workers and
USB protocol code in Chromium, with simulated USB/RF endpoints. They cover old
and current Companion sources, repeater sources, authenticated routed updates,
full and differential packages, nRF52 internal/QSPI staging, and the built
MkDocs site. Session tests inject interruption at the pull/install boundaries.

Final local results, including focused reruns after correcting failures:
219 JavaScript tests passed; all 119 browser test methods passed. One optional
JavaScript test that needs a supplied published Nordic DFU ZIP was skipped.
The simulated ZIP and browser ZIP update tests passed.

New regressions are in `test_browser_lora_ota_audit_runtime.py` and
`test_browser_lora_ota_interruptions.js` and are included in GitHub Actions.
The automatic-channel tests now use a valid firmware image through preparation,
instead of deliberately failing its file read after retuning.

Identity, package hashes, target compatibility, staged-download ownership and
post-reboot verification remain required. RXPS is not an update prerequisite
and its saved preference is left alone.

Physical USB loss, RF congestion, cable/hub faults and target bootloader behavior
still need hardware evidence. Passing these browser tests is not a hardware
qualification or a guarantee that every historical firmware version works.
