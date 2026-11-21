# RAK three-library controller test

The default Sensor and USB tracker Companion controller-test images link the
Apple Find My, Google Find Hub and Amazon Sidewalk SDKs into one nRF52840
executable per role. The
firmware starts MeshCore and Bluetooth. The three finder applications are
present for the size measurement and stay idle during this controller test.

For the firmware that starts all three native services on the same tag, use
the `--native-pings` build. Use a separate tag for the controller test because
its application can overwrite the combined firmware's settings partition.
[Combined native pings](../native_pings/README.md)

## Test on a RAK WisMesh Tag

1. Double-reset a bench tag with its stock S140 6.1.1 UF2 bootloader and copy
   the appropriate controller-test UF2 to the bootloader drive.
2. Open the tag's USB console and run `dog status` to confirm the role.
3. Run `locator ids start` and scan for the three names below. Each test beacon
   has its own random-static address. The controller interleaves broadcasts
   using ordinary nonconnectable Bluetooth advertisements.
4. Run `locator ids stop B` to turn off the Android beacon. Fresh packets from
   A and C should continue. Run `locator ids start B` to bring it back.
   `start`, `stop` and `status` each accept an optional A, B or C. Omitting
   the letter addresses all three. All start off on reboot and reuse their
   saved addresses when started again.
5. With the beacons stopped, run `locator capacity` twice. The command asks the physical controller how
   many advertising sets it supports, tries to allocate three concurrently,
   reports the count, and releases them. For example, a result ending in
   `allocated 3 of 3 idle advertising sets` on both runs confirms that the
   slots are available and reusable on that unit. This check sends no finder
   advertisements and uses no account credentials.

| Slot | Local BLE name | Native firmware profile | USB command in the native image |
| --- | --- | --- | --- |
| A | `RAK-A-Apple` | Apple Find My | `tag status` |
| B | `RAK-B-Android` | Google Find Hub | `findhub status` |
| C | `RAK-C-Sidewalk` | Amazon Sidewalk | `sidewalk status` |

The default controller test broadcasts the three local names above. The
[individual network profiles](../README.md) and the
[combined native build](../native_pings/README.md) include the enrollment and
sighting instructions for those images.

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

## Scanner regression

On Linux with the same Zephyr workspace, build the native test application.
It runs the same shell commands and Bluetooth host code as the firmware.
The Python test uses Bumble 0.0.236 and Python 3.11 or newer. A separate
simulated scanner checks every combination of the three names, address
persistence across two boots and continuous advertising by unaffected slots
when an individual command or a group start fails.

```sh
ZEPHYR_TOOLCHAIN_VARIANT=host west build --no-sysbuild -b native_sim/native/64 \
  -d /tmp/rak-ids examples/rak_location/controller_test/tests
python examples/rak_location/controller_test/tests/scan.py /tmp/rak-ids/zephyr/zephyr.exe
```
