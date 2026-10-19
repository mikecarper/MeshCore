<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/contact_memory_2026-10-19/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Contact RAM savings and RAK4631 Full capacity, 2026-10-19

Shared contact indexes save RAM across Companion profiles and terminal chat.
RAK4631 Full additionally saves **2,200 static RAM bytes and 488 application
bytes**, preserving its existing features and SD support. Five canonical
packages and a direct terminal build pass. Heltec V3 Full failed its preexisting
RAM-admission limit at this revision; the subsequent
[V3 Full repair](heltec_v3_full_memory_2026-10-19.md) is measured separately.
**No hardware was flashed for this contact-memory campaign.**

Baseline source: [`31e693ec209109a930dcf8aeef89dacbe9116a63`](https://github.com/mikecarper/MeshCore/commit/31e693ec209109a930dcf8aeef89dacbe9116a63).
Final source: [`aed4c22e51201f0ee3586e26e1007194a74b015d`](https://github.com/mikecarper/MeshCore/commit/aed4c22e51201f0ee3586e26e1007194a74b015d).
The [machine-readable results](contact_memory_2026-10-19.json) bind package,
ELF, memory-manifest and linked-check hashes to these revisions.

## Shared contact savings

`BaseChatMesh` uses `uint16_t` sort/match indexes when the complete contact table
has at most 65,535 slots; larger configurations keep `int`. API counts retain
their existing types. All Companion profiles and `simple_secure_chat` share this
implementation across nRF52, ESP32, RP2040 and STM32. Infrastructure client/ACL
tables use different record types and are outside this contact-table change.

| Existing configuration | Internal RAM saved by indexes | PSRAM saved by indexes |
| --- | ---: | ---: |
| Static 350-contact table | 764 bytes | None |
| PSRAM 350-contact table with internal fallback | 96 bytes | 716 bytes |
| Static 100-contact table | 264 bytes | None |

These totals include eight anonymous slots and 24 match results. PSRAM retains
its existing 24-slot internal fallback and allocation-failure behavior. Cached
nRF52 `ContactInfo` records also shrink from 96 to 92 bytes by moving the
runtime-only `storage_slot` field into an alignment gap; other record layouts
retain their sizes. Field-based serialization, contact/channel capacities,
features, queues and stack allocations remain unchanged.

## RAK4631 Full comparison

| Measurement | Baseline | Final | Improvement |
| --- | ---: | ---: | ---: |
| Actual application BIN extracted from DFU ZIP | 712,600 | 712,112 | 488 fewer |
| Application-region capacity | 712,704 | 712,704 | Unchanged |
| Actual flash margin | 104 | 592 | 488 more |
| Ordinary static RAM (`.data` + `.bss`) | 156,284 | 154,084 | 2,200 fewer |
| Linked heap capacity | 79,092 | 81,292 | 2,200 more |
| Modeled heap requirement | 74,576 | 74,576 | Unchanged |
| Modeled admission margin | 4,516 | 6,716 | 2,200 more |

`.data` stays at 1,660 bytes; ordinary `.bss` falls from 154,624 to 152,424.
Separately reserved linker arenas are excluded from this section sum. The
measured reduction includes final object alignment. Linked capacities and
admission models do not measure running free heap or physical stack high-water.

Four duplicate CRC loops share the existing implementation without changing
initial values, byte ranges or stored formats. Reader packing increases offline
Zopfli iterations from 15 to 1,000 and selects 16-bit block offsets only when
every block start fits. General 32-bit types/fallback, 4,096-byte blocks,
scratch policy and all canonical text remain unchanged.

| Reader flash data | Baseline | Final | Saving |
| --- | ---: | ---: | ---: |
| Compressed payload | 38,366 | 38,293 | 73 bytes |
| Twenty-four block indexes | 288 | 192 | 96 bytes |
| Payload plus index | 38,654 | 38,485 | 169 bytes |

The complete image saves 488 bytes; Reader payload plus index accounts for 169.
The Full marker and thirteen feature-verification entries are unchanged.
Existing SD support, its 2,048-byte SD/FAT runtime reserve and protected
authentication range remain intact. No physical SD operation was tested.

## Source-bound artifacts

| Artifact | SHA-256 |
| --- | --- |
| Baseline DFU application | `520cf40ec5ada891d3fe8081b23546ce777c712383f0479292bb8fac0017d4d7` |
| Final DFU application | `c2080afe8874c71eb9a5d1fb253fec06463cac8a774fc776ff46453abf2da420` |
| Baseline DFU ZIP | `ef641d49dc91db61f0a49912f191f6d81c2887a69950fa66a612f81b31f0e142` |
| Final DFU ZIP | `d99d904430de75dd5e507a530267d933b8f0bda762afe86cdb150209f4df4499` |
| Final ELF | `f002455df52a9cdf4278461685eaee3082cc14752f741428f6dfe2dcdc2bb2a7` |

Every admitted package's memory-bound file hashes were recomputed. Nordic UF2
payloads match the DFU applications, and initialized ELF flash-load bytes agree.
The JSON records the individual hashes and actual packaged selectors/profiles.

## Software verification

Contact coverage totals **26 Python unittest methods**: three cache/persistence
harness methods, eleven persistence-contract methods, seven stream-contract
methods, two index/layout methods, one lifetime matrix, and two RP2040/STM32
uncached-storage methods. They execute **24 ASan/UBSan C++ configurations**
(three cache, eleven index, eight lifetime, two uncached) and separately compile
eight actual cached/uncached record layouts across the four MCU families. The
lifetime matrix checks 284 assertions. Index execution uses a small stand-in
record; separate layout checks compile the real header. Contact code is
unchanged since `b0f141615`; results are retained in the tool transcript, with
no separate contact log claimed.

Adjacent checks pass: 16 Reader tests, 19 Beacon tests, a radio-profile matrix
with 32 scenarios, seven Morse-codec tests and sixteen persistent-format tests.
These focused counts do not establish a complete hosted CI run.

Final linked ARM checks all bind to the ELF above:

- Crypto: both RFC8032 vectors pass; keypair/sign frames are 1,152/1,928 bytes,
  within the existing 2,048-byte function-frame bound.
- OTA workspace: four acquisition/release lifetimes pass, preserving protected
  SD authentication range `0x20006008`–`0x20006050`.
- Reader: four disjoint partitions reconstruct all 879 references and every
  continuation page: 95,689 canonical bytes through 1,091 calls and 380,103,543
  ARM instructions. Exact disjoint reference coverage is verified. Maximum
  callee frame is 6,016 bytes; with the existing 1,536-byte caller reserve this
  is 7,552 of the 8,192-byte loop-stack model. Canonical source SHA-256 remains
  `916bf9d1feae0ad410d69f61e6c5f328ac6acba10651d751b5bb29719732c49e`.

The partitioned campaign invokes the unchanged linked Reader checker with
all-page reconstruction; partitioning is not an additional release gate.
Equivalent complete reproduction against the matching ELF uses Unicorn 2.1.4:

```sh
python scripts/check_nrf52_companion_crypto.py path/to/firmware.elf
python scripts/check_nrf52_companion_ota_workspace.py path/to/firmware.elf
python scripts/check_nrf52_companion_reader.py path/to/firmware.elf --every-page
```

## Final-source build matrix

Canonical packages used `v1.18.0.1-halo-keymind-cascade-dev`, radio preset
`usa-cascade-fixed` and profile `cascade`; Full selections required OTA.
PlatformIO commands ran one at a time. For example:

```sh
bash build.sh build-firmware RAK_4631_companion_radio_full --firmware-version v1.18.0.1-halo-keymind-cascade-dev --radio-preset usa-cascade-fixed --profile cascade --require-ota
```

| Requested target | Actual application bytes | Modeled RAM margin | Result |
| --- | ---: | ---: | --- |
| `RAK_4631_companion_radio_full` | 712,112 | 6,716 | PASS |
| `RAK_3401_companion_radio_usb` | 523,864 | 50,940 | PASS |
| `heltec_v4_2_v4_3_companion_radio_full_femon` | 2,558,856 | 34,536 | PASS, Full PSRAM profile |
| `Xiao_rp2040_companion_radio_usb` | 472,204 | 144,340 | PASS |
| `RAK_3x72_companion_radio_usb` | 229,372 | 7,112 | PASS; four actual flash bytes free |
| `Heltec_v3_companion_radio_full` | No admitted package | -21,024 | Verified preexisting RAM-gate failure |

Matched baseline V3 Full also fails: 177,144 available against 198,432 required
(21,288 short), versus final 177,408 (21,024 short). This wrapper preserves 100
contacts, gaining 264 bytes. The final failed ELF was cleaned by a later build;
the failure is bound to its build log; that ELF hash is not recorded.

The direct `pio run -e Heltec_v3_terminal_chat` default build passes with 350
contacts: 263,800 available, 77,856 required and 185,944 margin. ELF SHA-256 is
`1a193ae1bee829d0bf7d140310b8487d2a4671f0c85715d16cfb764850e06cb6`.
This qualifies a no-PSRAM/shared-terminal compile path; it is not a Full package
or a firmware-picker release-version/preset qualification.

## Retained failures and limits

Intermediate `b0f141615` failed the STM32 size check. Shared little-endian Morse
serialization and an equivalent branchless CRC loop restore the packaged fit.
STM32 static RAM
falls from 40,256 to 39,992 bytes; baseline actual BIN was 229,284/229,376 bytes
(92 free), although PlatformIO's section summary showed 444 free. Acceptance
uses actual BIN/ELF extent. The final four-byte margin and RAK4631 Full's
592-byte margin require the unchanged hard gates for subsequent versions.

The separate legacy V3 BLE target has the same five unresolved WiFi setup/seeder
symbols on matched baseline and intermediate source. Initial disk-quota failures
and an unsupported V4 command flag remain separate failed attempts; successful
retries establish the results above. No features, capacities or budgets were
reduced to obtain these passes. There is no six-canonical-pass claim.

These are software/build/linked-code results. No board was flashed. Physical
contact/client operation, PSRAM pressure/fallback, storage persistence, SD/radio
operation, power, timing and full-firmware ISR/SoftDevice stack high-water remain
outside this qualification.
