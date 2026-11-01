"""Execute the production OTA wire/pace diagnostics with bounded CLI replies."""
from pathlib import Path
import os
import shutil
import unittest

import test_ota_kiss_direct as native_tests
from test_ota_cli_listing import function


ROOT = Path(__file__).resolve().parents[1]


class OtaTransferDiagnosticsTest(unittest.TestCase):
    compile_run = native_tests.OtaKissDirectTest.compile_run

    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def test_real_branches_word_helpers_and_maximum_reply_bounds(self):
        source = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        control = function(source, "handle_control_command")
        start = control.index('} else if (OTA_TRANSFER_DIAGNOSTICS && is_cmd(a, "wire", &rest)) {')
        end = control.index('} else if (is_cmd(a, "stats", &rest)) {', start)
        branches = control[start:end]
        header = (ROOT / "src/helpers/ota/OtaManager.h").read_text()
        start = header.index("enum class WireChoice")
        end = header.index("const WireStats& wireStats()", start)
        declarations = header[start:end]
        helpers = "\n".join(function(source, name) for name in (
            "is_cmd", "wire_choice_word", "wire_fallback_word"))
        helpers += "\n" + function(
            (ROOT / "src/helpers/ota/OtaSpeedConfig.cpp").read_text(), "formatSpeedFactor")
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#define OTA_TRANSFER_DIAGNOSTICS @DIAGNOSTICS@
struct OtaManager {
  @DECLARATIONS@
  WireStats wire;
  bool v2=false,deflate=false;
  float rate[2]={1.0f,1.0f};
  uint32_t gap=0,retry=0;
  mutable unsigned rate_queries=0;
  const WireStats& wireStats() const { return wire; }
  bool fetchWireV2() const { return v2; }
  bool fetchAllowsDeflate() const { return deflate; }
  float adaptivePacketSpeed(uint8_t profile) const {
    assert(profile<2);rate_queries|=1u<<profile;return rate[profile];
  }
  uint32_t observedDataIntervalMs() const { return gap; }
  uint32_t fetchRetryTimeoutMs() const { return retry; }
};
struct Context { OtaManager manager; };
@HELPERS@
static bool diagnostics(const char* a,char* reply,Context& c) {
  const char* rest=nullptr;
  if (false) {
  @BRANCHES@
  } else return false;
  return true;
}
struct Reply {
  uint8_t before[8];char text[160];uint8_t after[8];
  Reply() { memset(before,0xa5,sizeof before);memset(text,0xcc,sizeof text);memset(after,0x5a,sizeof after); }
  void check() const {
    for (uint8_t v:before) assert(v==0xa5);
    for (uint8_t v:after) assert(v==0x5a);
    assert(memchr(text,0,sizeof text));assert(strlen(text)<sizeof text);
  }
};
int main() {
#if !OTA_TRANSFER_DIAGNOSTICS
  {
  Context c;
  for (const char* command : {"wire", "pace"}) {
    Reply out; assert(!diagnostics(command, out.text, c));
    for (char ch : out.text) assert(uint8_t(ch) == 0xcc);
  }
  assert(c.manager.rate_queries == 0);
  return 0;
  }
#endif
  using Choice=OtaManager::WireChoice;using Fallback=OtaManager::WireFallback;
  const Choice choices[]={Choice::None,Choice::Deflate,Choice::LegacyRequest,Choice::RawRequest,
      Choice::HostRaw,Choice::NoEncoder,Choice::NoWorkspace,Choice::NoGain,Choice::EncodeFailed};
  const char* words[]={"none","deflate","legacy-request","raw-request","host-raw",
      "no-encoder","no-workspace","not-smaller","encode-failed"};
  const Fallback reasons[]={Fallback::None,Fallback::LegacyPeer,Fallback::V2Timeout,
      Fallback::V2Stall,Fallback::InflateFailed};
  const char* reason_words[]={"none","legacy-peer","v2-timeout","v2-stall","inflate-failed"};
  assert(!strcmp(wire_choice_word(Choice(255)),"none"));
  assert(!strcmp(wire_fallback_word(Fallback(255)),"none"));
  Context c;
  { Reply out;assert(diagnostics("wire",out.text,c));out.check();
    assert(!strcmp(out.text,"wire tx=0 deflate=0 bytes=0 last=0/0:none rx=legacy/raw fallback=none:0"));
    assert(!c.manager.rate_queries);
  }
  c.manager.wire.data_packets=UINT32_MAX;c.manager.wire.deflated_packets=UINT32_MAX;
  c.manager.wire.data_bytes=UINT32_MAX;c.manager.wire.raw_bytes=UINT16_MAX;
  c.manager.wire.encoded_bytes=UINT16_MAX;c.manager.wire.fallbacks=UINT16_MAX;
  for (unsigned i=0;i<sizeof choices/sizeof *choices;++i) {
    assert(!strcmp(wire_choice_word(choices[i]),words[i]));
    c.manager.wire.choice=choices[i];
    for (unsigned j=0;j<sizeof reasons/sizeof *reasons;++j) {
      assert(!strcmp(wire_fallback_word(reasons[j]),reason_words[j]));
      c.manager.wire.fallback=reasons[j];
      for (unsigned flags=0;flags<4;++flags) {
        c.manager.v2=flags&1;c.manager.deflate=flags&2;
        Reply out;assert(diagnostics("wire",out.text,c));out.check();
        assert(strstr(out.text,"tx=4294967295 deflate=4294967295 bytes=4294967295"));
        char fragment[96];snprintf(fragment,sizeof fragment,"last=65535/65535:%s",words[i]);
        assert(strstr(out.text,fragment));
        snprintf(fragment,sizeof fragment,"rx=%s/%s",c.manager.v2?"v2":"legacy",c.manager.deflate?"deflate":"raw");
        assert(strstr(out.text,fragment));
        snprintf(fragment,sizeof fragment,"fallback=%s:65535",reason_words[j]);
        const char* tail=strstr(out.text,fragment);assert(tail && strlen(tail)==strlen(fragment));
      }
    }
  }
  { Reply out;assert(diagnostics("pace",out.text,c));out.check();
    assert(!strcmp(out.text,"pace last radio=1x radio2=1x data-gap=0ms retry=0ms"));
    assert(c.manager.rate_queries==3);
  }
  c.manager.rate[0]=0.15f;c.manager.rate[1]=0.95f;c.manager.gap=UINT32_MAX;c.manager.retry=UINT32_MAX;
  { Reply out;assert(diagnostics("pace",out.text,c));out.check();
    assert(!strcmp(out.text,"pace last radio=0.15x radio2=0.95x data-gap=4294967295ms retry=4294967295ms"));
  }
  c.manager.rate[0]=1.0f;c.manager.rate[1]=0.05f;
  { Reply out;assert(diagnostics("pace",out.text,c));out.check();
    assert(strstr(out.text,"radio=1x radio2=0.05x"));
  }
  for (const char* invalid:{"wireless","paces","stats",""}) {
    Reply out;assert(!diagnostics(invalid,out.text,c));
    for (char ch:out.text) assert(uint8_t(ch)==0xcc);
  }
}
'''.replace("@DECLARATIONS@", declarations).replace("@HELPERS@", helpers).replace(
            "@BRANCHES@", branches)
        # This test compiles only the extracted production branches. It performs
        # no OTA I/O and cannot allocate or activate a firmware OTA context.
        for enabled in (1, 0):
            with self.subTest(diagnostics=enabled):
                self.compile_run(code.replace("@DIAGNOSTICS@", str(enabled)))


if __name__ == "__main__":
    unittest.main()
