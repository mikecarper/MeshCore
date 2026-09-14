// RAM-only HIL firmware. No Mesh, network, identity, settings, or autonomous TX.
// Explicit host-requested low-power packet probes are provided for validation.
// Uses the production CustomSX1262 + RadioLibWrapper tuneProfile implementation.
#include <Arduino.h>
#include <SPI.h>
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include <helpers/radiolib/ESP32BufferedRadioHal.h>
#include "profile_switch_experiments.h"
#include "ProfileChannelTrace.h"
#ifdef HIL_INDICATOR
#include "IndicatorRadioHal.h"
#endif

class BenchBoard : public mesh::MainBoard {
 public:
  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override {
#ifdef HIL_INDICATOR
    return "Indicator timing bench";
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
    if (bulkTransfer) ESP32BufferedRadioHal::spiTransfer(out, len, in);
    else ArduinoHal::spiTransfer(out, len, in);
    channelTrace.observe(out,len,in);
  }
} radioHal(radioSpi);
ExperimentalSX1262 chip = new Module(&radioHal, P_LORA_NSS, P_LORA_DIO_1,
                                     P_LORA_RESET, P_LORA_BUSY);
class BenchWrapper : public CustomSX1262Wrapper {
 public:
  bool batched = true;
  using CustomSX1262Wrapper::CustomSX1262Wrapper;
  uint32_t receiveStartedUs() const { return _profile_visit_us; }
  mesh::RadioParamApplyResult hop(uint8_t target) {
    chip.beginHop(isInRecvMode());
    const auto result = tuneProfile(target);
    chip.endHop();
    return result;
  }
  void loop() override {
#ifdef HIL_INDICATOR
    radioHal.serviceInterrupt();
#endif
    CustomSX1262Wrapper::loop();
  }
 protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
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

void setup() {
#ifndef HIL_INDICATOR
  Serial.setTxBufferSize(4096);
  Serial.setTxTimeoutMs(250);
#endif
  Serial.begin(115200);
  delay(1500);
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
      int warm, sf, batched, power; unsigned count, mask, spiMHz, target, seq, len, preamble, bulk, dwell, trace, scanSf, freqKhz;
      char resultKind[12];
      if (sscanf(line,"diaglisten %u %u %u %u",&scanSf,&freqKhz,&seq,&count)==4) {
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
        Serial.printf("{\"ready\":%s,\"bench\":\"production-profile-switch-v8\",\"channel_sweep\":1,\"channel_trace\":2,\"channel_sf_select\":1,\"preamble_diagnostic\":1}\n", ready ? "true" : "false");
      } else if (sscanf(line,"result %11s %u",resultKind,&seq)==2) {
        replayBenchResult(resultKind,seq);
      } else Serial.println("{\"error\":\"use run <warm> <SF> <count> <batched> <mask> <spi MHz> <sequence>\"}");
    } else if (used + 1 < sizeof(line)) line[used++] = c;
  }
  if (preambleDiagnostic.active) {
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
