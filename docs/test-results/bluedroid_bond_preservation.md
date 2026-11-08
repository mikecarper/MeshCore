<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/bluedroid_bond_preservation/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32-S3 Bluedroid bond preservation

Validation date: 7 November 2026.

The change retains Bluedroid and its NVS database. It replaces three exact SDK
objects, leaves unsuccessful authentication out of the persistent storage path,
and rebuilds the Bluetooth host after disconnected sessions. It does not alter
the PIN requirement or permit unauthenticated Companion traffic.

## Checks

- Two independent local archive rebuilds produced the same SHA-256. All 355
  members were checked; only the intended three differ from the installed SDK.
- Unmodified source rebuilds matched all three stock objects in allocated
  sections, relocations and exported symbols.
- Windows and Linux reconstructed the same final archive from the packaged
  objects. The linker map check confirms it supplied the Bluetooth objects.
- Host storage-callback tests exercise unsuccessful authentication, pending
  recovery, mismatched staged identity and successful replacement. No controller
  or real keys are used. These and adapter lifecycle tests passed with ASan/UBSan.
- V4.2 application-only upgrade from published 1.18.0.1 retained the Pi central's
  existing pairing. Encrypted NUS communication worked after upgrading and again
  after a hardware reset. The revised session-cleanup build repeated this test.
- The revised candidate completed consecutive encrypted reconnects without a
  board reset. Forgetting only the Pi's bond and pairing again with the normal
  PIN flow also succeeded, followed by another encrypted reconnect.
- V4 Full and standard XIAO ESP32-S3 Wio BLE builds passed their link, size and
  RAM checks. The XIAO build selected the protection automatically without an
  experimental build flag. Original application and OTA data were
  restored and flash-verified after hardware tests; phone Bluetooth was restored.

The Android phone's existing pairing was not verified. On-radio authentication
failure injection, power loss during replacement, maximum bond capacity, every
phone model and non-S3 hardware are not established by these results. Successful
replacement writes and full-store eviction retain the SDK's existing semantics.

Early 15-second startup probes were too short even for the restored original
firmware. The completed tests waited up to 90 seconds and used the framed USB
protocol to establish startup before checking Bluetooth.
