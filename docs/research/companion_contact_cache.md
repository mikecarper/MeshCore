<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/research/companion_contact_cache/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Companion contact caches

Full Companions on ESP32 and nRF52 without PSRAM, plus nRF52 USB/BLE
profiles with more than 300 contacts, keep 16 outgoing paths
and 16 shared secrets in RAM. Every contact remains in the contact table;
selecting a different contact loads its saved path as needed. This recovers
about 25.4 KiB of internal RAM in the 350-contact ESP32 qualification builds.

See the [qualification results](../test-results/companion_contact_cache_results.md) for the
six-board RAM comparison, physical tests, and shared-secret timings.

PSRAM boards retain their existing complete contact table in external RAM.
Lower-contact legacy profiles and ordinary ESP32 USB/BLE/WiFi Companion
profiles retain inline paths and secrets. High-contact nRF52 USB/BLE profiles
(including Wio Tracker L1) enable the same cache,
keeping all 350 contacts while making room for the shared 256-frame offline/DM
pool. The build flag `MESH_CONTACT_CACHE=0` or `1` overrides this policy for
qualification; it is not a runtime feature switch.

The ordinary Wio Tracker L1 BLE build reproduced a runtime RAM failure with
48,964 bytes available against 59,968 required. Enabling the existing cache
for high-contact nRF52 profiles recovered 30,200 bytes: the linked build has
79,164 bytes available and a 19,196-byte margin against the unchanged budget.
It keeps 350 contacts, 40 channels and 256 normal offline frames. As on the
cached RAK profiles, up to 15 held one-key DMs temporarily use queue slots;
their storage is shared instead of reserving a second pool. This is a
linked-capacity result, not a physical load/soak test.

## Paths and persistence

The 16 resident paths use least-recently-used replacement. Contact records
keep small handles to their saved paths, including 1-, 2-, and 3-byte hop
hashes. The existing 152-byte contact record and Companion app frame formats
are unchanged. ESP32 uses `/contacts3`; nRF52 also supports its paged contact
store and migration from `/contacts3`. An erase is not required.

A hot path needs no flash read. A cold path is read from its existing contact
record. ESP32 reuses one open contact-file reader to avoid repeated SPIFFS
metadata scans during synchronization and saving. It closes that reader
before replacing the file or recovering a saved transaction.

Changing a copied contact preserves the old path for a pending app response
or rollback. Dirty entries are saved before eviction. If storage cannot be
read, written, or represented completely, the operation fails instead of
sending along a different route. Contact I/O failures request bounded recovery
between callbacks. A raw cached-path read fault first rechecks the current
immutable paths without resetting accepted RAM changes or their save deadline.
Persistent faults or uncertain file publication reload a readable live image,
otherwise a readable backup, and discard only unrecoverable contact data if
neither works. Recovery releases a pending contact-list snapshot before resetting
the table but preserves its requesting route; the changed table revision starts
a fresh list that can finish normally. Explicit session cancellation still stops
the stream. nRF52 preserves
healthy pages when another page is damaged. No archival `.bad` files are kept.
If damaged contacts cannot be removed, the node remains usable with volatile
contacts; explicit saves still report failure, while reboot/shutdown remain
available. Identity, credentials, channels and unrelated settings are not erased.
Allocation/capacity failures and shared filesystem-migration authority errors
are not corrupt-contact evidence and do not authorize this reset. Failed source
discovery also preserves contact files; if authority stays unknown, use volatile
contacts instead of deleting readable pages or selecting a potentially stale view.
The path checksum also rejects
a cold path changed underneath a live handle; it does not add a new checksum
to the legacy ESP32 on-disk format.

## Shared secrets and power

Both platforms reuse the 16 most recent secrets from RAM. Cache misses use a
platform-specific policy:

| Platform | Miss behavior |
| --- | --- |
| ESP32 | Recalculate the secret without reading or writing a secret file. |
| nRF52 | Try a saved LittleFS entry, then recalculate if it is unavailable. |

On the V4, a measured SPIFFS secret lookup took 132,427 microseconds while
key exchange took 21,932 microseconds. ESP32 therefore uses recalculation on
a RAM-cache miss. It avoids secret-file writes and reduces time awake for
this measured workload. These are elapsed times, not measurements of energy
in joules; other ESP32 hardware has not been physically benchmarked.

On a RAK3401, a LittleFS lookup took 7,812 microseconds versus 31,250
microseconds for key exchange. The nRF52 backend therefore uses saved flash
entries to avoid repeat key exchange when a usable entry exists, including
after reboot. Saved entries match the full
peer public key and a fingerprint of the local key pair, and have a record
checksum. Importing a different private or public key invalidates old results.
A corrupt or missing saved entry is recalculated. The native tests use the
firmware's actual Ed25519 key-exchange library and verify agreement with the
peer's independently calculated secret.

Derived secrets are expendable. Their storage keeps a reserve for contacts,
preferences and filesystem metadata; a full filesystem leaves the calculated
secret usable in RAM. nRF52 packs 56 entries into a 3,844-byte page to use its
4 KiB flash blocks efficiently. Small filesystems may not have space to persist
secrets for all 350 contacts. A miss without a usable saved entry still
performs key exchange.

`MESH_CONTACT_SECRET_FLASH_CACHE=0` or `1` overrides the miss policy for
qualification builds. The optional ESP32 flash backend groups eight entries
per file. The normal ESP32 policy does not create or use those files.

## Inspect the cache

From the Full Companion's ASCII USB terminal or authenticated local CLI:

```text
get contact.cache
```

Cached builds report `paths=16 secrets=16`, `miss=calculate` or `miss=flash`,
plus these boot-session counters:

| Field | Meaning |
| --- | --- |
| `ram_hits` | Shared secret reused from RAM. |
| `flash_hits` | Shared secret loaded from flash without recalculation. |
| `calculations` | Shared-secret key exchanges performed. |
| `save_skips` | Calculated secrets that could not be persisted, including storage reserve/backoff. |

Uncached builds report `paths=inline secrets=inline`. The normal `memory`
command on supported ESP32 Full Companions shows live heap and queue capacity.

`get contact.cache.timing` reports the last successful flash lookup and last
key calculation in microseconds. Zero means no such operation has completed
since boot. These timings exclude secret writes and are useful for comparing
the work avoided on a valid flash hit.

## Offline messages during mOTA

Contact caches are independent of the offline message queue. Builds using
the shared mOTA queue retain **256 frames normally and 128 while mOTA owns
its workspace**, then restore 256. No unread frames are discarded to start
mOTA: synchronize the queue and retry if it exceeds the retained capacity.
PSRAM-backed queues keep their existing capacity and allocation policy.

Each queue frame occupies 177 bytes. Retaining 32 instead of 128 would make
another 16,992 bytes (16.6 KiB) available inside the shared storage. It would
not increase free heap by itself: the normal 256-frame storage is statically
reserved, and the current mOTA context already fits in the loaned half. More
mOTA scratch data would have to share that space to obtain an additional RAM
saving. These changes keep the existing 128-frame mOTA limit.

## Build the six capacity trials

The optional environments in `platformio.nimble.ini` combine NimBLE, these
contact caches, and the shared mOTA queue at **350 contacts, 40 channels and
256 normal offline frames**. They remain outside the ordinary release matrix.
The ordinary six constrained release recipes still use their established
150-contact limits pending wider hardware qualification.

Enable `platformio.nimble.ini` as described in the
[NimBLE trial guide](../test-results/nimble_companion_trial.md), then build one environment at
a time. For example:

```sh
OUTPUT_DIR=.releases/contact-cache-v3 bash build.sh build-firmware \
  Heltec_v3_ram_trial_companion_radio_full_nimble \
  --firmware-version v1.17.1.5-halo-keymind-cascade-cache-trial \
  --radio-preset usa-cascadia --profile cascade --standard --require-ota
```

| Hardware | Trial environment |
| --- | --- |
| Heltec V3 | `Heltec_v3_ram_trial_companion_radio_full_nimble` |
| Wireless Tracker | `Heltec_Wireless_Tracker_ram_trial_companion_radio_full_nimble` |
| Tracker V2, FEM on | `heltec_tracker_v2_ram_trial_companion_radio_full_femon_nimble` |
| Heltec CT62 | `Heltec_ct62_ram_trial_companion_radio_full_nimble` |
| XIAO ESP32-C3 | `Xiao_C3_ram_trial_companion_radio_full_nimble` |
| Generic ESP-NOW | `Generic_ESPNOW_ram_trial_companion_radio_full_nimble` |

The firmware RAM guard remains enabled. A successful linked budget is distinct
from live free heap and physical Bluetooth/LoRa qualification on each board.

The smaller NimBLE applications let the CT62, XIAO C3 and Generic ESP-NOW
trials use two 1.5 MiB WiFi OTA slots on 4 MiB flash. Their 896 KiB SPIFFS and
coredump stay at the same addresses as the ordinary `huge_app.csv` Full
layout. Install the trial's merged image over USB first to install its
partition table. Subsequent WiFi updates must fit the new slots. An
application-only update from the ordinary single-slot Full layout cannot
change that layout or enable WiFi OTA.

## Regression checks

```sh
python3 -B test/test_contact_cache.py
python3 -B test/test_companion_contact_persistence_contract.py
python3 -B test/test_companion_contact_stream_contract.py
python3 -B test/test_nrf52_extrafs_contract.py
python3 -B test/test_shared_mota_queue.py
python3 -B test/test_t096_full_memory.py
python3 -B test/test_firmware_ram.py
```

The cache tests compile the production path/secret cache code and extracted
production persistence, routing and request functions with filesystem/radio
adapters. They exercise 350 routes, eviction, snapshot rollback, page
migration, identity changes, read/write/rename faults, handle exhaustion,
packet release on a failed path read, and recovery after a simulated reset
between file renames. They do not simulate a physical flash power cut.

`tools/hil/contact_cache_serial_stress.py` checks all 350 paths over USB,
replaces them with a different generation, and optionally exercises secret
eviction. Use an otherwise empty test node and exclusive access to its
Companion data port. `--populate` writes test contacts; `--secrets` transmits
23 directed LoRa datagrams to synthetic peers. For an ESP32 cache trial:

```sh
python3 tools/hil/contact_cache_serial_stress.py --port /dev/ttyACM0 \
  --populate --generation 1 --secrets --expect-calculate --reboot
python3 tools/hil/contact_cache_serial_stress.py --port /dev/ttyACM0 \
  --generation 1 --secrets --expect-calculate
```

Use the actual device path. Omit `--expect-calculate` for the nRF52 flash
policy. The second invocation verifies the persisted paths after reboot.
