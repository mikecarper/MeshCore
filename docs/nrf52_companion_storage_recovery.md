# nRF52 Companion automatic ExtraFS recovery

Companion builds using internal ExtraFS reserve 100 KiB at
`0xD4000..0xED000`. Identity and preferences remain in the separate 28 KiB
primary store at `0xED000..0xF4000`. This applies to internal-ExtraFS Companion
builds, including Full, not repeaters or external QSPI storage.

At startup the firmware:

1. Mounts primary storage without erasing nonblank media. Only proven-blank
   primary storage may be initialized automatically.
2. Checks the exact ExtraFS address, size, block geometry, and application
   linker reservation, then attempts a non-formatting secondary mount.
3. Validates primary filesystem metadata before any destructive secondary
   recovery. An unavailable primary store blocks recovery and identity replacement.
4. Keeps an already mounted, metadata-valid ExtraFS unchanged. If its initial
   mount or validation failed, unmounts and retries once without formatting.
5. If the retry remains unusable, automatically formats only the reserved
   100 KiB region, then remounts and validates it. There is one format attempt
   per boot; failure keeps the contact/channel write quarantine active.
6. Runs the existing verified migration from primary into ExtraFS. Primary
   contact/channel sources are retired only after every destination is copied,
   read back, and the migration transaction is committed. Identity and
   preferences stay on primary.

No terminal repair command, phone intervention, or bootloader update is needed
for this recovery path when a compatible application/layout is already used.
After successful recovery, the Companion storage API reports 100 KiB total
rather than the 28 KiB primary fallback.

## Data-loss boundary

Automatic rebuilding prioritizes a usable contact/channel store over retaining
an unreadable secondary filesystem. Data stored only in that damaged region
can be lost. Surviving primary files are migrated, but may not contain the
latest secondary-only changes. An API backup remains advisable before flashing.
Primary storage is never formatted by this recovery path.

This is mount/metadata recovery, not forensic file recovery. An invalid
migration journal, unreadable individual contact page, primary-source error,
full filesystem, or failed migration cleanup does **not** on its own trigger a
format of a mountable, metadata-valid secondary store. Those cases retain the
existing validation, retry, and incomplete-load protections.

## Regression coverage

`test/test_internal_secondary_fs_repair` exercises healthy/no-format startup,
successful remount, failed remount/traversal followed by one repair, and
format/remount/validation failure. `test/test_nrf52_extrafs_contract.py` checks
primary-first ordering, internal-only geometry guards, quarantine handling,
and the separation between migration failures and destructive recovery.

## T1000-E hardware qualification (2026-09-03)

A device reporting 25/28 KiB primary fallback and contact enumeration error 5
was flashed with the recovery build. With no explicit repair command, startup
activated 100 KiB ExtraFS and loaded all 125 contacts and 40 channel slots.
Identity, preferences, radio settings, and channels matched the pre-update API
snapshot. Add, edit, and delete of an owned temporary contact each survived a
separate normal reboot; the device ended with the original 125 contacts and
27/100 KiB usage. The 161 offline/native regression tests and target build passed.

This was not lossless recovery of every historical RAM value: 21 contacts had
older advertisement/local-modification timestamps, including one older advertised
location. No contact was missing, and no computer-backup restoration was used.
A subsequent read-only scan found no readable cached advert for any of the 125
contacts. Without a surviving newer copy, firmware cannot infer those earlier
values; receiving new verified advertisements can refresh them normally.
