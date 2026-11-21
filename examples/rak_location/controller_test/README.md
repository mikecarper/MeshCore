# RAK three-library controller test

These Sensor and USB tracker Companion images link the Apple Find My, Google
Find Hub and Amazon Sidewalk SDKs into one nRF52840 executable per role. The
firmware starts MeshCore and Bluetooth. The three finder applications are
present for the size measurement and stay idle during this controller test.

## Test on a RAK WisMesh Tag

1. Double-reset a bench tag with its stock S140 6.1.1 UF2 bootloader and copy
   the appropriate controller-test UF2 to the bootloader drive.
2. Open the tag's USB console and run `dog status` to confirm the role.
3. Run `locator capacity` twice. The command asks the physical controller how
   many advertising sets it supports, tries to allocate three concurrently,
   reports the count, and releases them. For example, a result ending in
   `allocated 3 of 3 idle advertising sets` on both runs confirms that the
   slots are available and reusable on that unit. This check sends no finder
   advertisements and uses no account credentials.

The build allocates `0xc1000..0xc2fff` to the bench MeshCore and Bluetooth
settings. The USB firmware update preserves the individual profiles' settings
and provisioning pages starting at `0xc3000`. On first boot the bench role
creates its own mesh identity. Double-reset and load a single-network UF2 to
return to that profile.

## Rebuild

Use the Sidewalk v1.2.0 add-on with nRF Connect SDK v3.3.0 and your locally
available Nordic Find My SDK Git checkout with tag v2.4.2. Set `ZEPHYR_BASE`
to the v3.3.0 workspace's `zephyr` directory. Use separate build workspaces
for Sensor and Companion. The helper copies only the release SDK sources
needed for the build and updates the Zephyr header names, connectable
advertising option, and partition-manager include in that copy. Use a Git
checkout of Keymind Cascade with the normal RAK PlatformIO dependencies
already installed, or pass `--libdeps` as in the other locator builds.

```sh
python examples/rak_location/controller_test/build.py sensor \
  --findmy-sdk /work/sdk-find-my --build-dir /work/rak-controller-sensor \
  --output /work/RAK_controller_sensor.uf2
```

## What remains for live sightings

The stock integrations all select local Bluetooth identity slot 1. They
also manage Bluetooth startup, ownership settings, and callbacks separately.
Allocating advertising sets alone does not integrate these behaviors or
verify pairing, ringing, separated-accessory alerts, or location reports.
The Apple build requires an authorized product plan and per-device credentials
for live enrollment. Google specifies one active finder network per accessory.
[Google switchable protocol guidelines](https://developers.google.com/nearby/fast-pair/specifications/extensions/fmdn#switchable-protocol-guidelines)
and [Apple Find My accessory program](https://developer.apple.com/find-my/).
