// RAM-only HIL firmware. No Mesh, network, identity, settings, or autonomous TX.
// Explicit host-requested low-power packet probes are provided for validation.
// Uses the production CustomSX1262 + RadioLibWrapper tuneProfile implementation.
#include <Arduino.h>
#include <SPI.h>
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include <helpers/radiolib/ESP32BufferedRadioHal.h>
#include "profile_switch_experiments.h"
#include "ProfileChannelTrace.h"
#include "ProfileFrequencyOffset.h"
#ifdef HIL_HELTEC_V4
#include "ProfileHeltecV4.h"
ProfileHeltecV4 v4FrontEnd;
#endif
#ifdef HIL_INDICATOR
#include "IndicatorRadioHal.h"
#endif

class BenchBoard : public mesh::MainBoard {
 public:
  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override {
#ifdef HIL_INDICATOR
    return "Indicator timing bench";
#elif defined(HIL_HELTEC_V4)
    return "Heltec V4 timing bench";
#else
    return "XIAO S3 WIO timing bench";
#endif
  }
  void reboot() override { ESP.restart(); }
  uint8_t getStartupReason() const override { return 0; }
} board;
SPIClass radioSpi;
#ifdef HIL_INDICATOR
using BenchHalBase = IndicatorRadioHal;
#else
using BenchHalBase = ESP32BufferedRadioHal;
#endif
class BenchHal : public BenchHalBase {
 public:
  bool bulkTransfer = false;
  using BenchHalBase::BenchHalBase;
  void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
    // ESP32-only experiment: same full-duplex bytes, CS and transaction scope.
    // Module still performs its original BUSY and command-status checks.
    uint8_t shifted[5];
    const uint8_t* wire=hilFrequencyCommand(out,len,shifted);
    if (bulkTransfer) ESP32BufferedRadioHal::spiTransfer(const_cast<uint8_t*>(wire), len, in);
    else ArduinoHal::spiTransfer(const_cast<uint8_t*>(wire), len, in);
    channelTrace.observe(wire,len,in);
  }
} radioHal(radioSpi);
ExperimentalSX1262 chip = new Module(&radioHal, P_LORA_NSS, P_LORA_DIO_1,
                                     P_LORA_RESET, P_LORA_BUSY);
int16_t benchTransmit(uint8_t* data, size_t len) {
#ifdef HIL_HELTEC_V4
  v4FrontEnd.beforeTransmit();
#endif
  const int16_t rc = chip.transmit(data, len);
#ifdef HIL_HELTEC_V4
  v4FrontEnd.afterTransmit();  // Restore the RF path even when transmit fails.
#endif
  return rc;
}
class BenchWrapper : public CustomSX1262Wrapper {
 public:
  bool batched = true;
  bool forceModulationWrite = false;
  bool warmStandby = true; // HIL rollback only; production wrapper unchanged
  unsigned hopPasses = 1; // HIL-only full retune repetition
  bool firstPassDetour=false;
  uint32_t firstPassOffsetSteps=10; // 10 Hz request -> 10 steps; 100 Hz -> 105
  uint32_t detourChecks=0,detourErrors=0,detourFirstRf=0,detourFinalRf=0;
  uint32_t detourFirstMod=0,detourFinalMod=0;
  uint32_t lastFirstPassUs=0,lastSecondPassUs=0,secondPassBlocked=0;
  using CustomSX1262Wrapper::CustomSX1262Wrapper;
  uint32_t receiveStartedUs() const { return _profile_visit_us; }
  mesh::RadioParamApplyResult hop(uint8_t target) {
    const bool detour=firstPassDetour && hopPasses==2;
    const auto desired=_profiles.params(target);
    auto setTarget=[&](const mesh::RadioProfileParams& p) {
      if(target==0) _profiles.setPrimary(p,true);
      else { auto secondary=_profiles.secondary;secondary.params=p;_profiles.setSecondary(secondary,true); }
    };
    if(detour) {
      // Use integer RF-word steps, not a float-MHz addition. The scoped HAL
      // detour applies the selected offset at CR4/6, then restores zero.
      auto temporary=desired;temporary.cr=6;
      setTarget(temporary);
    }
    lastFirstPassUs=lastSecondPassUs=0;
    const uint32_t firstStarted=micros();
    chip.beginHop(isInRecvMode());
    const auto result = tuneProfile(target);
    chip.endHop();
    if(detour) {
      detourFirstRf=channelTrace.rfWord;detourFirstMod=channelTrace.modulationWord;
      // Restore intended configuration even when pass one defers or fails.
      // Hardware rollback and RX guards remain tuneProfile's responsibility.
      setTarget(desired);
    }
    lastFirstPassUs=micros()-firstStarted;
    if(result!=mesh::RadioParamApplyResult::APPLIED || hopPasses==1) return result;
    const uint32_t busyStarted=micros();
    while(chip.isChipBusy()) {
      if(uint32_t(micros()-busyStarted)>=20000) return mesh::RadioParamApplyResult::FAILED;
    }
    lastFirstPassUs=micros()-firstStarted;
    // Force an actual second transaction, not tuneProfile's same-generation
    // no-op. Normal ownership/packet/BUSY guards apply independently again.
    _profile_refresh_required=true;
    const uint32_t secondStarted=micros();
    chip.beginHop(isInRecvMode());
    const auto second=tuneProfile(target);
    chip.endHop();
    lastSecondPassUs=micros()-secondStarted;
    if(second==mesh::RadioParamApplyResult::BUSY) {
      // The first pass already changed channels. Do not tell the scheduler
      // "BUSY/no change" and mislabel subsequent RX as the old channel.
      // Abort this diagnostic rather than override a newly acquired packet.
      ++secondPassBlocked;
      return mesh::RadioParamApplyResult::FAILED;
    }
    if(detour && second==mesh::RadioParamApplyResult::APPLIED) {
      detourFinalRf=channelTrace.rfWord;detourFinalMod=channelTrace.modulationWord;
      ++detourChecks;
      // This mode is bounded to SF10/BW125, with CR4/6 then CR4/5, LDRO off.
      // Verify the selected integer offset and exact nominal correction.
      const uint32_t intendedRf=uint32_t(double(desired.freq)*1048576.0);
      if(detourFirstRf!=intendedRf+firstPassOffsetSteps || detourFinalRf!=intendedRf
          || detourFirstMod!=0x0a040200 || detourFinalMod!=0x0a040100) {
        ++detourErrors;return mesh::RadioParamApplyResult::FAILED;
      }
    }
    return second;
  }
  void loop() override {
#ifdef HIL_INDICATOR
    radioHal.serviceInterrupt();
#endif
    CustomSX1262Wrapper::loop();
  }
 protected:
  void setProfileStandbyWarm(bool enabled) override {
    CustomSX1262Wrapper::setProfileStandbyWarm(enabled && warmStandby);
  }
  void beginProfileRetune(bool continuousRx) override {
    CustomSX1262Wrapper::beginProfileRetune(continuousRx);
    if (forceModulationWrite || hopPasses==2) chip.hilInvalidateModulation();
  }
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    // Scoped over only this apply; rollback to CR4/5 and pass two use no
    // offset, while rollback to the temporary CR4/6 tuple retains its offset.
    HilFrequencyOffsetScope offset(firstPassDetour && hopPasses==2 && cr==6 ? firstPassOffsetSteps : 0);
    if (batched) return CustomSX1262Wrapper::applyParams(freq, bw, sf, cr);
    // Exact pre-batching baseline. Only this modulation path changes in A/B;
    // transition guards, frequency, preamble, RX restart and oscillator match.
    bool success = chip.setFrequency(freq) == RADIOLIB_ERR_NONE
        && chip.setSpreadingFactor(sf) == RADIOLIB_ERR_NONE
        && chip.setBandwidth(bw) == RADIOLIB_ERR_NONE
        && chip.setCodingRate(cr) == RADIOLIB_ERR_NONE
        && updatePreamble(sf, bw);
    if (!success) return false;
    PacketMillis pm = calcMaxPacketMillis(sf, bw, cr, preambleLengthForParams(sf, bw));
    chip.setPreambleMillis(pm.preambleMillis);
    chip.setMaxPayloadMillis(pm.payloadMillis);
    return true;
  }
} driver(chip, board);

#include "ProfileSwitchUsb.h"

struct Timing {
  uint32_t n = 0, lo = UINT32_MAX, hi = 0;
  uint64_t sum = 0;
  void add(uint32_t us) { ++n; lo = min(lo, us); hi = max(hi, us); sum += us; }
  void print(const char* name) const {
    appendBenchResult(lastRunResult,"\"%s\":{\"n\":%u,\"min_us\":%u,\"mean_us\":%.3f,\"max_us\":%u}",
                  name, n, n ? lo : 0, n ? double(sum) / n : 0, hi);
  }
};
bool ready = false;
int readBenchRxGain() {
  uint8_t gain=0;
  return chip.readRegister(RADIOLIB_SX126X_REG_RX_GAIN,&gain,1)==RADIOLIB_ERR_NONE ? gain : -1;
}

bool waitBusy() {
  const uint32_t start = micros();
  while (chip.isChipBusy()) {
    if (uint32_t(micros() - start) > 20000) return false;
  }
  return true;
}

void drain() {
  uint8_t packet[256];
  driver.recvRaw(packet, sizeof(packet));
  driver.onReceiveProcessed();
}

void run(bool warm, int sf, unsigned count, bool batched, unsigned mask, unsigned spiMHz, unsigned sequence) {
  if (!ready || !waitBusy()) { Serial.println("{\"error\":\"not ready\"}"); return; }
  chip.experiment = mask & 255;
  chip.productionPath = chip.experiment == 0;
  chip.setProfileSwitchOptimization((mask & 512) != 0);
  radioHal.bulkTransfer = (mask & 256) != 0;
  radioHal.spiSettings = SPISettings(spiMHz * 1000000, MSBFIRST, SPI_MODE0);
  driver.batched = batched;
  drain();
  mesh::RadioProfileParams primary;
  primary.freq = 909.5f; primary.bw = 62.5f; primary.sf = 7; primary.cr = 5;
  if (driver.trySetPrimaryParams(primary, true) != mesh::RadioParamApplyResult::APPLIED) {
    Serial.println("{\"error\":\"primary rejected\"}"); return;
  }
  mesh::RadioProfileConfig secondary;
  secondary.params.freq = 910.5f; secondary.params.bw = 500;
  secondary.params.sf = sf; secondary.params.cr = 5;
  secondary.mode = mesh::RadioProfileMode::Rx;
  driver.profiles()->setSecondary(secondary, true);
  if (!waitBusy()) { Serial.println("{\"error\":\"startup busy timeout\"}"); return; }
  driver.loop();  // Exercise production dual-profile entry and oscillator hook.
  if (!chip.standbyXOSC) { Serial.println("{\"error\":\"warm hook not enabled\"}"); return; }
  if (!waitBusy()) { Serial.println("{\"error\":\"entry busy timeout\"}"); return; }

  // Only the baseline changes the policy after production mode entry. All
  // retunes, modulation setters, packet guards and RX startup stay production.
  chip.standbyXOSC = warm;
  Timing api[2], busy[2], total[2];
  uint32_t skipped = 0, failed = 0, modeErrors = 0, cacheErrors = 0, timeouts = 0, completed = 0;
  const uint32_t start = millis();
  const uint32_t fastStart = chip.getOptimizedProfileSwitches();
  for (unsigned attempt = 0; completed < count + 8 && attempt < count * 4 + 32; ++attempt) {
    if (uint32_t(millis() - start) > 30000) break;
    drain();
    if (!waitBusy()) { ++timeouts; break; }
    // Use the production visit duration, but keep host I/O and status reads
    // out of the measured hop. No cooperative loop/UI/network work in timing.
    const auto active = driver.receiveProfile();
    const auto dwell = driver.profiles()->listenUs(active,
        driver.profilePreamble(driver.profiles()->slowerProfile()));
    delay(dwell / 1000);
    delayMicroseconds(dwell % 1000);
    const uint8_t target = active ^ 1;
    const uint32_t t0 = micros();
    const auto result = driver.hop(target);
    const uint32_t t1 = micros();
    if (result == mesh::RadioParamApplyResult::BUSY) { ++skipped; continue; }
    if (result != mesh::RadioParamApplyResult::APPLIED) { ++failed; break; }
    if (!waitBusy()) { ++timeouts; break; }
    const uint32_t t2 = micros();
    if (sx126xReceiveMode(&chip) != 1) ++modeErrors;
    const auto& expected = driver.profiles()->params(target);
    if (chip.spreadingFactor != expected.sf || chip.bandwidthKhz != expected.bw
        || chip.codingRate != expected.cr - 4) ++cacheErrors;
    if (completed++ < 8) continue;  // first cold/warm transition is not steady-state
    api[target].add(t1 - t0); busy[target].add(t2 - t1); total[target].add(t2 - t0);
  }
  lastRunResult.sequence=sequence;
  lastRunResult.text[0]=0;
  appendBenchResult(lastRunResult,"{\"result\":%u,\"warm\":%s,\"batched\":%s,\"mask\":%u,\"spi_mhz\":%u,\"sf500\":%d,\"requested\":%u,"
                "\"skipped_busy\":%u,\"failures\":%u,\"busy_timeouts\":%u,\"rx_mode_errors\":%u,"
                "\"cache_errors\":%u,\"tcxo_delay_us\":%u,\"optimized_rx_resumes\":%u,\"directions\":[",
                sequence,warm ? "true" : "false", batched ? "true" : "false", mask, spiMHz, sf, count,
                skipped, failed, timeouts, modeErrors, cacheErrors,
                unsigned(chip.tcxoDelay),unsigned(chip.getOptimizedProfileSwitches()-fastStart));
  for (int i = 0; i < 2; ++i) {
    if (i) appendBenchResult(lastRunResult,",");
    appendBenchResult(lastRunResult,"{\"to_profile\":%d,", i);
    api[i].print("api"); appendBenchResult(lastRunResult,","); busy[i].print("busy_tail"); appendBenchResult(lastRunResult,",");
    total[i].print("total"); appendBenchResult(lastRunResult,"}");
  }
  // Restore the production flag before exercising its normal off transition.
  chip.standbyXOSC = true;
  secondary.mode = mesh::RadioProfileMode::Off;
  driver.profiles()->setSecondary(secondary, true);
  const uint32_t exitStart = millis();
  while (chip.standbyXOSC && uint32_t(millis() - exitStart) < 1000) {
    drain();
    if (!waitBusy()) break;
    driver.loop();
    delay(1);
  }
  appendBenchResult(lastRunResult,"],\"off_restored_rc\":%s}\n", chip.standbyXOSC ? "false" : "true");
  if (lastRunResult.sequence) emitBenchResult(lastRunResult);
  else Serial.println("{\"error\":\"result overflow\"}");
}

#include "profile_switch_packets.h"
#include "profile_switch_lifecycle.h"
#include "profile_switch_channels.h"
#include "profile_preamble_diagnostic.h"
#include "profile_stationary_baseline.h"

void setup() {
#ifndef HIL_INDICATOR
  Serial.setTxBufferSize(4096);
  Serial.setTxTimeoutMs(250);
#endif
  Serial.begin(115200);
  delay(1500);
#ifdef HIL_HELTEC_V4
  v4FrontEnd.begin();  // Power/select FEM before the radio's initial calibration.
#endif
#ifdef HIL_INDICATOR
  if (!radioHal.beginExpander()) { Serial.println("{\"error\":\"expander unavailable\"}"); return; }
#endif
  ready = chip.std_init(&radioSpi);
  if (ready) {
    driver.begin();
    ready = driver.setRxPowerSaving(false, 65625, 60000);
    drain();
    ready = ready && waitBusy();
  }
  Serial.printf("{\"ready\":%s,\"bench\":\"production-profile-switch-v8\",\"autonomous_tx\":false}\n",
                ready ? "true" : "false");
}
void loop() {
  static char line[80];
  static size_t used = 0;
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (!used) continue;
      line[used] = 0; used = 0;
      int warm, sf, batched, power; unsigned count, mask, spiMHz, target, seq, len, preamble, bulk, dwell, trace, scanSf, scanBw, freqKhz;
      char resultKind[12];
      if (!strcmp(line,"pairinfo")) {
        Serial.println(pairInfo);
      } else if (sscanf(line,"basepair %u %u",&target,&seq)==2 && target<2 && seq) {
        channelStepKhz=1000;
        startStationaryBaseline(target,pairProfiles[target].sf,seq,pairProfiles[target].bw);
      } else if (sscanf(line,"pairstart %u %u %u",&seq,&count,&dwell)==3 && seq
          && pairPlanSupported(count,dwell)) {
        if(stationaryBaseline.active || channelSweep.active || preambleDiagnostic.active) {
          Serial.println("{\"error\":\"reboot before pair scan\"}");
        } else {
          channelPair=true;pairLoopUs=count;pairSlowExtraUs=dwell;channelStepKhz=1000;
          startChannelSweep(2,32,seq,pairListenUs(0,pairLoopUs,pairSlowExtraUs),false,8,500);
        }
      } else if (!strcmp(line,"mixinfo")) {
        Serial.println(mixedChannelInfo);
      } else if (!strcmp(line,"retuneinfo")) {
        Serial.println("{\"full_retune_repeat\":1,\"max_passes\":2,\"guarded\":true}");
      } else if (!strcmp(line,"continueinfo")) {
        Serial.printf("{\"fixed_sample_scan\":1,\"timeout_ms\":10000,\"retune_on_miss\":false}\n");
      } else if (!strcmp(line,"detourinfo")) {
        // Trailing whitespace avoids exact 64/128-byte CDC reply boundaries.
        Serial.printf("{\"first_pass_detour\":1,\"requested_offset_khz\":%.2f,\"rf_offset_steps\":%u,\"offset_hz\":%.14f,\"first_cr\":6,\"final_cr\":5} \n",
            driver.firstPassOffsetSteps==105?0.10:0.01,unsigned(driver.firstPassOffsetSteps),
            double(driver.firstPassOffsetSteps)*32000000.0/33554432.0);
      } else if (sscanf(line,"basemixed %u %u",&target,&seq)==2 && target<4 && seq) {
        const auto& p=mixedChannelProfiles[target];
        startStationaryBaseline(target,p.sf,seq,p.bwKhz);
      } else if (sscanf(line,"basestart %u %u %u",&target,&scanSf,&seq)==3 && target<4 && scanSf>=5 && scanSf<=10 && seq) {
        startStationaryBaseline(target,scanSf,seq);
      } else if (sscanf(line,"baseexpectch %u %u",&target,&seq)==2 && target<4 && seq) {
        expectStationaryPacket(seq,target);
      } else if (sscanf(line,"baseexpect %u",&seq)==1 && seq) {
        expectStationaryPacket(seq);
      } else if (sscanf(line,"basestatus %u",&seq)==1 && seq) {
        stationaryBaselineStatus(seq);
      } else if (!strcmp(line,"basestop")) {
        stopStationaryBaseline();
      } else if (stationaryBaseline.active && strcmp(line,"info") && strncmp(line,"result ",7) && strcmp(line,"reboot")) {
        Serial.println("{\"error\":\"stop stationary baseline before other operations\"}");
      } else if (sscanf(line,"diaglisten %u %u %u %u",&scanSf,&freqKhz,&seq,&count)==4) {
        if((scanSf==6 || scanSf==8) && freqKhz>=909000 && freqKhz<=911000 && seq && count>=200 && count<=1000)
          diagnosticListen(scanSf,freqKhz,seq,count);
        else Serial.println("{\"error\":\"invalid diagnostic receiver parameters\"}");
      } else if (sscanf(line,"diagtxsetup %u %u %u",&scanSf,&freqKhz,&seq)==3) {
        if((scanSf==6 || scanSf==8) && freqKhz>=909000 && freqKhz<=911000 && seq)
          diagnosticTxSetup(scanSf,freqKhz,seq);
        else Serial.println("{\"error\":\"invalid diagnostic transmitter parameters\"}");
      } else if (sscanf(line,"diagtx %u",&seq)==1 && seq) {
        diagnosticTransmit(seq);
      } else if (!strcmp(line,"diagstop")) {
        stopPreambleDiagnostic();
      } else if (preambleDiagnostic.active && strcmp(line,"info") && strncmp(line,"result ",7) && strcmp(line,"reboot")) {
        Serial.println("{\"error\":\"stop diagnostic receiver before another operation\"}");
      } else if (sscanf(line,"scanfirstdetour %u",&count)==1 && count<=1) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before detour policy\"}");
        else { driver.firstPassDetour=count;Serial.printf("{\"first_pass_detour\":%s}\n",count?"true":"false"); }
      } else if (sscanf(line,"scandetouroffset %u",&count)==1 && (count==10 || count==100)) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before detour offset\"}");
        else { driver.firstPassOffsetSteps=count==100?105:10;Serial.printf("{\"requested_offset_hz\":%u}\n",count); }
      } else if (sscanf(line,"scanretunepasses %u",&count)==1 && (count==1 || count==2)) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before full retune policy\"}");
        else { driver.hopPasses=count;Serial.printf("{\"retune_passes\":%u}\n",count); }
      } else if (sscanf(line,"scanmixed %u",&count)==1 && count<=1) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before modulation plan\"}");
        else { channelMixed=count;Serial.printf("{\"mixed_profiles\":%s}\n",count?"true":"false"); }
      } else if (sscanf(line,"scanacceptoffchannel %u",&count)==1 && count<=1) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before acceptance policy\"}");
        else { channelAcceptOffChannel=count;Serial.printf("{\"accept_offchannel\":%s}\n",count?"true":"false"); }
      } else if (sscanf(line,"scancontinue %u",&count)==1 && count<=1) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before continuation policy\"}");
        else { channelContinueOnMiss=count;Serial.printf("{\"continue_on_miss\":%s}\n",count?"true":"false"); }
      } else if (sscanf(line,"scanstep %u",&count)==1 && (count==250 || count==1000)) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before spacing policy\"}");
        else { channelStepKhz=count;Serial.printf("{\"channel_step_khz\":%u}\n",count); }
      } else if (sscanf(line,"scantcxo %u",&count)==1 && (count==1600 || count==6000)) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before TCXO policy\"}");
        else { channelTcxoUs=count;Serial.printf("{\"tcxo_us\":%u}\n",count); }
      } else if (sscanf(line,"scanrollback %u",&count)==1 && count<=15) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before rollback policy\"}");
        else { channelRollback=count;Serial.printf("{\"rollback\":%u}\n",count); }
      } else if (sscanf(line,"scansettle %u",&count)==1 && count<=24000) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before changing settling delay\"}");
        else { channelSettleUs=count;Serial.printf("{\"settle_us\":%u}\n",count); }
      } else if (sscanf(line,"scanmodcache %u",&count)==1 && count<=1) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before changing cache policy\"}");
        else { driver.forceModulationWrite=!count;Serial.printf("{\"modulation_cache\":%s}\n",count?"true":"false"); }
      } else if (sscanf(line,"scanfreqrepeat %u",&count)==1 && count<=1) {
        if(channelSweep.active) Serial.println("{\"error\":\"stop scanner before changing frequency policy\"}");
        else { chip.repeatFrequency=count;Serial.printf("{\"frequency_repeat\":%s}\n",count?"true":"false"); }
      } else if (sscanf(line,"scanstart %u %u %u %u %u %u %u",&count,&preamble,&seq,&dwell,&trace,&scanSf,&scanBw)==7) {
        if(count>=4 && count<=64 && preamble>=12 && preamble<=256 && seq && trace<=1
            && (scanSf>=5 && scanSf<=10) && (scanBw==125 || scanBw==250)
            && dwell>=4*(1u<<scanSf)*1000/scanBw && dwell<=32*(1u<<scanSf)*1000/scanBw)
          startChannelSweep(count,preamble,seq,dwell,trace,scanSf,scanBw);
        else Serial.println("{\"error\":\"invalid scan SF/BW parameters\"}");
      } else if (sscanf(line,"scanstart %u %u %u %u %u %u",&count,&preamble,&seq,&dwell,&trace,&scanSf)==6) {
        if(count>=4 && count<=64 && preamble>=12 && preamble<=256 && seq
            && (scanSf==6 || scanSf==8) && dwell>=2048 && dwell<=65536 && trace<=1)
          startChannelSweep(count,preamble,seq,dwell,trace,scanSf);
        else Serial.println("{\"error\":\"invalid scan parameters\"}");
      } else if (sscanf(line,"scanstart %u %u %u %u %u",&count,&preamble,&seq,&dwell,&trace)==5
          && count>=4 && count<=64 && preamble>=12 && preamble<=256 && seq
          && dwell>=2048 && dwell<=16384 && trace<=1) {
        startChannelSweep(count,preamble,seq,dwell,trace);
      } else if (sscanf(line,"scanstart %u %u %u",&count,&preamble,&seq)==3
          && count>=4 && count<=64 && preamble>=12 && preamble<=256 && seq) {
        startChannelSweep(count,preamble,seq);
      } else if (sscanf(line,"scanexpect %u %u",&target,&seq)==2 && seq) {
        expectChannelPacket(target,seq);
      } else if (sscanf(line,"scantx %u %u %u %u %u %u",&target,&seq,&len,&preamble,&scanSf,&scanBw)==6) {
        if(target<64 && seq && len>=12 && len<=255 && preamble>=12 && preamble<=256
            && (scanSf>=5 && scanSf<=10) && (scanBw==125 || scanBw==250))
          sendChannelPacket(target,seq,len,preamble,scanSf,scanBw);
        else Serial.println("{\"error\":\"invalid scan TX SF/BW parameters\"}");
      } else if (sscanf(line,"scantx %u %u %u %u %u",&target,&seq,&len,&preamble,&scanSf)==5) {
        if(target<64 && seq && len>=12 && len<=255 && preamble>=12 && preamble<=256
            && (scanSf==6 || scanSf==8)) sendChannelPacket(target,seq,len,preamble,scanSf);
        else Serial.println("{\"error\":\"invalid scan TX parameters\"}");
      } else if (sscanf(line,"scantx %u %u %u %u",&target,&seq,&len,&preamble)==4
          && target<64 && seq && len>=12 && len<=255 && preamble>=12 && preamble<=256) {
        sendChannelPacket(target,seq,len,preamble);
      } else if (sscanf(line,"scanstatus %u",&seq)==1 && seq) {
        channelSweepStatus(seq);
      } else if (sscanf(line,"scantrace %u %u",&seq,&target)==2 && seq) {
        channelSweepTrace(seq,target);
      } else if (!strcmp(line,"scanstop")) {
        stopChannelSweep();
      } else if (channelSweep.active && strcmp(line,"info") && strncmp(line,"result ",7) && strcmp(line,"reboot")) {
        Serial.println("{\"error\":\"stop channel sweep before other experiments\"}");
      } else if (sscanf(line, "run %d %d %u %d %u %u %u", &warm, &sf, &count, &batched, &mask, &spiMHz, &seq) == 7 && seq
          && (warm == 0 || warm == 1) && (batched == 0 || batched == 1)
          && mask <= 1023 && (spiMHz == 2 || spiMHz == 4 || spiMHz == 8)
          && sf >= 7 && sf <= 9 && count >= 16 && count <= 1000) {
        packetListening=false;
        run(warm, sf, count, batched, mask, spiMHz, seq);
      } else if (sscanf(line,"listen %d %u %u %u %u",&sf,&target,&seq,&mask,&spiMHz)==5
          && sf>=7 && sf<=9 && target<=1 && mask<=1023 && (spiMHz==2 || spiMHz==4 || spiMHz==8)) {
        listenPacket(sf,target,seq,mask,spiMHz);
      } else if (sscanf(line,"tx %d %u %u %u %u %d %u %u",&sf,&target,&seq,&len,&preamble,&power,&spiMHz,&bulk)==8
          && sf>=7 && sf<=9 && target<=1 && len>=12 && len<=255 && preamble>=8 && preamble<=256 && power>=-9 && power<=0
          && (spiMHz==2 || spiMHz==4 || spiMHz==8) && bulk<=1) {
        sendPacket(sf,target,seq,len,preamble,power,spiMHz,bulk);
      } else if (!strcmp(line, "lifecycle")) {
        runLifecycle();
      } else if (!strcmp(line, "reboot")) {
        ESP.restart();
      } else if (!strcmp(line, "info")) {
#ifdef HIL_HELTEC_V4
        Serial.printf("{\"ready\":%s,\"bench\":\"production-profile-switch-v8\",\"channel_sweep\":1,\"channel_trace\":2,\"channel_sf_select\":1,\"channel_bw_select\":1,\"channel_step_select\":1,\"payload_delivery_ab\":1,\"preamble_diagnostic\":1,\"modulation_cache_ab\":1,\"frequency_repeat_ab\":1,\"stationary_baseline\":1,\"stationary_offset\":1,\"rollback_ab\":1,\"rx_gain_reg\":%d,\"board\":\"Heltec V4\",\"fem\":\"%s\",\"tx_fem_mode\":\"%s\"}\n", ready ? "true" : "false",readBenchRxGain(),v4FrontEnd.type(),v4FrontEnd.txMode());
#else
        Serial.printf("{\"ready\":%s,\"bench\":\"production-profile-switch-v8\",\"channel_sweep\":1,\"channel_trace\":2,\"channel_sf_select\":1,\"channel_bw_select\":1,\"channel_step_select\":1,\"payload_delivery_ab\":1,\"preamble_diagnostic\":1,\"modulation_cache_ab\":1,\"frequency_repeat_ab\":1,\"stationary_baseline\":1,\"stationary_offset\":1,\"rollback_ab\":1,\"rx_gain_reg\":%d}\n", ready ? "true" : "false",readBenchRxGain());
#endif
      } else if (sscanf(line,"result %11s %u",resultKind,&seq)==2) {
        replayBenchResult(resultKind,seq);
      } else Serial.println("{\"error\":\"use run <warm> <SF> <count> <batched> <mask> <spi MHz> <sequence>\"}");
    } else if (used + 1 < sizeof(line)) line[used++] = c;
  }
  if (stationaryBaseline.active) {
    serviceStationaryBaseline();delay(0);
  } else if (preambleDiagnostic.active) {
    servicePreambleDiagnostic();delay(0);
  } else if (channelSweep.active) {
    serviceChannelSweep();
    // No millisecond sleep: it would quantize a 2.458 ms receive visit.
    // FreeRTOS still preempts normally and USB is serviced every iteration.
    delay(0);
  } else {
    servicePacketProbe();
    delay(1);
  }
}
