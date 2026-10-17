"""Exercise production radio2 idle admission and its final role rechecks."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <RadioProfiles.h>
#define STATE_RX 1
#define MESH_ENABLE_TX_BEACONS 1
#define MESH_ENABLE_MORSE_RX 1
struct Rtc { uint32_t COUNTER = 200; } rtc;
#define NRF_RTC2 (&rtc)
struct NRF52Board {
  static bool ready;
  static bool canIdleRadioProfile() { return ready; }
};
bool NRF52Board::ready = true;
struct RadioLibWrapper {
  mesh::RadioProfiles _profiles;
  uint32_t _profile_idle_visit_ticks=1000, _profile_idle_stamp=100;
  uint32_t _profile_visit_stamp=100, _profile_idle_switch_us=1100;
  uint16_t _profile_idle_preamble=32, _physical_preamble=32;
  uint32_t _profile_generation=1, _profile_scan_generation[2]={1,1};
  uint32_t _profile_retry_at=0, _wd_observe_until=0;
  uint8_t state=STATE_RX, _active_profile=0;
  bool _params_valid=true, _profile_rxps_suspended=true;
  bool _rx_ps_enabled=false, _rx_ps_armed=false, _rx_hold_continuous=false;
  bool _rx_retry_pending=false, _profile_refresh_required=false;
  bool _nf_refresh_requested=false, _nf_calib_active=false;
  bool _noise_floor_valid=true, _noise_floor_secondary_valid=true;
  bool _cw_active=false, _morse_owned=false, _beacon_owned=false, _morse_rx_owned=false;
  bool irq=false;
  bool hasPendingRadioInterrupt() const { return irq; }
  bool getProfileIdleDelay(uint32_t&) const;
  RadioLibWrapper() {
    _profiles.secondary.mode=mesh::RadioProfileMode::RxTx;
    _profiles.switch_test_samples[0]=_profiles.switch_test_samples[1]=4;
    _profiles.switch_test_max_us[0]=_profiles.switch_test_max_us[1]=1000;
  }
};
@METHOD@
int main() {
  uint32_t delay=999;
  RadioLibWrapper w;
#if !defined(NRF52_PLATFORM)
  assert(!w.getProfileIdleDelay(delay) && delay==0);
#else
  assert(w.getProfileIdleDelay(delay) && delay==26);
  // Exact early-wake boundary, rounding, already expired, and RTC wrap.
  for (uint32_t remaining=0; remaining<=48; ++remaining) {
    rtc.COUNTER=100+1000-remaining;
    assert(!w.getProfileIdleDelay(delay) && delay==0);
  }
  rtc.COUNTER=100+1000-49;
  assert(w.getProfileIdleDelay(delay) && delay==1);
  rtc.COUNTER=1101;
  assert(!w.getProfileIdleDelay(delay));
  w._profile_visit_stamp=w._profile_idle_stamp=0x00fffff0;
  rtc.COUNTER=0x10;
  assert(w.getProfileIdleDelay(delay) && delay==29);
  w=RadioLibWrapper(); rtc.COUNTER=200;
  w._profile_idle_visit_ticks=0x00800000;
  assert(!w.getProfileIdleDelay(delay));
  // Every software owner, cached timing proof, and asynchronous IRQ fails closed.
  auto denied=[&](auto mutate) {
    w=RadioLibWrapper(); rtc.COUNTER=200; delay=999;
    mutate(); assert(!w.getProfileIdleDelay(delay) && delay==0);
  };
  denied([&]{NRF52Board::ready=false;}); NRF52Board::ready=true;
  denied([&]{w._profiles.secondary.mode=mesh::RadioProfileMode::Off;});
  denied([&]{w._profile_idle_visit_ticks=0;});
  denied([&]{w._params_valid=false;});
  denied([&]{w.state=3;});
  denied([&]{w._profile_rxps_suspended=false;});
  denied([&]{w._rx_ps_enabled=true;});
  denied([&]{w._rx_ps_armed=true;});
  denied([&]{w._rx_hold_continuous=true;});
  denied([&]{w._rx_retry_pending=true;});
  denied([&]{w._profile_retry_at=1;});
  denied([&]{w._profile_refresh_required=true;});
  denied([&]{w._nf_refresh_requested=true;});
  denied([&]{w._nf_calib_active=true;});
  denied([&]{w._noise_floor_valid=false;});
  denied([&]{w._noise_floor_secondary_valid=false;});
  denied([&]{w._wd_observe_until=1;});
  denied([&]{w._cw_active=true;});
  denied([&]{w._morse_owned=true;});
  denied([&]{w._beacon_owned=true;});
  denied([&]{w._morse_rx_owned=true;});
  denied([&]{w.irq=true;});
  denied([&]{w._profiles.resetSwitchTest();});
  denied([&]{++w._profiles.generation[0];});
  denied([&]{++w._profiles.generation[1];});
  denied([&]{w._active_profile=2;});
  denied([&]{++w._profile_generation;});
  denied([&]{++w._profile_visit_stamp;});
  denied([&]{++w._physical_preamble;});
  denied([&]{++w._profiles.switch_test_max_us[1];});
  // A second query must reject an interrupt or elapsed deadline after admission.
  w=RadioLibWrapper();
  assert(w.getProfileIdleDelay(delay)); w.irq=true;
  assert(!w.getProfileIdleDelay(delay));
  w.irq=false; rtc.COUNTER=1099;
  assert(!w.getProfileIdleDelay(delay));
  mesh::RadioProfileParams longfast;
  longfast.bw=250; longfast.sf=11;
  longfast.protocol=mesh::RadioProfileProtocol::Meshtext;
  assert(mesh::RadioProfiles::minimumListenUs(longfast,true)==37684);
  w._profiles.secondary.params=longfast;
  assert(w._profiles.preamble(1,32)==16);
#endif
}
'''


class ProfileIdleTests(unittest.TestCase):
    def test_scanner_cache_tracks_visits_and_invalidates_early_returns(self):
        from test_radio_profile_scan import HARNESS as scan_harness
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        names = [("uint8_t", "beginReconfigure"), ("void", "endReconfigure"),
                 ("void", "serviceProfileScan")] + [("mesh::RadioParamApplyResult", name) for name in
                 ("tuneProfile", "prepareTransmitProfile", "tryRestoreCodingRate",
                  "trySetParams", "trySetPrimaryParams")]
        methods = "\n".join(body(source, f"{kind} RadioLibWrapper::{name}(") for kind, name in names)
        methods += "\n" + body(source,
            "mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool")
        wrapper = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
        methods += "\n" + body(wrapper, "void setProfileStandbyWarm(").replace(
            "void setProfileStandbyWarm(bool enabled) override",
            "void RadioLibWrapper::setProfileStandbyWarm(bool enabled)")
        fixture = scan_harness.split("int main()", 1)[0].replace("@METHODS@", methods)
        fixture = fixture.replace("  bool _cw_active=false;", """
  uint32_t _profile_idle_visit_ticks=0, _profile_idle_stamp=0, _profile_idle_switch_us=0;
  uint16_t _profile_idle_preamble=0;
  bool _cw_active=false;
""")
        fixture += r'''
int main() {
  RadioLibWrapper w; w.enable();
  assert(w._profile_idle_visit_ticks==0); // restart needs a fresh pass
  w.serviceProfileScan();
  const auto ticks=w._profile_idle_visit_ticks;
  assert(ticks>0 && w._profile_idle_stamp==w._profile_visit_stamp);
  assert(ticks==uint64_t(w._profiles.listenUs(w._active_profile,
      w.profilePreamble(w._profiles.slowerProfile())))*512/15625);
  w._cw_active=true; w.serviceProfileScan();
  assert(w._profile_idle_visit_ticks==0);
  w._cw_active=false; w.serviceProfileScan(); assert(w._profile_idle_visit_ticks);
  w._profile_retry_at=millis()+1000; w.serviceProfileScan();
  assert(w._profile_idle_visit_ticks==0);
  w._profile_retry_at=0; w.serviceProfileScan(); assert(w._profile_idle_visit_ticks);
  w.busy=true; w.serviceProfileScan(); assert(w._profile_idle_visit_ticks==0);
  w.busy=false; w.packet=true; w.serviceProfileScan(); assert(w._profile_idle_visit_ticks==0);
  w.packet=false; w.events.record(); w.serviceProfileScan(); assert(w._profile_idle_visit_ticks==0);
  w.events.claim(); w.serviceProfileScan(); assert(w._profile_idle_visit_ticks);
  elapsed_us+=1000000; // hop cannot publish old visit with new timestamp
  const auto old=w._active_profile; w.serviceProfileScan();
  assert(w._active_profile!=old && w._profile_idle_visit_ticks==0);
  w.serviceProfileScan(); assert(w._profile_idle_visit_ticks);
  w._profiles.setSecondary({},false); w.serviceProfileScan();
  assert(w._profile_idle_visit_ticks==0);
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "Arduino.h").write_text("#pragma once\n#include <cstdint>\n#include <cmath>\n")
            (path / "test.cpp").write_text(fixture)
            result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                "-DNRF52_PLATFORM=1", "-Wall", "-Wextra", "-I", folder,
                "-I", str(ROOT / "src"), str(path / "test.cpp"), "-o", str(path / "test")],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(path / "test")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_integer_admission_and_unsupported_platform(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        method = body(source, "bool RadioLibWrapper::getProfileIdleDelay(")
        # Keep the final admission free of SPI, float work and clock mutation.
        for forbidden in ("profilePreamble(", "listenUs(", "profileTimestamp(",
                          "isChipBusy(", "isPacketPendingOrReceiving("):
            self.assertNotIn(forbidden, method)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "Arduino.h").write_text("#pragma once\n#include <cstdint>\n#include <cmath>\n")
            (path / "test.cpp").write_text(HARNESS.replace("@METHOD@", method))
            for defines in ([], ["-DNRF52_PLATFORM=1"]):
                result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                    "-Wall", "-Wextra", "-Werror", *defines, "-I", folder,
                    "-I", str(ROOT / "src"), str(path / "test.cpp"), "-o", str(path / "test")],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run([str(path / "test")], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_late_preparation_precedes_all_guards_and_final_recheck(self):
        for role in ("companion_radio", "simple_repeater", "simple_room_server", "simple_sensor"):
            source = (ROOT / "examples" / role / "main.cpp").read_text()
            loop = body(source, "\nvoid loop()")
            prepare = loop.index("board.prepareProfileIdleCycle()")
            predicate = loop.index("sleep_reason =" if role == "companion_radio" else "const auto sleep_guard")
            self.assertLess(prepare, predicate, role)
            final = loop.index("the_mesh.canIdleDualRadio()", predicate)
            sleep = loop.index("board.sleep", final)
            self.assertLess(final, sleep, role)
            self.assertNotIn("prepareProfileIdleCycle", loop[predicate:], role)
            if role == "simple_sensor":
                arm = loop.index("armTrackerSleepWake(", predicate)
                timer_recheck = loop.index("the_mesh.canIdleDualRadio()", arm)
                self.assertLess(timer_recheck, loop.index("board.sleep", arm))


if __name__ == "__main__":
    unittest.main()
