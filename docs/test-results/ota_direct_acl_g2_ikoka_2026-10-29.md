<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/ota_direct_acl_g2_ikoka_2026-10-29/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Station G2 to Ikoka Stick: direct ACL transfer sample, 2026-10-29

A Station G2 Full Repeater connected by USB served a capped application
transfer to an Ikoka Stick Repeater over a direct LoRa link. The receiver
confirmed **5 of 268 blocks**, or **10,240 bytes of application payload**.
The test stopped there; it did not install firmware or update the bootloader.

| Component | Tested value |
| --- | --- |
| Source | Station G2 Full Repeater, revision `ba0321a8` |
| Destination | Ikoka Stick nRF52 33 dBm Repeater, firmware `v1.17.1.9` |
| Destination bootloader | OTAFIX 2.4.11, QSPI staging |
| Administration | Existing admin ACL grant; native `ota.peer cmd.acl` |
| Link | Zero-hop; source secondary profile to destination temporary profile |
| Package | Full application package, 2,048-byte payload blocks |
| Transfer cap | Five blocks; requests beyond the cap were denied |

## Confirmed results

Direct ACL commands worked on both the normal and temporary profiles without
a password login. A `get tx` reply on the temporary profile took about 1.4
seconds. The returned setting was `9`; this is a configured radio value,
not a measurement of output power after the external amplifier.

The destination reported `FETCH 5/268` for the selected package. It was later
confirmed `FETCH idle`. No install or destination reboot was requested.
The cancellation reply was lost, so this run does not establish that the
persistent QSPI receive slot was invalidated.

On-air compression was not verified. The payload count and USB file reads
do not establish how many compressed bytes crossed the radio link. This
sample also does not qualify a complete download, installation, or the
guided wizard's end-to-end recovery flow.

## Command handling

A successful temporary-profile acknowledgement was accepted without repeated
status queries. Necessary read or idempotent commands were limited to three
attempts; `get tx` was used as a connectivity probe only after those attempts
failed. Relative temporary leases, download, install and cancel commands
were not candidates for blind replay after a lost reply.

At the operator's request, both temporary profiles were left active for 12
hours. G2 repeating was on and cross-profile forwarding was off. Destination
RX power saving was returned to maximum; G2 RX power saving remained off for
secondary-profile scanning. These were test-device settings, not wizard
defaults.
