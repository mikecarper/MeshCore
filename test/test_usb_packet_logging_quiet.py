"""Execute packet-only quiet periods with the real USB facade and RAW emitter."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/nrf52_usb_console"
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])

CASES = r'''
#include <helpers/SerialPacketLog.h>
#include <helpers/BaseSerialInterface.h>
@PUSH_CODE@
struct PacketTransport {
 unsigned frames=0;
 bool isConnected() const {return true;}
 size_t writeFrame(const uint8_t*,size_t size){++frames;return size;}
};
struct MyMesh {
 PacketTransport transport;
 PacketTransport* _serial=&transport;
 uint8_t out_frame[MAX_FRAME_SIZE]={};
 const char* getLogDateTime(){return "stamp";}
 void logRxRaw(float,float,const uint8_t*,int);
};
@RAW_EMITTER@
static void timerCases() {
 mesh::setUsbLoggingEnabled(true);mesh::resumeUsbPacketLogging();
 mesh::configureUsbLoggingPacketStream(false);g_mock_millis=0;
 assert(!mesh::isUsbPacketLoggingAlways());
 for(const char* ignored:std::initializer_list<const char*>{nullptr,""," \t\r\n","stats-core"," \tstats-radio\t ","stats-packets",
     "get usb.mode", "GET usb.mode", "GeT usb.mode", "xy|get usb.mode", "xy|\t get usb.mode", " \txy|GeT usb.mode",
     "\x01" "get name","get\x7f","\xff" "get name","get name\n","stats-core\r","get\rname"}) {
   mesh::noteUsbConsoleCommand(ignored);assert(mesh::isUsbPacketLoggingAllowed());
 }
 for(const char* human:{"get name","stats-core extra","stats-radiox","stats-packets 1",
     "get usb.mode ", "get usb.modex", "get usb.mode extra", "xy|get usb.mode extra",
     "x|get usb.mode", "xyz|get usb.mode", "xy|set usb.mode tinyusb", "get USB.MODE"}) {
   mesh::noteUsbConsoleCommand(human);assert(!mesh::isUsbPacketLoggingAllowed());
   mesh::resumeUsbPacketLogging();
 }
 mesh::noteUsbConsoleCommand("get name");
 assert(mesh::isUsbLoggingEnabled()&&!mesh::isUsbPacketLoggingAllowed());
 g_mock_millis=299999;mesh::noteUsbConsoleCommand("xy|GET usb.mode");
 assert(!mesh::isUsbPacketLoggingAllowed());
 ++g_mock_millis;assert(mesh::isUsbPacketLoggingAllowed());
 g_mock_millis+=0x80000000U;assert(mesh::isUsbPacketLoggingAllowed()); // Expiry latches.
 g_mock_millis=100;mesh::noteUsbConsoleCommand("get radio");
 g_mock_millis+=299000;mesh::noteUsbConsoleCommand("get name");
 g_mock_millis+=1000;assert(!mesh::isUsbPacketLoggingAllowed());
 g_mock_millis+=298999;assert(!mesh::isUsbPacketLoggingAllowed());
 ++g_mock_millis;assert(mesh::isUsbPacketLoggingAllowed());
 g_mock_millis=UINT32_MAX-100000;mesh::noteUsbConsoleCommand("get name");
 g_mock_millis+=299999;assert(!mesh::isUsbPacketLoggingAllowed());
 ++g_mock_millis;assert(mesh::isUsbPacketLoggingAllowed());
 // Zero is the inactive sentinel; its single-tick coercion must stay bounded.
 g_mock_millis=uint32_t(0U-300000U);mesh::noteUsbConsoleCommand("get name");
 g_mock_millis=0;assert(!mesh::isUsbPacketLoggingAllowed());
 g_mock_millis=1;assert(mesh::isUsbPacketLoggingAllowed());
 g_mock_millis=500;mesh::noteUsbConsoleCommand("get name");
 mesh::setUsbLoggingEnabled(true);assert(!mesh::isUsbPacketLoggingAllowed());
 mesh::setUsbLoggingEnabled(false);g_mock_millis+=300000;
 assert(!mesh::isUsbPacketLoggingAllowed()&&!mesh::isUsbLoggingEnabled());
 g_mock_millis+=0x80000000U;mesh::setUsbLoggingEnabled(true);
 assert(mesh::isUsbPacketLoggingAllowed()); // Reapply never resurrects an expired pause.
 mesh::noteUsbConsoleCommand("get name");mesh::resumeUsbPacketLogging();
 assert(mesh::isUsbPacketLoggingAllowed());
 mesh::setUsbLoggingEnabled(false);mesh::resumeUsbPacketLogging();
 assert(!mesh::isUsbPacketLoggingAllowed()&&!mesh::isUsbLoggingEnabled());
 mesh::setUsbLoggingEnabled(true);mesh::configureUsbLoggingPacketStream(true);
 mesh::noteUsbConsoleCommand("get name");assert(mesh::isUsbPacketLoggingAllowed());
 mesh::configureUsbLoggingPacketStream(false);assert(mesh::isUsbPacketLoggingAllowed());
 // Always-on capture clears an existing quiet period without enabling USB.
 mesh::noteUsbConsoleCommand("get name");assert(!mesh::isUsbPacketLoggingAllowed());
 mesh::setUsbPacketLoggingAlways(true);
 assert(mesh::isUsbPacketLoggingAlways()&&mesh::isUsbPacketLoggingAllowed());
 mesh::noteUsbConsoleCommand("get name");mesh::noteUsbConsoleCommand("get radio");
 assert(mesh::isUsbPacketLoggingAllowed());
 mesh::setUsbLoggingEnabled(false);
 assert(mesh::isUsbPacketLoggingAlways()&&!mesh::isUsbPacketLoggingAllowed());
 mesh::setUsbLoggingEnabled(true);
 assert(mesh::isUsbPacketLoggingAlways()&&mesh::isUsbPacketLoggingAllowed());
 mesh::setUsbPacketLoggingAlways(false);
 assert(!mesh::isUsbPacketLoggingAlways()&&mesh::isUsbPacketLoggingAllowed());
 mesh::noteUsbConsoleCommand("get name");assert(!mesh::isUsbPacketLoggingAllowed());
 mesh::setUsbLoggingEnabled(true);assert(!mesh::isUsbPacketLoggingAllowed());
 std::puts("PASS: packet quiet timers, reset, wrap, polls, stream and saved master");
}
static void outputCases() {
 mesh::setUsbLoggingEnabled(true);mesh::setUsbDebugEnabled(true);
 mesh::resumeUsbPacketLogging();tud_cdc_line_state_cb(0,true,false);service();
 MyMesh node;const uint8_t raw[]={0x00,0xab,0xff};
 auto& console=mesh::usbConsolePort();
 const std::string reply="-> name: Companion\r\n";
 assert(console.write(reinterpret_cast<const uint8_t*>(reply.data()),reply.size())==reply.size());
 mesh::noteUsbConsoleCommand("get name");
 node.logRxRaw(2.5f,-92,raw,sizeof(raw));drain();
 if(host!=reply||node.transport.frames!=1)
   std::fprintf(stderr,"quiet host=[%s] bytes=%zu frames=%u allowed=%d\n",host.c_str(),host.size(),node.transport.frames,mesh::isUsbPacketLoggingAllowed());
 assert(host==reply&&node.transport.frames==1);
 assert(mesh::isUsbLoggingEnabled()&&mesh::isUsbDebugLoggingEnabled());
 assert(mesh::serialLogDroppedCount()==0);host.clear();
 mesh::resumeUsbPacketLogging();
 uint8_t large[160];std::fill(large,large+sizeof(large),0xa5);
 during_write=[] {mesh::noteUsbConsoleCommand("get radio");};
 node.logRxRaw(2.5f,-92,large,sizeof(large));drain();
 std::string hex;for(unsigned i=0;i<sizeof(large);++i)hex+="A5";
#if MESH_PACKET_LOGGING_COMPACT
 assert(host=="R"+hex+"\n");
#else
 assert(host=="stamp RAW: "+hex+"\r\n");
#endif
 assert(!mesh::isUsbPacketLoggingAllowed()&&mesh::serialLogDroppedCount()==0);
 host.clear();node.logRxRaw(2.5f,-92,raw,sizeof(raw));drain();
 assert(host.empty()&&node.transport.frames==3);
 assert(console.write(reinterpret_cast<const uint8_t*>(reply.data()),reply.size())==reply.size());
 drain();assert(host==reply&&host.find('\x1b')==std::string::npos);
 assert(mesh::serialLogDroppedCount()==0);host.clear();
 mesh::resumeUsbPacketLogging();node.logRxRaw(2.5f,-92,raw,sizeof(raw));drain();
#if MESH_PACKET_LOGGING_COMPACT
 assert(host=="R00ABFF\n");
#else
 assert(host=="stamp RAW: 00ABFF\r\n");
#endif
 host.clear();mesh::setUsbPacketLoggingAlways(true);mesh::noteUsbConsoleCommand("get name");
 node.logRxRaw(2.5f,-92,raw,sizeof(raw));drain();
#if MESH_PACKET_LOGGING_COMPACT
 assert(host=="R00ABFF\n");
#else
 assert(host=="stamp RAW: 00ABFF\r\n");
#endif
 host.clear();mesh::setUsbPacketLoggingAlways(false);mesh::noteUsbConsoleCommand("get name");
 node.logRxRaw(2.5f,-92,raw,sizeof(raw));drain();assert(host.empty());
 std::puts("PASS: quiet RAW records preserve replies and complete in-progress records");
}
int main(int argc,char**) {if(argc==1)timerCases();else outputCases();}
'''

CORE_CASE = r'''
#include <helpers/UsbLogging.h>
#include <helpers/UsbLoggingClientActivity.h>
#include <atomic>
#include <cassert>
namespace mesh {
static std::atomic<bool> usb_logging_enabled{true};
static std::atomic<uint32_t> usb_packet_logging_quiet_until{0};
static std::atomic<bool> usb_packet_logging_always{false};
bool dedicated=false,stream=false;
bool hasDedicatedUsbLoggingPort(){return dedicated;}
bool isUsbLoggingPacketStream(){return stream;}
@METHODS@
}
int main() {
 mesh::noteUsbConsoleCommand("get name");assert(!mesh::isUsbPacketLoggingAllowed());
 mesh::dedicated=true;assert(mesh::isUsbPacketLoggingAllowed());
 mesh::resumeUsbPacketLogging();mesh::noteUsbConsoleCommand("get name");
 mesh::dedicated=false;assert(mesh::isUsbPacketLoggingAllowed());
 mesh::dedicated=true;mesh::usb_logging_enabled=false;
 assert(!mesh::isUsbPacketLoggingAllowed());
}
'''


class UsbPacketLoggingQuietTests(unittest.TestCase):
    def compile(self, work, name, text, flags=(), sources=()):
        source = work / (name + ".cpp")
        source.write_text(text)
        binary = work / (name + ".exe")
        built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
            "-Werror", *SANITIZERS, "-DARDUINO", *flags,
            "-I" + str(FIXTURE / "mocks"), "-I" + str(ROOT / "src"),
            str(source), *[str(path) for path in sources], "-o", str(binary)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        return binary

    def test_actual_usb_facade_timers_and_raw_emitter_output(self):
        prefix = (FIXTURE / "test_console.cpp").read_text().split("int main()")[0]
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        cases = CASES.replace("@PUSH_CODE@", next(line for line in source.splitlines()
            if line.startswith("#define PUSH_CODE_LOG_RX_DATA")))
        cases = cases.replace("@RAW_EMITTER@", method(source, "void MyMesh::logRxRaw("))
        with tempfile.TemporaryDirectory(prefix="meshcore-packet-quiet-") as directory:
            work = Path(directory)
            for compact in (0, 1):
                with self.subTest(compact=compact):
                    binary = self.compile(work, "quiet" + str(compact), prefix + cases,
                        ["-DNRF52_PLATFORM", "-DUSE_TINYUSB", "-DENABLE_USB_INTERFACE",
                         "-DMESH_DEBUG=1", "-DMESH_PACKET_LOGGING=1", "-DMESH_PACKET_LOGGING_COMPACT=" + str(compact)],
                        [ROOT / "src/helpers" / name for name in (
                            "UsbLogging.cpp", "UsbLoggingClientActivity.cpp", "UsbLoggingLineStateOverride.cpp")])
                    for arguments in ([], ["output"]):
                        ran = subprocess.run([str(binary), *arguments], capture_output=True, text=True, timeout=20)
                        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                        self.assertIn("PASS:", ran.stdout)

    def test_actual_core_dedicated_endpoint_bypasses_automatic_pause(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        methods = "\n".join(method(source, signature) for signature in (
            "bool isUsbLoggingEnabled(", "void noteUsbConsoleCommand(",
            "void resumeUsbPacketLogging(", "void setUsbPacketLoggingAlways(",
            "bool isUsbPacketLoggingAlways(", "bool isUsbPacketLoggingAllowed("))
        with tempfile.TemporaryDirectory(prefix="meshcore-packet-dedicated-") as directory:
            binary = self.compile(Path(directory), "dedicated", CORE_CASE.replace("@METHODS@", methods), ["-DMESH_DEBUG=1"])
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_dual_cdc_inline_apis_preserve_both_continuous_capture_modes(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        harness = '''
#include <helpers/UsbLogging.h>
#include <cassert>
#include <initializer_list>
namespace mesh {
bool enabled=false,packet_stream=false;
bool isUsbLoggingEnabled(){return enabled;}
bool isUsbLoggingPacketStream(){return packet_stream;}
@DEDICATED@
}
int main(){
 for(bool stream:{false,true})for(bool enabled:{false,true}){
   mesh::packet_stream=stream;mesh::enabled=enabled;
   assert(mesh::hasDedicatedUsbLoggingPort()==!stream);
   mesh::noteUsbConsoleCommand(nullptr);mesh::noteUsbConsoleCommand("get name");
   assert(mesh::isUsbPacketLoggingAllowed()==enabled);
   mesh::setUsbPacketLoggingAlways(true);assert(!mesh::isUsbPacketLoggingAlways());
   mesh::resumeUsbPacketLogging();
   assert(mesh::isUsbPacketLoggingAllowed()==enabled);
 }
}
'''.replace("@DEDICATED@", method(source, "bool hasDedicatedUsbLoggingPort("))
        with tempfile.TemporaryDirectory(prefix="meshcore-packet-dual-") as directory:
            binary = self.compile(Path(directory), "dual", harness,
                ["-DMESH_DEBUG=1", "-DMESH_DUAL_CDC_LOGGING=1"])
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_disabled_logging_build_has_noop_pause_apis(self):
        with tempfile.TemporaryDirectory(prefix="meshcore-packet-disabled-") as directory:
            for flags in ([], ["-DMESH_DEBUG=1", "-DMESH_USB_LOGGING_DISABLED=1"]):
                binary = self.compile(Path(directory), "disabled", '''
#include <helpers/UsbLogging.h>
#include <cassert>
int main(){
 static_assert(!MESH_USB_LOGGING_AVAILABLE,"disabled test profile");
 mesh::noteUsbConsoleCommand("get name");mesh::resumeUsbPacketLogging();
 mesh::setUsbPacketLoggingAlways(true);assert(!mesh::isUsbPacketLoggingAlways());
 assert(!mesh::isUsbPacketLoggingAllowed());
}
''', flags)
                ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
