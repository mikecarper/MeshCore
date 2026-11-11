#!/usr/bin/env python3
"""Exercise the UDP origin marker through real packet, pool and radio paths."""
from pathlib import Path
from itertools import product
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UDPPacketOriginTests(unittest.TestCase):
    def test_origin_is_local_copies_with_retries_and_clears_on_reuse(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        fixture = (ROOT / "test/fixtures/dispatcher/startup_watchdog_test.cpp").read_text()
        support = fixture[:fixture.index("static void delayedSetup()")]
        support = support.replace("  using Dispatcher::setRadioAvailable;",
            "  using Dispatcher::setRadioAvailable;\n"
            "  using Dispatcher::tryParsePacket;\n"
            "  using Dispatcher::obtainNewPacket;")
        checks = r'''
int main() {
  mesh::Packet original;
  original.header = (PAYLOAD_TYPE_OTA << PH_TYPE_SHIFT) | ROUTE_TYPE_FLOOD;
  original.path_len = 0;
  original.payload_len = 1;
  original.payload[0] = 42;
  uint8_t before[MAX_TRANS_UNIT], after[MAX_TRANS_UNIT];
  const auto length = original.writeTo(before);
#if defined(WITH_UDP_BRIDGE)
  assert(!original.udp_bridge_ingress);
  original.udp_bridge_ingress = true;
  mesh::Packet retry = original;
  assert(retry.udp_bridge_ingress);
  assert(original.writeTo(after) == length && memcmp(before, after, length) == 0);
  assert(retry.readFrom(before, length) && !retry.udp_bridge_ingress);
  retry.udp_bridge_ingress = true;
  const uint8_t invalid[] = {0xC1, 0};
  assert(!retry.readFrom(invalid, sizeof(invalid)) && !retry.udp_bridge_ingress);

  StaticPoolPacketManager pool(1);
  auto* packet = pool.allocNew();
  assert(packet && !packet->udp_bridge_ingress);
  packet->udp_bridge_ingress = true;
  pool.free(packet);
  auto* recycled = pool.allocNew();
  assert(recycled == packet && !recycled->udp_bridge_ingress);
  assert(pool.allocNew() == nullptr);
  pool.free(recycled);

  TestClock clock; TestRadio radio;
  TestDispatcher dispatcher(radio, clock, pool);
  auto* local = dispatcher.obtainNewPacket();
  assert(local && !local->udp_bridge_ingress);
  local->udp_bridge_ingress = true;
  assert(dispatcher.tryParsePacket(local, before, length));
  assert(!local->udp_bridge_ingress);
  pool.free(local);
#else
  assert(original.writeTo(after) == length && memcmp(before, after, length) == 0);
#endif
  printf("%zu\n", sizeof(mesh::Packet));
}
'''
        with tempfile.TemporaryDirectory(prefix="udp-packet-origin-") as temporary:
            work = Path(temporary)
            source = work / "main.cpp"
            source.write_text(support + checks, encoding="ascii")
            for pram, seq_tx, auto_trim in product((False, True), repeat=3):
                with self.subTest(pram=pram, seq_tx=seq_tx, auto_trim=auto_trim):
                    feature_flags = [
                        f"-DMESH_W12_PRAM_EXPERIMENT={int(pram)}",
                        f"-DMESH_W12_PRAM_SEQ_TX_LAB={int(seq_tx)}",
                        f"-DMESH_ENABLE_LR2021_AUTO_XTRIM={int(auto_trim)}",
                    ]
                    sizes = []
                    for enabled in (False, True):
                        with self.subTest(udp=enabled):
                            binary = work / ("udp" if enabled else "ordinary")
                            built = subprocess.run([
                                compiler, "-std=c++17", "-Wall", "-Wextra",
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                "-fno-pie", "-no-pie", *feature_flags,
                                *(["-DWITH_UDP_BRIDGE=1"] if enabled else []),
                                "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
                                str(ROOT / "src/Dispatcher.cpp"), str(ROOT / "src/Packet.cpp"),
                                str(ROOT / "src/helpers/StaticPoolPacketManager.cpp"),
                                str(source), "-o", str(binary),
                            ], capture_output=True, text=True, timeout=60)
                            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                            environment = os.environ.copy()
                            # Firmware packet pools live for the entire boot.
                            environment["ASAN_OPTIONS"] = environment.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
                            checked = subprocess.run([str(binary)], capture_output=True, text=True,
                                                     env=environment, timeout=10)
                            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                            sizes.append(int(checked.stdout.strip()))
                    if len(sizes) == 2:
                        self.assertEqual(sizes[0], sizes[1],
                                         "UDP metadata must fit existing packet padding")


if __name__ == "__main__":
    unittest.main()
