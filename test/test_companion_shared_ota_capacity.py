#!/usr/bin/env python3
"""Exercise split C3/S3 queues while explicit OTA work borrows cold storage."""

from pathlib import Path
import re
import resource
import signal
import shutil
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


class SharedCompanionOtaCapacityTest(unittest.TestCase):
    def compile(self, text, *, run=False, rtc=False, args=(), expected_returncode=0):
        with tempfile.TemporaryDirectory(prefix="meshcore-shared-c3-") as temp:
            binary = Path(temp) / "queue"
            (Path(temp) / "sdkconfig.h").write_text("#pragma once\n")
            (Path(temp) / "esp_attr.h").write_text(
                '#pragma once\n#define RTC_FAST_ATTR __attribute__((section(".rtc.force_fast.test")))\n'
                '#define RTC_SLOW_ATTR __attribute__((section(".rtc.force_slow.test")))\n')
            command = ["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       "-I", str(ROOT / "src"), "-I", temp, "-x", "c++", "-",
                       "-o", str(binary)]
            if rtc:
                s3 = rtc in ("ESP32S3", "ESP32S3_36")
                slow_slots = 36 if rtc == "ESP32S3_36" else (32 if s3 else 0)
                command[1:1] = ["-DNDEBUG", "-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1",
                    "-DOTA_SHARED_COMPANION_QUEUE=1", f"-DOFFLINE_QUEUE_SIZE={256 if s3 else 224}",
                    f"-DCONFIG_IDF_TARGET_{'ESP32S3' if s3 else 'ESP32C3'}=1",
                    *(["-DM5STACK_CARDPUTER_ADV=1"] if rtc == "ESP32S3_36" else []),
                    "-DCONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP=1",
                    "-DCOMPANION_RTC_FAST_OFFLINE_FRAMES=32",
                    f"-DCOMPANION_RTC_SLOW_OFFLINE_FRAMES={slow_slots}"]
            if run:
                command[1:1] = ["-fsanitize=address,undefined", "-g"]
            result = subprocess.run(command, input=text, text=True,
                                    capture_output=True)
            if run:
                self.assertEqual(result.returncode, 0, result.stderr)
                if rtc:
                    objdump = shutil.which('objdump')
                    self.assertIsNotNone(objdump, 'objdump is required for the physical RTC bank check')
                    sections = subprocess.check_output([objdump, '-h', str(binary)], text=True)
                    match = re.search(r'\.rtc\.force_fast\.test\s+([0-9a-fA-F]+)', sections)
                    self.assertIsNotNone(match, sections)
                    self.assertEqual(int(match[1], 16), 32 * 177, sections)
                    if s3:
                        match = re.search(r'\.rtc\.force_slow\.test\s+([0-9a-fA-F]+)', sections)
                        self.assertIsNotNone(match, sections)
                        self.assertEqual(int(match[1], 16), slow_slots * 177, sections)
                def no_core_dump():
                    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                result = subprocess.run([str(binary), *args], text=True,
                                        capture_output=True, preexec_fn=no_core_dump)
                self.assertEqual(result.returncode, expected_returncode, result.stderr)
            return result

    def queue_fixture(self, rtc=False, capacity=224):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        declaration = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        retained = re.search(
            r"static constexpr unsigned OTA_RETAINED_OFFLINE_FRAMES = (\d+);",
            declaration)[1]
        methods = "\n".join(method(source, signature) for signature in (
            "mesh::ota::OtaContext* MyMesh::acquireOfflineQueueForOta(void* owner)",
            "uint16_t MyMesh::drainOfflineQueueForOta(void* owner)",
            "void MyMesh::releaseOfflineQueueFromOta(void* owner)",
        ))
        harness = r'''
#include <helpers/BorrowableFrameBuffer.h>
#include <helpers/CompanionRtcQueue.h>
#include <cstdlib>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <deque>
#undef assert
#define assert(value) do { if (!(value)) std::abort(); } while (0)
constexpr unsigned MAX_FRAME_SIZE = 176;
namespace mesh { namespace ota {
struct OtaContext {
  static unsigned live, constructions, destructions;
  uint8_t bytes[11284]; // source-only ESP32 linked context, not a RAM budget
  OtaContext() {
    ++live; ++constructions;
    memset(bytes, 0xA5, sizeof bytes);
  }
  ~OtaContext() {
    --live; ++destructions;
    memset(bytes, 0x5A, sizeof bytes);
  }
};
unsigned OtaContext::live = 0, OtaContext::constructions = 0,
         OtaContext::destructions = 0;
} }
struct MyMesh {
  struct Frame { uint8_t len; uint8_t buf[MAX_FRAME_SIZE]; };
  static constexpr unsigned OTA_RETAINED_OFFLINE_FRAMES = @RETAINED@;
  @STORAGE@
  int offline_queue_len = 0, offline_queue_head = 0;
  unsigned next = 0, notifications = 0;
  std::deque<unsigned> expected;
  static mesh::ota::OtaContext* acquireOfflineQueueForOta(void* owner);
  static uint16_t drainOfflineQueueForOta(void* owner);
  static void releaseOfflineQueueFromOta(void* owner);
  void add() {
    assert(offline_queue_len < int(offline_queue.capacity()));
    Frame& frame = offline_queue.at(
        (offline_queue_head + offline_queue_len) % offline_queue.capacity());
    frame.len = sizeof frame.buf;
    memset(frame.buf, next % 251, sizeof frame.buf);
    memcpy(frame.buf, &next, sizeof next);
    expected.push_back(next++);
    ++offline_queue_len;
  }
  int getFromOfflineQueue(uint8_t* bytes) {
    if (!offline_queue_len) return 0;
    Frame& frame = offline_queue.at(offline_queue_head);
    unsigned sequence;
    memcpy(&sequence, frame.buf, sizeof sequence);
    assert(frame.len == sizeof frame.buf && sequence == expected.front());
    for (unsigned i = sizeof sequence; i < sizeof frame.buf; ++i)
      assert(frame.buf[i] == sequence % 251);
    memcpy(bytes, frame.buf, frame.len);
    expected.pop_front();
    --offline_queue_len;
    offline_queue_head = offline_queue_len
        ? (offline_queue_head + 1) % offline_queue.capacity() : 0;
    ++notifications;
    return frame.len;
  }
  void pop() { uint8_t bytes[MAX_FRAME_SIZE]; assert(getFromOfflineQueue(bytes)); }
};
@BANKS@
@METHODS@
int main() {
  using mesh::ota::OtaContext;
  MyMesh mesh;
  for (unsigned head = 0; head < @CAPACITY@; ++head) {
    while (mesh.offline_queue_len < @CAPACITY@) mesh.add();
    // Visit every wrapped head with a full normal queue.
    for (unsigned i = 0; i < head; ++i) { mesh.pop(); mesh.add(); }
    int original_head = mesh.offline_queue_head;
    unsigned constructed = OtaContext::constructions;
    assert(!MyMesh::acquireOfflineQueueForOta(&mesh));
    assert(mesh.offline_queue_len == @CAPACITY@);
    assert(mesh.offline_queue_head == original_head);
    assert(OtaContext::constructions == constructed && OtaContext::live == 0);
    // Only the explicit drain consumes frames, preserving the newest 128.
    unsigned notified = mesh.notifications;
    assert(MyMesh::drainOfflineQueueForOta(&mesh) == @DRAINED@);
    assert(mesh.notifications == notified + @DRAINED@ && mesh.offline_queue_len == 128);
    assert(MyMesh::drainOfflineQueueForOta(&mesh) == 0);
    OtaContext* owner = MyMesh::acquireOfflineQueueForOta(&mesh);
    assert(owner && OtaContext::live == 1 && mesh.offline_queue.capacity() == 128);
    assert(OtaContext::constructions == constructed + 1);
    assert(MyMesh::acquireOfflineQueueForOta(&mesh) == owner);
    assert(OtaContext::constructions == constructed + 1);
    memset(owner->bytes, 0xCC, sizeof owner->bytes);
    for (unsigned i = 0; i < head % 128; ++i) { mesh.pop(); mesh.add(); }
    MyMesh::releaseOfflineQueueFromOta(&mesh);
    assert(OtaContext::live == 0 && mesh.offline_queue.capacity() == @CAPACITY@);
    assert(OtaContext::constructions == OtaContext::destructions);
    MyMesh::releaseOfflineQueueFromOta(&mesh); // idle release cannot destroy twice
    while (mesh.offline_queue_len < @CAPACITY@) mesh.add();
    while (mesh.offline_queue_len) mesh.pop();
    assert(mesh.expected.empty());
  }
}
'''.replace("@RETAINED@", retained).replace("@METHODS@", methods).replace(
    "@CAPACITY@", str(capacity)).replace("@DRAINED@", str(capacity - 128))
        if rtc:
            start = declaration.index('  // Concrete definitions in MyMesh.cpp')
            end = declaration.index('\n#endif\n#if defined(OTA_SHARED_COMPANION_QUEUE)', start)
            shared_start = declaration.index('  static constexpr unsigned OTA_RETAINED_OFFLINE_FRAMES')
            queue_start = declaration.index('  mesh::BorrowableFrameBuffer<Frame', shared_start)
            queue_end = declaration.index('  static mesh::ota::OtaContext* acquireOfflineQueueForOta', queue_start)
            storage = (declaration[start:end] + '\n' + declaration[queue_start:queue_end]
                + '\n  MyMesh() : offline_queue(rtcFastQueueBank(), rtcSlowQueueBank()) {}\n')
            bank_start = source.index('#if COMPANION_RTC_FAST_OFFLINE_FRAMES\n')
            bank_end = source.index('bool MyMesh::Frame::isChannelMsg()', bank_start)
            banks = source[bank_start:bank_end]
        else:
            storage = ('mesh::BorrowableFrameBuffer<Frame, 224, OTA_RETAINED_OFFLINE_FRAMES, '
                       'mesh::ota::OtaContext> offline_queue;')
            banks = ''
        return harness.replace('@STORAGE@', storage).replace('@BANKS@', banks)

    def test_224_normal_slots_and_128_retained_slots_preserve_fifo_and_ownership(self):
        self.compile(self.queue_fixture(), run=True)

    def test_rtc_backed_224_and_128_fifo_all_heads_use_actual_mesh_storage_and_callbacks(self):
        self.compile(self.queue_fixture(rtc=True), run=True, rtc=True)

    def test_s3_256_and_128_fifo_all_heads_use_both_banks_and_actual_mesh_callbacks(self):
        self.compile(self.queue_fixture(rtc=True, capacity=256),
                     run=True, rtc="ESP32S3")

    def test_cardputer_36_slow_slots_preserve_full_and_active_ota_capacity(self):
        self.compile(self.queue_fixture(rtc=True, capacity=256),
                     run=True, rtc="ESP32S3_36")

    def test_rtc_owner_reset_geometry_and_release_build_safeguards(self):
        harness = self.queue_fixture(rtc=True).split('int main() {', 1)[0] + r'''
using Frame = MyMesh::Frame;
using Workspace = mesh::ota::OtaContext;
using Inline = mesh::BorrowableFrameBuffer<Frame, 224, 128, Workspace>;
using Retained = mesh::CompanionRtcQueue<Frame, 128>;
using Split = mesh::BorrowableFrameBuffer<Frame, 224, 128, Workspace, Retained>;
struct Legacy {
  Frame retained[128];
  union Extra { Frame frames[96]; Workspace workspace; Extra() {} ~Extra() {} } extra;
  bool borrowed;
};
static_assert(sizeof(Inline) == sizeof(Legacy), "Ordinary targets keep the exact BSS layout");
static_assert(alignof(Inline) == alignof(Legacy), "Ordinary targets keep their alignment");
static_assert(sizeof(Inline) - sizeof(Split) >= 32 * sizeof(Frame) - 2 * sizeof(void*) - alignof(Split),
              "The complete 32-slot bank must leave internal DRAM");
static_assert(Retained::frame_count == 128, "All active-OTA slots remain independent of workspace");
static_assert(!std::is_copy_constructible<Split>::value && !std::is_move_constructible<Split>::value,
              "A bank lease must never be duplicated");
int main(int argc, char** argv) {
  alignas(4) Frame bank[32];
  memset(bank, 0xA5, sizeof bank);
  if (argc > 1 && !strcmp(argv[1], "null")) { Split bad(nullptr, nullptr); return 1; }
  if (argc > 1 && !strcmp(argv[1], "misaligned")) {
    Split bad(reinterpret_cast<Frame*>(reinterpret_cast<uint8_t*>(bank) + 1), nullptr); return 1;
  }
  if (argc > 1 && !strcmp(argv[1], "range")) {
    Retained owner(bank, nullptr); owner.at(128); return 1;
  }
  {
    Split owner(bank, nullptr);
    for (const auto& frame : bank) {
      assert(frame.len == 0);
      for (uint8_t byte : frame.buf) assert(byte == 0);
    }
    for (unsigned i = 0; i < 128; ++i) {
      auto& frame = owner.at(i); assert(frame.len == 0);
      for (uint8_t byte : frame.buf) assert(byte == 0);
    }
    if (argc > 1 && !strcmp(argv[1], "second")) { Split second(bank, nullptr); return 1; }
    if (argc > 1 && !strcmp(argv[1], "other-count")) {
      mesh::CompanionRtcQueue<Frame, 224> second(bank, nullptr); return 1;
    }
    if (argc > 1 && !strcmp(argv[1], "foreign")) {
      // A stale or foreign lease must be rejected, even with NDEBUG.
      mesh::companion_rtc::Lease<Frame>::owner() = nullptr;
      owner.at(0); return 1;
    }
    for (unsigned i = 0; i < 224; ++i) memset(&owner.at(i), 0xBB, sizeof(Frame));
    int head = 217;
    assert(owner.acquire(128, head) && head == 0 && owner.capacity() == 128);
    // Destruction must end an active workspace exactly once before releasing its bank.
  }
  assert(Workspace::live == 0 && Workspace::constructions == Workspace::destructions);
  Split next(bank, nullptr);
  for (unsigned i = 0; i < 224; ++i) {
    const auto& frame = next.at(i); assert(frame.len == 0);
    for (uint8_t byte : frame.buf) assert(byte == 0);
  }
}
'''
        harness = harness.replace('#include <deque>', '#include <deque>\n#include <type_traits>')
        self.compile(harness, run=True, rtc=True)
        for argument in ('null', 'misaligned', 'second', 'other-count', 'foreign', 'range'):
            with self.subTest(argument=argument):
                self.compile(harness, run=True, rtc=True, args=(argument,),
                             expected_returncode=-signal.SIGABRT)

    def test_insufficient_workspace_or_no_retained_region_cannot_compile(self):
        harness = r'''
#include <helpers/BorrowableFrameBuffer.h>
#include <cstdint>
struct Frame { uint8_t len; uint8_t bytes[176]; };
struct Workspace { uint8_t bytes[@SIZE@]; };
mesh::BorrowableFrameBuffer<Frame, @CAPACITY@, 128, Workspace> queue;
int main() { return queue.capacity() == @CAPACITY@ ? 0 : 1; }
'''
        # Exact region bounds and the existing 256/128 geometry remain valid.
        for capacity, size, accepted in ((224, 96 * 177, True),
                                         (224, 96 * 177 + 1, False),
                                         (256, 19624, True),
                                         (128, 1, False)):
            with self.subTest(capacity=capacity, size=size):
                result = self.compile(harness.replace("@CAPACITY@", str(capacity))
                                      .replace("@SIZE@", str(size)))
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
                if not accepted:
                    self.assertIn("static assertion failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
