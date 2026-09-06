# Releasing Firmware

For the USA Cascade 1.17.1.5 matrix, use
[the option 3 release instructions](docs/releases/1.17.1.5.md).
Package the qualified outputs with `scripts/package_cascade_release.py`.
Local staging does not publish or push anything. Include the
[feature switches by role](docs/role_feature_switches.md),
[Full Companion guide](docs/full_companion_features.md), and
[USB web console](https://flasher.meshcore.io/console) in each role's notes.
The five 1.17.1.5 pages are development prereleases.

## Legacy tag-triggered GitHub Actions

The repository also has older workflows matching `companion-*`, `repeater-*`,
and `room-server-*` tags (for example `repeater-v1.0.0`). They invoke the shared
`firmware-builder.yml`, build their own target set, and request a draft release.
They are separate from the qualified option 3 matrix and local release staging.

**The broad `repeater-*` trigger also matches `repeater-room-*` Cascade tags.**
For 1.17.1.5 this workflow added its own artifacts and changed the title of the
already-published Repeater/Room Server page. Do not assume a published Cascade
page is untouched while that workflow is running. Check workflow completion,
release title/body, and the final asset inventory after publication. Preserve
existing assets when making a documentation-only release edit.

Use role-specific introductions for Companion, Repeater/Room Server, Sensor,
LoRa OTA Repeater, and expanded Full infrastructure pages. Link the exact
board/storage [OTAFIX 2.4.6 release](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/0.11.0-OTAFIX2.4.6)
where nRF52 bootloader requirements apply. Later documentation commits can be
linked from release notes without moving the firmware tag or rebuilding assets.
