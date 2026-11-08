#!/usr/bin/env python3
"""Run the real fixed-capacity FIFO across RTC banks, ownership and active OTA."""
from pathlib import Path
import os
import re
import resource
import shlex
import shutil
import signal
import subprocess
import tempfile
import unittest
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/CompanionFrameLimits.h>
#include <helpers/CompanionRtcQueue.h>
#include <helpers/ota/OtaManager.h>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <deque>
#include <memory>
#include <set>
#include <vector>
#include <type_traits>
#include "mota_vectors.h"
#define MESH_DEBUG_PRINTLN(...) ((void)0)
@CONSTANTS@
static void check(bool value) { if (!value) std::abort(); }
struct MyMesh {
@FRAME@;
  int offline_queue_len = 0, offline_queue_head = 0;
@STORAGE@
  MyMesh() : offline_queue(rtcFastQueueBank(), rtcSlowQueueBank()) {}
  int getOfflineQueueCapacity() const;
  Frame& offlineQueueFrameAt(int);
  bool addToOfflineQueue(const uint8_t*, int);
  int peekOfflineQueue(uint8_t*);
  void popOfflineQueue();
  void reset() { offline_queue.reset(); offline_queue_len = 0; offline_queue_head = 0; }
};
@BANKS@
@METHODS@
static_assert(sizeof(MyMesh::Frame) == 177, "Keep the original complete frame");
static_assert(std::is_trivial<MyMesh::Frame>::value, "No constructors in retained bytes");
static_assert(!std::is_copy_constructible<decltype(MyMesh::offline_queue)>::value,
              "A queue lease must not be duplicated");
static_assert(!std::is_move_constructible<decltype(MyMesh::offline_queue)>::value,
              "Moving a queue lease would invalidate its owner");
static_assert(sizeof(decltype(MyMesh::offline_queue)) ==
    177 * (OFFLINE_QUEUE_SIZE - COMPANION_RTC_FAST_OFFLINE_FRAMES -
           COMPANION_RTC_SLOW_OFFLINE_FRAMES) + 2 * sizeof(void*),
    "The complete remaining frames, and only bank pointers, occupy ordinary DRAM");
#ifdef OTA_SHARED_COMPANION_QUEUE
#error "This FIFO must retain its complete capacity while OTA is active"
#endif
using Bytes = std::vector<uint8_t>;
static Bytes frame(uint32_t id, bool channel = false) {
  Bytes bytes(MAX_FRAME_SIZE, 0);
  bytes[0] = channel ? RESP_CODE_CHANNEL_MSG_RECV : 0xFE;
  std::memcpy(bytes.data() + 1, &id, sizeof(id));
  for (size_t i = 5; i < bytes.size(); ++i) bytes[i] = uint8_t(id + i);
  return bytes;
}
static void enqueue(MyMesh& mesh, const Bytes& bytes) {
  check(mesh.addToOfflineQueue(bytes.data(), bytes.size()));
}
static void expectFront(MyMesh& mesh, const Bytes& bytes) {
  uint8_t actual[MAX_FRAME_SIZE];
  check(mesh.peekOfflineQueue(actual) == int(bytes.size()));
  check(!std::memcmp(actual, bytes.data(), bytes.size()));
}
static void expectAll(MyMesh& mesh, std::deque<Bytes> expected) {
  check(mesh.offline_queue_len == int(expected.size()));
  while (!expected.empty()) {
    expectFront(mesh, expected.front()); mesh.popOfflineQueue(); expected.pop_front();
  }
  uint8_t ignored[MAX_FRAME_SIZE];
  check(mesh.offline_queue_len == 0 && mesh.offline_queue_head == 0);
  check(mesh.peekOfflineQueue(ignored) == 0);
}
static void fill(MyMesh& mesh, std::deque<Bytes>& expected, int channel = -1) {
  mesh.reset(); expected.clear();
  for (int i = 0; i < OFFLINE_QUEUE_SIZE; ++i) {
    Bytes value = frame(i, i == channel); enqueue(mesh, value); expected.push_back(value);
  }
  check(mesh.getOfflineQueueCapacity() == OFFLINE_QUEUE_SIZE);
  check(!mesh.addToOfflineQueue(nullptr, 0));
}
struct WirePacket { bool to_source; Bytes bytes; };
static std::deque<WirePacket> packets;
static bool send(void* to_source, const uint8_t* bytes, uint16_t length, bool) {
  packets.push_back({to_source != nullptr, {bytes, bytes + length}}); return true;
}
static void transferWithFullQueue(MyMesh& mesh, const std::deque<Bytes>& expected) {
  using namespace mesh::ota;
  OtaManager source, receiver;
  OtaStoreRam<8192> destination;
  source.begin(EXP_TARGET_ID, send, nullptr);
  receiver.begin(EXP_TARGET_ID, send, reinterpret_cast<void*>(1));
  receiver.set_fetch_store(&destination);
  check(source.serve(MOTA_VEC, MOTA_VEC_LEN));
  check(receiver.pull(EXP_MERKLE_ROOT, EXP_TARGET_ID) == OtaManager::PULL_STARTED);
  bool active = false;
  for (uint32_t time = 100; time < 300000 && receiver.fetchState() != OtaManager::COMPLETE;
       time += 100) {
    active = true;
    check(mesh.getOfflineQueueCapacity() == OFFLINE_QUEUE_SIZE);
    check(mesh.offline_queue_len == OFFLINE_QUEUE_SIZE);
    expectFront(mesh, expected.front());
    source.set_clock(time); receiver.set_clock(time);
    source.serviceEgress(); receiver.serviceEgress();
    if (time % 1000 == 0) { source.loop(); receiver.loop(); }
    while (!packets.empty()) {
      WirePacket packet = packets.front(); packets.pop_front();
      (packet.to_source ? source : receiver).on_message(packet.bytes.data(), packet.bytes.size());
    }
  }
  check(active && receiver.fetchState() == OtaManager::COMPLETE);
  check(destination.staged_size() == MOTA_VEC_LEN);
  check(!std::memcmp(destination.data(), MOTA_VEC, MOTA_VEC_LEN));
  check(mesh.getOfflineQueueCapacity() == OFFLINE_QUEUE_SIZE);
}
int main(int argc, char** argv) {
  auto mesh = std::unique_ptr<MyMesh>(new MyMesh);
  if (argc == 2 && !std::strcmp(argv[1], "second-owner")) {
    auto second = std::unique_ptr<MyMesh>(new MyMesh); return 0;
  }
  if (argc == 2 && !std::strcmp(argv[1], "different-count-owner")) {
    mesh::CompanionRtcQueue<MyMesh::Frame, OFFLINE_QUEUE_SIZE + 1> second(
        mesh->rtcFastQueueBank(), mesh->rtcSlowQueueBank()); return 0;
  }
  check(reinterpret_cast<uintptr_t>(mesh->rtcFastQueueBank()) % 4 == 0);
  check(reinterpret_cast<uintptr_t>(mesh->rtcSlowQueueBank()) % 4 == 0);
  std::set<MyMesh::Frame*> addresses;
  const int main_count = OFFLINE_QUEUE_SIZE - COMPANION_RTC_FAST_OFFLINE_FRAMES -
                         COMPANION_RTC_SLOW_OFFLINE_FRAMES;
  for (int i = 0; i < OFFLINE_QUEUE_SIZE; ++i) {
    auto& slot = mesh->offlineQueueFrameAt(i); check(slot.len == 0);
    check(addresses.insert(&slot).second);
    if (i >= main_count && i < main_count + COMPANION_RTC_FAST_OFFLINE_FRAMES)
      check(&slot == mesh->rtcFastQueueBank() + i - main_count);
    if (i >= main_count + COMPANION_RTC_FAST_OFFLINE_FRAMES)
      check(&slot == mesh->rtcSlowQueueBank() + i - main_count - COMPANION_RTC_FAST_OFFLINE_FRAMES);
  }
  std::deque<Bytes> expected;
  fill(*mesh, expected);
  Bytes extra = frame(0xABCDEF); check(!mesh->addToOfflineQueue(extra.data(), extra.size()));
  transferWithFullQueue(*mesh, expected); expectAll(*mesh, expected);
  fill(*mesh, expected);
  for (int i = 0; i < OFFLINE_QUEUE_SIZE * 2 + 17; ++i) {
    expectFront(*mesh, expected.front()); mesh->popOfflineQueue(); expected.pop_front();
    Bytes value = frame(OFFLINE_QUEUE_SIZE + i); enqueue(*mesh, value); expected.push_back(value);
  }
  expectAll(*mesh, expected);
  // Removal shifts complete personal-message frames across every bank boundary.
  for (int channel : {main_count - 1, main_count, main_count + 31, OFFLINE_QUEUE_SIZE - 1}) {
    fill(*mesh, expected, channel);
    for (int i = 0; i < 19; ++i) {
      mesh->popOfflineQueue(); expected.pop_front();
      Bytes value = frame(OFFLINE_QUEUE_SIZE + i); enqueue(*mesh, value); expected.push_back(value);
    }
    enqueue(*mesh, extra); expected.erase(expected.begin() + channel - 19); expected.push_back(extra);
    expectAll(*mesh, expected);
  }
  fill(*mesh, expected); mesh->reset();
  uint8_t ignored[MAX_FRAME_SIZE]; check(mesh->peekOfflineQueue(ignored) == 0);
  for (int i = 0; i < OFFLINE_QUEUE_SIZE; ++i) check(mesh->offlineQueueFrameAt(i).len == 0);
  fill(*mesh, expected); mesh.reset();
  // RTC bytes outlive a C++ owner. A new owner must not resurrect its frames.
  mesh.reset(new MyMesh); check(mesh->peekOfflineQueue(ignored) == 0);
  for (int i = 0; i < OFFLINE_QUEUE_SIZE; ++i) {
    const auto& slot = mesh->offlineQueueFrameAt(i); check(slot.len == 0);
    for (uint8_t byte : slot.buf) check(byte == 0);
  }
}
'''


class CompanionRtcQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='companion-rtc-queue-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        (self.work / 'sdkconfig.h').write_text('#pragma once\n', encoding='ascii')
        (self.work / 'esp_attr.h').write_text(
            '#pragma once\n#define RTC_FAST_ATTR __attribute__((section(".rtc.force_fast.test")))\n'
            '#define RTC_SLOW_ATTR __attribute__((section(".rtc.force_slow.test")))\n', encoding='ascii')
        self.compiler = shlex.split(os.environ.get('CXX', 'g++'))

    def fixture(self):
        header = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        start = header.index('#elif COMPANION_RTC_FAST_OFFLINE_FRAMES || COMPANION_RTC_SLOW_OFFLINE_FRAMES', header.index('static constexpr unsigned OTA_RETAINED_OFFLINE_FRAMES'))
        end = header.index('#elif defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)', start)
        common_start = header.index('  // Concrete definitions in MyMesh.cpp')
        common_end = header.index('\n#endif\n#if defined(OTA_SHARED_COMPANION_QUEUE)', common_start)
        storage = (header[common_start:common_end] + '\n'
                   + header[start:].split('\n', 1)[1][:end - start - len(header[start:].split('\n', 1)[0]) - 1])
        banks_start = source.index('#if COMPANION_RTC_FAST_OFFLINE_FRAMES\n')
        banks_end = source.index('bool MyMesh::Frame::isChannelMsg()', banks_start)
        methods = '\n'.join(extract_braced(source, name) for name in (
            'bool MyMesh::Frame::isChannelMsg()', 'int MyMesh::getOfflineQueueCapacity()',
            'MyMesh::Frame& MyMesh::offlineQueueFrameAt(', 'bool MyMesh::addToOfflineQueue(',
            'int MyMesh::peekOfflineQueue(', 'void MyMesh::popOfflineQueue()'))
        constants = '\n'.join(re.search(r'^#define ' + name + r'\s+.*$', source, re.M).group()
                              for name in ('RESP_CODE_CHANNEL_MSG_RECV', 'RESP_CODE_CHANNEL_MSG_RECV_V3', 'RESP_CODE_CHANNEL_DATA_RECV'))
        return (HARNESS.replace('@FRAME@', extract_braced(header, '  struct Frame {'))
                .replace('@STORAGE@', storage).replace('@BANKS@', source[banks_start:banks_end])
                .replace('@METHODS@', methods).replace('@CONSTANTS@', constants))

    @staticmethod
    def no_core_dump():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def check_fifo(self, count, slow, chip):
        source = self.work / 'test.cpp'; source.write_text(self.fixture(), encoding='ascii')
        binary = self.work / 'test'
        sources = [ROOT / 'src/helpers/ota' / name for name in (
            'OtaManager.cpp', 'OtaProtocol.cpp', 'MotaContainer.cpp', 'MerkleTree.cpp')]
        sources.append(ROOT / 'src/Utils.cpp')
        result = subprocess.run(self.compiler + ['-std=c++17', '-Os', '-DNDEBUG',
            '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-sign-compare',
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
            '-DESP32_PLATFORM=1', '-DCOMPANION_RADIO_FULL=1', '-DENABLE_OTA=1',
            '-DCONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP=1', '-DCONFIG_IDF_TARGET_' + chip + '=1',
            '-DOFFLINE_QUEUE_SIZE=' + str(count), '-DCOMPANION_RTC_FAST_OFFLINE_FRAMES=32',
            '-DCOMPANION_RTC_SLOW_OFFLINE_FRAMES=' + str(slow),
            '-I', str(self.work), '-I', str(ROOT / 'src'), '-I', str(ROOT / 'test/mocks'),
            '-I', str(ROOT / 'test/test_ota'),
            str(source), *map(str, sources), '-o', str(binary)], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for command in ('second-owner', 'different-count-owner'):
            result = subprocess.run([str(binary), command], capture_output=True, text=True,
                                    preexec_fn=self.no_core_dump, timeout=10)
            self.assertEqual(result.returncode, -signal.SIGABRT, result.stdout + result.stderr)

    def test_generic_c3_retains_all_224_frames_during_real_ota(self):
        self.check_fifo(224, 0, 'ESP32C3')

    def test_tracker_and_mke_s3_retain_all_256_frames_during_real_ota(self):
        self.check_fifo(256, 32, 'ESP32S3')

    def test_v3_s3_fast_bank_retains_all_256_frames_during_real_ota(self):
        self.check_fifo(256, 0, 'ESP32S3')

    def test_concrete_banks_use_rtc_sections_and_reset_has_no_large_stack_temporary(self):
        objdump = shutil.which('objdump')
        if not objdump:
            self.skipTest('host objdump required for section-placement check')
        source = self.work / 'sections.cpp'
        source.write_text(self.fixture(), encoding='ascii')
        obj = self.work / 'sections.o'
        result = subprocess.run(self.compiler + ['-std=c++17', '-O0', '-DNDEBUG',
            '-fno-inline', '-fstack-usage', '-ffunction-sections', '-fdata-sections',
            '-DESP32_PLATFORM=1', '-DCOMPANION_RADIO_FULL=1', '-DENABLE_OTA=1',
            '-DCONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP=1', '-DCONFIG_IDF_TARGET_ESP32S3=1',
            '-DOFFLINE_QUEUE_SIZE=256', '-DCOMPANION_RTC_FAST_OFFLINE_FRAMES=32',
            '-DCOMPANION_RTC_SLOW_OFFLINE_FRAMES=32',
            '-I', str(self.work), '-I', str(ROOT / 'src'), '-I', str(ROOT / 'test/mocks'),
            '-I', str(ROOT / 'test/test_ota'), '-c', str(source), '-o', str(obj)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sections = subprocess.check_output([objdump, '-h', str(obj)], text=True)
        for bank in ('fast', 'slow'):
            match = re.search(r'\.rtc\.force_' + bank + r'\.test\s+([0-9a-fA-F]+)', sections)
            self.assertIsNotNone(match, sections)
            self.assertEqual(int(match[1], 16), 32 * 177)
        stack = obj.with_suffix('.su').read_text()
        selected = [line for line in stack.splitlines()
                    if 'CompanionRtcQueue' in line
                    and any(method in line for method in ('::reset(', '::at(', '::CompanionRtcQueue('))]
        self.assertGreaterEqual(len(selected), 3, stack)
        for line in selected:
            self.assertLessEqual(int(line.split('\t')[1]), 256, line)

    def test_unselected_header_adds_no_sdk_dependency_or_storage(self):
        source = self.work / 'default.cpp'
        source.write_text('#include <helpers/CompanionRtcQueue.h>\nint main() {}\n', encoding='ascii')
        result = subprocess.run(self.compiler + ['-std=c++11', '-E', '-P', '-I', str(ROOT / 'src'), str(source)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'int main() {}')

    def test_incompatible_chip_storage_and_retention_settings_fail_closed(self):
        source = self.work / 'policy.cpp'
        source.write_text('#include <helpers/CompanionRtcQueue.h>\n', encoding='ascii')
        base = ['ESP32_PLATFORM=1', 'COMPANION_RADIO_FULL=1', 'CONFIG_IDF_TARGET_ESP32C3=1',
                'CONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP=1', 'COMPANION_RTC_FAST_OFFLINE_FRAMES=32']
        cases = ([value for value in base if not value.startswith('CONFIG_IDF_TARGET_')],
                 base + ['BOARD_HAS_PSRAM=1'], [value for value in base if not value.startswith('CONFIG_IDF_TARGET_')]
                 + ['OTA_SHARED_COMPANION_QUEUE=1', 'CONFIG_IDF_TARGET_ESP32=1'],
                 base + ['ONE_KEY_DM_SHARED_OFFLINE_QUEUE=1'],
                 base + ['COMPANION_RTC_SLOW_OFFLINE_FRAMES=32'],
                 [value for value in base if not value.startswith('COMPANION_RADIO_FULL')],
                 [value for value in base if not value.startswith('CONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP')])
        for defines in cases:
            with self.subTest(defines=defines):
                result = subprocess.run(self.compiler + ['-std=c++11', '-fsyntax-only',
                    *['-D' + value for value in defines], '-I', str(self.work), '-I', str(ROOT / 'src'), str(source)],
                    capture_output=True, text=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)

    def test_only_named_full_policies_select_banks_without_capacity_reductions(self):
        source = (ROOT / 'build_legacy.sh').read_text()
        start = source.index('  # BLE + WiFi exhaust internal DRAM')
        start = source.index('  case "${env_name,,}" in', start)
        end = source.index('\n  esac', start) + len('\n  esac')
        case = source[start:end]
        prefix = source[:start]
        self.assertNotIn('COMPANION_RTC_FAST_OFFLINE_FRAMES', prefix)
        self.assertNotIn('COMPANION_RTC_SLOW_OFFLINE_FRAMES', prefix)
        for target, fast, slow, contacts, queue in (
                ('Generic_ESPNOW_companion_radio_full', 32, 0, 100, 224),
                ('Xiao_C3_companion_radio_full', 32, 0, 100, 224),
                ('Heltec_CT62_companion_radio_full', 32, 0, 100, 224),
                ('Heltec_v3_companion_radio_full', 32, 0, 100, 256),
                ('Heltec_v3_companion_radio_usb', 0, 0, 350, 256),
                ('Heltec_v3_companion_radio_ble', 0, 0, 350, 256),
                ('Heltec_v3_companion_radio_wifi', 0, 0, 350, 256),
                ('Heltec_Wireless_Tracker_companion_radio_full', 32, 32, 150, 256),
                ('MKE_s3_companion_radio_full', 32, 32, 350, 256),
                ('ThinkNode_M2_companion_radio_full', 32, 32, 350, 256),
                ('ThinkNode_M5_companion_radio_full', 32, 32, 350, 256),
                ('M5Stack_Cardputer_Adv_companion_radio_full', 32, 32, 350, 256),
                ('SenseCapIndicator-ESPNow_companion_radio_full', 0, 0, 350, 256),
                ('RAK_4631_companion_radio_full', 0, 0, 350, 256)):
            with self.subTest(target=target):
                program = ('env_name=' + shlex.quote(target) + '\nPLATFORMIO_BUILD_FLAGS=""\n'
                    'append_platformio_build_unflags() { :; }\nrecord_build_reduction() { :; }\n'
                    'requires_esp32_companion_full_ota_fallback() { return 1; }\n' + case
                    + '\nprintf "%s" "$PLATFORMIO_BUILD_FLAGS"\n')
                result = subprocess.run(['bash', '-c', program], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                flags = dict(re.findall(r'-D([A-Z0-9_]+)=(\d+)', result.stdout))
                self.assertEqual(int(flags.get('COMPANION_RTC_FAST_OFFLINE_FRAMES', 0)), fast)
                self.assertEqual(int(flags.get('COMPANION_RTC_SLOW_OFFLINE_FRAMES', 0)), slow)
                self.assertEqual(int(flags.get('MAX_CONTACTS', 350)), contacts)
                self.assertEqual(int(flags.get('OFFLINE_QUEUE_SIZE', 256)), queue)
                if target in ('Xiao_C3_companion_radio_full', 'Heltec_CT62_companion_radio_full'):
                    self.assertEqual(flags.get('OTA_SHARED_COMPANION_QUEUE'), '1')
                    self.assertEqual(flags.get('ONE_KEY_DM_COMPACT_HELD'), '1')
                elif target == 'Heltec_Wireless_Tracker_companion_radio_full':
                    self.assertEqual(flags.get('OTA_SHARED_COMPANION_QUEUE'), '1')
                elif target == 'M5Stack_Cardputer_Adv_companion_radio_full':
                    self.assertEqual(flags.get('OTA_SHARED_COMPANION_QUEUE'), '1')
                    self.assertEqual(flags.get('COMPANION_EXCLUSIVE_WIFI_BLE'), '1')
                else:
                    self.assertNotIn('OTA_SHARED_COMPANION_QUEUE', flags)


if __name__ == '__main__':
    unittest.main()
