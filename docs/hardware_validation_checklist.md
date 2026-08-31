# Hardware validation checklist

Use this checklist for release-candidate hardware runs. A check is complete only
when its log identifies the physical device, firmware artifact, artifact hash,
command result, and cold/warm boot outcome. Do not infer success from a tool's
exit code when the tool has a documented false-success mode.

## Current marathon ledger (2026-08-30)

| Hardware | Stable identity | Current state | Next blocking check |
| --- | --- | --- | --- |
| Seeed XIAO nRF52840 | `B35E71C1C3726CE7` | Exact Full Companion application and latency-disable bootloader candidate installed. An exact bonded B-A-B link-profile test passed at 6.225, 3.343, and 6.231 kB/s for 15 ms, untouched 30 ms control, and 15 ms repeat; CRC, activation, target disconnect, and stable USB return passed three times | Power-removal cold test, then exercise BLE-controlled LoRa OTA |
| Seeed Tracker T1000-E | `34A9141999729D5D` | Later connection-policy-reapply OTAFIX candidate and temporary Full Companion are running; the exact bonded warm baseline passed at 3.34 kB/s. Two lab-only pre-START 15 ms/latency-0 DFUs with `0/0` no-preference event-length hints passed at 6.23 and 6.52 kB/s; a 10 ms event-length control timed out before START | Prove installed bootloader bytes by readback, exercise a physical cold 20-to-244-byte readiness transition, restore the protected Repeater identity, cold boot, then force LR1110 reset |
| RAK3401 | `0B81C9C68D8D01B4`; FICR `8D8D01B4 0B81C9C6` | Direct V4 LoRa OTA from `bf092b24` to exact `3caf9dcf` passed. OTAFIX test version `0x02040403` is installed; bidirectional application UF2, exact SWD application/bootloader readback, and unchanged UICR all pass | Add RF packet counters, then repeat bandwidth and routed-hop matrix |
| Heltec MeshTower V2 with SD | `9352162A72082314` | Exact SD LoRa-OTA Repeater `e26d48e4` is running after identity-gated BLE recovery. Card format/cooldown/forced-format/raw-erase/remount pass; live bootloader contract reports ABI 3, FULL+INPLACE, and SD apply | Signed interrupted-download resume, corruption/signature rejection, then full/delta apply |
| Heltec V4 | USB MAC `44:1B:F6:6A:E8:44`; BLE `44:1B:F6:6A:E8:45` | USB/BLE/Wi-Fi and all three TCP services pass on hardware. The final fresh-NTP-gated Full Companion image builds with 4.31 MB app space free | Flash the exact final NTP build, prove NTP-before-TLS success/failure ordering, then OTA seeding |
| SenseCAP Indicator LoRa | CH340 plus USB MAC `D8:3B:DA:75:23:AC`; BLE `D8:3B:DA:75:23:AD` | Exact 2,145,640-byte Full Companion font-recovery application, SHA-256 `88d8d8ae138b297fc7841005ab40c303e1efe9d27a2bb4a89a30735748a073c4`, is installed; identity-gated flash, USB ASCII/Binary switching, runtime logging, configured Wi-Fi, BLE, all three TCP services, fresh-NTP-gated HTTPS recovery, strict Range resume, STAGEV2 install, and exact RP USB readback pass | Physical display-wrap check, LoRa/TempRadio, and OTA seeding |

The RAK failure log is
`/home/mikec/hwtest/runs/rak3401-syncidle-clean-tSp7PLgA/result.txt` on
the Mercerwood Pi. The tested UF2 SHA-256 was
`650c9fda456685cdf99c1f2996c60f1f551cc9216d2b746a22c8cb78464b0ff7`.
`cp` returned success in 95 ms, but `sync -f` failed with `EIO` after
157,188 ms. Post-failure SWD readback proved that all seven ordinary sectors
whose bytes differed between the old and intended builds still contained the
old values, and the settings page was still erased. The only available
pre-copy SWD dump was taken several hours earlier, however, so it cannot prove
that zero unchanged blocks were written during this attempt or establish when
the stale S140-v7 tail in the first application page appeared. The next run
must capture an immediate pre-copy dump as well as the post-copy dump. Serial
DFU recovery then completed in 35,698 ms with the exact `Device programmed.`
marker. All 1,830 UF2 payload blocks matched SWD readback after recovery, and
the application passed a subsequent real power-removal boot.

The corrected 2.4.4-preview.2 candidate was installed through identity-gated
SWD from combined-image SHA-256
`9b1654baccd2a920f68651893e31d79170400d1a7982a3f854ae640f1d3513a8`.
An immediate pre-copy dump closed the earlier evidence gap. A deliberately old
UF2 then copied and flushed successfully in 16,568 ms, and the exact formerly
failing UF2 above copied and flushed successfully in 16,601 ms. After each
direction, all 1,830 payload blocks matched a full SWD readback with zero header
or payload mismatches. The return update changed 123 bytes across five expected
application pages, UICR remained byte-identical, and a subsequent downstream
USB power removal plus Pi reboot returned the RAK as the same stable identity.
It reported the intended `3caf9dcf` application, board, node name, USA radio
tuple, and 22 dBm setting after that cold boot. The new bootloader's CLI source
string is truncated before the injected preview number, so the independently
hashed SWD artifact is the authoritative candidate identity for this run.

The later TinyUSB lifecycle/generation candidate identifies itself as test
version `0x02040403`. Its exact 40,960-byte bootloader readback SHA-256 is
`f609eb18754070c54f127325b1cb48547baf5c6b1c695d36fd0c8e0874713c13`.
With that candidate installed, a full `CURRENT.UF2` read completed before each
write. The 936,960-byte `bf092b24` application UF2 (SHA-256
`12477a9734cf12eb920361bd21024e39bf303953fcf79d0bf720e00847e2ae92`)
copied and flushed in 28,203 ms and returned to application mode four seconds
later. The exact `3caf9dcf` application UF2 (SHA-256
`650c9fda456685cdf99c1f2996c60f1f551cc9216d2b746a22c8cb78464b0ff7`)
then copied and flushed in 26,479 ms and also returned in four seconds. Both
boots retained `RAK3401-OTA-BENCH` and `910.525/62.5/SF7/CR5`.

Identity-gated SWD then read the entire 468,480-byte padded UF2 application
range. Its SHA-256 exactly matched the converted source on both sides at
`b2e37a7681027ca91d1b2bd8e39b55395d9e91d4863aed17131e50c1ba1d2227`.
The bootloader still matched the installed `0x02040403` image byte-for-byte,
and UICR still matched the pre-test image at
`d029ea630c2f632a1b690cb52b2a96a28b6f870c656c3e1bd74ba3a571c53b9c`.
The authoritative logs are under
`/home/mikec/hwtest/runs/rak3401-uf2-02040403-20260830-170829` and
`/home/mikec/hwtest/runs/rak3401-uf2-return-02040403-sfHiGEqA` on Mercerwood.

The subsequent direct LoRa qualification used the V4 as controller/seeder and
the RAK's exact `bf092b24` body `91CF4EC2882F23D6` as the base. Its mandatory
fixed three-minute rehearsal passed on `909.95/250/SF5/CR5`, including natural
expiry and exact normal-radio recovery. The 2,132-byte, two-block in-place
delta reached `2/2` and ready in six seconds; install was accepted, and the new
body appeared at the 60-second reboot probe. The automation took 514 seconds
including rehearsal, clock/radio safety checks, and post-install verification.
The final body was `27A223DDFC3A8F47`, version `1.17.1.5` at `3caf9dcf`, and
the bootloader reported `blrc:B8`, which is OTAFIX's successful application
apply result rather than an error. Exact target TX power was wrapped from
22 dBm to 0 dBm for the co-located transfer and restored to 22 dBm afterward;
the source RXPS configuration and both normal radio tuples were also restored.

Identity-gated SWD then read the live application at `0x26000`. All 468,384
bytes matched the intended release image exactly (SHA-256
`b496e3f4419c9f771564ed3c74b3407822f45acdf60cbd6c4625a54b181ca084`),
and the independently recomputed EndF hash was `27A223DDFC3A8F47`. The target
ID was `2FA509C1`, hardware ID was `RAK_3401`, and version word decoded to
`1.17.1.5`. The 40,024-byte bootloader readback also matched the exact installed
`ffb1580`/test-version-`0x02040402` artifact byte-for-byte; UICR was unchanged.
After SWD reset-halt/resume, USB again reported the expected application,
identity, name, radio, 22 dBm, no TempRadio lease, and `blrc:00` (the retained
success diagnostic is intentionally cleared by the next normal boot).

A serialized VM build of the six connected-board qualification profiles then
completed 6/6 in 136.122 seconds: XIAO nRF52 BLE Companion, T1000-E BLE
Companion, RAK3401 LoRa-OTA Repeater, SenseCAP Indicator LoRa USB/Wi-Fi
Companion, MeshTower V2 SD LoRa-OTA Repeater, and the consolidated Heltec V4
V4.2/V4.3 Full Companion. The earlier V4 selector typo was rejected before any
compile began and is not a firmware build failure.

After the fresh-NTP download gate, shared SNTP coordinator, MQTT-to-MeshCore
RTC handoff, and daily refresh policy were added, the complete native matrix
passed 1,102/1,102 cases in 93.953 seconds. The scheduler is host-tested at
24-hours-minus-one-millisecond, the exact 24-hour boundary, normal and wrapped
`millis()` values, a short retry that overrides the daily cadence, the zero
deadline sentinel, and a Wi-Fi reconnect. A reconnect schedules one fresh
sample and clears an arbitrarily old short-retry deadline; while continuously
connected, successful samples are requested only once per day. All current
Python and shell contract suites passed, and the expanded LoRa-OTA automation
suite passed 317/317 cases.

Serialized real builds also passed after that change. The final event-safe
implementation uses an atomic `GOT_IP` latch so even a disconnect/reconnect
shorter than the 10-second MQTT status sample cannot skip the required fresh
NTP request. The final Heltec V4 MQTT Observer application is 1,853,304 bytes
with SHA-256
`1938ae88fcdbe601cf8698df44b548656079562d899801886b808af913e5f9ac`.
The consolidated Full V4 image used 2,247,757/6,553,600 flash bytes and left
4,305,368 bytes of app-image space. Its 2,248,232-byte application image has
SHA-256
`2547bb245349a500b7c9a112ac25bcec91ce70733c648ee0ff9ac7de3176da1a`;
the 2,313,768-byte merged image has SHA-256
`1f89ff127d50f06c3e3ce11c642611e079dae74772f169da9f2e4643da55a797`.
The focused daily-NTP policy suite passed all 38 cases after the latch was
added, and the Full capability manifest verified all ten expected features.
The reduced RAK3401 LoRa-OTA Repeater also compiled with the verified WisBlock
I2C aliases. At that source state its link used 471,344/815,104 flash bytes; the
reduced external-sensor contract retains INA219/INA226/INA260/INA3221 as the
voltage/current entries in the optional environmental-telemetry table. These
are not the image's only I2C consumers. The image separately retains its
SSD1306 display, auto-discovered I2C RTCs,
RAK12500 I2C GPS, and RAK12501/L76K UART GPS paths. The configured INA3221 and
RAK12500 both default to I2C address `0x42`; those two devices cannot coexist
at those addresses. The supported combined arrangement leaves RAK12500 at
`0x42`, straps INA3221 A0 to SCL for `0x43`, and uses a build with
`-DTELEM_INA3221_ADDRESS=0x43`.
The nRF52840 die-temperature and ADC battery-voltage paths are also retained.
The 472,227-byte DFU ZIP SHA-256 is
`fdd369005a61635756efa9e38b217376a952f334b0864f927a46799b65264fa9`,
and the matching 943,104-byte UF2 SHA-256 is
`2155632fde730e0ae2e4a72ce64f89910861f6c5105a4bd7ac96aaa538a33dca`.
These VM results do not replace the remaining exact-final hardware test with
UDP/123 blocked.

The later serialized RAK3401 contract build, after the non-blocking nRF52 USB
logging change, used 471,576 bytes before its 56-byte EndF trailer. Its EndF
body hash is `026749684bf6ee8f`, target ID is `2FA509C1`, hardware ID is
`RAK_3401`, and version word is `0x01110105`. The 472,435-byte DFU ZIP has
SHA-256
`cf0880b26f2d6bd8157f8af76a6718fe1f998de8ea7df8cd952e734448201fb9`;
the 943,616-byte UF2 has SHA-256
`f893a8cebf309af12a4a6f5483acb28f94158e18632f660e011658c846e8ca7f`.
The linked ELF contains GPS, SSD1306, RTC, and the four INA families, while an
exact symbol audit finds none of AHT10/20, BME280, BMP280, SHTC3, SHT4X,
LPS22HB, MLX90614, VL53L0X, BME680/BSEC, BMP085, or RAK12035. The complete
native matrix passed 1,112/1,112 cases at that intermediate source state. These
are VM gates only; flash the exact artifacts and repeat the physical peripheral
and blocked-clock checks before marking any later source state
hardware-qualified.

Subsequent source hardening changed RAK peripheral discovery: the shared 3V3_S
rail remains enabled, UART GPS requires a complete checksum-valid NMEA sentence,
an exact INA3221 identity blocks a conflicting u-blox probe, and a successful
I2C GPS claim suppresses a sensor only on the same bus and address. The hashes
and sizes above describe their recorded intermediate artifacts; they are not
current-HEAD evidence and must not be reused for the hardened source.

The immutable font endpoint was re-probed only after the VM reported
`NTPSynchronized=yes`. GitHub returned HTTP 206 for bytes `0-65535`, an exact
65,536-byte match to the checked-in font (range SHA-256
`8b0dd308eb01469d1f9e732fba260720a80cc86f6cb5385c28e8a16c76dcfbf3`),
with total length 1,302,608 and strong ETag
`"39ff0cfbe37e36905507697d042869c05374c3202dd96d5a9f98812153f324c1"`.
This validates the current strict Range assumptions against the live service;
the exact-final Indicator still needs the negative hardware run proving that
blocked UDP/123 prevents every TLS request and RP2040 staging write.

Mercerwood subsequently recovered without a target write. Both its Tailscale
and LAN addresses again accepted SSH, Bluetooth was idle, and the exact T1000-E
stable serial `34A9141999729D5D` was present as application USB `239a:8029`.
The RAK3401 simultaneously uses the same VID/PID, which confirms that every
continuation must resolve the stable serial/by-id identity rather than VID/PID.
The T1000-E remained in the exact bonded Full Companion application; no stale
DFU, scanner, `meshcli`, or HCI-capture process was running.

## Run-wide gates

- [ ] Record commit, version string, role/profile, artifact filename, size, and
      SHA-256 before flashing.
- [ ] Archive the exact ELF and symbol/objdump map beside each qualification
      firmware. A rebuilt ELF is acceptable for diagnosis only after its
      firmware binary matches byte-for-byte; compiler `__DATE__`/`__TIME__`
      strings can otherwise change both bytes and the EndF body hash.
- [ ] Bind every operation to a stable USB serial number or physical USB path;
      never rely on a changing `ttyACM`/`ttyUSB` number.
- [ ] After erasing/reflashing a BLE board that reuses its Bluetooth address,
      remove any stale host bond and pair again with the board's current PIN
      before diagnosing service-discovery disconnects. On Linux, verify the
      BlueZ agent actually accepted the passkey; a scanner seeing the device is
      not proof that Companion GATT is usable.
- [ ] Do not use BlueZ's advertisement `service_uuids` as a same-address warm
      handoff identity gate. After application/bootloader transitions BlueZ can
      merge the bootloader's cached GATT UUIDs into the live application's
      advertisement. Gate the application advertisement by exact address and
      name, then require the live connected BLEDfu service, control/revision
      characteristics, retained bond/encryption, notification subscription,
      and observed target disconnect before accepting the handoff.
- [ ] Do not treat `meshcli -P` as an infallible pairing probe. Immediately
      after one successful post-DFU session, a second invocation with the exact
      paired XIAO address hit the meshcore-cli `NoneType ... pair/disconnect`
      path before it created a Bleak client. BlueZ still reported the exact
      bond and the same command without `-P` connected and synchronized the
      clock successfully. Capture this as a host-tool failure, then verify the
      device independently before blaming firmware.
- [ ] After a successful `BleakClient.connect()`, start a fresh disconnect
      generation before beginning DFU. BlueZ can fail one internal LE attempt,
      retry successfully, and still deliver the failed attempt's callback. A
      stale event caused the host to stop notifications and issue a local HCI
      disconnect after a valid image CRC but before ACTIVATE; the target and
      bootloader had not dropped the link. Preserve an HCI trace through
      activation so local-host and remote-target disconnects remain
      distinguishable.
- [ ] Before a BLE pairing/throughput test on a Wi-Fi-managed Pi, record both
      management addresses and a same-LAN recovery vantage point. The XIAO
      pairing run completed, but Mercerwood then stopped answering on both its
      Wi-Fi and Tailscale addresses; BellevueBBS confirmed the old LAN address
      was absent at ARP and no replacement LAN host exposed SSH. This is an
      infrastructure outage until Pi logs prove a cause, not a firmware or BLE
      failure. The T1000-E pairing run reproduced the management outage: its
      exact application paired successfully and exposed the complete DFU
      service, then Mercerwood stopped accepting SSH and ultimately stopped
      answering Tailscale pings. No buttonless DFU write had been sent, and the
      T1000 remained in its valid application. Capture Pi Wi-Fi/firmware logs
      after recovery and avoid leaving discovery enabled between operations.
- [ ] Confirm the selected board and role before every erase or flash.
- [ ] Before every SWD write, read the target FICR device ID and match it to the
      intended board's stable USB serial (and physical wiring record). A USB
      product name or remembered cable position is not sufficient.
- [ ] Confirm no other PlatformIO process is active; run only one PlatformIO
      build, test, clean, or upload at a time.
- [ ] Record the installed bootloader, SoftDevice, partition table, or flash
      layout independently of the application banner where applicable.
- [ ] Test both a software reboot and a real power removal/cold boot.
- [ ] After an update, read back identity, version, radio tuple, node name, and
      persistent settings instead of assuming they survived.
- [ ] Save command output and elapsed milliseconds. Mark a test `FAIL`, not
      `PASS`, if the transport reports an I/O error even when firmware later
      boots.
- [ ] Before calling a node hung, validate that the probe uses that profile's
      command terminator, check a second transport, and capture PC/LR/watchdog
      state over identity-gated SWD before reset. Resume once and retest before
      using reset as recovery so the diagnostic state is not destroyed first.
- [ ] Treat a remote login acknowledgement and the following command reply as
      separate LoRa packets. A lost login acknowledgement is not proof of
      failed authentication when a command-matched private reply from the
      selected contact key is present; an explicit negative login result still
      fails closed.
- [ ] Restore the intended USA radio preset and normal-radio mode after tests.
- [ ] Compare both semantic version and exact EndF/body hash. A transition
      between two commits carrying the same version string must stop at the
      normal reinstall guard unless the run plan explicitly records and passes
      `--allow-non-upgrade`; a different commit name alone is not authorization.
- [ ] Do not allow a non-interactive transmission-failure prompt to continue
      retry cycles forever. Optional capability probes must fail/fall back after
      a bounded interval, while mutations and cleanup retain their stronger
      bounded-lease recovery rules.
- [ ] Before the first remote command after any controller/source reboot, read
      its RTC and compare it with a trusted host clock. Advance a clock which is
      behind and prove readback; never move a radio clock backward. Fail closed
      when it is more than 10 minutes ahead. During the RAK rehearsal the V4
      rebooted to `2026-03-01` while the RAK remained at `2026-08-30`; the RAK
      received the direct packets but correctly rejected their old timestamps
      as replays. Setting the V4 to host epoch immediately restored login and
      the exact public-key reply at SNR -1.5 dB.
- [ ] Before every external HTTPS download, require a fresh SNTP response for
      that bounded operation, a plausible signed wall clock, connected Wi-Fi,
      and a non-expired proof immediately before opening TLS. Re-check the proof
      before every resumed/Range TLS connection. A retained plausible RTC or
      mesh time is not a substitute for observing NTP, and failure must occur
      before any HTTP request body or flash writer begins.
- [ ] Treat an unacknowledged immediate `tempradio` mutation as potentially
      active, not merely lost. On the tested repeater, scheduled-radio expiry
      deliberately waits for `hasOutbound()` to clear; a CLI reply stranded on
      the temporary tuple can therefore hold the node there beyond its nominal
      lease. Prefer one fixed absolute `tempradioat` window per node, so duplicate
      delivery cannot extend it, and prove the return on both tuples before a
      transfer. If recovering an older immediate handoff, join the exact
      temporary tuple to drain the reply before issuing `normalradio`.
- [ ] Do not diagnose a co-located high-power LoRa bench failure as weak RF
      without an overdrive check. On the RAK3401/V4 bench, the RAK at 22 dBm
      produced only 3/6 status replies around -1.5 dB SNR, while a reversible
      0 dBm setting produced 6/6 replies in 5--8 seconds at +12 to +12.5 dB
      SNR. Read the original power, lower it only for the near-field test, and
      require exact restoration afterward.

### Compute-placement gate

- [ ] Run firmware builds, PlatformIO, mOTA/delta generation, compression,
      full-image hashing sweeps, symbol generation, and other sustained CPU or
      memory work on the VM. Do not offload these jobs to a Pi merely because
      the hardware and source artifacts are attached there.
- [ ] Use the Mercerwood Pi as the identity-gated hardware gateway, SWD/USB/BLE
      test host, and LoRa/Wi-Fi seeder. Copy only the completed artifact plus
      its manifest/hash to the Pi, then verify the copied SHA-256 before use.
- [ ] Run CPU work on a Pi only when Pi performance or deployment behavior is
      itself the test subject, and label that exception in the run log.
- [ ] Do not assume a non-interactive SSH session has the same `PATH` as an
      interactive shell. Resolve pipx-installed tools such as esptool and
      detools to their explicit venv path, record the version, and fail before
      erase if the pinned executable is unavailable.
- [ ] Treat short serial/BLE/SWD control scripts on the Pi as hardware I/O, not
      as permission to generate artifacts there. Use the explicit Python from
      the relevant pipx environment when a probe needs one of its dependencies;
      the Pi system Python did not contain `pyserial` during the RAK run.

### Mercerwood Pi USB power-cycle gate

- [ ] Confirm the Pi is reachable through Wi-Fi/Tailscale and that `eth0` is not
      the active management path before cycling its downstream USB tree.
- [ ] Stop all flashes/transfers, run `sync`, and release SWD GPIO 8 and GPIO 11
      to inputs with no pulls before removing USB power. This prevents an SWD
      signal from partially back-powering an otherwise unpowered nRF52.
- [ ] Never issue a standalone `uhubctl ... -a off` to Pi root hub location `1`.
      Once its only port is off, the hub disappears from `uhubctl` discovery and
      a separate `-a on` command fails with `No compatible devices detected at
      location 1!`. This has interrupted two hardware runs.
- [ ] A downstream hub may be cycled only after resolving the board's stable
      USB serial to an exact sysfs path and proving that hub advertises
      per-port power switching. Record the hub location and port, cycle only
      that port, and gate the re-enumerated VID/PID/serial before continuing.
      The T1000-E qualification safely used child hub `1-1.2`, port 3; this is
      not permission to cycle the ganged root hub at location `1`.
- [ ] Do not use `uhubctl` `off`, `on`, **or** atomic `cycle` on Mercerwood root
      hub location `1`. Physical testing showed that even one atomic `cycle`
      powers the ganged root hub down and then cannot rediscover it to restore
      power. Use a planned Wi-Fi-issued `sudo reboot`, which is the verified
      recovery, or arrange a true upstream VBUS removal when that distinction
      is part of the test.
- [ ] Treat root hub `1` as a ganged operation: every attached test board and USB
      Ethernet adapter loses power. After recovery, enumerate every stable
      `/dev/serial/by-id` identity again before resuming any board-specific test.
- [ ] On this heavily populated root hub, inspect kernel logs for real transfer
      faults before and after a flash, and stop ModemManager while it could open
      a test TTY. A CH340/ESP32-S3 write which lost the chip at 460800 baud
      completed and verified at 115200 after a planned Pi reboot; start at the
      board-qualified conservative rate rather than treating a fast partial
      transfer as firmware failure.
- [ ] Under non-interactive SSH, do not let `udisksctl` fall through to an
      interactive polkit password prompt. After resolving the exact UF2 block
      device by board serial, either use an already-mounted volume or an
      explicit `sudo -n mount` at a board-specific mount point; re-check
      `INFO_UF2.TXT` model/version before copying.

## Seeed XIAO nRF52840

- [ ] Stable USB identity distinguishes application (`2886:8044`) from UF2
      bootloader (`2886:0044`).
- [ ] Do not repeat the known-failing full-application UF2 copy under the
      installed initial 2.4.4 candidate. The identity-gated 897,024-byte copy
      returned exit 0, then `sync -f` failed with `EIO` in 850 ms; the kernel
      logged an offline device, lost queued writes, and FAT errors before USB
      disappeared. Recover through a separately hash-gated BLE/serial path and
      qualify a corrected bootloader candidate before retrying UF2.
- [ ] SWD readback exactly matches the intended bootloader-region SHA-256.
- [ ] Actual SoftDevice FWID and runtime application base match the artifact
      (`S140 7.3.0`: FWID `0x0123`, application base `0x27000`).
- [ ] Exercise and independently verify the exact-board combined
      bootloader+SoftDevice recovery path before relying on it in the field.
      A wrong-layout nRF52840 application can overwrite part of S140 while the
      UF2 bootloader still accepts files and the application still reads flash;
      internal writes or `InternalFS.format()` may then hang or reboot. An
      application-only UF2 and a filesystem erase cannot repair that state.
      The known XIAO incident in upstream issue #3284 recovered only after the
      matching Sense/non-Sense OTAFIX bootloader+S140 ZIP was installed. Keep
      board identity, SoftDevice FWID, application base, and package contents
      as four separate pre-write gates.
- [ ] Invalid settings enter persistent UF2 recovery after a cold boot.
- [ ] UF2 application copy returns success; `sync -f` returns success; the host
      has no offline-sector, lost-write, or FAT I/O errors.
- [ ] Application enumerates after update and reports the expected board,
      version, role, and radio tuple.
- [ ] Legacy serial DFU crosses all application pages, emits the exact
      `Device programmed.` success marker, and rejects false-success output.
      Two application serial-DFU attempts on this board timed out waiting for
      the start ACK while `adafruit-nrfutil` still returned exit 0, so exit
      status alone is explicitly insufficient.
- [ ] Legacy BLE DFU verifies each cumulative PRN receipt exactly and fails on
      a missing, short, regressive, or overreported offset; it may adapt
      only among the packet-size-specific safe receipt windows after timely
      exact receipts and must step back down on slow exact receipts. Final
      application/SoftDevice/bootloader CRC and a peer disconnect are mandatory
      before declaring success.
- [ ] Every upward PRN move is a bounded adjacent-level probe. A failed probe
      or a later real demotion blocks further increases for that transfer, while
      additional slow exact receipts may still negotiate down one level at a
      time. This prevents `4 -> 8 -> 4 -> 8` churn without disabling a safe
      initial probe or a `32 -> 16 -> 8` fallback.
- [x] The exact Full Companion ZIP
      `Xiao_nrf52_companion_radio_full-v1.17.1.5-halo-keymind-cascade-3caf9dcf.zip`
      (SHA-256 `9a916c1a3ea83d8f6dd704465fe9c044a363478f852a4369cf6b13424a7a9430`)
      installed over BLE in 237,297 ms at about 2,110 B/s using ATT MTU 247,
      244-byte writes, PRN 4, and exact cumulative receipts. Validate and the
      target-initiated disconnect passed. USB then reported the exact expected
      version/hash, USA radio tuple, key, RXPS policy, and reversible
      Terminal/Binary mode; the new application advertised NUS as
      `MeshCore-4610BBD2`.
- [x] The post-update application was paired through BlueZ with the expected
      PIN. Companion GATT then returned the same public key, USA radio tuple,
      22 dBm limit, non-repeater role, board, and build over BLE. Its RTC was
      18,038 seconds behind the trusted host; `clock sync` advanced it and the
      immediate readback was within one second of the host interval. No
      backward clock write was performed.
- [x] A second exact same-image update exercised application buttonless DFU and
      retained-peer bootloader entry. The bootloader deliberately reused the
      bonded application address `E5:C3:A8:B0:60:66` rather than the cold-entry
      address ending in `:67`; exact name, Legacy DFU service, and DIS model
      gates still passed. The first adaptive threshold remained at PRN 4 and
      completed all 448,416 bytes at about 877 B/s in 527,832 ms; validate,
      target disconnect, USB identity, application key, role, and USA radio
      readback all passed. The application clock was then about 10 minutes
      behind because its RTC did not advance during the bootloader transfer;
      it was advanced to the host clock and proved by immediate readback.
- [x] A third same-image A/B run used bounded neutral-window exploration. At
      244-byte writes, two exact neutral PRN-4 receipts triggered one adjacent
      PRN-8 probe. PRN 8 measured about 845 B/s against an approximately
      879 B/s PRN-4 baseline, so the helper rolled back to PRN 4 after two
      receipts and never re-probed. All 448,416 bytes completed at about
      870 B/s in 531,804 ms; CRC validation, target disconnect, USB return,
      exact Companion key/role/radio readback, and post-DFU forward clock sync
      passed. This proves up/down sender-window adaptation and no churn, while also showing
      that PRN receipt overhead is not the bonded-path bottleneck on this run.
- [x] A bootloader candidate built from exact ZIP SHA-256
      `9c70200fc15a315a50ec68d2a905b5bcf2fc07a796a21fbd439535ec19a608be`
      disabled inherited slave latency locally during DFU DATA. The controller
      trace still reported the peer-negotiated 30 ms interval and latency 4,
      with no connection-parameter update after START; the change is therefore
      a local event-attendance override, not a GAP speed renegotiation. A cold
      address-generation control completed 448,416 bytes at 2,231 B/s and
      passed validation/activation in 226,103 ms.
- [x] Two subsequent bonded application-to-bootloader runs completed the exact
      same 448,416-byte image at 3,524 and 3,534 B/s. Their complete elapsed
      times, including exact application advertisement proof, buttonless
      handoff, bootloader scan, erase, DATA, CRC validation, activation, and
      reboot, were 169,414 and 169,221 ms. Both targets initiated the final
      disconnect; USB returned as stable serial `B35E71C1C3726CE7`; the public
      key, 22 dBm setting, USA `910.525/62.5/SF7/CR5` tuple, board, and build
      read back exactly. Compared with the prior roughly 877 B/s bonded run,
      the DATA rate improved by about 4.0x without increasing the PRN window
      beyond 8.
- [x] An exact bonded B-A-B link-profile sequence used the same Full Companion
      ZIP SHA-256 `9a916c1a3ea83d8f6dd704465fe9c044a363478f852a4369cf6b13424a7a9430`,
      lab helper SHA-256 `4909c617de37b5666cc622b7190568f002280f55bf26a05843ea283f2be8f566`,
      handoff helper SHA-256
      `1bad570173079b623bbe2bbb9e5ba7c4f8ac379bf966bfd4382c5266fb9fd214`,
      stable USB serial, bonded address, and 448,416-byte application throughout.
      First B, `xiao-ble-event-length-prestart-20260830-072641-W7COxm`, used one
      exact pre-START 15 ms/latency-0 update with `0/0` no-preference event-length
      hints and completed DATA in 72.030 seconds at 6,225 B/s; DFU through target
      disconnect was 102.792 seconds and the full workflow was 117,064 ms.
      Control A, `xiao-ble-control-prestart-20260830-073149-yKf0lO`, requested no
      update, retained the bootloader connection's 30 ms/latency-4 link, and
      completed DATA in 134.131 seconds at 3,343 B/s; DFU was 165.137 seconds and
      the full workflow was 182,624 ms. Final B,
      `xiao-ble-exact15-prestart-20260830-073554-FuGPfH`, repeated exact 15 ms and
      completed DATA in 71.970 seconds at 6,231 B/s; DFU was 102.799 seconds and
      the full workflow was 117,179 ms. Both B rates were about 1.86x control and
      differed by only 0.096%.
- [x] The B-A-B protocol and link gates passed independently. Both B runs probed
      PRN 4 -> 8 after an exact 0.135-second four-packet receipt, rolled back
      once to PRN 4 after the exact 0.345-second eight-packet receipt, and never
      re-probed. Control A likewise probed after 0.269 seconds and rolled back
      after 0.600 seconds. Each B bootloader connection had exactly one matching
      HCI Update Complete before START; control A had no bootloader-connection
      update. All three captures contained zero connection updates from START
      through DATA, CRC validation, activation, and the target-initiated
      disconnect. Each run ended `RESULT=PASS`, `RC=0`, `STAGE=complete`; final B
      returned the exact Full Companion application as USB `2886:8044`, stable
      serial `B35E71C1C3726CE7`, with current application name
      `MeshCore-4610BBD2`. A fresh post-B binary Companion query independently
      re-gated the unchanged public identity, name, Full Companion role, 22 dBm
      power, and USA `910.525/62.5/SF7/CR5` radio tuple. A separate bonded BLE
      application query then returned the same board, build, and Full Companion
      role, so the USB return did not mask a broken Companion BLE path or stale
      bootloader alias.
- [x] The current host-controller ceiling is independently characterized. The
      Pi Zero 2 W Broadcom UART controller reports HCI/LMP 4.2 but supports only
      LE 1M; LE Read Maximum Data Length and Read Suggested Default Data Length
      both return Unknown HCI Command. The final B capture contains 1,837 full
      244-byte firmware writes,
      each split into ten 27-byte HCI ACL fragments; all 18,425 transmitted ACL
      fragments received completed-packet accounting. The median full-write gap
      was 29.947 ms at 15 ms versus 59.936 ms in control A. A BLE 5 controller
      with working Data Length Extension is therefore the next host-side speed
      experiment; this built-in controller cannot exercise DLE or LE 2M.
- [x] The host helper now clears a disconnect callback left by an unsuccessful
      internal BlueZ connection attempt only after `connect()` has returned a
      live link, and labels PRN changes as adaptation rather than link
      negotiation. All 81 strict host tests pass. The first physical run before
      this fix still proved the 3,520 B/s DATA rate and returned a successful
      validation response, but is correctly recorded as FAIL because the stale
      host event prevented ACTIVATE.
- [ ] Warm app-to-bootloader handoff is tested with the application watchdog
      active; cold recovery is tested separately.
- [ ] BLE Companion/recovery behavior and radio-reset recovery are exercised.

## Seeed Tracker T1000-E

- [x] Stable application identity `34A9141999729D5D` is recorded and is not
      confused with the RAK3401 even though both currently enumerate with USB
      product ID `239a:8029`. Application is `239a:8029`; the exercised
      bootloader is `2886:0057`, with the same stable serial and exact
      `T1000-E`/`T1KE_DFU` model/name gates.
- [ ] Board, version, repeater role, bootloader status, and USA radio tuple are
      correct after erase/install and after reboot. The live baseline and a
      2,184 ms software reboot passed: `3caf9dcf`, `Seeed Tracker T1000-E`, 22 dBm,
      `910.525/62.5/SF7/CR5`, OTA target `7B071FA0`, bootloader
      `0.11.0-OTAFIX2.4.3`, and full OTA base hash
      `3E92B157128B6963`. A real power-removal boot is still required.
- [ ] UF2 and serial DFU installation paths complete without host I/O errors.
      On OTAFIX 2.4.3, an identity-gated root copy of the exact old UF2 returned
      success in 112 ms, but the device disconnected before host writeback was
      durable: `sync -f` failed with `EIO` after 17,695 ms and the kernel logged
      lost asynchronous writes/FAT read errors. The old application happened to
      boot and retained its settings, but this transport is a **FAIL**. Retest
      with the corrected 2.4.4 candidate before qualifying T1000-E UF2.
- [x] The installed `t1000e_repeater_lora_ota_no_external_sensors` profile does
      not compile the Companion BLE transport and did not advertise during a
      post-reboot BlueZ scan; that is expected for this repeater image. BLE
      Companion qualification belongs to the separate T1000-E Companion build.
- [x] The exact latency-disable bootloader candidate ZIP (SHA-256
      `ebe9739ce40c2c7cc1f52076f9614194959fcf6df93d37666b69b7bd415fab68`)
      installed through identity-gated serial DFU. The final output contained
      exactly one terminal `Device programmed.` success line, the bootloader
      returned as `2886:0057`, and its embedded manifest identifies
      `T1KE_DFU`, S140 7.3/FWID `0x0123`, application base `0x27000`, and test
      version `0x02040403`.
- [ ] The later connection-policy-reapply combined ZIP (SHA-256
      `c2de1fb2704154b4abc371b6b4827c32ad09c4d15f3f6d97d0f4182bb51fff58`)
      was identity-gated, transferred, validated, activated, and returned as
      exact `2886:0057` recovery USB. `INFO_UF2.TXT` reported
      `0.11.0-OTAFIX2.4.3-3-gffb1580-dirty-test-version-0x02040403`, S140
      7.3.0, and the T1000-E board. The earlier and later candidates share the
      same visible version string, however, so package sequencing and INFO_UF2
      do not independently prove the installed bootloader bytes. Require an
      identity-gated live bootloader-region readback or a uniquely versioned
      rebuild before checking this item.
- [x] The combined-image BLE transport itself passed: 193,688 bytes at
      3,331 B/s, target CRC validation, and target-initiated activation. The
      package contains SoftDevice plus bootloader and `APP=0`, so remaining in
      recovery USB was the correct postcondition. Its first supervisor record
      is not accepted as run-level evidence: TERM inherited status zero and
      wrote a false `PASS` at `STAGE=post_copy_usb`. A separate audit records
      the transport result, and all later wrappers map INT/TERM to nonzero and
      refuse `PASS` unless `STAGE=complete`.
- [x] A separate exact application recovery after that combined transfer
      completed 404,468 bytes at 2,095 B/s, validated, activated, and returned
      stable application USB in 225,899 ms. The lower rate is the expected
      cold/unbonded class, not the bonded warm baseline.
- [x] A cold exact Full Companion application ZIP (SHA-256
      `b3be000ab2391527a325cc868e2706dec291c167b6e6cd3495908addb8af6c65`)
      completed all 404,468 DATA bytes over BLE at 1,572 B/s. The complete
      workflow took 281,236 ms (about 1,438 B/s end to end, including setup,
      validation, activation, and reboot). ATT MTU reached 247, but BlueZ's
      independently cached maximum write size was still 20, so the
      identity-gated helper correctly used 20-byte packets, adapted PRN
      8 -> 16 -> 32, validated CRC, activated, observed the target-initiated
      disconnect, and gated the exact USB application return.
- [x] The Full Companion application advertised as `MeshCore-F9AD7082` at the
      expected address generation, paired with the qualification PIN, and
      exposed its bonded, MITM-protected Legacy DFU service after connection.
      That DFU service is intentionally absent from the advertisement, so an
      advertisement-only check is insufficient. A 1200-baud USB touch instead
      selects serial-only DFU and correctly produces no BLE advertisement;
      use the bonded application control characteristic for a warm BLE handoff.
- [x] Exact same-address bonded warm buttonless DFU passed. The application
      advertisement, Paired/Bonded state, connected BLEDfu service/control/
      revision layout, encrypted notification/write gate, observed target
      disconnect, `T1KE_DFU` advertisement, DIS model `T1000-E`, ATT MTU 247,
      and 244-byte write capability were all checked. All 404,468 bytes were
      confirmed in 121.081 seconds at 3,340 B/s; DFU through target disconnect
      took 144.076 seconds and the full supervisor took 176.958 seconds. CRC,
      activation, and exact `239a:8029`/`34A9141999729D5D` USB return passed.
- [x] Two accepted in-DATA 30 ms -> 15 ms -> 30 ms controller experiments
      completed without corrupting either image, but were decisive negative
      performance results. The first finished DATA in 425.398 seconds at
      951 B/s; the later candidate finished DATA in 449.063 seconds at 901 B/s.
      HCI capture found the cause: `hcitool lecup` silently encoded both
      connection-event-length hints as `0x0001` (0.625 ms). The Pi's Broadcom
      4.2 controller consequently scheduled about one ACL fragment per event:
      roughly 64 fragments/s at 15 ms and 32 fragments/s after the 30 ms
      "rollback", versus roughly 120--144 fragments/s on the untouched link.
      The later pre-START controls prove that 15 ms itself is not slow; the
      restrictive event-length hint and in-DATA update/rollback were unsafe.
- [x] Two exact 15 ms/latency-0, zero-event-length lab controls passed:
      `t1000-ble-event-length-prestart-20260830-071549-QOeVuw` completed all
      404,468 DATA bytes in 64.905 seconds at 6,232 B/s (95.635 seconds through
      target disconnect; 112,639 ms full workflow), and
      `t1000-ble-event-length-prestart-20260830-071825-VE9Re3` completed DATA in
      62.070 seconds at 6,516 B/s (92.794 seconds through target disconnect;
      106,807 ms full workflow). Both used exact `0/0` no-preference minimum and
      maximum connection-event lengths. On each bootloader connection the HCI
      trace contains exactly one successful update before START and none from
      START through DATA, validation, activation, and the target disconnect;
      Update Complete preceded START by 7.535 and 7.536 seconds. Completed-packet
      clusters followed the 15 ms connection anchors, while the median full-size
      DFU write gap was 29.997 ms in both traces. Exact package hash, CRC,
      activation, and stable application USB return passed twice.
- [x] `t1000-ble-event-length-prestart-20260830-071358-ogzmRy` is the bounded
      negative control. HCI accepted exact 15 ms/latency 0 with both event-length
      hints set to 10 ms, then reported `Connection Timeout (0x08)` 6.810 seconds
      after Update Complete during the 20-second pre-START pause. The run failed
      closed at `STAGE=pre_start_update` in 44,227 ms; START and application DATA
      were never sent. A 10 ms event-length hint is therefore rejected for this
      adapter/target pair.
- [ ] Production BLE DFU keeps the controller-negotiated link and issues no raw
      host connection update unless that exact adapter/target profile has been
      separately graduated. The opt-in lab path may issue exactly one request on
      the bootloader connection before START while sent/confirmed are zero: exact
      interval and latency, `0/0` no-preference event-length hints, bounded pause,
      and a matching HCI Update Complete are mandatory. Failure or mismatch
      disconnects before START. Never update or roll back the link during DATA;
      only the protocol PRN/window may adapt there. Compare complete repeated
      control/candidate DFUs offline before graduating a production profile.
- [ ] Repeat the cold transfer with the host's bounded BlueZ readiness wait.
      When ATT MTU is greater than 23 while the cached write capability remains
      20, it polls the same connected DFU characteristic for at most three
      seconds, aborts on disconnect, disappearance, or characteristic-handle
      change, and otherwise falls back safely to 20. The four readiness unit
      cases and all 81 strict helper tests pass; a physical 20 -> 244-byte
      promotion is still required.
- [ ] Restore the protected
      `t1000e_repeater_lora_ota_no_external_sensors` application (target
      `0x7B071FA0`, exact ZIP SHA-256
      `9b9be2bee9ebb6644d22806d404fe02c415d037707713d206fa43a8c689cf632`)
      through identity-gated application DFU while retaining the candidate
      bootloader. Do not use the generic repeater image or the unqualified UF2
      path. Verify the protected private/public identity without printing the
      private key, plus exact name, latitude/longitude, build/body/OTA target,
      USA radio, 22 dBm, GPS off, power saving on, RX gain on, RXPS on,
      watchdog on, telemetry access `all`, no TempRadio/download, and a valid
      admin login.
- [ ] LoRa radio reset/recovery is forced and normal receive/transmit resumes.
      Normal post-reboot bidirectional LoRa is already proven: the V4 learned a
      zero-hop path, received the exact message-specific ACK in 558 ms, and
      received the T1000-E's exact `3caf9dcf` version reply. A deliberately
      forced LR1110 recovery event is still required.
- [x] Direct V4 LoRa OTA receive, apply, reboot, and post-update verification
      passed from exact `bf092b24` body `9443FB5E6F23FC1B` to exact `3caf9dcf`
      body `3E92B157128B6963` on OTAFIX 2.4.3. The mandatory fixed three-minute
      rehearsal passed at `909.950/250/SF5/CR5`, including natural expiry and
      return to `910.525/62.5/SF7/CR5`. The 2,036-byte/two-block delta reached
      ready in six seconds, the new body replied at the 50-second probe, and the
      complete safety-wrapped runner took 519 seconds. V4 source RXPS and both
      normal radio tuples were restored. The co-located T1000 was temporarily
      reduced to 0 dBm and restored to 22 dBm using bounded retries across its
      RXPS busy windows.
- [x] Independent post-LoRa-OTA USB verification matched the protected pre-run
      private key, public key, name, latitude, and longitude without printing
      private material. GPS remained off; device power saving, system watchdog,
      RX gain, and RXPS remained on; telemetry access remained `all`; no
      TempRadio lease or download remained. `blrc:B8` is the expected OTAFIX
      application-apply success diagnostic. A subsequent software reboot reset
      uptime and preserved the exact build/body, name, radio, 22 dBm, GPS,
      power-saving, RXPS, watchdog, and no-TempRadio state. The CDC symlink did
      not disappear during this warm reset, so uptime/readback—not USB removal—
      is the required reboot proof on this board.
- [x] GPS was initially off, enabled successfully under device power saving,
      reported active/no-fix/zero-satellites with its duty-cycle deadline, and
      was restored to off. Device power saving reported on, RXPS reported
      `on,18205,20423`, GPS advert policy `prefs`, and telemetry access `all`.

## RAK3401

- [x] Stable USB identity and SWD FICR identity are independently matched.
- [x] Source-level WisBlock peripheral pins are complete and non-conflicting:
      I2C SDA/SCL are P0.13/P0.14 through both `PIN_WIRE_*` and
      `PIN_BOARD_*`; GPS-module RX crosses to MCU UART TX P0.16, GPS-module TX
      crosses to MCU UART RX P0.15, and PPS is P0.17. A static contract test
      prevents these aliases from silently disappearing or being uncrossed.
- [x] Ordinary/full-sensor RAK3401 profiles use the source-built Adafruit BME680
      provider, not Bosch's precompiled BSEC archive, so upstream issue #3292's
      soft-float/hard-float link mismatch is not reachable there. The reduced
      LoRa-OTA profile deliberately omits BME680/BSEC and the other declared
      optional environmental/ranging drivers. A static contract test requires
      the hard-float path fix and BSEC dependency if BSEC is enabled in this
      board or an inherited full-sensor recipe later.
- [ ] With the RAK13302 radio installed, detect a RAK12501/L76K UART GPS from a
      complete checksum-valid NMEA sentence in sensor slot A. Confirm random
      UART bytes, an incomplete sentence, and a bad checksum do not claim GPS.
- [ ] Detect RAK12500 I2C GPS alone at `0x42`; exercise GPS power saving and
      confirm the shared 3V3_S rail, OLED, RTC, and other slots remain powered.
- [ ] Detect INA3221 alone at the firmware-configured `0x42` and prove the
      discovery path does not send u-blox configuration traffic to it.
- [ ] Exercise RAK12500 at `0x42` and INA3221 at `0x43` together after strapping
      INA3221 A0 to SCL and building with
      `-DTELEM_INA3221_ADDRESS=0x43`; verify both retain independent telemetry.
- [ ] With a representative omitted environmental sensor attached, confirm the
      reduced image leaves the generic I2C bus healthy but does not advertise a
      driver that was intentionally removed.
- [x] Local USB terminal probes are terminated with carriage return (`\r`).
      This CLI echoes an LF-only command without executing it; an echoed
      `ver` with no reply is therefore a bad probe, not evidence of a hung
      application. Confirm any suspected hang with a CR-terminated command,
      LoRa status, and SWD state before resetting the board.
- [x] Assert CDC DTR when probing the RAK terminal. With DTR deasserted, writes
      reached the port but no CLI replies were returned; the same exact
      stable-identity port returned the expected version immediately after DTR
      was asserted. Keep 1200-baud bootloader touch separate from 115200-baud
      application probes, and allow a full reply window so delayed responses
      are not attributed to the following command.
- [x] Installed S140 6.1.1 FWID (`0x00B6`) and runtime application base
      (`0x26000`) are independently verified.
- [x] Bootloader version and apply capabilities are independently reported.
- [x] UF2 application copy returns success; `sync -f` returns success; no USB
      reset/timeout or offline-sector errors occur; SWD readback matches every
      UF2 payload byte. Current 2.4.4-preview.2 result is **PASS** in both
      current→old and old→current directions.
- [x] Board, repeater role, version, name, and USA radio tuple are verified
      after serial recovery and again after a real cold boot.
- [x] Six consecutive authenticated `ota self` attempts leave the application
      healthy. In the qualification run, two login/reply pairs were lost, one
      login succeeded while its command reply was lost, and the next three
      commands replied in about 2.5 seconds each. A CR-terminated local `ver`
      probe passed after every attempt; packet loss did not become a node hang.
- [ ] An unchanged admin login does not rewrite the ACL flash file, while an
      actually changed/newly learned persistent route schedules exactly one
      delayed save and survives reboot. Cover delayed flood-path replies,
      already-unknown paths, changed permissions/secrets, new/evicted clients,
      and the intentionally non-persistent guest case. Do not rely on an
      unrelated login write to capture a later PATH response.
- [ ] Location and private-key persistence are verified without exposing the
      private key in logs.
- [x] Serial recovery rejects nrfutil's false exit-0 failure, requires the exact
      success marker, and produces a byte-exact SWD application readback.
- [ ] UF2/serial recovery works with both the deployed older bootloader family
      and the current backward-compatible application.
- [ ] Bluetooth advertisement and connection work when enabled. The tested
      repeater profile does not advertise BLE during normal operation.
- [x] Direct LoRa OTA from the V4 passes with elapsed time, bounded retry
      behavior, final hash/version verification, exact radio restoration, and
      independent SWD readback. The qualified `bf092b24` base
      `91CF4EC2882F23D6` reached `3caf9dcf`/`27A223DDFC3A8F47` using a
      two-block, 2,132-byte delta: ready in six seconds, new body at the
      60-second reboot probe, and 514 seconds for the complete safety-wrapped
      run. Log:
      `/home/mikec/hwtest/runs/rak3401-0dbm-final-9KWjey61/run.log`.
- [ ] Add exact RF packet sent/confirmed counters to the device/host telemetry.
      The qualified run records two payload blocks, all seeder reads, command
      retries, and final success, but those are not equivalent to LoRa packet
      counts and must not be presented as such.
- [ ] Repeat at each supported bandwidth and record 0-hop, 1-hop, and 2-hop
      estimates from measured airtime rather than extrapolating only by file size.

## RAK4631 reduced profiles

- [x] Serialized VM contracts distinguish the three application identities:
      the plain and Serial2 reduced profiles retain the combined WisBlock GPS
      provider and all four INA voltage/current drivers; the explicit legacy
      Serial1 profile retains the INA drivers and omits GPS. This is build
      evidence only, not physical peripheral qualification.
- [ ] On the plain reduced profile, detect and read RAK12500 I2C GPS, a
      RAK12501/L76K UART GPS in a separate run, the SSD1306 OLED, a supported
      autodiscovered RTC, and representative INA telemetry. Verify GPS sleep and
      repeated discovery never lower the shared WB_IO2/3V3_S rail.
- [ ] With a UART RAK12501 installed and detected, confirm the Serial2 bridge
      and GPS remain usable together. Select Serial1 and try to enable the
      bridge with the GPS preference both on and off; verify each attempt is
      refused before the bridge starts and does not change GPS transport,
      acquisition, hold, or cached-fix state. The shared WB_IO2/3V3_S rail must
      remain high and GPS must remain usable after each refusal.
- [ ] With only an I2C RAK12500 installed, and again with neither GPS installed,
      select Serial1 on the merged image and confirm bridge enable is refused
      without changing saved UART/runtime state. Silence or an I2C-only detection
      must not defeat the fail-closed RAK12501 reservation. Confirm Serial2 starts
      and I2C GPS telemetry remains usable.
- [ ] Exercise RAK12500 at `0x42` and INA3221 at `0x43` together with the
      address-matched build. Separately place INA3221 alone at the configured
      `0x42` and prove the identity guard prevents u-blox configuration writes.
- [ ] With a logic analyzer, hold SDA low and then SCL low before sensor
      discovery. Confirm the validated board pins receive at most nine
      open-drain recovery clocks and a STOP only after SCL rises; the firmware
      must skip discovery if either line remains low. Repeat on ProMicro's
      remapped SDA 8/SCL 7 and verify the user-button pin is never driven.
- [ ] Record the remaining core limitation: the pinned Adafruit nRF52 Wire
      implementation has no transaction timeout. This preflight cannot protect
      display/RTC calls made earlier in boot or interrupt a peripheral that
      wedges after a transaction begins. Qualify those cases with a hard power
      cut/watchdog fixture before claiming bus-hang recovery.
- [ ] Confirm the explicit legacy Serial1 image exposes neither UART RAK12501
      nor I2C RAK12500. Record this as a combined-provider build limitation, not
      an assertion that RAK12500 electrically conflicts with the UART.

## Heltec MeshTower V2 with SD

- [x] Stable serial `9352162A72082314` and physical downstream-hub path
      `1-1.2` port 4 identify this board across application and bootloader
      descriptors; no other attached nRF52 endpoint was selected. The older
      2.4.3 bootloader's UF2/serial failure was rejected rather than treated as
      a successful flash.
- [x] Exact `1.17.1.5-halo-keymind-cascade-marathon-hwtest-e26d48e4` SD
      Repeater boots with hardware ID `Heltec_tower_v2`, target `0A9DBBF0`,
      body hash `E9282D470AAC348B`, and USA radio
      `910.525/62.5/SF7/CR5`. `ota self` reports SD apply ABI 3 with codec mask
      `0x5`; `ota bootloader` reports board `239A0071`, target `1150F50E`, name
      `TOWER_V2_OTA`, and capability byte `09`.
- [x] Card detection and destructive maintenance pass. Normal format took
      3,927 ms; an immediate repeat was rejected by the five-minute cooldown;
      forced format took 3,933 ms; and raw erase plus format took 3,947 ms.
      The card remounted empty with 16.0 KiB used and 959.6 MiB free, and both
      completion ages were recorded. Automatic archive capture was then
      disabled deterministically, leaving only `/mota/cache.off`.
- [ ] SD card detection, staging, hash validation, apply, cleanup, and recovery
      from missing/corrupt media pass.
- [ ] Multi-page application and bootloader update paths pass across a cold boot.
- [ ] LoRa OTA receive/apply reports the expected target ID and installed version.

## Heltec V4

- [x] Exact V4 hardware variant is reported (`Heltec V4.3 OLED`); OLED/TFT
      variants are not merged.
- [x] Full Companion boots with USB logging off and Binary Companion selected.
      The text terminal independently reports the complete build string;
      protocol `ver` has a shorter fixed-width version field.
- [ ] USB logging changes take effect immediately on this single-TTY ESP32
      profile, require no reboot, and return to Binary Companion only through
      the explicit terminal-stop mode change.
- [x] Bluetooth pairing and Companion protocol operations pass. Qualification
      required removing the Pi's stale pre-flash bond, pairing through a BlueZ
      `KeyboardOnly` agent with the current displayed/reported PIN, then
      reconnecting; BLE `infos` and `ver` returned the exact V4.3 OLED identity.
- [x] Saved Wi-Fi connects to `SlowFi`, returns at `192.168.1.51` after the test
      host reboot, reports Wi-Fi power save `min`, and remains governed by the
      configured-network behavior rather than the unconfigured 30-minute
      shutdown policy.
- [x] TCP Binary Companion (`5000`), Wi-Fi OTA (`5001`), and text CLI (`5002`)
      are each tested and cannot be confused with one another. Port 5000
      returned the exact Companion identity; 5002 returned the full terminal,
      version, Wi-Fi, logging, RXPS, and memory state; and a real `motatool
      serve --tcp 192.168.1.51:5001 -v` session received `COUNT -> 0` from an
      intentionally empty folder. Disconnect then restored `folder:not
      connected` while the 5001 listener stayed active.
- [ ] For a shared Full-Companion controller/seeder, bind the source identity
      to the exact public key already read from Binary Companion port 5000.
      Do not require `get public.key` from text port 5002: the hardware-tested
      V4 Full CLI rejects that command. Port 5002 must still prove the local
      TempRadio state/tuple, while keyed on-air destination replies prove the
      physical LoRa handoff.
- [ ] V4 seeds direct and routed LoRa OTA; TempRadio preflight, apply, and
      automatic normal-radio restoration all pass.

## Seeed SenseCAP Indicator LoRa

- [x] Flashing selects the ESP32-S3 CH340 endpoint, never the Indicator RP2040
      ACM endpoint; esptool MAC probing succeeds before erase. The qualifying
      merged write used 115200 baud and completed with flash hash verification.
- [x] A fresh merged install completes SPIFFS initialization and reports the
      intended Full Companion version/role and `Seeed SenseCAP Indicator`
      hardware identity. The exact qualifying artifact was
      `SenseCapIndicator-LoRa_companion_radio_full-1.17.1.5-halo-keymind-cascade-marathon-e26d48e4-merged.bin`,
      2,070,152 bytes, SHA-256
      `df08b770313ca8776436456525681cf286cd9b5b40e05e7cf55f21ef097f338a`;
      its ELF, map, manifest, and partition signature are archived beside it on
      the Mercerwood Pi.
- [x] Keep one identity-gated CH340 descriptor open with DTR/RTS deasserted
      through startup. On the valid-current-font path, the Indicator streams a
      1,302,608-byte font from its RP2040 at 1 Mbps before registering the USB
      terminal, so allow at least 30 seconds before the first command. A
      missing/corrupt-font recovery instead runs in the background after the
      interfaces start. Reopening short probes can reset the ESP32 repeatedly
      and create a false UART-hang diagnosis; the controlled run returned
      `ver` and `board` after the same apparent-silence condition.
- [x] Automatic font recovery starts from a physically corrupt RP asset. The
      setup deliberately installed 64 all-zero bytes, CRC32 `758d6336`, in
      place of `/meshcore/ui-font.vlw`; the ESP32 rejected that asset and kept
      its built-in fallback font while recovery ran.
- [x] Every new HTTPS recovery attempt waits for a fresh SNTP callback and a
      signed epoch at or after the compiled asset publication time before it
      opens TLS. The forced-resume run observed fresh syncs in 3,700, 400, and
      699 ms across three outer attempts, including two failed TLS handshakes;
      the final normal run synced in 1,999 ms and only then logged the first
      `api.github.com` connection. A retained merely plausible clock is not
      accepted. CA validation and the compiled asset SHA-256 remain mandatory.
- [x] The release-source normal path downloaded all 1,302,608 bytes into PSRAM
      in 13,205 ms, closed TLS, verified SHA-256
      `61bce9662db314054e7bcfaa26147a28ad7b500b51baac4cae1caacce90b7421`,
      staged receiver-paced 512-byte `MCFONT STAGEV2` chunks in 36,676 ms,
      committed, and verified RP CRC32 `19f80d64` at 76.7 seconds from reset.
      An independent `MCFONT GET` over RP USB then returned exactly 1,302,608
      bytes with the same CRC and SHA in 15.273 seconds. The exact installed
      application is
      `SenseCapIndicator-LoRa_companion_radio_full-1.17.1.5-halo-keymind-cascade-marathon-font-recovery-e26d48e4.bin`;
      its log is
      `/home/mikec/hwtest/runs/indicator-release-recovery-lUZEmBHL/esp-serial.log`
      on Mercerwood. The current RP service reports protocol 2 and completed
      STAGEV2 accounting; its installed flash was not independently read back,
      so no RP artifact hash is inferred from the archived candidates.
- [x] Strict Range recovery passed a forced hardware interruption. Exact test
      application SHA-256
      `cfd5aec497cb9919f87c0f82f5deaf8614d411582a709cea1c36275529c0bb68`
      closed the initial response at verified offset 1,048,576. Its first
      same-offset resume handshake failed; bounded retry 2/2 then accepted only
      the remaining 254,032-byte `206` response under the original strong
      ETag/`If-Range`, retained the same PSRAM buffer and SHA stream, completed
      the download in 49,422 ms, staged in 36,702 ms, committed, and passed an
      independent exact USB readback. The full log is
      `/home/mikec/hwtest/runs/indicator-range-v2-xbKr09wI/esp-serial.log`.
      An earlier forced 512 KiB run proved one correct resume at 524,288 but
      later exhausted its second reconnect on a TLS failure; it is retained as
      regression-trigger evidence rather than an end-to-end pass.
- [x] Rebooting the exact release-source application with the current valid
      font performed no NTP query, TLS connection, or GitHub request. The
      valid-font boot log is
      `/home/mikec/hwtest/runs/indicator-release-valid-a84vd7Du/esp-serial.log`.
- [x] The post-qualification shared SNTP-coordinator source compiles in the
      real Arduino-ESP32 2.x LoRa USB/WiFi profile (1,587,965 bytes, 60.6% of
      its OTA slot), the dynamically generated ESP-NOW Full profile (2,093,465
      bytes, 79.9%), and the matching RP2040 transactional service. After the
      VM independently reported `NTPSynchronized=yes`, an
      end-to-end request to the immutable GitHub asset returned HTTP 200,
      exactly 1,302,608 bytes, and SHA-256
      `61bce9662db314054e7bcfaa26147a28ad7b500b51baac4cae1caacce90b7421`.
      A live `524288-1302607` continuation then returned HTTP 206, the same
      strong ETag, an exact 778,320-byte `Content-Length`, and a byte-for-byte
      hash match against the checked-in font tail; the pinned Sectigo E46 root
      independently verified the current `api.github.com` chain.
      The same coordinator also compiles in the real
      `heltec_v4_repeater_observer_mqtt` profile: its 1,852,392-byte EndF image
      has SHA-256
      `fe8352654437f67488b412e1c1a690f2874e836abb5a5d209654bd030c713d7e`.
      MQTT's three process-global `configTime()` paths are lease-protected,
      and a busy asynchronous refresh uses a wrap-safe five-second retry
      deadline instead of overwriting another service's SNTP callback or
      recording a false successful refresh.
      The exact final LoRa Full artifact gate then passed 11/11 linked-image
      markers, including both `requesting fresh NTP time before download` and
      the subsequent TLS-open marker. Its 2,146,312-byte application has
      SHA-256
      `f557088a5ef3f49f74ab01ca95fefc76911cca4073f01b77cee4882284fc5110`
      and 475,128 bytes remain in its app slot; the matching 2,211,848-byte DIO
      merged image has SHA-256
      `52a4559774384b54dd7fb81da82c791e2c9564991fcf016bcad3b6ad756c1fb2`.
      The exact ELF, map, capability manifest, and partition signature are
      archived together under `/tmp/indicator-full-daily-ntp.WQ6ozM` on
      the VM pending transfer to the hardware-run archive.
      The ESP-NOW Full sibling passed the same 11/11 gate with a 2,093,928-byte
      application (527,512 bytes free), SHA-256
      `fcd9e0e8a37551a845a234fc10eaa4a9f420d54172039b9241f4696a12d4c65f`,
      and matching DIO merged-image SHA-256
      `f3637c23f58204f5b752b68a1bd494601cb68bee9c27b664832d40abd03cc186`;
      its complete archive is `/tmp/indicator-espnow-full-ntp-gate.YN9T8l`.
      Repeat the negative UDP/123-blocked hardware gate on this exact final
      source before treating the coordinator refinement as hardware-qualified.
- [ ] Full Wi-Fi SSID, setup address, and related status text wrap without
      clipping on the physical display.
- [x] With no saved SSID, setup Wi-Fi powers down after the absolute 30-minute
      window even if queried or used.
- [x] A software reboot starts a fresh unconfigured setup window.
- [x] With a saved SSID, Wi-Fi reconnects to `SlowFi`, reports minimum modem
      power save, and remains governed by normal configured-network behavior.
- [x] Bluetooth Companion pairing and protocol operations pass after saving a
      deterministic qualification PIN, removing any stale host bond, pairing,
      disconnecting the `bluetoothctl` client, and reconnecting with meshcli.
      Binary `infos`/`ver` returned the exact Indicator identity and USA tuple.
      A second connection after the USB-open reboot and logging-mode exercise
      also returned the exact build; one initial scanner miss was resolved by
      an explicit BlueZ discovery pass and was not a firmware advertisement
      failure.
- [x] Wi-Fi/TCP Companion, CLI, and OTA endpoints pass at `192.168.1.54`:
      port 5000 returned Binary Companion identity, port 5002 returned the full
      text version/status, and motatool completed a port-5001 `COUNT -> 0`
      protocol exchange against an empty served folder.
- [x] Fresh Full starts with USB logging off. On this single-TTY ESP32 profile,
      logging on/off applies and persists without a descriptor-changing reboot;
      the same held-open ASCII terminal reported off -> on -> off correctly,
      emitted live Wi-Fi/radio diagnostics while on, then accepted the explicit
      terminal-stop token and returned a valid 85-byte Binary Companion device
      info frame for `Seeed SenseCAP Indicator` without rebooting. Companion
      remained independently usable over Bluetooth and Wi-Fi.
- [x] Normal-radio LoRa transmit and receive pass. At the installed 22 dBm,
      a co-located discovery request overloaded the nearby receivers and found
      zero nodes; temporarily reducing only the Indicator to 0 dBm produced
      three repeater replies at RSSI -29 to -59 dBm, proving both directions.
      The saved 22 dBm value was restored and independently read back after the
      test; this near-field result is a lab-power gate, not a firmware failure.
- [ ] TempRadio sanity check, OTA seeding, and normal-radio restoration pass.

## Cross-radio LoRa OTA acceptance

- [ ] Run a three-minute reachability preflight before staging or changing any
      repeater radio settings.
- [ ] Test direct, fully controlled multi-hop, passive TempRadio hop, and mixed
      controlled/passive paths independently.
- [ ] Record bytes, LoRa packets sent, packets confirmed, retries, useful
      throughput, total elapsed time, and per-phase elapsed time.
- [ ] Confirm every controlled intermediate returns to its saved radio tuple.
- [ ] Verify target application and bootloader hashes after apply, not merely the
      OTA tool's final status line.
- [ ] Exercise interruption/retry and confirm incomplete data cannot be approved
      or booted.
