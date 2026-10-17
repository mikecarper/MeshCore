"""Execute HIL fast-policy parsing/admission without touching radio hardware."""
from pathlib import Path
import configparser
import re
import unittest

import test_sx1262_batched_modulation as compiler
import test_radio_profile_clock as clock
from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]


class ProfileFastPolicyTest(unittest.TestCase):
    compile_run = compiler.BatchedModulationTests.compile_run

    def test_hil_radiolib_matches_current_production_dependency(self):
        config = configparser.ConfigParser(interpolation=None, strict=False,
                                           inline_comment_prefixes=(";",))
        config.read([ROOT / "platformio.ini", ROOT / "tools/hil/profile_switch.ini"])

        def resolve(section, option, depth=0):
            self.assertLess(depth, 30, "recursive HIL recipe interpolation")
            text = config.get(section, option)
            return re.sub(r"\$\{([^}]+)\}", lambda match:
                          resolve(*match[1].split(".", 1), depth + 1), text)

        pattern = r"https://github\.com/jgromes/RadioLib\.git#[0-9a-f]{40}"
        production = re.findall(pattern, resolve("arduino_base", "lib_deps"))
        self.assertEqual(len(production), 1)
        environments = [section for section in config.sections()
                        if section.startswith("env:profile_switch_")]
        self.assertGreaterEqual(len(environments), 6)
        for section in environments:
            with self.subTest(environment=section):
                self.assertEqual(re.findall(pattern, resolve(section, "lib_deps")), production)

    def test_policy_selection_is_exact_and_quiet_receiver_only(self):
        source = (ROOT / "tools/hil/profile_switch.cpp").read_text()
        policy = method(source, "bool selectProfileFastPolicy(")
        fixture = r'''
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <string>
struct { bool active=false; } stationaryBaseline,preambleDiagnostic,channelSweep;
bool packetListening=false;
struct { bool hilUseFsOnRetune=false,hilSkipUnchangedPacket=false,hilSkipUnchangedIq=false; } chip;
struct { bool hilUsePort0Only=true; } radioHal;
struct SerialMock {
  std::string text;
  void println(const char* s) { text=s; }
  void printf(const char* format,...) {
    char bytes[256];va_list args;va_start(args,format);
    vsnprintf(bytes,sizeof(bytes),format,args);va_end(args);text=bytes;
  }
} Serial;
@POLICY@
int main() {
  assert(selectProfileFastPolicy("fs 1") && chip.hilUseFsOnRetune);
  assert(Serial.text=="{\"fs\":true}\n");
  assert(selectProfileFastPolicy("packetcache 1") && chip.hilSkipUnchangedPacket);
  assert(Serial.text=="{\"packet_cache\":true}\n");
  assert(selectProfileFastPolicy("fs 0") && !chip.hilUseFsOnRetune);
  assert(chip.hilSkipUnchangedPacket);
  for(const char* invalid:{"fs 2","fs -1","fs 1 junk","fs 1 0","fs",
                           "packetcache 2","packetcache 1 junk","packetcache", "info"}) {
    Serial.text.clear();assert(!selectProfileFastPolicy(invalid));
    assert(Serial.text.empty() && !chip.hilUseFsOnRetune && chip.hilSkipUnchangedPacket);
  }
  bool* guards[]={&packetListening,&stationaryBaseline.active,
                 &preambleDiagnostic.active,&channelSweep.active};
  for(bool* guard:guards) {
    *guard=true;
    assert(selectProfileFastPolicy("fs 1") && !chip.hilUseFsOnRetune);
    assert(Serial.text.find("error")!=std::string::npos);
    assert(selectProfileFastPolicy("packetcache 0") && chip.hilSkipUnchangedPacket);
    assert(Serial.text.find("error")!=std::string::npos);
    *guard=false;
  }
  assert(selectProfileFastPolicy("packetcache 0") && !chip.hilSkipUnchangedPacket);
  assert(selectProfileFastPolicy("iqcache 1") && chip.hilSkipUnchangedIq);
  assert(Serial.text=="{\"iq_cache\":true}\n");
  for(bool* guard:guards) {
    *guard=true;assert(selectProfileFastPolicy("iqcache 0"));
    assert(chip.hilSkipUnchangedIq && Serial.text.find("error")!=std::string::npos);
    *guard=false;
  }
  assert(selectProfileFastPolicy("iqcache 0") && !chip.hilSkipUnchangedIq);
  for(const char* invalid:{"iqcache 2","iqcache -1","iqcache 1 junk","iqcache"})
    assert(!selectProfileFastPolicy(invalid) && !chip.hilSkipUnchangedIq);
#ifdef HIL_INDICATOR
  assert(selectProfileFastPolicy("port0 0") && !radioHal.hilUsePort0Only);
  assert(Serial.text=="{\"port0\":false}\n");
  for(bool* guard:guards) {
    *guard=true;assert(selectProfileFastPolicy("port0 1"));
    assert(!radioHal.hilUsePort0Only && Serial.text.find("error")!=std::string::npos);
    *guard=false;
  }
  assert(selectProfileFastPolicy("port0 1") && radioHal.hilUsePort0Only);
  for(const char* invalid:{"port0 2","port0 -1","port0 0 junk","port0"})
    assert(!selectProfileFastPolicy(invalid) && radioHal.hilUsePort0Only);
#else
  assert(!selectProfileFastPolicy("port0 0") && radioHal.hilUsePort0Only);
#endif
}
'''.replace("@POLICY@", policy)
        self.compile_run(fixture)
        self.compile_run("#define HIL_INDICATOR\n" + fixture)

    def test_receive_timestamp_retains_esp32_microsecond_units_and_wrap(self):
        source = (ROOT / "tools/hil/profile_switch.cpp").read_text()
        getter = method(source, "uint32_t receiveStartedUs()")
        self.assertNotIn("_profile_visit_us", getter)
        self.assertIn("#if !defined(ESP32_PLATFORM)", getter)
        self.assertIn('#error "profile_switch.cpp requires the ESP32 microsecond profile clock"', getter)
        fixture = r'''
#include <cassert>
#include <cstdint>
#define ESP32_PLATFORM
uint32_t clockUs=0;
uint32_t micros() { return clockUs; }
@CLOCK@
struct BenchWrapper { uint32_t _profile_visit_stamp=0; @GETTER@ };
int main() {
  BenchWrapper driver;
  clockUs=0xfffffff0;
  driver._profile_visit_stamp=profileTimestamp(true);
  assert(driver.receiveStartedUs()==clockUs);
  clockUs=0x15;
  assert(uint32_t(micros()-driver.receiveStartedUs())==37);
  assert(profileElapsedUs(driver.receiveStartedUs(),profileTimestamp(true),true)==37);
  clockUs=1234567;driver._profile_visit_stamp=profileTimestamp(false);
  assert(driver.receiveStartedUs()==1234567);
}
'''.replace("@CLOCK@", clock.CLOCK).replace("@GETTER@", getter)
        self.compile_run(fixture)

    def test_timing_and_rf_readiness_report_exercised_fast_operations(self):
        sources = (("profile_switch.cpp", "void run("),
                   ("profile_switch_packets.h", "void listenPacket("))
        for name, signature in sources:
            with self.subTest(source=name):
                source = (ROOT / "tools/hil" / name).read_text()
                body = method(source, signature)
                for field in ("fs", "packet_cache", "fs_entries", "packet_skips", "iq_cache", "iq_workaround_skips"):
                    self.assertIn('\\"' + field + '\\"', body)
                self.assertIn("chip.hilFsEntries-fsStart", body)
                self.assertIn("chip.hilPacketSkips-packetStart", body)
                self.assertIn("chip.hilUseFsOnRetune", body)
                self.assertIn("chip.hilSkipUnchangedPacket", body)
                self.assertIn("chip.hilIqWorkaroundSkips-iqStart", body)
        chip = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text()
        self.assertIn("bool hilUseFsOnRetune = false;", chip)
        self.assertIn("bool hilSkipUnchangedPacket = false;", chip)
        self.assertIn("bool hilSkipUnchangedIq = false;", chip)


if __name__ == "__main__":
    unittest.main()
