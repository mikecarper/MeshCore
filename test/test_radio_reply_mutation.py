"""Compile production secondary CLI and Dispatcher with injected transport/storage faults."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(text, signature):
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


LIFECYCLE = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>
#include <helpers/TempRadioReplyBarrier.h>
static uint32_t now_ms = 1000;
uint32_t millis() { return now_ms; }
namespace mesh {
struct Packet {};
struct Mesh {
  virtual void onSendComplete(Packet*) {}
  virtual void onSendFail(Packet*) {}
  virtual void onRadioProfileCopyQueued(Packet*, const Packet*, uint8_t) {}
};
}
struct Profiles {
  bool pending = false, accepted = false;
  unsigned completions = 0;
  bool finishReplyMutation(bool delivered) {
    ++completions; accepted = delivered; pending = delivered; return true;
  }
};
struct CLI {
  Profiles profiles;
  Profiles& radioProfiles() { return profiles; }
};
struct Manager {
  std::vector<mesh::Packet*> queue;
  int getOutboundTotal() const { return queue.size(); }
  mesh::Packet* getOutboundByIdx(int i) { return queue[i]; }
  mesh::Packet* removeOutboundByIdx(int i) {
    auto* packet = queue[i]; queue.erase(queue.begin()+i); return packet;
  }
};
struct MyMesh : mesh::Mesh {
  CLI _cli;
  Manager manager; Manager* _mgr = &manager;
  mesh::TempRadioReplyBarrier temp_radio_reply_barrier;
  bool radio_reply_secondary = false, primary_radio_mutation_starts_temp = false;
  uint32_t radio_reply_deadline = 0;
  mesh::Packet* pending_battery_alert_packet = nullptr;
  bool battery_alert_sent = false;
  uint64_t last_battery_alert_sent = 0, uptime_millis = 0;
  uint32_t last_millis = 0;
  mesh::Packet* outbound = nullptr;
  unsigned released = 0, normal_schedules = 0, cancellations = 0;
  const mesh::Packet* getOutboundInFlight() const { return outbound; }
  void cancelOutboundRadioRetry(const mesh::Packet* p) { assert(p == outbound); ++cancellations; }
  void releasePacket(mesh::Packet*) { ++released; }
  bool scheduleNormalRadio() { ++normal_schedules; return true; }
  void onSendComplete(mesh::Packet*) override;
  void onSendFail(mesh::Packet*) override;
  void onRadioProfileCopyQueued(mesh::Packet*, const mesh::Packet*, uint8_t) override;
  void finishRadioReply(bool);
  void serviceRadioReplyDeadline();
  void arm(mesh::Packet* a, mesh::Packet* b = nullptr) {
    temp_radio_reply_barrier.prepare(a);
    if (b) onRadioProfileCopyQueued(b, a, 0);
    temp_radio_reply_barrier.arm(a);
    radio_reply_deadline = now_ms + 300000;
  }
};
@METHODS@
int main() {
  unsigned scenarios = 0;
  for (bool secondary : {false, true}) for (unsigned success = 0; success < 4; ++success) {
    MyMesh m; mesh::Packet a, b, unrelated;
    m.radio_reply_secondary = secondary;
    m.primary_radio_mutation_starts_temp = true;
    m._cli.profiles.pending = secondary;
    m.arm(&a, &b);
    m.onSendComplete(&unrelated);
    assert(m.temp_radio_reply_barrier.waiting());
    if (success & 1) m.onSendComplete(&a); else m.onSendFail(&a);
    assert(m.temp_radio_reply_barrier.waiting());
    assert(m._cli.profiles.completions == 0 && m.normal_schedules == 0);
    if (success & 2) m.onSendComplete(&b); else m.onSendFail(&b);
    assert(!m.temp_radio_reply_barrier.waiting());
    assert(m.radio_reply_deadline == 0);
    if (secondary) {
      assert(m._cli.profiles.completions == 1 && m._cli.profiles.accepted == (success != 0));
      assert(m.normal_schedules == 0);
    } else assert(m.normal_schedules == (success == 0 ? 1U : 0U));
    ++scenarios;
  }
  { // Deleting an active schedule may restore saved radio, but must not erase other slots on ACK failure.
    MyMesh m; mesh::Packet a;
    m.primary_radio_mutation_starts_temp = false;
    m.arm(&a); m.onSendFail(&a);
    assert(!m.temp_radio_reply_barrier.waiting() && m.normal_schedules == 0);
    ++scenarios;
  }
  for (bool rollover : {false, true}) {
    now_ms = rollover ? UINT32_MAX - 10 : 1000;
    MyMesh m; mesh::Packet a, b, ordinary;
    m.radio_reply_secondary = true;
    m.arm(&a, &b); m.manager.queue = {&a, &ordinary, &b};
    now_ms += 299999; m.serviceRadioReplyDeadline();
    assert(m.temp_radio_reply_barrier.waiting() && m.released == 0);
    ++now_ms; m.serviceRadioReplyDeadline();
    assert(!m.temp_radio_reply_barrier.waiting() && m.released == 2);
    assert(m.manager.queue.size() == 1 && m.manager.queue[0] == &ordinary);
    assert(!m._cli.profiles.accepted && m._cli.profiles.completions == 1);
    ++scenarios;
  }
  { // A physically transmitting copy retains ownership until its actual completion/watchdog.
    MyMesh m; mesh::Packet a, b;
    m.radio_reply_secondary = true;
    m.arm(&a, &b); m.outbound = &a; m.manager.queue = {&b};
    now_ms += 300000; m.serviceRadioReplyDeadline();
    assert(m.cancellations == 1 && m.released == 1);
    assert(m.temp_radio_reply_barrier.waiting() && m._cli.profiles.completions == 0);
    m.onSendComplete(&a);
    assert(!m.temp_radio_reply_barrier.waiting() && m._cli.profiles.accepted);
    ++scenarios;
  }
  { // Once one copy succeeded, a timeout can retire the other without canceling the mutation.
    MyMesh m; mesh::Packet a, b;
    m.radio_reply_secondary = true;
    m.arm(&a, &b); m.onSendComplete(&a); m.manager.queue = {&b};
    now_ms += 300000; m.serviceRadioReplyDeadline();
    assert(m.released == 1 && m._cli.profiles.accepted);
    assert(!m.temp_radio_reply_barrier.waiting());
    ++scenarios;
  }
  assert(scenarios == 13);
}
'''


class RadioReplyMutationTest(unittest.TestCase):
    @staticmethod
    def compiler_flags():
        return ['-std=c++17', '-Wall', '-Wextra'] + ([] if os.name == 'nt' else
            ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie', '-no-pie'])

    def test_radio_transactions_and_coding_rate_restore(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as work:
            exe = Path(work) / 'radio_replies.exe'
            result = subprocess.run([compiler, *self.compiler_flags(),
                '-I', str(ROOT/'test/fixtures/radio_profiles/mocks'),
                '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'),
                str(ROOT/'src/helpers/RadioProfileCLI.cpp'),
                str(ROOT/'src/Dispatcher.cpp'), str(ROOT/'src/Packet.cpp'),
                str(ROOT/'src/helpers/StaticPoolPacketManager.cpp'),
                str(ROOT/'test/fixtures/radio_profiles/reply_mutation_test.cpp'),
                '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[-14000:])
            env = os.environ.copy()
            # The production pool is a boot-lifetime singleton without a
            # destructor; this harness constructs several independent pools.
            # Keep bounds/UAF/UB sanitizers, but don't count that intentional
            # lifetime allocation as a host-process teardown leak.
            if os.name != 'nt':
                env['ASAN_OPTIONS'] = env.get('ASAN_OPTIONS', '') + ':detect_leaks=0'
            subprocess.run([str(exe)], check=True, env=env)

    def test_production_repeater_ack_callbacks_and_deadline(self):
        source = (ROOT/'examples/simple_repeater/MyMesh.cpp').read_text(encoding='utf-8')
        methods = '\n'.join(method(source, signature) for signature in (
            'void MyMesh::onSendComplete(', 'void MyMesh::onSendFail(',
            'void MyMesh::onRadioProfileCopyQueued(', 'void MyMesh::finishRadioReply(',
            'void MyMesh::serviceRadioReplyDeadline('))
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as work:
            cpp = Path(work)/'lifecycle.cpp'; exe = Path(work)/'lifecycle.exe'
            cpp.write_text(LIFECYCLE.replace('@METHODS@', methods), encoding='utf-8')
            result = subprocess.run([compiler, *self.compiler_flags(), '-I', str(ROOT/'src'),
                str(cpp), '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[-14000:])
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
