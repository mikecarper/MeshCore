"""Actual lab echo/dispatch boundary; no BLE, serial hardware or radio."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class CompanionBleBenchTest(unittest.TestCase):
    def compile(self, source, *flags, expect_failure=False):
        compiler = shutil.which("c++") or shutil.which("g++")
        if not compiler:
            self.skipTest("a native C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="mesh-ble-bench-") as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            cpp.write_text(source)
            result = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), *flags, str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=30)
            if expect_failure:
                self.assertNotEqual(result.returncode, 0)
                return result.stderr
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_production_echo_exact_binary_maximum_and_malformed_bounds(self):
        self.compile(r'''
#include <helpers/CompanionBleBench.h>
#include <cassert>
#include <array>
#include <cstring>
using namespace mesh::companion;
int main() {
  static_assert(kBleBenchCommand == 0x70 && kBleBenchHeaderBytes == 15, "wire contract");
  std::array<uint8_t, MAX_FRAME_SIZE> request{};
  std::array<uint8_t, MAX_FRAME_SIZE + 2> output{};
  for (size_t total : {size_t(15), size_t(20), size_t(64), size_t(172), size_t(176)}) {
    request[0] = kBleBenchCommand; memcpy(request.data() + 1, "BLE1", 4);
    for (size_t i = 5; i < total; ++i) request[i] = uint8_t(i * 71);
    const size_t payload = total - kBleBenchHeaderBytes;
    request[13] = payload; request[14] = payload >> 8;
    output.fill(0xa5);
    assert(buildBleBenchEcho(request.data(), total, output.data() + 1, total) == total);
    assert(!memcmp(request.data(), output.data() + 1, total));
    assert(output[0] == 0xa5 && output[total + 1] == 0xa5);
    output.fill(0xa5);
    assert(buildBleBenchEcho(request.data(), total, output.data(), total - 1) == 0);
    for (uint8_t value : output) assert(value == 0xa5);
  }
  // Full binary byte range, including NUL/FEND/FESC/FF, is neither escaped nor
  // interpreted. Two sequences cover all possible byte values in payloads.
  for (unsigned part : {0U, 128U}) {
    const size_t total = 143;
    request[13] = 128; request[14] = 0;
    for (size_t i = 0; i < 128; ++i) request[15 + i] = uint8_t(part + i);
    assert(buildBleBenchEcho(request.data(), total, output.data(), output.size()) == total);
    assert(!memcmp(request.data(), output.data(), total));
  }
  for (size_t total = 0; total < kBleBenchHeaderBytes; ++total)
    assert(buildBleBenchEcho(request.data(), total, output.data(), output.size()) == 0);
  assert(buildBleBenchEcho(nullptr, 15, output.data(), output.size()) == 0);
  assert(buildBleBenchEcho(request.data(), 15, nullptr, 176) == 0);
  assert(buildBleBenchEcho(request.data(), 177, output.data(), output.size()) == 0);
  request[13] = 0; request[14] = 0;
  for (unsigned fault = 0; fault < 6; ++fault) {
    auto bad = request;
    if (fault == 0) bad[0] ^= 1;
    if (fault == 1) bad[1] ^= 1;
    if (fault == 2) bad[4] = '2';
    if (fault == 3) bad[13] = 1;
    if (fault == 4) bad[14] = 1;
    if (fault == 5) bad[13] = bad[14] = 255;
    assert(buildBleBenchEcho(bad.data(), 15, output.data(), output.size()) == 0);
  }
}
''', "-DMESH_HIL_BLE_BENCH=1")

    def test_real_dispatch_returns_once_before_rf_or_pending_operation_service(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        dispatch = extract_braced(source,
            "if (cmd_frame[0] == mesh::companion::kBleBenchCommand)")
        handler = extract_braced(source, "void MyMesh::handleCmdFrame(")
        self.assertLess(handler.index(dispatch), handler.index("servicePendingSerialReply();"))
        self.assertRegex(handler, r"#if defined\(MESH_HIL_BLE_BENCH\) && MESH_HIL_BLE_BENCH\s+"
                                 r"if \(cmd_frame\[0\] == mesh::companion::kBleBenchCommand\)")
        for forbidden in ("sendDirect", "sendPacket", "self_id", "savePrefs", "_radio", "new ", "malloc"):
            self.assertNotIn(forbidden, dispatch)
        self.compile(r'''
#include <helpers/CompanionBleBench.h>
#include <cassert>
#include <vector>
static const uint8_t ERR_CODE_ILLEGAL_ARG = 6;
struct Serial {
  bool busy = false;
  unsigned calls = 0;
  std::vector<uint8_t> frame;
  size_t writeFrame(const uint8_t* bytes, size_t count) {
    ++calls;
    if (busy) return 0; // No retry loop, queue growth or fallback broadcast.
    frame.assign(bytes, bytes + count); return count;
  }
};
struct Handler {
  uint8_t cmd_frame[MAX_FRAME_SIZE + 1]{}, out_frame[MAX_FRAME_SIZE + 1]{};
  Serial serial; Serial* _serial = &serial;
  unsigned errors = 0, normal_path = 0;
  void writeErrFrame(uint8_t code) { assert(code == 6); ++errors; }
  void run(size_t len) {
    @DISPATCH@
    ++normal_path;
  }
};
int main() {
  Handler h; h.cmd_frame[0] = 0x70; memcpy(h.cmd_frame + 1, "BLE1", 4);
  h.run(15);
  assert(h.serial.calls == 1 && h.serial.frame.size() == 15 && h.normal_path == 0 && h.errors == 0);
  h.serial.busy = true; h.run(15);
  assert(h.serial.calls == 2 && h.normal_path == 0 && h.errors == 0);
  h.run(177);
  assert(h.serial.calls == 2 && h.errors == 1 && h.normal_path == 0);
  h.cmd_frame[13] = 1; h.run(15);
  assert(h.serial.calls == 2 && h.errors == 2 && h.normal_path == 0);
  h.cmd_frame[0] = 0x05; h.run(15);
  assert(h.normal_path == 1 && h.serial.calls == 2 && h.errors == 2);
}
'''.replace("@DISPATCH@", dispatch), "-DMESH_HIL_BLE_BENCH=1")

    def test_release_zero_effect_and_invalid_flag_rejected(self):
        source = "#include <helpers/CompanionBleBench.h>\nint main() {}\n"
        self.compile(source)
        self.compile(source, "-DMESH_HIL_BLE_BENCH=0")
        failure = self.compile(source, "-DMESH_HIL_BLE_BENCH=2", expect_failure=True)
        self.assertIn("must be explicitly 0 or 1", failure)
        # No echo symbols/constants exist at all in normal release compilation.
        failure = self.compile(source.replace("int main() {}", "int main() { return mesh::companion::kBleBenchCommand; }"),
                               expect_failure=True)
        self.assertIn("mesh", failure)

    def test_echo_uses_existing_required_response_route_and_queue_admission(self):
        self.compile(r'''
#include <helpers/CompanionBleBench.h>
#include <helpers/CompanionFrameQueue.h>
#include <cassert>
struct Frame { uint8_t buf[MAX_FRAME_SIZE]{}; size_t len = 0; };
int main() {
  const uint8_t echo[] = {0x70, 'B','L','E','1', 1,0,0,0, 2,0,0,0, 0,0};
  assert(mesh::companionFrameRequiresDelivery(echo, sizeof(echo)));
  assert(mesh::companionFrameUsesRequesterRoute(echo, sizeof(echo)));
  Frame queue[2]; size_t count = 0;
  assert(mesh::enqueueCompanionFrame(queue, count, 2, echo, sizeof(echo)));
  assert(mesh::enqueueCompanionFrame(queue, count, 2, echo, sizeof(echo)));
  assert(count == 2);
  assert(!mesh::enqueueCompanionFrame(queue, count, 2, echo, sizeof(echo)));
  assert(count == 2); // Never grows or evicts another required response.
  const uint8_t log[] = {0x88, 0};
  count = 0;
  assert(mesh::enqueueCompanionFrame(queue, count, 2, log, sizeof(log)));
  assert(mesh::enqueueCompanionFrame(queue, count, 2, echo, sizeof(echo)));
  assert(mesh::enqueueCompanionFrame(queue, count, 2, echo, sizeof(echo)));
  assert(count == 2 && queue[0].buf[0] == 0x70 && queue[1].buf[0] == 0x70);
}
''', "-DMESH_HIL_BLE_BENCH=1")

    def test_lab_opcode_does_not_collide_with_shipped_protocol_commands(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        for token in re.findall(r"^#define CMD_\w+\s+(0[xX][0-9a-fA-F]+|[0-9]+)", source, re.MULTILINE):
            self.assertNotEqual(int(token, 0), 0x70)
        for name in ("CompanionHardwareCommandCompat.h", "CompanionMotaControl.h"):
            source = (ROOT / "src/helpers" / name).read_text()
            self.assertNotRegex(source, r"=\s*(?:0x70|112)\s*;")


if __name__ == "__main__":
    unittest.main()
