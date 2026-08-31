# SenseCAP Indicator SD font service

The Indicator connects its SD card to the RP2040 rather than the ESP32-S3.
This small RP2040 image owns the card and streams `/meshcore/ui-font.vlw` to
the main application over the internal UART at boot. The ESP32-S3 verifies the
whole-file CRC and falls back to its built-in font if the service, card, file,
or emoji map is unavailable or invalid.

Build and flash the service through the RP2040 USB connector:

```bash
pio run -d tools/sensecap_indicator_rp2040
pio run -d tools/sensecap_indicator_rp2040 -t upload --upload-port /dev/ttyACM2
```

Then upload the generated font to the SD card through that same USB serial
port. The update is checksummed and uses temporary and backup files so an
interrupted upload keeps the last complete font:

```bash
python3 -m pip install pyserial
python3 tools/sensecap_indicator_rp2040/upload_font.py --port /dev/ttyACM2
```

The checked-in asset contains binary 18x24 Noto Sans Mono Bold text and a
12x12 RGB332 Unicode Emoji 17.0 color atlas. Text and emoji transparency have
no antialiasing; the ESP32-S3 renders the asset at the panel's native 480x480
resolution. See
`../sensecap_indicator_font/README.md` to regenerate the asset.

For diagnostics, send `MCFONT STATUS` over RP2040 USB serial. The response
reports internal-UART INFO requests, GET attempts, completed streams, and
bytes sent since the RP2040 last booted. `MCFONT STAGESTATUS` reports the
version-2 staging attempts, completed stages, cumulative bytes and elapsed
milliseconds from the last attempt, and its final result. For example:

```text
MCFONT STAGESTATUS 2 1 1 1302608 18437 STAGED
```

The result is one of `IDLE`, `RECEIVING`, `STAGED`, `SD`, `OPEN`, `TIMEOUT`,
`WRITE`, `CHECKSUM`, `METADATA`, or `ABORTED`.

The internal ESP32 UART also supports the recovery-only two-phase commands
`MCFONT STAGE`, `MCFONT STAGEV2`, `MCFONT COMMIT`, and `MCFONT ABORT`. A staged
file never replaces the live font and is discarded after a reset. Only a
subsequent commit moves the CRC-verified staged pair through the same
temporary/backup transaction used by USB upload. The public USB command parser
does not accept these commands; USB retains its explicit `MCFONT PUT` workflow.
The ESP32 buffers the complete HTTPS response in PSRAM and verifies the
compiled SHA-256 before it starts this staged UART transfer. The RP2040 writes
that verified buffer to the separate staging file and still withholds commit
until the size and CRC32 also match.

After the ESP32 sends `MCFONT COMMIT`, either an `OK` response or a missing or
malformed response moves recovery to local-only verification. A lost UART reply
is ambiguous because the RP2040 may already have completed its durable rename;
it must not authorize another download of the same GitHub blob. Only an
explicit `ERROR ...` response is treated as a rejected transaction eligible
for a later network attempt. Local verification runs immediately and then with
bounded 2-, 5-, and 15-second delays, for at most four post-commit probes per
boot, and does not require Wi-Fi. When the built-in fallback is active, a probe
must stream the exact compiled size, CRC32, and SHA-256 before activating the
font. When a valid older runtime font is deliberately retained until reboot,
the probe uses exact INFO size and CRC32 instead; RP2040 COMMIT has already read
and CRC-verified the complete stored file, and this avoids allocating a second
1.3 MiB runtime buffer. Failure exhausts locally, keeps the existing/fallback
runtime font, and waits for reboot to classify the service again.

`STAGEV2` adds receiver pacing for SD-card latency. The distinct command name
also avoids being prefix-parsed as `STAGE 2 ...` by older services. Its
exchange is:

```text
ESP32 -> MCFONT STAGEV2 <total-size> <crc32-hex> 512
RP2040 -> READY 2 512
ESP32 -> exactly 512 binary bytes (or the exact shorter final chunk)
RP2040 -> ACK <cumulative-byte-count>
... repeat one chunk at a time ...
RP2040 -> STAGED
```

The RP2040 does not acknowledge a partial chunk. It writes each complete chunk
to the SD file before acknowledging it, and accepts no more than 512 bytes per
chunk. Both the receiver-paced protocol and the legacy `STAGE`/USB `PUT` path
have a 10-second idle timeout and a 180-second whole-transfer timeout, so even a
slow byte trickle cannot hold the command service forever. Transfer errors are
single bounded lines (`ERROR SD`,
`ERROR OPEN`, `ERROR TIMEOUT`, `ERROR WRITE`, `ERROR CHECKSUM`, or
`ERROR INSTALL`); invalid requests are rejected before receiving binary data
with `ERROR SIZE`, `ERROR CHUNK`, or `ERROR READONLY`. The original unpaced
`STAGE` command remains available for compatibility with already-deployed
ESP32 firmware, while new firmware should prefer `STAGEV2`.

The whole-transfer deadline also covers the final SD write and flush: a last
chunk received at the deadline edge is cleaned up and reported as
`ERROR TIMEOUT`, never published as `STAGED` after the budget has expired.

If the SD card is not ready on the RP2040's first mount attempt, the service
clears the failed filesystem state and retries every two seconds for up to 30
attempts (about one minute). INFO and upload commands remain responsive with a
missing/SD error during that window and begin using the card automatically as
soon as a retry succeeds. The finite retry budget prevents a permanently
missing card from creating an endless mount loop; reboot to start a new window.

Boot recovery also covers resets between either pair of file renames: a live
font whose metadata still has the temporary or backup name, and an old backup
font whose metadata still has the live name. These abnormal cross-path pairs
must pass a full-file CRC check before metadata is renamed or a pair is
promoted. If any install transaction artifact exists, the live pair must also
pass its full CRC before recovery deletes the candidates. The normal healthy
boot still uses the metadata/size check and lets the ESP32 verify the streamed
CRC, avoiding an extra full SD read every boot.
