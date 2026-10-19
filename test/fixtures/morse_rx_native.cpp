#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <vector>
#include <helpers/radiolib/LR2021Band.h>
#include <helpers/radiolib/RadioRegulatorState.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_NC 0xffffffffU
#define RFSWITCH_PIN_FLAG 0x80U
#define RADIOLIB_LRXXXX_DIOx_VAL(pin) ((pin)&0x7fU)
#define RADIOLIB_LR11X0_RX_TIMEOUT_INF 0xffffffU
#define RADIOLIB_LR2021_RX_TIMEOUT_INF 0xffffffU
#define RADIOLIB_LR2021_DIO_SLEEP_PULL_UP 1
#define RADIOLIB_LR2021_DIO_SLEEP_PULL_AUTO 0
#define RADIOLIB_LR2021_DIO_FUNCTION_RF_SWITCH 6
#define RADIOLIB_SX126X_REG_RSSI_AVG_WINDOW 0x089b
#define SX127X_CURRENT_LIMIT 120
#define SX126X_CURRENT_LIMIT 120
#define SX126X_DIO2_AS_RF_SWITCH true
#define SX126X_RXEN 8
#define SX126X_TXEN 9
struct Hal { int GpioLevelHigh=1; };
struct Module {
  static constexpr size_t RFSWITCH_MAX_PINS=3;
  struct RfSwitchMode_t { uint8_t mode; int values[RFSWITCH_MAX_PINS]; };
  Hal storage; Hal* hal=&storage;
  int write_result=0; unsigned writes=0;
  int16_t SPIsetRegValue(int address,int value,int msb,int lsb) {
    constexpr int expected_window =
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL && defined(MESH_MORSE_HIL_RSSI_WINDOW)
      MESH_MORSE_HIL_RSSI_WINDOW << 2;
#else
      0;
#endif
    assert(address==RADIOLIB_SX126X_REG_RSSI_AVG_WINDOW && value==expected_window && msb==4 && lsb==2);
    ++writes; return write_result;
  }
};
struct LR11x0 { enum { MODE_STBY=1,MODE_RX=3,MODE_TX=4,MODE_END_OF_TABLE=0 }; };
#include <helpers/radiolib/LR11x0MorseRfSwitch.h>
#include <helpers/radiolib/SX126xMorseReceiver.h>
struct Native;
using SX126x=Native;
inline int16_t setSX126xMorseRssiWindow(SX126x*);
struct Native {
  Module module; Module* mod=&module;
  float tcxoVoltage=2.7f, frequency=0, bitrate=0,deviation=0,bandwidth=0;
  bool useRegulatorLDO=true,standbyXOSC=true,seen_standby_xosc=true,seen_ldo=false;
  bool rxBoostedGainMode=true;
  int power=0,preamble=0,fail=0; bool receiving=false;
  unsigned tx=0; int gain=-1,rssi_samples=-1;
  uint8_t syncWordLength=16,preambleDetLength=4;
  int crc_result=0,sync_result=0,gain_result=0;
  unsigned crc_calls=0,sync_calls=0;
  uint8_t enable=0,modes[7]={};
  std::vector<int> calls;
  int16_t call(int step) { calls.push_back(step); return step==fail ? -705 : 0; }
  Module* getMod() { return mod; }
  int16_t beginGFSK(float freq,float br,float dev,float bw,int8_t dbm,uint16_t pre,float tcxo) {
    assert(tcxo==tcxoVoltage);
    frequency=freq; bitrate=br; deviation=dev; bandwidth=bw; power=dbm; preamble=pre;
    enable=0; memset(modes,0,sizeof(modes));
    rxBoostedGainMode=true; // native modem reset restores its own default
    return call(1);
  }
  int16_t beginFSK(float freq,float br,float dev,float bw,int8_t dbm,uint16_t pre,bool ook) {
    assert(!ook); return beginGFSK(freq,br,dev,bw,dbm,pre,tcxoVoltage);
  }
  int16_t beginFSK(float freq,float br,float dev,float bw,int8_t dbm,uint16_t pre,float tcxo,bool ldo) {
    seen_standby_xosc=standbyXOSC; seen_ldo=ldo;
    return beginGFSK(freq,br,dev,bw,dbm,pre,tcxo);
  }
  int16_t applyMeshCoreTcxoDelay() { return call(2); }
  int16_t setRegulatorDCDC() { return call(3); }
  int16_t setRegulatorLDO() { return call(3); }
  int16_t setDioAsRfSwitch(uint8_t mask,uint8_t a,uint8_t b,uint8_t c,uint8_t d,uint8_t e,uint8_t f,uint8_t g) {
    enable=mask; const uint8_t values[]={a,b,c,d,e,f,g}; memcpy(modes,values,sizeof(modes));
    return call(4);
  }
  int16_t setRxBoostedGainMode(uint8_t value) {
    gain=value; rxBoostedGainMode=value;
    const auto status=call(5); return gain_result ? gain_result : status;
  }
  int16_t startReceive(uint32_t timeout,uint32_t a,uint32_t b,size_t c) {
    assert(timeout==0xffffffU && !a && !b && !c);
    const auto result=call(6); receiving=result==0; return result;
  }
  int16_t setCurrentLimit(int value) { assert(value==120); return call(2); }
  int16_t setDio2AsRfSwitch(bool enabled) { assert(enabled); return call(3); }
  void setRfSwitchPins(int rx,int txpin) { assert(rx==8 && txpin==9); }
  int16_t setSyncWord(uint8_t*,size_t length) {
    ++sync_calls;
    assert(!length); syncWordLength=preambleDetLength=0;
    return sync_result ? sync_result : call(4);
  }
  int16_t setCRC(uint8_t value) { ++crc_calls; assert(!value); return crc_result ? crc_result : call(5); }
  int16_t fixedPacketLengthMode(uint8_t length) { assert(length==255); return call(9); }
  @FAST_RSSI@
  int16_t startReceive() { const auto result=call(7); receiving=result==0; return result; }
  int16_t setRSSIConfig(uint8_t samples) { rssi_samples=samples; return call(6); }
  int16_t receiveDirect() { const auto result=call(7); receiving=result==0; return result; }
  int16_t setDioFunction(uint8_t dio,uint8_t function,uint8_t pull) {
    assert(dio>=5 && function==6 && (pull==0 || pull==1)); return call(4);
  }
  int16_t setDioRfSwitchConfig(uint8_t dio,uint8_t mask) {
    assert(dio>=5); enable|=1U << (dio-5); modes[dio-5]=mask; return call(4);
  }
};
#include <helpers/radiolib/SX126xMorseRssi.h>
using LR1110=Native; using LR1121=Native;
struct LR2021 : Native { enum { MODE_RX=3,MODE_RX_HF=6,MODE_END_OF_TABLE=0 }; };
using SX1276=Native;
struct LR1110Probe : LR1110 {
  @LR1110_REGULATOR@
  struct State { unsigned invalidations=0; void invalidate() { ++invalidations; } } _profileSwitch;
  bool _profilePacketDirty=true,_rx_boosted=true;
  LR11x0MorseRfSwitch _morse_rf_switch;
  @LR1110@
};
struct LR1121Probe : LR1121 {
  @LR1121_REGULATOR@
  bool _rx_boosted=true;
  LR11x0MorseRfSwitch _morse_rf_switch;
  @LR1121@
};
struct LR2021Probe : LR2021 {
  @LR2021_REGULATOR@
  uint8_t gainModeLf=1,gainModeHf=3;
  uint32_t _rf_switch_pins[Module::RFSWITCH_MAX_PINS]={0x80,0x81,RADIOLIB_NC};
  const Module::RfSwitchMode_t* _rf_switch_table=nullptr;
  @LR2021_RESTORE@
  @LR2021@
};
struct SX1276Probe : SX1276 { @SX1276@ };
static const uint32_t pins[]={0x80,0x81,7};
static const Module::RfSwitchMode_t table[]={
  {LR11x0::MODE_TX,{0,1,1}}, {LR11x0::MODE_RX,{1,0,0}},
  {LR11x0::MODE_STBY,{0,0,0}}, {LR11x0::MODE_END_OF_TABLE,{0,0,0}}};
template<typename T> static void lr_native() {
  for (int fault=0; fault<=5; ++fault) {
    T radio; radio.fail=fault;
    radio._morse_rf_switch.capture(radio.mod,pins,table);
    assert(radio.beginMorseReceive(902.1f)==(fault ? -705 : 0));
    assert(radio.frequency==902.1f && radio.bitrate==mesh::MorseRx::ReceiverBitRateKbps && radio.deviation==0.6f);
    assert(radio.bandwidth==4.8f && radio.power==2 && radio.preamble==16 && !radio.tx);
    if (!fault) {
      assert(!radio.receiving && radio.gain==1 && radio.enable==3);
      assert(!radio.syncWordLength && !radio.preambleDetLength);
      assert(radio.modes[LR11x0::MODE_RX-LR11x0::MODE_STBY]==1);
      assert(radio.modes[LR11x0::MODE_TX-LR11x0::MODE_STBY]==2);
    } else assert(!radio.receiving);
    // LR1121 has no TCXO-delay phase; its step2 injection is skipped.
  }
}
int main() {
  // Every checked SPI setup failure prevents receive admission and no path TXs.
  for (int fault : {0,1,3,4,5}) {
    LR1121Probe radio; radio.fail=fault;
    radio._morse_rf_switch.capture(radio.mod,pins,table);
    assert(radio.beginMorseReceive(902.1f)==(fault ? -705 : 0));
    assert(radio.bitrate==mesh::MorseRx::ReceiverBitRateKbps);
    assert(!radio.tx && !radio.receiving);
    if (!fault) assert(radio.enable==3 && radio.modes[2]==1 && radio.modes[3]==2);
  }
  lr_native<LR1110Probe>();
  LR1110Probe crc11; crc11.crc_result=-705;
  assert(crc11.beginMorseReceive(902.1f)==-705 && !crc11.receiving);
  LR1121Probe crc12; crc12.crc_result=-705;
  assert(crc12.beginMorseReceive(902.1f)==-705 && !crc12.receiving);
  LR1110Probe cache; cache.beginMorseReceive(902.1f);
  assert(!cache._profilePacketDirty && cache._profileSwitch.invalidations==1);
  for (float freq : {902.1f,2440.0f}) for (int fault : {0,1,4,5}) {
    LR2021Probe radio; radio.fail=fault; radio._rf_switch_table=table;
    assert(radio.beginMorseReceive(freq)==(fault ? -705 : 0));
    assert(radio.bitrate==mesh::MorseRx::ReceiverBitRateKbps);
    assert(radio.bandwidth==4.8f && !radio.receiving && !radio.tx);
    if (!fault) assert(radio.gain==(freq>1900 ? 3 : 1) && !radio.syncWordLength && !radio.preambleDetLength);
  }
  for (bool sync : {false,true}) {
    LR2021Probe failed;
    if (sync) failed.sync_result=-705; else failed.crc_result=-705;
    assert(failed.beginMorseReceive(902.1f)==-705 && !failed.receiving);
  }
  for (int fault : {0,1,2,6}) {
    SX1276Probe radio; radio.fail=fault;
    assert(radio.beginMorseReceive(902.1f)==(fault ? -705 : 0));
    assert(radio.bitrate==mesh::MorseRx::ReceiverBitRateKbps);
    assert(radio.bandwidth==2.6f && !radio.receiving && !radio.tx);
    if (!fault) assert(radio.rssi_samples==0);
  }
  for (bool gain : {false,true}) for (int fault : {0,1,2,3,4,5,8,9}) {
    Native radio; radio.fail=fault==8 ? 0 : fault;
    radio.rxBoostedGainMode=gain;
    if (fault==8) radio.module.write_result=-705;
    constexpr bool packet_gating=
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL && defined(MESH_MORSE_HIL_PACKET_GATING) && MESH_MORSE_HIL_PACKET_GATING
      true;
#else
      false;
#endif
    const bool failure=fault && !(packet_gating && fault==4);
    assert(prepareSX126xMorseReceiver(&radio,902.1f)==(failure ? -705 : 0));
    assert(!radio.seen_standby_xosc && radio.standbyXOSC && radio.seen_ldo);
    assert(radio.bandwidth==4.8f && radio.bitrate==mesh::MorseRx::ReceiverBitRateKbps && radio.deviation==0.6f && !radio.tx);
    assert(!radio.receiving);
    if (!failure) {
      assert(radio.module.writes==1 && radio.rxBoostedGainMode==gain && radio.gain==int(gain));
      assert(radio.sync_calls==unsigned(!packet_gating) && radio.crc_calls==unsigned(!packet_gating));
      assert(radio.syncWordLength==(packet_gating?16:0) && radio.preambleDetLength==(packet_gating?4:0));
      assert(radio.calls.back()==5); // native gain restoration is last, still before wrapper SetRX
    }
  }
  for(bool gain : {false,true}) {
    Native radio; radio.rxBoostedGainMode=gain; radio.gain_result=-705;
    assert(prepareSX126xMorseReceiver(&radio,902.1f)==-705);
    assert(radio.gain==int(gain) && radio.module.writes==1 && radio.calls.back()==5);
    assert(!radio.receiving && !radio.tx);
  }
  LR11x0MorseRfSwitch unconfigured;
  assert(unconfigured.restore([](uint8_t,const uint8_t*)->int16_t { assert(false); return -705; })==0);
}
