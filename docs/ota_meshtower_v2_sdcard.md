# MeshTower V2 microSD LoRa OTA

The `Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors` target uses the MeshTower V2 onboard
microSD socket as persistent storage for its own LoRa OTA downloads. It accepts
both full `.mota` images and in-place delta `.mota` images. After verification,
the matching SD-aware OTAFIX bootloader reads the staged file from the card and
programs the nRF52840 application region.

The pin assignment follows the
[Heltec MeshTower V2 partial reference circuit](https://resource.heltec.cn/download/MeshTower-V2/schematic/MeshTower_V2_Partial_Reference_Circuit.pdf):

| Signal | nRF52840 pin | Arduino pin number |
|---|---:|---:|
| SD CS | P1.00 | 32 |
| SD MOSI | P1.01 | 33 |
| SD SCK | P0.06 | 6 |
| SD MISO | P0.26 | 26 |

The SD socket uses its own SPI peripheral, so card traffic does not change the
LoRa radio pinout.

## Card requirements

Use a FAT16, FAT32, or exFAT card with an MBR partition table whose first
partition starts after sector 1. This is the normal layout produced by most SD
formatters. GPT and unpartitioned "super-floppy" layouts are rejected.

MeshCore creates `/meshcore-ota.mota` as a contiguous file. Sector 1, which is
outside the partition, holds a checksummed bootloader handoff record. The
firmware validates this gap before writing it; an incompatible card layout
fails safely without modifying sector 1.

## Capacity and update types

The card removes the internal-flash staging limit. The `.mota` container may be
much larger than the old internal staging gap, and either a full image or an
in-place delta may be downloaded. The installed firmware itself must still fit
the nRF52840 application region below InternalFS (ending at `0xED000`); SD
storage does not increase the MCU's executable flash.

For this S140 v6 target, the maximum application image including its `EndF`
trailer is `0xC7000` bytes (815,104 bytes). To package a full self-update:

```bash
motatool build --fw ./Heltec_tower_v2_sdcard-new.hex --out-dir ./motas
motatool verify ./motas/*.mota
```

A delta uses the exact installed image as its base. The normal `0x98000`
workspace remains compatible. If either image is larger than that legacy
limit, build the patch with the SD target's larger workspace:

```bash
motatool build \
  --base ./Heltec_tower_v2_sdcard-running.hex \
  --fw ./Heltec_tower_v2_sdcard-new.hex \
  --patch-type in-place \
  --inplace-memory 0xC7000 \
  --out-dir ./motas
motatool verify ./motas/*.mota
```

The download is resumable because the partial `.mota` stays on the card.
Once it reaches `ready`, `ota install` performs the final verification,
publishes the bootloader handoff, and reboots. Keep the card inserted through
the reboot and installation.

The SD-aware bootloader is mandatory. `ota install` refuses to reboot if the
bootloader capability marker does not advertise SD staging and the selected
codec. Existing `Heltec_tower_v2_repeater` firmware continues to use the
internal-flash delta path and is unchanged.

## SD card CLI

The SD-backed target provides these CLI commands:

```text
set sdcard format [--force]
set sdcard erase [--force]
get sdcard
get sdcard *
get sdcard format
get sdcard erase
get sdcard free
get sdcard ls
get sdcard ls 2
get sdcard dir 3
```

`format` creates a new FAT16, FAT32, or exFAT filesystem according to card
size. `erase` first uses the card's raw media erase command and then formats it,
so a successful erase finishes with a usable filesystem. Both operations
destroy all data on the card and cancel any staged OTA download.

The firmware records successful format and erase completion times in RAM.
Repeating the same operation within five minutes is rejected unless `--force`
is present. Format and erase have independent cooldowns. Because erase also
formats the card, a successful erase updates both timestamps. The timestamps
reset when the device reboots. The `get sdcard` age queries report how long ago
each operation completed. `get sdcard free` reports used and free filesystem
space in human-readable binary units.

`get sdcard ls` and `get sdcard dir` recursively list files on the card, four
files per page. A bare command shows page 1; append a positive page number to
move through the remaining results. Each row includes the path and a compact
file size. The header reports the selected page, total pages, and total files.

## Persistent OTA archive and seeder

On the SD-backed target, automatic OTA archiving is on by default. While the
temporary OTA radio is active, the node requests full catalogs from seeders and
saves every complete mOTA it discovers, including firmware for other hardware
targets and codecs that this node cannot install. Archive downloads use the
same per-block Merkle proof checks as an install download, but archived images
are never selected for local installation.

Completed containers are stored as `/mota/<manifest-id>.mota`. An interrupted
download remains `/mota/<manifest-id>.part` and resumes when that mOTA is seen
again. Completed files survive reboot, are enumerated on the first archive
access or TempRadio window, and are advertised and served directly from SD.
The SD target supports the protocol maximum served set: its own running
firmware plus up to 254 archived mOTAs.

Automatic capture preserves an 8 MiB free-space reserve for manual
`/meshcore-ota.mota` installation staging. It stops starting new archive files
when the next file would cross that reserve. Archive allocation first tries the
fast contiguous path and then falls back to an ordinary fragmented FAT file;
only the bootloader staging file requires contiguous sectors.

### Preload many mOTAs from a computer

You can populate the archive much faster on a computer than over LoRa. Use only
complete, verified `.mota` containers. Do not copy firmware `.bin`, `.hex`,
`.zip`, or `.part` files into the archive.

Install the standalone [`motatool`](https://github.com/vk496/motatool) first if
it is not already available:

```bash
git clone https://github.com/vk496/motatool.git
cargo install --path ./motatool
```

The on-card filename is part of the archive index and has a strict format:

```text
/mota/<merkle_root>.mota
```

`<merkle_root>` is the eight-hex-digit value printed by `motatool inspect`. It
is also the mOTA's four-byte manifest ID. The extension must be lowercase, the
file must be directly inside `/mota`, and descriptive release filenames are
not indexed. For example, if inspection reports:

```text
merkle_root    : ABCD1234
```

copy that container to:

```text
/mota/abcd1234.mota
```

If two containers have the same Merkle root, they have the same protocol ID
and cannot both be present. Keep only the intended one. The current SD seeder
can index up to 254 archived files. A served file must use a logical block size
of at most 1024 bytes and contain at most 2048 blocks; check `block_size` and
`block_count` in `motatool inspect` when importing unusually large images.
Files outside that serve geometry are not counted or advertised. If a malformed
`/mota/<id>.mota` conflicts with a newly discovered valid image, the repeater
preserves the malformed file as `<id>.bad` through `<id>.bad9` and downloads a
clean replacement instead of treating path existence as a valid cache hit.

To prepare and load the card:

1. Format it as described under [Card requirements](#card-requirements). The
   easiest way to guarantee the expected layout is to insert it in the
   repeater, run `set sdcard format`, power the repeater off, and then move the
   card to the computer. Formatting destroys the existing card contents.
2. Mount the card on the computer and create a directory named `mota` at the
   filesystem root. Do not use a nested directory such as `/firmware/mota`.
3. Run `motatool verify FILE.mota` for every source file. Do not copy a file
   that reports `FAIL`.
4. Run `motatool inspect FILE.mota`, read its `merkle_root`, and copy the file
   to `/mota/<lowercase-merkle-root>.mota` on the card.
5. Flush pending writes, safely eject the card, power the repeater off, insert
   the card, and boot
   `Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors`.

On Linux or macOS, this Bash example verifies and imports every `.mota` from
`./motas`. Replace the example mount path before running it:

```bash
card_mount=/media/YOU/MESHCORE
mkdir -p "$card_mount/mota"

shopt -s nullglob
for image in ./motas/*.mota; do
  motatool verify "$image" || exit 1
  mid=$(motatool inspect "$image" |
    awk '$1 == "merkle_root" { print tolower($3) }')
  if [[ ! $mid =~ ^[0-9a-f]{8}$ ]]; then
    echo "Could not read the Merkle root from: $image" >&2
    exit 1
  fi

  destination="$card_mount/mota/$mid.mota"
  if [[ -e $destination ]]; then
    cmp -s "$image" "$destination" || {
      echo "Different containers have the same ID: $mid" >&2
      exit 1
    }
  else
    cp "$image" "$destination"
  fi
done
sync
```

On Windows, create `E:\mota`, inspect each source with `motatool inspect`, and
rename it to the reported lowercase root in the same way. Safely eject the
drive after all copies finish.

After boot, scan the archive and confirm the card contents from the repeater
console:

```text
get sdcard ls
get sdcard ls 2
ota cache
ota folder
```

`ota cache` should report the imported count. `ota folder` reports the current
served count; once OTA serving starts in TempRadio, that set also includes the
repeater's running firmware. If the files appear in `get sdcard ls` but not in
the served count, check their exact names, run `motatool verify` again, and
inspect their block geometry. It is fine to run `ota cache off` for a curated,
read-mostly archive: that disables capture of new mOTAs but continues serving
every valid file already on the card.

### Serve the preloaded archive over TempRadio

LoRa OTA traffic exists only during an active temporary-radio window. The SD
repeater, each receiving node, and every intermediate repeater in the path need
overlapping windows on the same temporary channel. Use a frequency permitted
for the node's configured region. This North American example uses the
recommended fast OTA settings and a 120-minute window:

```text
tempradio 909.950,250,5,5,120
```

Start the farthest receiving node first, then intermediate repeaters, and the
SD source last so their windows overlap for as long as possible. Before or
during the source window, `ota cache` makes the source scan and attach the SD
archive. Entry into TempRadio automatically triggers an OTA advertisement
burst; `ota announce` can send another advertisement immediately.

On a receiving OTA-capable node, allow a few seconds for catalog exchange,
then run:

```text
ota ls
ota ls 2
ota get 1 flash
ota status
```

Use `ota ls 2`, `ota ls 3`, and so on when the source advertises more than the
two rows that fit in one remote CLI reply. The update numbers are global across
pages, so select the displayed number for the desired target instead of assuming
it is always `1`. A receiver retains the complete protocol catalog and verifies
every transferred block. It still applies its normal target, hardware, codec,
signature, and installation checks. The SD
repeater may advertise images for many hardware families; it never installs
those archive files merely because it serves them.

When a temporary window expires or a node reboots, it returns to its saved
radio settings and OTA transfer stops. The archive remains on the card. Start
another set of overlapping `tempradio` windows to resume an interrupted
download, or use synchronized `tempradioat` entries for a scheduled window.

Archive capture is lower priority than operator work. An explicit `ota pull`
to install storage or a host folder immediately takes the single receive slot;
the archive partial is checkpointed and resumes later. Existing cached files
can still answer peer requests while another archive file is downloading.

Use these commands to inspect or control automatic capture:

```text
ota cache
ota cache on
ota cache off
ota config cache on
ota config cache off
```

The on/off choice is saved on the SD card. Turning capture off stops new
downloads but keeps serving files already cached. Formatting or erasing the
card removes both the archive and its off marker, so a newly formatted card
returns to the default-on setting. `ota config sdseed on|off` is also accepted
as an alias.
