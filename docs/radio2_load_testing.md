<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/radio2_load_testing/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Radio2 Load Testing

The host scanner now has small read-only [scan-health counters](radio_profiles.md#host-scan-health).
These diagnose scheduling delays separately from observed RX/TX holds. They
do not change dwell, preambles, or admission rules and cannot measure packet loss.

## Repeatable Native Traffic

The runner extracts the production scanner and retune methods into the existing
native radio fixture. It generates seeded mixed-PHY traffic, short/long preambles,
payloads up to 255 bytes, admitted local TX blackouts, and CPU/UI/storage service
gaps. It records every offered frame, modeled lock, CRC failure, partial-preamble
reset, delivery/loss, and exposure to each delay. It compares the scanner with
fixed receivers both with the same admitted TX blackouts and without local TX.

Run from the repository root with Python 3 and a native C++17 compiler:

```sh
python3 -B tools/sim/radio_profile_load.py --seed 20261124 --seed 7 --output radio2-load.json
python3 -B test/test_radio_profile_load.py -v
```

On Linux, enable address/undefined-behavior sanitizers:

```sh
RADIO2_LOAD_SANITIZE=1 python3 -B test/test_radio_profile_load.py -v
python3 -B tools/sim/radio_profile_load.py --sanitize --seed 7 --output radio2-load-sanitized.json
```

`--pair 0` through `--pair 3` select individual mixed-SF/BW/CR pairs; omit it to
run all four. Both seed and pair can be repeated. `--duration-us` changes the
traffic window (default 60000000). Receipts retain the workload, source hashes,
model assumptions, and seed so the same revision can replay exactly.

The model requires four consecutive preamble symbols to acquire a packet.
Same-profile overlapping frames fail CRC; capture, fading, frequency error,
actual sensitivity, and acquisition probability are not modeled. Service pauses
leave RX on but delay retunes and consumption of a single completion FIFO.
Fixed-receiver baselines use independent unlimited completion buffers. Startup
calibration is completed before traffic and noise-floor refresh is disabled.
These idealizations are explicit, not hardware-performance claims. Regression
controls deliberately produce real modeled losses; zero loss is not a pass
criterion for arbitrary offered traffic.

## T-Deck Real RF During Redraw

The optional [delivery harness and collector](../tools/hil/tdeck_radio2_delivery_README.md)
extend the existing receive-only timing harness with bounded RAM packet capture.
The collector compares static display, blocking redraw, and cooperative buffered
redraw with exact packet bytes and actual receive-profile tags. It requires an
explicitly selected T-Deck, a separately configured sender, and an independent
RX-only witness. No settings are changed and RF sends are never retried.

Hardware-free preview and collector regression tests:

```sh
python3 -S tools/hil/tdeck_radio2_delivery.py --plan --profile 1 --count 3 --seconds 5 --fps 20
python3 -B -m unittest discover -s tools/hil -p test_tdeck_radio2_delivery.py -v
```

The linked guide gives fixture requirements and explicit live commands.
Building the optional `tdeck_display_switch` environment does not flash it;
select and authorize the hardware fixture separately before any live campaign.
Retain failed and incomplete reports as well as successes. A passing campaign
requires sender TX evidence, an exact independently witnessed payload, and an
exact T-Deck wire packet on the intended profile. Timing counters and local ACKs
alone never count as delivery.

The [existing redraw measurements](test-results/tdeck_radio2_display.md) concern
service latency, not this packet-delivery campaign. The new campaign has not
been hardware-run as part of this change. It tests raw PHY delivery during an
active redraw workload, not exact RF/SPI interrupt overlap, Full Companion app
delivery, simultaneous dual-source traffic, or WiFi/Bluetooth coexistence.
