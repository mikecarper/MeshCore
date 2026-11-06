<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/nrf52_embedded_bootloader/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Embedded nRF52 bootloader update

For **MeshCore 1.18.0.1**, select your exact board and Repeater or Room Server
role in the [firmware picker](firmware_picker.md), then choose **OTAFIX 2.4.12
bootloader included** where available. See the
[release notes](releases/1.18.0.1.md) and [update guide](ota_user_guide.md) for
the application downloads and installation route.

An opt-in Repeater or Room Server build can carry its application firmware and
the exact board's [OTAFIX 2.4.12 bootloader](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/v0.11.0-OTAFIX2.4.12)
in one application image. After installing that application, it checks the
bootloader once at 70 seconds after startup and updates it when needed. The
check waits for idle radio and OTA activity, and skips a valid matching
bootloader at the same or newer version. Reviewed stock bootloaders are checked by exact installed-image hashes rather
than their names. Recovery and unrecognized bootloaders are excluded from
automatic installation. Ordinary firmware has no automatic bootloader update.

**XIAO and Sense:** The installed bootloader must match the bundled candidate.
The two factory bootloaders have distinct reviewed image hashes; OTAFIX uses
distinct board IDs. A base-XIAO bundle skips a Sense bootloader, and unknown or
mismatched loaders are refused. Existing XIAO bundles contain only the base
candidate, so they do not automatically update a Sense bootloader. These checks
identify the installed bootloader; they cannot verify the physical module if a
different bootloader was previously installed manually.

This follows the internal-flash bridge in
[MeshEnvy firmware PR #2](https://github.com/MeshEnvy/meshcore-firmware/pull/2).
The replacement stays inside the running application's verified image until
Nordic's MBR copies it to the bootloader region. It needs no external staging
storage and does not replace the SoftDevice or change the filesystem layout.

## Build

The complete USA Cascade release build includes ordinary application images,
Full and Reduced sensor profiles, and separate bootloader-carrying Repeater
and Room Server variants for hardware in the pinned normal OTAFIX catalog:

```sh
bash build.sh release-build --firmware-version v1.18.0.1-halo-keymind-cascade-dev
```

The 1.18.0.1 [update utilities](https://github.com/mikecarper/MeshCore/releases/tag/utility-v1.18.0.1-halo-keymind-cascade-dev-03214a16)
also include the exact-board standalone OTAFIX 2.4.12 UF2,
SoftDevice/bootloader DFU ZIP, and SWD HEX downloads. These serve
Companion and Sensor installations as well as the infrastructure roles.
Pending, blocked and retired bootloader profiles are excluded and identified
in the catalog. A normal application image does not update its bootloader.

From the repository root, replace `TARGET` with the exact OTA-enabled nRF52840
Repeater or Room Server target:

```sh
bash build.sh build-firmware TARGET --with-bootloader 2.4.12 \
  --firmware-version v1.18.0.1-halo-keymind-cascade-dev
```

For direct PlatformIO builds, the equivalent opt-in is:

```sh
MESHCORE_NRF52_EMBED_BOOTLOADER=2.4.12 pio run -e TARGET
```

The generator matches the board and storage to the 2.4.12 catalog and verifies
the release asset before embedding only its 40 KiB bootloader region. Host
validation covers all 23 published profiles, including SenseCAP Solar P1.
P1 uses its exact `SCAP_DFU` identity and S140 v7 layout; XIAO's `XIAO_DFU`
identity remains separate despite the shared USB board ID.
Unsupported profiles are refused. The usual application,
capacity and memory checks still apply: the resource adds 40 KiB plus update
code, so some profiles will not fit. The build does not remove features or
shrink the filesystem to make room.

Opt-in outputs include `-with-bootloader-2.4.12` in their names. Ordinary builds
and their output names stay unchanged. The embedded image remains an
application UF2/DFU package, not a combined SoftDevice/bootloader DFU package.

An installed bootloader with valid compatible identity metadata must match the
candidate's board and runtime layout. Modern identity mismatches never fall back
to stock-loader rules. Current all-in-one Repeater and Room Server builds also
include a small, exact-byte stock inventory automatically; no environment variable
or separate legacy file is needed for those images. The initial reviewed inventory
covers reviewed stock images for RAK4631, RAK3401, R1 Neo, T114, T-Echo/Lite,
T1000-E, Wio Tracker L1 and matching XIAO product images. XIAO Plus stock
bootloaders are not admitted until their replacement compatibility is verified. See
[`nrf52_legacy_bootloaders.py`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/scripts/nrf52_legacy_bootloaders.py)
for exact source URLs, file and padded-image hashes, hardware and layout mappings.

Each entry binds the entire 40 KiB bootloader region, the physical build target,
the replacement profile and the S140/application layout. A different byte, shared
USB ID or a matching version string is insufficient. A RAK3401 may legitimately
have the factory RAK4631 loader; the selected build target determines its
board-specific replacement. Related boards do not inherit representative
module/SDK hashes automatically.
Unknown or recovery images remain excluded, and flash protection, MBR geometry,
watchdog and durable-state checks remain mandatory. These are source/image
compatibility checks, not a claim that every transition or power interruption has
been hardware-qualified. Full Companion is **not** an all-in-one updater: use the
separate exact-board USB bootloader ZIP and reinstall its application afterward.

For the separately qualified legacy
RAK4631 transition, also supply the exact installed bootloader image:

```sh
MESHCORE_NRF52_LEGACY_BOOTLOADER=/absolute/path/full.hex \
  bash build.sh build-firmware RAK_4631_repeater --with-bootloader 2.4.12
```

The legacy exception binds the entire padded bootloader region by SHA-256.
It accepts the qualified MeshEnvy `0.9.2-ev1` RAK4631 image, whose full-region
SHA-256 is `64bfe69d56eefc2d0fe51770902aff70ea0cc06cad261d3d219e4a3c0efac8dc`.
A matching version string alone is insufficient; another board's image or an
unknown stock bootloader is not accepted by that explicit exception. It remains
separate from the automatic reviewed-stock inventory.

A P1 already running a XIAO bootloader needs a deliberate identity migration;
the embedded installer does not change one board identity into another. Use
the documented temporary recovery bridge for the installed identity, followed
by the normal P1 bootloader. The all-in-one P1 variant embeds the normal 2.4.12
P1 image, not a recovery image.

## Install and activate

1. Install the application through its normal USB UF2 or serial/Bluetooth DFU
   route. LoRa delivery requires an already working, compatible application OTA
   updater. An internal-only legacy receiver needs a delta from its exact
   installed application; this image does not give a stock bootloader its first
   LoRa update capability.
2. Leave the new application running for at least 70 seconds. It checks once
   when radio and OTA activity are idle, then waits for the usual 1.5-second
   grace and persistence checks before replacement and reboot. To start sooner,
   use the local CLI or authenticated remote admin CLI:

   ```text
   ota bootloader update
   ```

   The command checks the installed bootloader and skips the update if a valid,
   matching bootloader is already 2.4.12 or newer. Repeating the command while
   it is armed is harmless. An `armed` reply means the update is queued until
   the CLI reply and queued/in-flight radio transmissions drain.
3. After reboot, check `get bootloader.ver`. Optional diagnostics are
   `ota bootloader embedded status` and `ota bootloader embedded audit`; save an
   audit before installation to compare the application, filesystem, settings
   and SoftDevice hashes afterward. The older
   `ota bootloader embedded install HASH16` command remains available with the
   confirmation shown by `status`.

Automatic installation is limited to one attempt per firmware image. A saved
40-byte record in `/bl_auto` contains the full SHA-256 of the running image,
including its version trailer. It survives reboots and power cycles. Flashing
a different firmware image permits a new check; recovery bootloaders remain
excluded. Older recovery images without a recognizable normal bootloader label
also skip automatic installation.

The record is committed before inspecting the bootloader, so an interrupted or
failed check also requires manual retry or different firmware. Missing persistent
storage, corrupt records, or an unfinished file transaction block automatic updates.
Manual `ota bootloader update` remains available under its existing identity,
version and persistence checks. The first lock write changes the filesystem
audit hash; it does not change the application or SoftDevice.

The retained reboot guard still covers the initial checks and copy. A guard
from the previous firmware does not prevent a verified different image from
qualifying again. Status reports `auto=locked for this firmware`, `auto=recovery`,
`auto=unrecognized`, `auto=storage unavailable`, or `auto=blocked` when relevant.
These guards prevent repeated update attempts; they cannot repair a bootloader
that fails before the application starts. Erasing the filesystem also erases
the saved firmware lock.

The bridge validates the live application, candidate hash, installed identity,
chip/layout, MBR addresses, watchdog and flash protection before copying. A
protected region is refused; the bridge does not unlock it. Existing staged
`ota bootloader status` and `ota bootloader install MID8 HASH16` commands keep
their separate signed-package behavior. See
[bootloader updates over LoRa](ota_nrf52_bootloader_update.md) for that path.

## Qualification

The [Tower SD 2.4.9 hardware test](test-results/tower_249_embedded_bootloader_2026-10-26.md)
reproduced `last=9` with unchanged application, settings, filesystem and
SoftDevice audit hashes. That loader protects flash before starting the
application, so the embedded bridge refuses it. Its missing normal version
record also causes the automatic check to report `unrecognized`. The separate
signed SD update installed 2.4.12 successfully, reporting `blup:C8`.

The source PR documents one successful RAK4631 migration with unchanged
application, filesystem, settings and SoftDevice hashes. Its later guards have
host checks, but the recorded hardware run predates the final PR build.
Other boards and power-cut recovery are not hardware-qualified by that test.
Keep local recovery access when qualifying a new transition; an older loader's
initial application-delta stage may itself be destructive. The MBR parameter
page is intentionally used during the bootloader copy and is outside the
unchanged-settings claim.
