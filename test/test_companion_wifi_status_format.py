"""Exercise the bounded combined Companion WiFi status with real C++ code."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


HARNESS = r'''
#include <cassert>
#include <climits>
#include <cstring>
#include <string>
#include <helpers/CompanionWiFiStatusFormat.h>
using mesh::wifi::CompanionWiFiStatusSnapshot;
using mesh::wifi::formatCompanionWiFiStatusSnapshot;

CompanionWiFiStatusSnapshot snapshot() {
  return {"connected", "test", "192.168.11.102", true, -74,
          "bgn", "bgn", "0x07", "normal", "normal", "min",
          true, false, false, 5001};
}

int main() {
  char output[512]={};
  auto value=snapshot();
  assert(formatCompanionWiFiStatusSnapshot(output,sizeof(output),value));
  assert(std::string(output)=="> connected ssid=test ip=192.168.11.102 rssi=-74\n"
      "protocol=bgn/bgn/0x07 mode=normal ps=min coex=yes ota=off");
  value.protocol_saved="bg"; value.mode_saved="wifi-only";
  value.ota_listening=true;
  assert(formatCompanionWiFiStatusSnapshot(output,sizeof(output),value));
  assert(std::string(output).find("protocol=bg/bgn/0x07 mode=wifi-only/normal")!=std::string::npos);
  assert(std::string(output).find("ota=5001:listen")!=std::string::npos);
  value.ota_attached=true;
  assert(formatCompanionWiFiStatusSnapshot(output,sizeof(output),value));
  assert(std::string(output).find("ota=5001:client")!=std::string::npos);

  // A maximum-length SSID, IPv4 address, RX RSSI and port must fit all
  // saved/boot/live protocol and mode combinations in the 157-byte routed
  // reply budget, without dropping any applicable field.
  const char* protocols[]={"bg","bgn"};
  const char* modes[]={"normal","wifi-only"};
  const char* physical[]={"0x03","0x07","n/a"};
  const char* saves[]={"none","min","max","n/a"};
  for (size_t ssid_size:{31u,32u}) {
  const std::string ssid(ssid_size,'S');
  for (const char* saved:protocols) for (const char* boot:protocols)
  for (const char* phy:physical) for (const char* mode_saved:modes)
  for (const char* mode_boot:modes) for (const char* ps:saves)
  for (bool coexist:{false,true}) for (bool listening:{false,true})
  for (bool attached:{false,true}) {
    value={"connected",ssid.c_str(),"255.255.255.255",true,-128,
           saved,boot,phy,mode_saved,mode_boot,ps,coexist,listening,attached,65535};
    char routed[157];
    assert(formatCompanionWiFiStatusSnapshot(routed,sizeof(routed),value));
    assert(strlen(routed)<sizeof(routed));
    const std::string actual(routed);
    assert(actual.find(ssid)!=std::string::npos);
    assert(actual.find("ip=255.255.255.255 rssi=-128")!=std::string::npos);
    assert(actual.find(std::string("protocol=")+saved+"/"+boot+"/"+phy)!=std::string::npos);
    const std::string wanted_mode=std::string(" mode=")+mode_saved+
        (strcmp(mode_saved,mode_boot)==0 ? "" : std::string("/")+mode_boot);
    assert(actual.find(wanted_mode+" ps="+ps)!=std::string::npos);
    assert(actual.find(coexist ? " coex=yes" : " coex=no")!=std::string::npos);
    const char* wanted_ota=attached ? " ota=65535:client" : listening
        ? " ota=65535:listen" : " ota=off";
    assert(actual.find(wanted_ota)!=std::string::npos);
  }
  }

  // Longer connection-state descriptions have no fictitious station IP or
  // RSSI. Missing SDK information is explicit, not an omitted diagnostic.
  const std::string ssid(32,'S');
  for (const char* state:{"off", "inactive", "connecting/retrying",
                         "disconnected", "suspended", "setup AP", "not configured"}) {
    value=snapshot();value.state=state;value.ip=nullptr;value.has_rssi=false;
    value.ssid=ssid.c_str();value.phy="n/a";value.powersave="n/a";
    value.mode_saved="wifi-only";value.mode_boot="normal";
    value.ota_listening=true;value.ota_attached=true;value.ota_port=65535;
    assert(formatCompanionWiFiStatusSnapshot(output,157,value));
    assert(!strstr(output," ip=") && !strstr(output," rssi="));
    assert(strstr(output,"protocol=bgn/bgn/n/a") && strstr(output,"ps=n/a"));
    assert(strstr(output,"ota=65535:client"));
  }
  value={nullptr,nullptr,nullptr,false,0,nullptr,nullptr,nullptr,nullptr,nullptr,
         nullptr,false,false,false,0};
  assert(formatCompanionWiFiStatusSnapshot(output,sizeof(output),value));
  assert(std::string(output)=="> n/a ssid=n/a\nprotocol=n/a/n/a/n/a mode=n/a ps=n/a coex=no ota=off");
  value=snapshot();value.ip="";value.has_rssi=true;value.rssi=INT_MIN;
  assert(formatCompanionWiFiStatusSnapshot(output,sizeof(output),value));
  assert(!strstr(output," ip=") && strstr(output," rssi=-2147483648"));

  // Exercise every capacity, preserving guards before and after the exact
  // destination. A small buffer gets the exact prefix, a truthful false
  // result and NUL termination, never a selectively abbreviated report.
  value=snapshot();value.ssid=ssid.c_str();value.ip="255.255.255.255";
  value.rssi=-128;value.mode_saved="wifi-only";value.ota_listening=true;
  value.ota_attached=true;value.ota_port=65535;
  assert(formatCompanionWiFiStatusSnapshot(output,sizeof(output),value));
  const std::string complete(output);
  for (size_t capacity=1;capacity<=160;++capacity) {
    unsigned char guarded[164]; memset(guarded,0x5a,sizeof(guarded));
    char* destination=reinterpret_cast<char*>(guarded+2);
    const bool fits=formatCompanionWiFiStatusSnapshot(destination,capacity,value);
    assert(fits==(capacity>complete.size()));
    assert(guarded[0]==0x5a && guarded[1]==0x5a);
    for (size_t i=2+capacity;i<sizeof(guarded);++i) assert(guarded[i]==0x5a);
    const size_t prefix=complete.size()<capacity-1 ? complete.size() : capacity-1;
    assert(destination[prefix]==0);
    assert(std::string(destination)==complete.substr(0,prefix));
  }
  char untouched='X';
  assert(!formatCompanionWiFiStatusSnapshot(&untouched,0,value));
  assert(untouched=='X');
  assert(!formatCompanionWiFiStatusSnapshot(nullptr,160,value));
  assert(!formatCompanionWiFiStatusSnapshot(nullptr,0,value));
}
'''


class CompanionWiFiStatusFormatTest(unittest.TestCase):
    def test_combined_status_boundaries_and_all_field_combinations(self):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler, "A host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="companion-wifi-status-") as directory:
            folder = Path(directory)
            source = folder / "test.cpp"
            source.write_text(HARNESS)
            binary = folder / ("test.exe" if os.name == "nt" else "test")
            flags = [] if os.name == "nt" else [
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"
            ]
            built = subprocess.run([
                compiler, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror", *flags,
                "-I", str(ROOT / "src"), str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
