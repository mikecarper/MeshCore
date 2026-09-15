"""Run the production deferred OTA apply and TX guard with an active radio."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_ota_dev_reboot_guard import function_body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <helpers/ota/OtaTiming.h>
#define PAYLOAD_TYPE_OTA 0x0c
#define TOTAL_FLOOD_RETRY_SLOTS 1
namespace mesh {
namespace ota {
struct OtaContext {
  bool apply_pending=false, bootloader_apply_pending=false;
  uint32_t apply_at=0, apply_hard=0;
};
OtaContext context;
OtaContext* active=&context;
int applications=0, bootloaders=0;
OtaContext& ota_ctx() { return context; }
OtaContext* ota_context_if_active() { return active; }
void ota_reboot_to_apply() { ++applications; }
void ota_reboot_to_bootloader_update() { ++bootloaders; }
}
struct Packet { uint8_t getPayloadType() const { return 0; } };
struct Clock { uint32_t now=0; uint32_t getMillis() const { return now; } };
struct Manager { int queued=0; int getOutboundTotal() const { return queued; } };
struct Mesh {
  Clock clock; Clock* _ms=&clock;
  Manager manager; Manager* _mgr=&manager;
  Packet* outbound=nullptr;
  bool flush_ok=true;
  int _active_flood_retry_count=0;
  struct { bool active=false, queued=false; Packet* packet=nullptr; uint8_t retry_attempts_sent=0; } _flood_retries[1];
  uint8_t getEligibleFloodRetryMaxAttempts(const Packet*) const { return 0; }
  bool isAnyTempRadioActive() const { return true; }
  bool hasOutbound() const { return outbound != nullptr; }
  bool millisHasNowPassed(uint32_t deadline) const { return (int32_t)(clock.now-deadline)>0; }
  uint32_t futureMillis(uint32_t ms) const { return clock.now+ms; }
  bool prepareForOtaReboot() { return flush_ok; }
  bool allowPacketTransmit(const Packet* packet) const { @ALLOW@ }
  void serviceApply() {
    ota::OtaContext& oc=ota::ota_ctx();
    if (oc.apply_pending) { @APPLY@ }
  }
};
}
int main() {
  using namespace mesh;
  Mesh node;
  Packet packet;
  auto reset = [&]() {
    ota::context={}; ota::active=&ota::context;
    ota::applications=ota::bootloaders=0;
    node.clock.now=0; node.outbound=nullptr;
    node.manager.queued=0; node.flush_ok=true;
    ota::context.apply_pending=true;
  };
  reset(); node.serviceApply();
  node.clock.now=2000; node.outbound=&packet;
  node.serviceApply();
  assert(ota::applications==0); // dequeued is not the same as finished transmitting
  node.outbound=nullptr; node.serviceApply();
  assert(ota::applications==1);

  reset(); node.serviceApply();
  node.clock.now=15001; node.outbound=&packet; node.manager.queued=99;
  assert(!node.allowPacketTransmit(&packet)); // busy traffic cannot keep extending reboot
  node.serviceApply(); assert(ota::applications==0);
  node.outbound=nullptr; node.serviceApply();
  assert(ota::applications==1);

  reset(); node.serviceApply();
  node.clock.now=15001; node.flush_ok=false;
  node.serviceApply(); assert(ota::applications==0);
  assert(node.allowPacketTransmit(&packet)); // storage backoff releases the TX gate
  node.flush_ok=true; node.clock.now+=1001; node.serviceApply();
  assert(ota::applications==1);

  reset(); node.clock.now=UINT32_MAX-1499; node.serviceApply();
  assert(ota::context.apply_at!=0); // exact rollover must not re-arm forever
  node.clock.now=1; node.outbound=&packet; node.serviceApply();
  assert(ota::applications==0);
  node.clock.now=2; node.outbound=nullptr;
  ota::context.bootloader_apply_pending=true; node.serviceApply();
  assert(ota::bootloaders==1 && ota::applications==0);

  ota::context.apply_pending=false;
  assert(node.allowPacketTransmit(&packet));
  ota::active=nullptr;
  assert(node.allowPacketTransmit(&packet));
  assert(ota::active==nullptr); // idle inspection must not borrow an OTA workspace
}
'''


class OtaApplyDeliveryTest(unittest.TestCase):
    def test_reply_completion_busy_queue_flush_failure_and_rollover(self):
        source = (ROOT / "src/Mesh.cpp").read_text()
        maintenance = function_body(source, "void __attribute__((noinline)) Mesh::serviceLoopMaintenance()")
        apply = function_body(maintenance, "if (oc.apply_pending)")
        allow = function_body(source, "bool Mesh::allowPacketTransmit(")
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "apply.cpp"
            binary = Path(directory) / "apply"
            cpp.write_text(HARNESS.replace("@APPLY@", apply).replace("@ALLOW@", allow))
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-DENABLE_OTA=1",
                            "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
