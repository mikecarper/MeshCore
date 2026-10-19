<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/heltec_v3_full_memory_2026-10-19/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Heltec V3 Full memory qualification, 2026-10-19

Canonical V3 Full now passes its flash and runtime-RAM gates with **6,032 bytes
of modeled RAM margin**, retaining its existing features and capacities.
**Physical testing is pending:** no V3 is currently exposed to the VM, and no
board was flashed during this work.

Measured source: [`eb9259f2a97113b1d482ca9610661fb24c1c8e98`](https://github.com/mikecarper/MeshCore/commit/eb9259f2a97113b1d482ca9610661fb24c1c8e98).
The [machine-readable evidence](heltec_v3_full_memory_2026-10-19.json) records
package hashes, allocation components, capabilities and linked ARM checks.

## What changed

V3 Full uses the existing NimBLE-Arduino 2.5.1 Companion adapter. It preserves
the authenticated NUS service, PIN, address/stealth controls and frame queues.
The canonical profile retains one active BLE session, **15 saved phones and
30 CCCD records** for NUS TX and Service Changed subscriptions. This does not
reuse the smaller default bond store from the optional trial configuration.

**Bluetooth upgrade step:** existing Bluedroid bonds are not migrated. Forget
the previous pairing in the phone's Bluetooth settings and pair again after
installing this image. The PIN setting remains available as before.

Non-PSRAM ESP32 Full also uses a bounded external-source Merkle cache. It keeps
32-leaf groups and their roots, then rereads the selected group to generate the
same wire proof. It supports the same 4,096 source blocks, folder capture,
raw/host-encoded/device-encoded serving, getter requests and transport protocols.
A proof step issues at most one metadata transaction; jobs survive partial
progress and radio backpressure.

The complete workspace is 1,004 bytes in the 32-bit Xtensa layout, with a
production `sizeof` assertion enforcing a 1,536-byte bound. Its output block and
allocation allowance remain separate. The legacy 16 KiB source-leaf allocation
is absent on this path. PSRAM, nRF52 and other unqualified paths retain their
existing implementation, as do explicit larger custom source limits.

The RAM check accounts for this smaller real allocation. The 16 KiB transient
margin, Bluetooth/WiFi allowances, MQTT admission, stacks and minimum floors
remain unchanged. There is no new OTA queue loan or reduction in offline frames.

## Measured image

Both comparisons use the canonical release-length version, Cascade profile and
USA Cascade preset. The baseline is `31e693ec2`; it already failed admission
before the shared contact-memory changes.

| Measurement | Baseline | Final |
| --- | ---: | ---: |
| Internal capacity after linking | 177,144 | 189,616 |
| Modeled allocation requirement | 198,432 | 183,584 |
| Modeled margin | -21,288 | **6,032** |
| Application bytes | Not admitted | 2,114,744 |
| Application-slot capacity | — | 3,342,336 |
| Application-slot margin | — | 1,227,592 |

The existing V3 Full recipe retains **100 contacts, 40 channels, 256 offline
frames and one MQTT runtime slot**. These limits were not reduced by this fix.
All 24 linked/package feature checks pass, alongside the Full profile marker.
USB, Bluetooth, simultaneous WiFi, direct MQTT, WebConfig, logging and OTA remain
in the image. Modeled capacity is not a running free-heap or load measurement.

Final ELF SHA-256: `3a56805b2d0216026c3710e149db43dedb677684e47ad08ebfe748094c1de55b`.
Application SHA-256: `c4f276095626560abc4c6875216c147d2fae292bf5c514e5029ef7d03454d3ce`.
Every file bound by the packaged memory manifest was hash-verified.

## Verification

- New proof tests pass under ASan/UBSan, with 22,240 exact wire-proof comparisons,
  partial/failing reads, negotiated transport-size changes, allocation failure,
  cancellation, idle cleanup, folder capture, getter bytes and backpressure.
- The RAM-policy suite passes 47 tests. Ten adapter/variant tests pass, including
  real adapter behavior under ASan/UBSan and the V3 DIO policy.
- Existing cold-buffer tests pass on four platform configurations. Serial
  deadlines and source-only folder-resume tests each pass two test methods.
- Clean-source canonical RAK4631 Full and RAK3x72 USB also build. RAK4631 retains
  6,716 modeled RAM bytes and 592 application bytes of margin; RAK3x72 retains
  7,112 RAM bytes and four application bytes. Their existing hard gates remain.
- The final-source RAK4631 ELF passes linked RFC8032 crypto, four OTA workspace
  lifetimes and every page of all 879 Reader references. Those checks bind to
  its own ELF hash in the JSON rather than to an earlier contact-memory image.

PlatformIO commands ran one at a time in a clean checkout. Reproduce the V3
package with:

```sh
bash build.sh build-firmware Heltec_v3_companion_radio_full --firmware-version v1.18.0.1-halo-keymind-cascade-dev --radio-preset usa-cascade-fixed --profile cascade --require-ota
```

Hardware acceptance still requires the physical V3: preserve its flash and
settings, then check boot, live heap under simultaneous transports, contacts,
Bluetooth pairing/reconnect, MQTT, WiFi administration, OTA and persistence.
These software results do not establish that hardware acceptance.
