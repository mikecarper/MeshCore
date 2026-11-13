#include <RadioLib.h>
#include <helpers/radiolib/SX126xAgcRetention.h>
#include <helpers/radiolib/SX126xReset.h>
#include <string>
#include <iostream>

struct Custom : SX1262 {
@GAIN_METHOD@
};

int main(int argc, char** argv) {
  assert(argc == 2); const std::string scenario = argv[1];
  Custom radio;
  if (scenario == "sleep") {
    radio.regs[0x08B5] = 0xA4;
    radio.regs[0x0736] = 0xAA; radio.retain(0x0736);
    assert(sx126xApplyRetainedAgcPatch(&radio) == 0);
    assert(radio.regs[0x08B5] == 0xA5);
    assert(radio.contains(0x0736) && radio.contains(0x08AC) && radio.contains(0x08B5));
    for (unsigned i = 0; i < 100; ++i) {
      radio.sleep(true);
      assert(radio.regs[0x08B5] == 0xA5 && radio.regs[0x08AC] == 0x96);
      assert(radio.regs[0x0736] == 0xAA);
      assert(sx126xApplyRetainedAgcPatch(&radio) == 0 && radio.regs[0x029F] == 3);
    }
  } else if (scenario == "gain") {
    radio.retain(0x0736); radio.regs[0x0736] = 0xAA;
    for (unsigned i = 0; i < 20; ++i) {
      const bool gain = i % 2;
      assert(radio.setRxBoostedGainMode(gain) == 0 && !radio.last_gain_persist);
      radio.sleep(true);
      assert(radio.regs[0x08B5] == 5 && radio.regs[0x08AC] == (gain ? 0x96 : 0x94));
      assert(radio.regs[0x0736] == 0xAA && radio.regs[0x029F] == 3);
    }
  } else if (scenario == "reset") {
    radio.retain(0x0736); radio.regs[0x0736] = 0xAA;
    SX126x* base = &radio; // Same static type as the production reset helper.
    for (unsigned i = 0; i < 20; ++i) {
      sx126xResetAGC(base, bool(i % 2));
      assert(!radio.last_gain_persist && radio.regs[0x029F] == 3);
      radio.sleep(true);
      assert(radio.regs[0x08B5] == 5 && radio.regs[0x0736] == 0xAA);
      assert(radio.regs[0x08AC] == (i % 2 ? 0x96 : 0x94));
    }
  } else if (scenario == "capacity") {
    radio.retain(0x0736); radio.retain(0x08AC); radio.retain(0x08B5); radio.retain(0x0740);
    assert(sx126xApplyRetainedAgcPatch(&radio) == 0 && radio.regs[0x029F] == 4);
    Custom full; full.retain(0x0736); full.retain(0x0740); full.retain(0x0741); full.retain(0x08AC);
    assert(sx126xApplyRetainedAgcPatch(&full) != 0 && full.writes == 0);
    Custom partial; partial.retain(0x0736); partial.retain(0x0740); partial.retain(0x0741);
    assert(sx126xApplyRetainedAgcPatch(&partial) != 0 && partial.writes == 0);
    assert(partial.regs[0x029F] == 3);
    Custom malformed; malformed.regs[0x029F] = 0xFF;
    assert(sx126xApplyRetainedAgcPatch(&malformed) != 0 && malformed.writes == 0);
  } else if (scenario == "failures") {
    Custom control; assert(sx126xApplyRetainedAgcPatch(&control) == 0);
    for (unsigned i = 1; i <= control.operations; ++i) {
      Custom failed; failed.fail_at = i;
      assert(sx126xApplyRetainedAgcPatch(&failed) == -77);
      assert(failed.operations == i); // No writes based on failed reads.
    }
    for (uint16_t address : {0x029F, 0x08B5}) {
      Custom failed; failed.drop_write = address;
      assert(sx126xApplyRetainedAgcPatch(&failed) != 0);
    }
    Custom gain_failed; gain_failed.fail_at = 1;
    assert(gain_failed.setRxBoostedGainMode(true) == -77 && gain_failed.operations == 1);
    Custom reset_failed; reset_failed.fail_at = 2; // gain write, then patch read
    sx126xResetAGC(static_cast<SX126x*>(&reset_failed), true);
    assert(reset_failed.writes == 1 && reset_failed.operations == 2);
  } else if (scenario == "patch_only") {
    assert(sx126xApplyRetainedAgcPatch(&radio, false) == 0);
    assert(radio.contains(0x08B5) && !radio.contains(0x08AC));
    radio.sleep(true); assert(radio.regs[0x08B5] == 5);
  } else { assert(false); }
  std::cout << "PASS " << scenario << '\n';
}
