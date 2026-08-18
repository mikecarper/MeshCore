# MeshCore memory monitoring

MeshCore exposes current allocator information through the CLI. On an ESP32
build, run `memory` over a supported local or administrator CLI transport:

```text
memory
  -> Free: 102796, Min: 83544, Max: 75764, Queue: 0, IntFree: 68420, IntMax: 53248, PSRAM: 3918400/4194304
```

For a USB serial console, identify the device port and open it at the baud rate
configured by the build (normally 115200):

```bash
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
screen /dev/ttyACM0 115200
```

Then enter `memory` periodically. Exit GNU Screen with `Ctrl-A`, then `K`.

The repository does not ship a `monitor_memory.py` collector. For a long soak,
use a serial terminal with timestamped logging, or have the test harness send
`memory` at a conservative interval and retain each complete reply.

## ESP32 fields

| Field | Meaning |
|---|---|
| `Free` | Bytes currently free in the ESP heap. |
| `Min` | Lowest free-heap value observed since boot. This is a low-water mark and does not rise when memory is released. |
| `Max` | Largest single allocation currently available from the ESP heap. |
| `Queue` | Current MeshCore transmit-queue length. It is not specifically an MQTT queue. |
| `IntFree` | Bytes currently free in internal-capability RAM. |
| `IntMax` | Largest single allocation currently available in internal-capability RAM. |
| `PSRAM` | Free/total external PSRAM bytes. A board or build without PSRAM normally reports `0/0`. |

Non-ESP builds that expose the common CLI may return the shorter
`Heap: free=<bytes>, used=<bytes>` form. The public build matrix only promises
the detailed `memory` command on ESP32; see the
[CLI availability matrix](docs/cli_command_availability.md#memory).

## Interpreting a soak

Establish a separate idle and workload baseline for each board and firmware
profile. Fixed thresholds such as “50 KB is always low” are misleading because
heap size, PSRAM, enabled features, and allocation capabilities differ by build.
Look for trends instead:

- `Free` repeatedly returns to roughly the same baseline after transient work.
- `Min` can fall during a new peak workload; continued new lows under an
  identical repeating workload deserve investigation.
- A shrinking `Max` while `Free` remains stable can indicate fragmentation or a
  changed allocation pattern.
- A `Queue` that grows and does not drain points to radio backpressure or stalled
  processing, not necessarily a leak.
- On PSRAM builds, inspect internal RAM separately. Plenty of PSRAM cannot satisfy
  allocations that require internal-capability memory.

Sample immediately after boot, after network and MQTT startup, during the
intended peak workload, and again after that workload becomes idle. Preserve
the firmware version, build environment, uptime, and workload alongside the
samples so results are comparable.

## Related diagnostics

- `stats-core` reports battery, uptime, queue length, and core debug flags over a
  local serial session.
- `stats-radio` and `stats-packets` help distinguish memory pressure from radio
  or queue congestion.
- On MQTT observer builds, `get mqtt.stats` reports bridge publish health and a
  heap snapshot; `get mqtt.status` reports bridge state and schedules.

See the [CLI command reference](docs/cli_commands.md#statistics) and
[MQTT command availability](docs/cli_command_availability.md#mqtt-stats).

## Troubleshooting

- If the device does not answer, verify the port, build-specific baud rate, and
  that the selected firmware exposes a CLI on that transport.
- If `memory` returns `Unknown command`, check the build matrix. Do not infer a
  memory failure from an unavailable command.
- If output stops during a soak, retain the last complete sample and capture the
  device log and reset reason. The last free-heap value alone is not a crash
  diagnosis.
- If only `Queue` rises, inspect radio and packet statistics before treating the
  symptom as allocator exhaustion.
