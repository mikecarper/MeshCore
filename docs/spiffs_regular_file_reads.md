# SPIFFS regular-file reads and login replay state

Arduino-ESP32 SPIFFS can return a truthy directory handle from a read-open of a
nonexistent filename. `File::operator bool()` alone does not prove that a
regular file exists. A directory's `size()` and `read()` are zero.

The first privileged login on an upgraded G2 encountered this in
`ClientACL::writeClientLoginReplayCeiling()`: opening the not-yet-created
`/s_login_replay` appeared successful, then subtracting the eight-byte trailer
from size zero underflowed. Copying the supposed records failed and login was
rejected. Waiting or changing the repeater clock cannot fix that file-open bug.

## Fix

Use `mesh::openFileRead()` for regular-file reads. It checks existence and
rejects directory handles, while preserving real empty files. Replay record
counts also validate the minimum trailer size, record alignment, and maximum
count before subtraction. An existing replay file that becomes unreadable is
not treated as a new store.

No replay records are cleared and authentication is not weakened. Invalid or
unwritable replay state still fails closed. The first successful privileged
login creates a 44-byte file: one 36-byte identity/ceiling record plus its
eight-byte integrity trailer.

Setting the repeater clock must not clear this file. Admission compares the
sender's timestamp with that sender's saved boundary, not with the repeater's
current time. Clearing the boundary could make captured requests reusable. A
sender clock rollback is a separate condition and remains subject to the saved
boundary after this fix.

Companion directory enumeration uses the separate `openDirectory()` API,
which deliberately permits SPIFFS virtual directories. Regular-file reads are
also enforced for companion data, repeater/room logs, flood-rule verification,
and HTTP packet-log downloads (missing logs return 404; real empty logs 200).

## Audit boundary

The audit covered file opens and size arithmetic in `src` and `examples`.
Identity, region, clock, and common preference loaders already gate reads with
filesystem existence checks; SPIFFS's `exists()` explicitly excludes directory
handles. Relevant MQTT length subtraction follows validated headers and exact
reads. ESP32 OTA staging uses partition APIs, not these SPIFFS file handles.
Intentional directory enumeration must not be changed to a regular-file read.

## Regression checks

- `test/test_client_acl_spiffs.py` compiles the actual `ClientACL.cpp` against
  a filesystem that reproduces the misleading missing-file directory handle.
  It covers first creation, fresh/stale login, reboot ceilings, corrupt and
  unreadable state, short writes, failed publication, and truncated sources.
- `test/test_regular_file_reads.py` executes production companion readers,
  flood-file verification, and HTTP log delivery with the same filesystem
  behavior, including platform-specific file API differences.
- `test/test_esp32_tinyusb_cooperative_output.py` exercises both real role log
  pumps, including missing logs and bounded output.
- Native ACL transaction, login persistence, and client-path persistence suites
  retain the existing security and recovery coverage.

The pre-fix actual ACL code was reproduced as `first_login=rejected`, one
missing read-open, and no replay file. The fixed code accepted that identical
scenario without a missing read-open and created the valid 44-byte record.
Host simulations do not replace a post-flash LoRa login test on the G2.
