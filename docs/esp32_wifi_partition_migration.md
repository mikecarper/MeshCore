<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/esp32_wifi_partition_migration/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32 legacy-partition migration over USB, Wi-Fi or LoRa

The partition-migration bridge lets a supported ESP32 node move from a small
dual-OTA layout to a verified larger dual-OTA layout without cable flashing or
a full-chip erase. It fits a legacy 1.25 MiB slot. The Wi-Fi version exposes an
updater for the Full image after migration. The LoRa version preserves a
verified old OTA receiver so it can fetch the Full image into the expanded
inactive slot. On 8/16 MiB flash the bridge restores identity before handing
off; the 4 MiB route keeps identity staged until the Full image's first boot.

The distinct USB version performs verified identity/configuration restoration
before the host writes the final app. It keeps application USB CDC disabled
during flash operations and prevents Arduino startup from automatically
erasing damaged/full NVS. S3/C3 return to ROM download automatically once
restoration settles; original ESP32 reports matching-MAC settled UART status
before the host uses DTR/RTS. Neither route needs physical BOOT access, but
classic boards require their working USB-UART automatic-reset circuit.

The host must verify the same physical USB device and original chip MAC, the
exact target table, CRC-valid NVS restored/no-pending markers, and current OTA
metadata before flashing the unchanged final app. USB reappearance, a timer,
or settled status alone is not recovery proof. An interrupted migration retains
its pending host record and device-side restoration markers for a safe retry.

It has explicit, byte-for-byte generated target plans for the 4 MiB MeshCore
dual-OTA Full table and Arduino ESP32 `default_8MB.csv` and
`default_16MB.csv`. It does not guess a table for other flash sizes. Adding a
flash size later means adding one reviewed target plan and its generated
table-prefix test; the bridge core is otherwise shared.

| Flash size | Target table | app0 / app1 size | Typical board |
| --- | --- | --- | --- |
| 4 MiB | `variants/dual_ota_full_4MB.csv` | 0x1F0000 / 0x1F0000 | ThinkNode M2/M5, LilyGo T3S3, Nibble, Heltec CT62, Waveshare LR1121 observers |
| 8 MiB | `default_8MB.csv` | 0x330000 / 0x330000 | Seeed XIAO ESP32-S3, Heltec V3 |
| 16 MiB | `default_16MB.csv` | 0x640000 / 0x640000 | Heltec V4 / V4.3, Station G2 |

These are the supported migration plans. The 8/16 MiB tables retain more
filesystem storage and a coredump partition; their application slots are not
the absolute maximum permitted by the physical flash capacity.

## Ordinary Full releases and installation

Ordinary ESP32 node releases use Full for each exact board and firmware role.
The existing RAM and application-size guards still apply: a failed Full
qualification fails that release target rather than publishing a smaller
ordinary image. Direct `--standard` builds and reduced identities remain
compatibility/development tools outside the ordinary release selection.

An app-only update cannot change the installed partition table. If a device
already has the required layout, use the matching Full application through a
supported updater. If its layout needs expansion, use a verified migration
ZIP for its exact entry in `--list-boards` and an updater supported by the old
image. Otherwise, install the exact board/role's Full `-merged.bin` through
USB/serial using that board's flashing procedure; the merged image contains
the bootloader, partition table, and application. Never upload a merged image
through an app-only browser or LoRa updater.

The new Full defaults outside the migration catalog, including ESP32-C6
boards and other boards introduced with `min_spiffs.csv`, have no reviewed wireless
expansion package in this recipe. If their Full app fits the unchanged live
layout and update identity, an ordinary app update can suffice. A table change
requires USB/serial until a matching migration package is reviewed. A Full
image fitting physical flash does not by itself prove it can be installed
through an old updater. Full Companion images with a single app slot likewise
need USB/serial for local firmware replacement; their host-backed LoRa sending
role does not provide a second local update slot.

## Waveshare ESP32-S3 LR1121 observer migration

Only the Waveshare **repeater observer** and **room-server observer** select the
larger 4 MiB table. Ordinary repeater, room, Companion and KISS recipes retain
`min_spiffs.csv`. The two exact migration selections are:

```bash
sh scripts/build_esp32_partition_migration.sh \
  --board waveshare-s3-lr1121-observer-repeater \
  --board waveshare-s3-lr1121-observer-room-server \
  --version v1.18.0-test --radio-preset usa-cascadia --profile cascade --dry-run
```

Remove `--dry-run` only to build and verify the packages from a clean commit;
this command does not flash hardware. Each observer role has its own exact-target
Partition Expander. Both its Wi-Fi application and first-stage LoRa package use
that same configuration-preserving bridge, not the older identity-only LoRa
handoff. It accepts a reviewed source layout, stages and verifies identity and
supported configuration before destructive writes, and refuses unknown layouts
or saved data that cannot be preserved safely. The final table has two
1984 KiB application slots and only **64 KiB SPIFFS**.

The bridge can migrate from either existing OTA slot. After reboot it must
verify the new table, private key and saved configuration before enabling the
LoRa receiver. It uses the saved primary radio profile and fetches only the
exact observer role's Full image. The node name, ACL, radio 1/radio 2 and saved
GPS preferences remain part of the configuration handoff; temporary sessions
are not converted into permanent preferences. Already-expanded boards use the
existing verified-image/no-handoff safety checks rather than migrating again.

The recipes reuse the real LR1121 board implementation, including its 1.8 V
TCXO and HP-PA/RF-switch wiring. The first-stage image must pass the legacy
1.25 MiB size limit and both final-image and mOTA staging guards. A failed guard
must fail the package, not produce a reduced image. These are build/host-test
contracts, **not physical qualification**: test both A/B placements, restored
settings and interruptions on a sacrificial Waveshare before remote deployment.
The ESP32's single partition-table-sector erase/write is still not atomic;
power loss in that window can require cable recovery.

## Repeatable ESP32 build recipe

From a clean commit, run the shell menu in WSL/Linux:

```bash
sh scripts/build_esp32_partition_migration.sh
```

Choose one exact board/role, or **all listed board/role pairs**. The menu
also offers all repeaters, all room servers, or all sensors separately. It asks
for the firmware version, radio preset, and profile, then builds Full application
images first, the available bridge(s) for each selected board, and verifies each
ZIP. Only one PlatformIO process runs at a time; `build.sh` can clean the
shared `.pio/build` tree between Full targets. The same menu can be run
non-interactively:

```bash
sh scripts/build_esp32_partition_migration.sh --board heltec-v4 \
  --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
sh scripts/build_esp32_partition_migration.sh --all \
  --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
sh scripts/build_esp32_partition_migration.sh --role sensor \
  --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
```

`--all` creates one verified ZIP per listed board/role pair and a release
ZIP containing all of them, their hashes, and a manifest. This is a **local
release bundle**, not an upload to GitHub Releases. It is not an archive of
old firmware binaries. `--list-boards` shows the configured choices.
The release fails instead of publishing a partial bundle if one board package
is absent or fails verification.

The underlying Python recipe remains available:

```bash
python3 -B scripts/build_esp32_partition_migration.py \
  --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
```

The default builds all configured board/role recipes. Add `--board heltec-v4`
or `--board xiao-s3-wio` to build one; repeat `--board` for a selected set.
Use `--dry-run` to inspect the command order without building. The resulting
ZIPs appear under `.releases/esp32-expanded-<commit>/packages/`. The recipe
requires a clean checkout so each ZIP identifies the firmware commit it was
built from. It creates files only; it does not flash a device or erase flash.

## Independent USB firmware set

The USB intermediate firmware has six distinct chip/flash-capacity builds:

| Chip | Flash capacities | USB completion |
| --- | --- | --- |
| Original ESP32 | 4, 8 MiB | Settled UART status, then host DTR/RTS |
| ESP32-C3 | 4 MiB | Automatic ROM return |
| ESP32-S3 | 4, 8, 16 MiB | Automatic ROM return |

To build only this complete set, without rebuilding or relabeling any Full
firmware release, run serially in WSL/Linux:

```bash
python3 -B scripts/build_esp32_partition_migration.py --usb-only
```

This creates `meshcore-usb-partition-bridges.zip` under
`.releases/esp32-usb-bridges-<commit>/`. Its manifest records each binary's chip,
capacity, bytes and SHA-256, plus the utility source commit, dirty-tree state
and runtime/configuration source hashes. All six are mandatory, and both the
USB migration and NVS no-autoerase guard markers are verified. Existing Wi-Fi
and LoRa artifacts remain unchanged. Exact new board/role migration packages
also include their matching `usb-bridge.bin`.

A host can pair this standalone set with an older, exact Full migration ZIP;
the Full image retains its original release/source identity. Meshfirmware
downloads a versioned, checksum-pinned published USB set when a qualified
older package has no USB bridge. It does not use an untested `latest` bridge,
and an ordinary update that fits does not download migration utilities.
For an offline or explicitly selected set, use `MESHFIRMWARE_USB_MIGRATION_SET`,
or place the ZIP beside the flasher at `tools/meshcore-usb-partition-bridges.zip`.
The original Full
package must still match the selected final image and prove the target layout.
No app-only upload or manual partition-table write substitutes for that proof.

The catalog covers canonical ESP32 repeater, room-server, and sensor roles on
4, 8, and 16 MiB chip-family plans, including historical 1.25 MiB candidates
now built with expanded tables. This includes ESP32-S3, original ESP32, and
ESP32-C3 boards with an evidenced old default layout. `--list-boards` is the
authoritative list of exact targets; specialty ESP-NOW/observer aliases are
separate identities and are not packaged under their old IDs. With Wi-Fi OTA
they can migrate to the same physical board's canonical role image. The Wi-Fi
bridge verifies and restores its supported saved settings; other settings may
need to be recreated. The LoRa route still requires an exact old target ID. The
catalog omits boards with no evidenced legacy default-layout build, such as
boards introduced with `min_spiffs.csv` or another non-1.25 MiB layout. Some
listed boards use larger slots today but had historical default-layout builds.
A listed package is usable only when the installed old image supports the
selected updater and the bridge verifies its live source table. Menu
inclusion does **not** override either check.

Every listed board/role has Wi-Fi and LoRa bridge recipes. Heltec V4 and XIAO
S3 WIO repeaters retain their board-specific bridge names; the other entries
use matching original-ESP32, ESP32-S3, or ESP32-C3 chip-family bridges. LoRa
still requires a verified same-target old receiver, and 4 MiB boards require
the slot-B-only sequence below. A 4 MiB Full build offers 1,984 KiB per slot.
The packager verifies both the Full application's slot fit and its mOTA
staging overhead. It supports at most 4096 blocks of 2 KiB for the Full
transfer, and records the seeder proof-scratch requirement in the manifest.
Older 4 KiB-scratch seeders are limited to 1024 blocks. A package fails if
its required Full LoRa transfer cannot fit the supported staging/block limits.

Other ESP32 boards need a reviewed source updater, chip/flash-size bridge,
verified Full image, and board entry before they can be added. The LoRa route
also needs an exact old OTA target ID and a non-overlapping receiver handoff.
Matching flash capacity alone is not enough to claim a package is safe.

## What the bridge preserves

Before it touches the partition table, the bridge reads the historical
`/identity/_main.id` SPIFFS file. It requires all 96 bytes (32-byte public key
followed by its 64-byte private key), stages them in an NVS namespace that is
unchanged by the source and target layouts, then changes the table. The Wi-Fi
bridge and 8/16 MiB LoRa bridge restore the staged value into expanded SPIFFS,
read it back byte-for-byte, and only then clear the NVS staging record. On the
4 MiB LoRa route, the Full application's first boot performs this restoration.

The Wi-Fi bridge also stages and verifies its supported saved configuration:
node name and preferences, radio profiles, ACL and login replay state, region
keys, and selected network/OTA, filtering, display, and telemetry settings.
It refuses migration if the saved files cannot fit or verify in NVS. The older
LoRa handoff route preserves only the private identity, so saved settings may
reset. The board/role-specific Partition Expander routes preserve their
supported configuration and automatically fetch the exact-target Full image;
follow the package README for those routes.

The old SPIFFS image is deliberately not raw-copied into an expanded partition:
SPIFFS is not safe to resize that way. Files outside the supported preservation
set, including logs and temporary radio sessions, are not preserved.

Migration does erase the 4 KiB partition-table sector and the destination app
sectors. Expanded SPIFFS can be reformatted. It never issues a full-chip erase.

## Eligible source layout

The bridge only accepts a source table that has all of the following:

- NVS at `0x9000`, size `0x5000`, and OTA metadata at `0xE000`, size `0x2000`.
- Two distinct, non-empty OTA application slots and a non-empty SPIFFS
  partition, all inside the physical flash size.
- A known exact 4, 8, or 16 MiB physical flash capacity and a source table
  different from that capacity's target table.

This means the source app-slot and SPIFFS sizes can vary; the bridge validates
the live geometry instead of carrying board-name rules. It also ensures it is
running from one source OTA slot. If it arrived in B, it CRC-copies itself to
the future A address, selects A in OTA metadata, writes the table, and
restarts. The Wi-Fi route therefore works whether the browser OTA updater
placed the bridge in legacy A or B. Single-app source layouts cannot use this
route.

Keep power stable while the bridge is replacing the 4 KiB table sector. A loss
of power before that point leaves the old table and source intact. After that
small critical write, the selected A copy of the bridge is the recovery path.
The stock ESP32 bootloader reads one partition-table sector; rewriting that
sector is **not atomic**. A power loss during its erase/write window can leave
an unreadable table and require cable recovery. This recipe must not be called
power-loss-proof on a device whose USB/serial flash port is inaccessible.

## Migrate a Seeed XIAO ESP32-S3

Use `--board xiao-s3-wio` with the recipe above. Its Wi-Fi bridge is a
legacy-slot application image; its Full image keeps the old repeater's exact
LoRa OTA target ID. Upload `wifi-bridge.bin` from the ZIP through the legacy
node's existing Wi-Fi browser updater.
Then join `MeshCore-Migrate` and wait for **Expanded layout ready**. Its Wi-Fi
password is the admin password saved on the old node, so only someone who can
already administer the node can join and upload firmware. Upload
`full-application.bin` at `/update`.
Do not upload an `-merged.bin`: merged images include
bootloader and table offsets for cable flashing, not browser OTA.

WPA2 needs a password of at least 8 printable ASCII characters. If the saved
admin password is shorter, uses other characters, or was never saved (the node
still runs on its built-in default), the bridge falls back to
`meshcore-migrate`. That password is published here, so anyone nearby could
then join and upload firmware. Before uploading the bridge, set an admin
password of 8 to 15 characters with `password <new password>`. A LoRa
first-stage bridge does not carry saved settings into the expanded layout, so
its Wi-Fi recovery page after the table change normally uses the fallback.

`xiao_s3_partition_legacy_seed` is a disposable-hardware test image only. It
models the legacy table and an identity file; it is not a deployable repeater.

## LoRa-only migration of a listed role

The exact old board/role must already support MeshCore LoRa mOTA and have a valid
EndF image identity. The LoRa bridge checks the old app's target ID and body
hash before any partition-table write; an unsupported or damaged receiver is
refused. Keep a capable seeder on the old receiver's compiled default radio
profile before starting: this route may reset saved radio settings. Verify
that the seeder lists the Full image and has the manifest's required proof
scratch before installing the first-stage bridge.

On 8/16 MiB flash the bridge can run from either legacy A or B. It copies
legacy B to the future B address, boots the bridge once to restore the private
key in expanded SPIFFS, then boots the preserved old receiver. That receiver
can fetch the Full image into its now-expanded inactive slot.

On 4 MiB flash the expanded app1 overlaps old app1, so that copy cannot be
used. A verified old same-target receiver must remain in old A and the bridge
must be installed in old B; a bridge in A refuses migration. If the old node
is running B, first install and boot a working same-target old receiver in A,
then install the bridge in B. After expansion the old receiver in A may use a
temporary identity. The original key stays staged in NVS until the Full image
restores it on first boot. Complete the second update before treating that
device as the migrated node.

The board-specific LoRa bridges are `heltec_v4_partition_migrator_lora_repeater`
and `xiao_s3_partition_migrator_lora_repeater`; the other listed roles use their
configured chip-family LoRa bridges. Build the final application with
`bash build.sh build-firmware <target> --full-exact` so its mOTA target ID
still matches the old role. The repeatable recipe already uses this selector.
`scripts/package_esp32_partition_migration.py`
checks the images, partition tables, hashes and mOTA containers and creates
one Wi-Fi/LoRa migration ZIP for each selected exact board/role. The ZIP README gives
the exact `ota ls`, `ota pull <id> flash`, `ota install` sequence.

An old image without LoRa mOTA cannot use the LoRa-only route. Use its working
Wi-Fi updater with a listed package, or USB/serial if no supported updater or
package is available.

## Heltec V4 / V4.3

Build `heltec_v4_partition_migrator`, upload it through the legacy Wi-Fi
updater, then upload the Full V4 app-only image after the bridge reports ready.
The 16 MiB target table creates two 6.25 MiB OTA slots. See the
[V4 build notes](heltec_v4_wifi_partition_migration.md) for its build command.
