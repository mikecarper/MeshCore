# Native USB backpressure and radio liveness

ESP32-S2/S3 builds using native TinyUSB CDC (`ARDUINO_USB_MODE=0` and
CDC-on-boot) must not wait for a computer to read USB output. In the bundled
Arduino-ESP32 2.0.17 core, `USBCDC::write()` can wait indefinitely for transmit
space; its configured timeout bounds a mutex, not that wait. This can stop the
same loop that services LoRa, even while the USB connection still appears open.

## Firmware behavior

- Native CDC output uses a single FIFO attempt, not Arduino's wait-for-space
  write or flush. TinyUSB's internal short mutex operations still apply; this
  is a no-host-progress-wait guarantee, not a lock-free driver replacement.
- Functional text and diagnostics share one ordered 4 KiB queue. Diagnostics
  leave 3 KiB reserved for commands. A diagnostic backlog alone does not prevent
  command input. A stalled host may lose diagnostic records; radio work continues.
- A connected terminal that exceeds its bounded output capacity gets an explicit
  dropped-byte notice when transmission resumes. Large local file dumps and
  recent-repeater listings advance between radio service passes instead.
- File dumps stop at their initial file size and emit `-> EOF` only after the
  final accepted record. Corrupt stored lines exceeding 640 bytes are omitted
  with a visible notice. Recent-repeater output is a live, bounded-cursor view;
  incoming packets can change its ordering while it is being printed.
- Disconnects discard pending old-session text and reset the role's partial
  command/listing state. Binary/mOTA transitions suppress pending text notices.
  Bytes already transmitted cannot be recalled.
- Companion frames retain and retry short writes. An mOTA request is admitted
  only when its complete, at-most-11-byte record fits.
- nRF52, UART, and USB-Serial-JTAG behavior is not changed by this native-CDC path.

Host software must also keep reading independently of command writes. A serial
relay should start its reader before the first command, use finite write and
response deadlines, avoid discarding received packet logs, and cancel I/O during
shutdown. A response timeout must not allow a late reply to satisfy another
command: the CLI has no transaction identifiers. Updating firmware alone does
not correct an indefinite host-side serial write.

## Regression checks

Run only one PlatformIO process in the checkout at a time.

```sh
python test/test_esp32_tinyusb_nonblocking.py
python test/test_esp32_tinyusb_role_hygiene.py
python test/test_esp32_tinyusb_cooperative_output.py
python test/test_esp32_usb_serial_hygiene.py
python test/test_nrf52_usb_logging_contract.py
pio test -e native -f test_nrf52_debug_output -f test_serial_packet_log -f test_serial_mode_switch -f test_mesh_tables
```

The first test compiles the real USB facade against a simulated 64-byte FIFO,
including stopped readers, reconnects, protocol transitions, and other-platform
fallbacks. Host simulations cannot establish that every real USB driver or
endpoint failure has recovered.

A Full Station G2 validation build with USA Cascadia radio settings and the Cascade
profile can be made using the normal build entry point. The portable `standard`
recipe preserves the deployed partition layout but omits LoRa OTA; it still has
the browser firmware uploader. The `auto`/`full` recipe instead enables the
expanded feature set and partition layout: its merged image is **not** an
app-only update for a device with the legacy layout.

```sh
MESHDEBUG_OVERRIDE=on PACKET_LOGGING_OVERRIDE=on \
  bash build.sh build-firmware Station_G2_repeater --build-profile full \
  --radio-preset usa-cascadia --profile cascade
```

Before installing on hardware, preserve the device identity, preferences, and
existing partition layout. Then verify USB command responses and repeated LoRa
logins with the relay running, paused, and stopped, including a host that leaves
USB open without reading. Check LoRa recovery separately from USB OUT recovery;
fixing transmit backpressure does not prove an unrelated OUT endpoint fault is
resolved. Do not erase or repartition the radio as part of this test.
