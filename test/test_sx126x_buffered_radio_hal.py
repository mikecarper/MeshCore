"""Execute portable radio SPI buffering, settings and bus ownership."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RADIO = ROOT / "src/helpers/radiolib"

MOCK = r'''
#pragma once
#include <cassert>
#include <cstddef>
#include <cstdint>
constexpr unsigned MSBFIRST=1, SPI_MODE0=0;
struct SPISettings {
  unsigned hz, order, mode;
  SPISettings(unsigned h, unsigned o, unsigned m) : hz(h), order(o), mode(m) {}
};
struct SPIClass {
  unsigned bursts=0, bytes=0, begins=0, ends=0;
  const void* lastOut=nullptr; void* lastIn=nullptr; size_t lastLength=0;
  void begin() { ++begins; }
  void end() { ++ends; }
  void burst(const void* out, void* in, size_t length) {
    ++bursts; lastOut=out; lastIn=in; lastLength=length;
    const auto* tx=static_cast<const uint8_t*>(out);
    auto* rx=static_cast<uint8_t*>(in);
    for (size_t i=0; i<length; ++i) rx[i]=tx[i]^0xa5;
  }
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  void transfer(const void* out, void* in, size_t length) { burst(out,in,length); }
#else
  void transferBytes(const uint8_t* out, uint8_t* in, size_t length) {
    burst(out,in,length);
  }
#endif
  uint8_t transfer(uint8_t value) { ++bytes; return value^0xa5; }
};
struct RadioLibHal { virtual ~RadioLibHal()=default; };
struct ArduinoHal : RadioLibHal {
  SPIClass* spi; SPISettings spiSettings; bool initInterface=false;
  explicit ArduinoHal(SPIClass& bus,SPISettings settings)
      : spi(&bus),spiSettings(settings) {}
  virtual ~ArduinoHal()=default;
  virtual void init() { if (initInterface) spi->begin(); }
  virtual void term() { if (initInterface) spi->end(); }
  virtual void spiTransfer(uint8_t* out,size_t length,uint8_t* in) {
    for (size_t i=0;i<length;++i) in[i]=spi->transfer(out[i]);
  }
};
'''

FIXTURE = r'''
#include <array>
#include <initializer_list>
#include <helpers/radiolib/SX126xBufferedRadioHal.h>
SPIClass unused, explicitBus, fallbackBus;
int main() {
  SX126xBufferedRadioHal external(explicitBus);
  SX126xBufferedRadioHal fallback(fallbackBus,MC_SX1262_SPI_HZ,true);
  SX126xBufferedRadioHal slower(explicitBus,4000000);
  assert(external.spiSettings.hz == MC_SX1262_SPI_HZ);
  assert(slower.spiSettings.hz == 4000000);
  assert(external.spiSettings.order == MSBFIRST && external.spiSettings.mode == SPI_MODE0);
  assert(!external.initInterface && fallback.initInterface);
  ArduinoHal& hal=external;
  hal.init(); hal.term();
  assert(explicitBus.begins==0 && explicitBus.ends==0);
  fallback.init(); fallback.term();
  assert(fallbackBus.begins==1 && fallbackBus.ends==1);
  // A board/storage handoff can restart this SAME bus without replacing HAL.
  explicitBus.end(); explicitBus.begin();
  assert(hal.spi == &explicitBus);
  std::array<uint8_t,260> tx{}, rx{};
  for (size_t i=0;i<tx.size();++i) tx[i]=uint8_t(i);
  const auto original=tx;
  for (size_t length : {size_t(1),size_t(2),size_t(7),size_t(260)}) {
    rx.fill(0x77);
    const unsigned before=explicitBus.bursts;
    hal.spiTransfer(tx.data(),length,rx.data());
    assert(explicitBus.bursts==before+1 && explicitBus.bytes==0);
    assert(explicitBus.lastOut==tx.data() && explicitBus.lastIn==rx.data());
    assert(explicitBus.lastLength==length && tx==original);
    for (size_t i=0;i<length;++i) assert(rx[i]==uint8_t(tx[i]^0xa5));
    for (size_t i=length;i<rx.size();++i) assert(rx[i]==0x77);
  }
  const unsigned before=explicitBus.bursts;
  hal.spiTransfer(nullptr,0,nullptr);
  assert(explicitBus.bursts==before && explicitBus.bytes==0);
  assert(unused.bursts==0 && fallbackBus.bursts==0);

  using Tuning=mesh::RadioSwitchTuning;
  using Capabilities=mesh::RadioSwitchCapabilities;
#if defined(ESP32_PLATFORM) && !defined(CONFIG_IDF_TARGET_ESP32C6)
  constexpr uint8_t spi9=Capabilities::Spi9;
#else
  constexpr uint8_t spi9=0;
#endif
  static_assert(sizeof(Tuning)==2 && sizeof(Capabilities)==2,"compact saved SPI policy");
  assert(TunableRadioHal::find(&hal)==&external);
  assert(external.defaultHz()==MC_SX1262_SPI_HZ);
  assert(external.effectiveHz()==MC_SX1262_SPI_HZ);
  assert(external.getTuning().spi_mhz==0);
  assert(external.getTuning().flags==Tuning::Buffer);
  assert(external.capabilities().flags==Tuning::Buffer);
  assert(external.capabilities().spi_clocks==(2|4|8|spi9));
  // Disable bulk transfers without changing the controller, bus lifetime or
  // packet response. Driver-owned feature bits are handled by the wrapper.
  Tuning tuning;
  tuning.flags=Tuning::Warm;
  tuning.spi_mhz=2;
  assert(external.setTuning(tuning));
  assert(external.getTuning().flags==0 && external.effectiveHz()==2000000);
  assert(external.spiSettings.hz==2000000 && hal.spi==&explicitBus);
  const unsigned bytes=explicitBus.bytes;
  rx.fill(0x77);
  hal.spiTransfer(tx.data(),7,rx.data());
  assert(explicitBus.bursts==before && explicitBus.bytes==bytes+7);
  for (unsigned i=0;i<7;++i) assert(rx[i]==uint8_t(tx[i]^0xa5));
  for (unsigned i=7;i<rx.size();++i) assert(rx[i]==0x77);
  for (uint8_t clock : {uint8_t(4),uint8_t(8)}) {
    tuning.spi_mhz=clock;
    assert(external.setTuning(tuning));
    assert(external.effectiveHz()==unsigned(clock)*1000000);
    assert(external.spiSettings.hz==external.effectiveHz());
  }
  const Tuning before9=external.getTuning();
  tuning.spi_mhz=9;
#if defined(ESP32_PLATFORM) && !defined(CONFIG_IDF_TARGET_ESP32C6)
  assert(external.setTuning(tuning));
  assert(external.getTuning().spi_mhz==9);
  assert(external.effectiveHz()==8888888 && external.spiSettings.hz==8888888);
#else
  assert(!external.setTuning(tuning));
  assert(external.getTuning()==before9 && external.effectiveHz()==8000000);
#endif
  assert(hal.spi==&explicitBus && explicitBus.begins==1 && explicitBus.ends==1);
  (void)before9;
  const Tuning previous=external.getTuning();
  for (uint8_t clock : {uint8_t(1),uint8_t(3),uint8_t(16),uint8_t(255)}) {
    tuning.spi_mhz=clock;
    assert(!external.setTuning(tuning) && external.getTuning()==previous);
  }
  tuning.spi_mhz=0;
  tuning.flags=Tuning::Buffer;
  assert(external.setTuning(tuning));
  assert(external.effectiveHz()==MC_SX1262_SPI_HZ);
  assert(external.spiSettings.hz==MC_SX1262_SPI_HZ);
  hal.spiTransfer(tx.data(),7,rx.data());
  assert(explicitBus.bursts==before+1 && explicitBus.bytes==bytes+7);
  assert(explicitBus.begins==1 && explicitBus.ends==1);

  // The plain nRF SPIM3 byte route exposes clock selection but never admits
  // the buffered path that needs a separately qualified controller mapping.
  {
    TunableRadioHal byteHal(unused);
    assert(TunableRadioHal::find(&byteHal)==&byteHal);
    assert(byteHal.capabilities().flags==0 && byteHal.defaultHz()==8000000);
    assert(byteHal.capabilities().spi_clocks==(2|4|8|spi9));
    assert(!byteHal.setTuning(tuning));
    tuning.flags=0;tuning.spi_mhz=2;
    assert(byteHal.setTuning(tuning) && byteHal.spiSettings.hz==2000000);
    const auto before=byteHal.getTuning();
    tuning.spi_mhz=9;
#if defined(ESP32_PLATFORM) && !defined(CONFIG_IDF_TARGET_ESP32C6)
    assert(byteHal.setTuning(tuning) && byteHal.spiSettings.hz==8888888);
#else
    assert(!byteHal.setTuning(tuning) && byteHal.getTuning()==before);
    assert(byteHal.spiSettings.hz==2000000);
#endif
    (void)before;
  }
  // Explicit maximums describe hardware capability rather than the current
  // default. Only ESP32 can generate the fractional-divider 9 alias, including
  // its byte HAL; granting 16 MHz on another MCU must not grant 9 implicitly.
  for (uint32_t maximum : {8000000U,8888887U,8888888U,16000000U}) {
    SPIClass shared;
    TunableRadioHal bounded(shared,4000000,false,false,maximum);
    const auto before=bounded.getTuning();
    tuning.flags=0;tuning.spi_mhz=9;
    const bool supports9=spi9 && maximum>=8888888;
    assert(bool(bounded.capabilities().spi_clocks & Capabilities::Spi9)==supports9);
    assert(bounded.setTuning(tuning)==supports9);
    if (supports9) {
      assert(bounded.getTuning().spi_mhz==9 && bounded.effectiveHz()==8888888);
      assert(bounded.spiSettings.hz==8888888);
    } else {
      assert(bounded.getTuning()==before && bounded.effectiveHz()==4000000);
      assert(bounded.spiSettings.hz==4000000);
    }
    assert(bool(bounded.capabilities().spi_clocks & 16)==(maximum>=16000000));
    tuning.spi_mhz=16;
    assert(bounded.setTuning(tuning)==(maximum>=16000000));
    assert(bounded.spi==&shared && !shared.begins && !shared.ends);
    assert(!shared.bursts && !shared.bytes);
  }
#if defined(ESP32_PLATFORM)
  {
    SPIClass lrBus;
    ESP32BufferedRadioHal lr1110(lrBus,16000000);
    assert(lr1110.defaultHz()==16000000 && lr1110.effectiveHz()==16000000);
    assert(lr1110.capabilities().spi_clocks==(2|4|8|16|spi9));
    tuning.flags=Tuning::Buffer;tuning.spi_mhz=9;
    assert(lr1110.setTuning(tuning)==bool(spi9));
    assert(lr1110.spiSettings.hz==(spi9 ? 8888888U : 16000000U));
    tuning.spi_mhz=0;
    assert(lr1110.setTuning(tuning) && lr1110.spiSettings.hz==16000000);
    assert(!lrBus.begins && !lrBus.ends);
  }
#endif
  ArduinoHal foreign(unused,SPISettings(2000000,MSBFIRST,SPI_MODE0));
  assert(TunableRadioHal::find(&foreign)==nullptr);
  assert(TunableRadioHal::find(nullptr)==nullptr);
  assert(TunableRadioHal::find(&external)==&external);
}
'''


class SX126xBufferedRadioHalTests(unittest.TestCase):
    def test_real_platform_buffers_forward_bytes_without_taking_explicit_bus_ownership(self):
        compiler=shutil.which(os.environ.get("CXX","g++"))
        self.assertIsNotNone(compiler)
        headers={name:(RADIO/name).read_text(encoding="ascii") for name in (
            "SX126xBufferedRadioHal.h","Nrf52BufferedRadioHal.h","ESP32BufferedRadioHal.h",
            "TunableRadioHal.h","RadioFastCode.h")}
        with tempfile.TemporaryDirectory(prefix="sx126x-buffered-radio-") as folder:
            base=Path(folder)
            (base/"hal/Arduino").mkdir(parents=True)
            (base/"helpers/radiolib").mkdir(parents=True)
            (base/"hal/Arduino/ArduinoHal.h").write_text(MOCK,encoding="ascii")
            (base/"helpers/RadioSwitchTuning.h").write_text(
                (ROOT/"src/helpers/RadioSwitchTuning.h").read_text(),encoding="ascii")
            cpp,executable=base/"test.cpp",base/"test"
            cpp.write_text(FIXTURE,encoding="ascii")
            for arch,defines,header in (
                    ("nrf52",("NRF52_PLATFORM",),"Nrf52BufferedRadioHal.h"),
                    ("esp32",("ESP32_PLATFORM",),"ESP32BufferedRadioHal.h"),
                    ("esp32c6",("ESP32_PLATFORM","CONFIG_IDF_TARGET_ESP32C6"),
                     "ESP32BufferedRadioHal.h"),
                    ("rp2040",("RP2040_PLATFORM",),"SX126xBufferedRadioHal.h")):
                # Without the real override, byte-for-byte correctness alone
                # still passes; the per-command transfer requirement must fail.
                legacy=re.sub(r"  (?:MC_RADIO_FAST_CODE_ATTR )?void spiTransfer\([^}]+\}\n","",headers[header])
                self.assertNotEqual(legacy,headers[header])
                for clock in (8000000,8888888):
                    for name,implementation,succeeds in (("buffered",headers[header],True),
                                                         ("per-byte",legacy,False)):
                        with self.subTest(arch=arch,clock=clock,implementation=name):
                            for file,content in headers.items():
                                (base/"helpers/radiolib"/file).write_text(
                                    implementation if file==header else content,encoding="ascii")
                            built=subprocess.run([compiler,"-std=c++17","-Wall","-Wextra",
                                "-Werror",*["-D"+value for value in defines],
                                f"-DMC_SX1262_SPI_HZ={clock}",
                                "-I",str(base),"-I",str(ROOT/"src"),"-I",str(RADIO),str(cpp),"-o",str(executable)],
                                capture_output=True,text=True,timeout=60)
                            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
                            ran=subprocess.run([str(executable)],capture_output=True,
                                               text=True,timeout=10)
                            if succeeds:
                                self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)
                            else:
                                self.assertNotEqual(ran.returncode,0,
                                    "per-byte negative control did not fail")

    def test_default_clock_matches_all_capable_spi_controllers(self):
        header=(RADIO/"SX126xBufferedRadioHal.h").read_text(encoding="ascii")
        self.assertIn("#define MC_SX1262_SPI_HZ 8000000",header)


if __name__ == "__main__":
    unittest.main()
