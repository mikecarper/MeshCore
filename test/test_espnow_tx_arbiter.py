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
  coordinator.setTurnaround(8);
  peer.setTurnaround(8);
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
  for (auto& node : nodes) node.setTurnaround(8);
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

void testBusyChannelReturnsGrant() {
  Arbiter coordinator, peer;
  coordinator.setTurnaround(8);
  peer.setTurnaround(8);
  coordinator.begin(true, 0);
  peer.begin(false, 0);
  assert(!peer.permitted(2500, 125, 1));
  coordinator.receive(peer.poll(2500), 2502);
  peer.receive(coordinator.poll(2502), 2504);
  assert(peer.permitted(2504, 125, 2));
  peer.yield(2505);
  assert(!peer.permitted(2505, 125, 3));
  const auto released = peer.poll(2505);
  assert(released.kind == Kind::Done && released.id == 1);
  coordinator.receive(released, 2507);
  assert(coordinator.acquire(2515, 470, 4));
}

void testConfiguredTurnaround() {
  Arbiter coordinator;
  assert(coordinator.turnaround() == 250);
  coordinator.setTurnaround(32);
  coordinator.begin(true, 0);
  assert(coordinator.turnaround() == 32);
  assert(coordinator.acquire(2500, 125, 1));
  coordinator.complete(2625);
  assert(!coordinator.acquire(2656, 125, 2));
  assert(coordinator.acquire(2657, 125, 2));
  coordinator.setTurnaround(4);
  coordinator.complete(2782);
  assert(!coordinator.acquire(2785, 125, 3));
  assert(coordinator.acquire(2786, 125, 3));
}

void testLongTurnaroundBetweenPeers() {
  Arbiter coordinator, peer;
  coordinator.setTurnaround(Arbiter::MaxTurnaroundMs);
  peer.setTurnaround(Arbiter::MaxTurnaroundMs);
  coordinator.begin(true, 0);
  peer.begin(false, 0);
  assert(coordinator.acquire(2500, 125, 1));
  assert(!peer.acquire(2510, 125, 2));
  coordinator.receive(peer.poll(2510), 2512);
  coordinator.complete(2625);
  assert(!coordinator.acquire(2874, 125, 3));
  assert(coordinator.poll(2874).kind == Kind::None);
  const auto grant = coordinator.poll(2875);
  assert(grant.kind == Kind::Grant && grant.id == 2);
  peer.receive(grant, 2876);
  assert(peer.acquire(2876, 125, 2));
  peer.complete(3001);
  coordinator.receive(peer.poll(3001), 3002);
  assert(!peer.acquire(3250, 125, 4));
  assert(!coordinator.acquire(3251, 125, 3));
  assert(coordinator.acquire(3252, 125, 3));
}

int main() {
  testLateGrant();
  testDutyCycle();
  testBusyChannelReturnsGrant();
  testConfiguredTurnaround();
  testLongTurnaroundBetweenPeers();
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
