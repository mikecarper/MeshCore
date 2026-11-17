<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/gat562_ota_failure_2026-11-17/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# GAT562 OTA installation failure investigation

Investigation date: 17 November 2026 UTC. The reported GAT562 30S update
finished transferring, did not return after installation, and continued double
blinking after reset. Reflashing over USB restored operation. The affected
device and its failed flash contents were not available for inspection.

## Exact transfer identified

The reported running hash `E8798107`, target `180BBE1F`, manifest ID
`9DF841DF`, and 234 blocks reproduce the **first intermediate installation**
in the browser's GAT562 bridge route. They do not identify the final 1.18.0.2
application as the image being installed at that point.

| Item | Exact identity |
| --- | --- |
| Starting application | Full 1.18.0.1, release source `03214a16`, 601752 bytes |
| Starting body hash | `E87981076C6B0586` |
| Starting complete SHA-256 | `c308f6e097045692d3b9ab7f4f542b8a01ce6ed21954e905734ab1f84fe4706e` |
| Intermediate destination | Full bridge source `27d9aaf7de1beb3ea1afbbe068fc1d49542ce24a`, 606800 bytes |
| Destination body hash | `ABA0CFAF1DF1EC08` |
| Destination complete SHA-256 | `d06a56d213ff15b3bd03b87ebf36b31d842d0226a450804f5162d575786fe2a9` |
| Reproduced package SHA-256 | `5cdacdbb1b2b3dc4a6c1105da66fd9470427decb0211b28610394dc70aa749ba` |

The unchanged browser `buildInPlaceDelta` function reproduces the package
with `allowSameVersion: true`, `memorySize: 626688`,
`runtimeStageCeiling: 0xED000`, and the confirmed hybrid-RAM capability
`OTA RAM: hybrid=1 abi=1 record=72 arena=65536`.

The result is codec 2, manifest ID `9DF841DF`, 240061 container bytes,
238915 payload bytes, and 234 blocks of 1024 bytes. The reported `maxblk:2048`
is a receiver capability; this package uses 1024-byte blocks. Its 626688-byte
rewrite workspace and 176128-byte flash-staged prefix occupy 802816 of the
815104 available bytes, leaving 12288 bytes of flash margin.

## Verified binary properties

Both application EndF identities name `GAT562_30S_Mesh_Kit` and target
`180BBE1F`. Their layout records specify application base `0x26000`, end and
staging ceiling `0xED000`, and hybrid-RAM flag `0x10`. Both initial stack
pointers are `0x20030000`; their reset vectors lie within their respective
application images. The starting release's Nordic DFU metadata requires
SoftDevice ID `0xB6`, consistent with the S140 v6 application base. No GAT562
variant or board configuration change was found between these source commits.

The published GAT562 OTAFIX 2.4.12 image used by the browser's compatibility
pin has bootloader base `0xF4000`, initial stack `0x20030000`, and a
`MOTARAMA` capability record at offset 37648. Its advertised retained arena
is 65536 bytes, ABI 1, with a 72-byte authorization record. These binary
properties do not establish which bootloader was installed on the failed
physical device.

## Correction and validation limits

The GAT562 intermediate route is withdrawn from automatic preparation and
resume. Its content-addressed records remain available for investigation;
their presence is not qualification to install them. Recovery records must
be preserved rather than silently selecting another installation after an
uncertain result.

The earlier bridge qualification established byte-for-byte delta
reconstruction, manifest integrity, workspace arithmetic, browser workers,
and simulated reboot behavior. It did **not** establish that these
intermediate applications booted on a physical GAT562 or that the complete
installation survived that board's bootloader and reset path. The failure
exposes that qualification gap. Withdrawing the route prevents another
automatic attempt through these unqualified artifacts; it is not proof of
the underlying physical failure mechanism or a repaired bootloader.

A root-cause claim still requires a captured install/boot result or a
hardware reproduction using the exact application pair and installed
bootloader. Radio transfer completion alone does not prove installation or
successful application startup.
