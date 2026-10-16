#!/usr/bin/env python3
"""Exercise the Full overlay, real linker reserve, and sensor allocation failure."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(text, signature):
    start = text.index(signature)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def full_flags(target):
    result = subprocess.run([
        "bash", "-c", r'''
source build.sh
PIO_ENV_PLATFORM_BY_NAME["$1"]=NRF52_PLATFORM
pio_env_option_contains() { return 0; }
apply_companion_radio_full_profile "$1" "$1"
printf '%s\n%s\n' "$PLATFORMIO_BUILD_FLAGS" "$PLATFORMIO_BUILD_UNFLAGS"
''', "test", target], cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout


class T096FullMemoryTest(unittest.TestCase):
    def test_t096_full_overlay_preserves_features_and_reserves_heap(self):
        for suffix in ("femon", "femoff"):
            with self.subTest(suffix=suffix):
                flags = full_flags("Heltec_t096_companion_radio_full_" + suffix)
                self.assertIn("-DOFFLINE_QUEUE_SIZE=256", flags.splitlines()[0])
                self.assertNotIn("-DOFFLINE_QUEUE_SIZE=128", flags.splitlines()[0])
                self.assertIn("-DOTA_SHARED_COMPANION_QUEUE=1", flags)
                self.assertIn("--defsym=__mesh_nrf52_min_heap_size=73728", flags)
                for feature in ("ENABLE_USB_INTERFACE", "OTA_SEEDER_ONLY",
                                "COMPANION_FEATURE_BLE_MOTA_SOURCE",
                                "COMPANION_FEATURE_USB_MOTA_SOURCE",
                                "COMPANION_FEATURE_DEDICATED_USB_LOGGING"):
                    self.assertIn("-D" + feature + "=1", flags)
                self.assertNotIn("-DMAX_CONTACTS", flags)
                self.assertNotIn("-DMAX_GROUP_CHANNELS", flags)

    def test_all_nrf52_full_profiles_share_without_changing_legacy(self):
        for target in ("RAK_4631_companion_radio_full", "WioTrackerL1Eink_companion_radio_full",
                       "Heltec_t114_companion_radio_full", "RAK_3401_companion_radio_full"):
            with self.subTest(target=target):
                flags = full_flags(target)
                self.assertIn("-DOTA_SHARED_COMPANION_QUEUE=1", flags)
                self.assertIn("-DOFFLINE_QUEUE_SIZE=256", flags.splitlines()[0])
        flags = full_flags("Heltec_t096_companion_radio_ble_femon")
        self.assertNotIn("-DOTA_SHARED_COMPANION_QUEUE", flags)
        self.assertNotIn("__mesh_nrf52_min_heap_size", flags)

    def test_real_linker_rejects_release_heap_and_enforces_boundary(self):
        flags = full_flags("Heltec_t096_companion_radio_full_femon")
        reserve = int(re.search(r"__mesh_nrf52_min_heap_size=(\d+)", flags)[1])
        self.assertIn('INCLUDE "boards/nrf52_heap_reserve.ld"',
                      (ROOT / "boards/nrf52840_s140_v6_extrafs.ld").read_text())
        with tempfile.TemporaryDirectory(prefix="meshcore-heap-") as temp:
            obj = Path(temp) / "empty.o"
            subprocess.run(["cc", "-x", "c", "-c", "-", "-o", str(obj)],
                           input="int linked_fixture;", text=True, check=True)
            # Released 26303793 leaves 54,724 bytes between the real heap
            # symbols. Static RAM fitting alone previously admitted it.
            for size, minimum, accepted in (
                (54724, reserve, False), (reserve - 1, reserve, False),
                (reserve, reserve, True), (reserve + 16384, reserve, True),
                (-1, reserve, False), (1024, None, True),
            ):
                with self.subTest(size=size, minimum=minimum):
                    command = ["ld", "-r", str(obj), "-o", str(Path(temp) / "linked.o"),
                               "-T", str(ROOT / "boards/nrf52_heap_reserve.ld"),
                               "--defsym=__HeapBase=0x20006008",
                               f"--defsym=__HeapLimit={0x20006008 + size}"]
                    if minimum is not None:
                        command += [f"--defsym=__mesh_nrf52_min_heap_size={minimum}"]
                    result = subprocess.run(command, text=True, capture_output=True)
                    self.assertEqual(result.returncode == 0, accepted, result.stderr)
                    if not accepted:
                        self.assertIn("nRF52", result.stderr)

    def test_sensor_page_survives_failed_cayenne_allocation(self):
        refresh = method((ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text(),
                         "void refresh_sensors()")
        harness = r'''
#include <cassert>
#include <cstdint>
#include <stdexcept>
#define AUTO_OFF_MILLIS 15000
#define UI_RECENT_LIST_SIZE 3
#define TELEM_CHANNEL_SELF 0
uint32_t millis() { return 100; }
struct Lpp {
  uint8_t storage[8];
  bool allocated = false;
  uint8_t size = 0;
  uint8_t* getBuffer() { return allocated ? storage : nullptr; }
  void reset() { size = 0; }
  uint8_t getSize() { return size; }
  void addVoltage(int, float) {
    if (!getBuffer()) throw std::runtime_error("sensor menu NULL write");
    size = 4;
  }
};
struct LPPReader {
  bool pending;
  LPPReader(uint8_t* buf, int size) : pending(buf && size) {}
  bool readHeader(uint8_t&, uint8_t&) { bool p = pending; pending = false; return p; }
  void skipData(uint8_t) {}
};
struct Board { int getBattMilliVolts() { return 4000; } } board;
struct Sensors { int calls = 0; void querySensors(int, Lpp&) { ++calls; } } sensors;
struct Home {
  Lpp sensors_lpp;
  int sensors_nb = 5, sensors_scroll_offset = 4;
  uint32_t last_sensors_refresh = 0;
  bool sensors_scroll = true;
  @REFRESH@
};
int main() {
  Home home;
  home.refresh_sensors();
  assert(home.sensors_nb == 0 && !home.sensors_scroll);
  assert(home.sensors_scroll_offset == 0 && sensors.calls == 0);
  home.sensors_lpp.allocated = true;
  home.refresh_sensors();
  assert(home.sensors_nb == 1 && sensors.calls == 1);
  assert(home.last_sensors_refresh == 100);
  home.refresh_sensors();
  assert(sensors.calls == 1);
}
'''.replace("@REFRESH@", refresh)
        with tempfile.TemporaryDirectory(prefix="meshcore-sensor-oom-") as temp:
            binary = Path(temp) / "menu"
            built = subprocess.run(["c++", "-std=c++17", "-x", "c++", "-",
                                    "-o", str(binary)], input=harness,
                                   text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_queue_preserves_fifo_across_wrap_and_workspace_reuse(self):
        harness = r'''
#include <helpers/BorrowableFrameBuffer.h>
#include <cassert>
#include <cstdint>
#include <deque>
#include <cstring>
struct Frame { uint8_t len; uint8_t buf[176]; };
struct Workspace {
  static int alive;
  uint32_t words[4906]; // matches the roughly 19 KiB embedded mOTA context
  Workspace() { ++alive; memset(words, 0xA5, sizeof words); }
  ~Workspace() { --alive; memset(words, 0x5A, sizeof words); }
};
int Workspace::alive = 0;
using Buffer = mesh::BorrowableFrameBuffer<Frame, 256, 128, Workspace>;
int main() {
  Buffer buffer;
  int head = 0, count = 0;
  unsigned sequence = 0;
  std::deque<unsigned> expected;
  auto add = [&]() {
    assert(count < int(buffer.capacity()));
    Frame& frame = buffer.at((head + count) % buffer.capacity());
    frame.len = 176;
    memset(frame.buf, sequence % 251, sizeof frame.buf);
    memcpy(frame.buf, &sequence, sizeof sequence);
    expected.push_back(sequence++);
    ++count;
  };
  auto pop = [&]() {
    assert(count > 0);
    const Frame& frame = buffer.at(head);
    unsigned id;
    memcpy(&id, frame.buf, sizeof id);
    assert(frame.len == 176 && id == expected.front());
    for (unsigned i = sizeof id; i < sizeof frame.buf; ++i)
      assert(frame.buf[i] == id % 251);
    expected.pop_front();
    --count;
    head = count ? (head + 1) % buffer.capacity() : 0;
  };
  for (int cycle = 0; cycle < 300; ++cycle) {
    while (count < 256) add();
    // A full queue refuses the loan without dropping or reordering messages.
    int previous_head = head;
    assert(!buffer.acquire(count, head));
    assert(count == 256 && head == previous_head && Workspace::alive == 0);
    while (count > 129) pop();
    assert(!buffer.acquire(count, head));
    pop();
    // Exercise every possible wrapped head at the exact 128-frame boundary.
    for (int i = 0; i < cycle % 256; ++i) { pop(); add(); }
    Workspace* workspace = buffer.acquire(count, head);
    assert(workspace && head == 0 && buffer.capacity() == 128);
    assert(Workspace::alive == 1 && buffer.acquire(count, head) == workspace);
    for (auto& word : workspace->words) word = 0xC0DEC0DE;
    for (int i = 0; i < cycle % 128; ++i) { pop(); add(); }
    buffer.release(head);
    assert(head == 0 && buffer.capacity() == 256 && Workspace::alive == 0);
    buffer.release(head); // repeated stop is harmless
    while (count < 256) add();
    while (count) pop();
  }
  assert(expected.empty());
  assert(buffer.acquire(count, head)); // empty queue / destructor while borrowed
}
'''
        with tempfile.TemporaryDirectory(prefix="meshcore-shared-queue-") as temp:
            binary = Path(temp) / "shared_queue"
            built = subprocess.run([
                "c++", "-std=c++11", "-fsanitize=address,undefined", "-g",
                "-I", str(ROOT / "src"), "-x", "c++", "-", "-o", str(binary),
            ], input=harness, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
