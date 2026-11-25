import pathlib
import subprocess
import tempfile
import unittest


SOURCE = r"""
#include "helpers/bridges/ESPNowTxArbiter.h"
#include <assert.h>
#include <deque>
using Arbiter = mesh::bridge::EspNowTxArbiter;
using Kind = Arbiter::Kind;

void testLateGrant() {
  Arbiter coordinator, peer;
  coordinator.begin(true, 0);
  peer.begin(false, 0);
  assert(!coordinator.acquire(2499, 470, 1));
  assert(coordinator.acquire(2500, 470, 1));
  assert(!peer.acquire(2510, 125, 2));
  coordinator.receive(peer.poll(2510), 2512);
  assert(coordinator.poll(2515).kind == Kind::None);
  coordinator.complete(2970);
  assert(coordinator.poll(2977).kind == Kind::None);
  const auto grant = coordinator.poll(2978);
  assert(grant.kind == Kind::Grant && grant.id == 2);
  peer.receive(grant, 4900);
  assert(!peer.acquire(4900, 125, 3));
  assert(!coordinator.acquire(5011, 470, 1));
  assert(coordinator.acquire(5012, 470, 1));
}

void testDutyCycle() {
  struct ControlFrame {
    uint32_t due;
    unsigned sender;
    Arbiter::Message message;
  };
  Arbiter nodes[2];
  nodes[0].begin(true, 0);
  nodes[1].begin(false, 0);
  std::deque<ControlFrame> link;
  const uint32_t airtime[2] = {470, 125};
  uint32_t available[2] = {2600, 2600}, finished[2] = {}, sent[2] = {};
  uint64_t nonce = 1;
  for (uint32_t ms = 0; ms < 62600; ++ms) {
    while (!link.empty() && ms >= link.front().due) {
      const auto frame = link.front();
      link.pop_front();
      nodes[1 - frame.sender].receive(frame.message, ms);
    }
    for (unsigned i = 0; i < 2; ++i) {
      if (finished[i] == ms && ms != 0) {
        nodes[i].complete(ms);
        finished[i] = 0;
      }
    }
    for (unsigned i = 0; i < 2; ++i) {
      if (ms >= available[i] && nodes[i].acquire(ms, airtime[i], nonce++)) {
        assert(finished[i] == 0 && finished[1 - i] == 0);
        finished[i] = ms + airtime[i];
        available[i] += airtime[i] * 4;
        ++sent[i];
      }
      const auto message = nodes[i].poll(ms);
      if (message.kind != Kind::None) link.push_back({ms + 2, i, message});
    }
  }
  for (unsigned i = 0; i < 2; ++i)
    assert(sent[i] * airtime[i] >= 14400 && sent[i] * airtime[i] <= 15500);
}

int main() {
  testLateGrant();
  testDutyCycle();
}
"""


class EspNowTxArbiterTests(unittest.TestCase):
    def test_pair_serializes_transmissions_and_rejects_late_grants(self):
        include = pathlib.Path(__file__).resolve().parents[1] / "src"
        with tempfile.TemporaryDirectory() as temporary:
            source = pathlib.Path(temporary) / "tx_arbiter.cpp"
            source.write_text(SOURCE, encoding="utf-8")
            binary = source.with_suffix(".exe")
            subprocess.run(
                ["g++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(include), str(source), "-o", str(binary)], check=True
            )
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
