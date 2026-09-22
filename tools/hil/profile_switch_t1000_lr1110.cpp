// Application-only T1000-E LR1110 production-wrapper RX retune timing bench.
// RAM-only; no Mesh identity, saved settings, autonomous TX or TX command.
// RX-only test profiles are 909.5 and 909.75 MHz; never 910.525 MHz.
#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <SPI.h>
#include <helpers/radiolib/CustomLR1110Wrapper.h>
#include "ProfileSwitchUsb.h"

class BenchBoard : public mesh::MainBoard {
public:
  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override { return "Seeed T1000-E LR1110 timing bench"; }
  void reboot() override { NVIC_SystemReset(); }
  uint8_t getStartupReason() const override { return 0; }
} board;

class CountingHal : public ArduinoHal {
public:
  using ArduinoHal::ArduinoHal;
  bool bulkTransfer = true;
  uint32_t spiHz = 16000000;
  void setSpiHz(uint32_t hz) {
    spiHz = hz;
    spiSettings = SPISettings(hz, MSBFIRST, SPI_MODE0);
  }
  uint32_t rfWrites = 0, modulationWrites = 0, packetWrites = 0;
  uint32_t standbyWrites = 0, rxWrites = 0, dioWrites = 0, packetTypeReads = 0;
  uint32_t clearWrites = 0, fsWrites = 0;
  void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
    if (len >= 2 && out[0] == 2 && out[1] == 0x0B) ++rfWrites;
    if (len >= 2 && out[0] == 2 && out[1] == 0x0F) ++modulationWrites;
    if (len >= 2 && out[0] == 2 && out[1] == 0x10) ++packetWrites;
    if (len >= 2 && out[0] == 1 && out[1] == 0x1C) ++standbyWrites;
    if (len >= 2 && out[0] == 1 && out[1] == 0x14) ++clearWrites;
    if (len >= 2 && out[0] == 1 && out[1] == 0x1D) ++fsWrites;
    if (len >= 2 && out[0] == 2 && out[1] == 0x09) ++rxWrites;
    if (len >= 2 && out[0] == 1 && out[1] == 0x13) ++dioWrites;
    if (len >= 2 && out[0] == 2 && out[1] == 0x02) ++packetTypeReads;
    if (bulkTransfer) spi->transfer(out, in, len);
    else ArduinoHal::spiTransfer(out, len, in);
  }
} radioHal(SPI, SPISettings(16000000, MSBFIRST, SPI_MODE0));

class BenchLR1110 : public CustomLR1110 {
public:
  using CustomLR1110::CustomLR1110;
  uint16_t preambleSymbols() const { return preambleLengthLoRa; }
  uint64_t standbyCycles = 0, startRxCycles = 0;
  uint32_t standbyCount = 0, startRxCount = 0;
  int16_t standby() override {
    const uint32_t started = DWT->CYCCNT;
    const int16_t rc = CustomLR1110::standby();
    standbyCycles += DWT->CYCCNT - started;
    ++standbyCount;
    return rc;
  }
  int16_t startReceive() override {
    const uint32_t started = DWT->CYCCNT;
    const int16_t rc = CustomLR1110::startReceive();
    startRxCycles += DWT->CYCCNT - started;
    ++startRxCount;
    return rc;
  }
} chip = new Module(&radioHal, P_LORA_NSS, P_LORA_DIO_1,
                    P_LORA_RESET, P_LORA_BUSY);
class BenchWrapper : public CustomLR1110Wrapper {
public:
  using CustomLR1110Wrapper::CustomLR1110Wrapper;
  uint64_t applyCycles = 0, receivingCycles = 0, startModeCycles = 0;
  uint32_t applyCount = 0, receivingCount = 0, startModeCount = 0;
  uint32_t busyHighChecks = 0, receivingTrueChecks = 0;
  mesh::RadioParamApplyResult hop(uint8_t profile) { return tuneProfile(profile); }
  void startRx() { startRecv(); }
  void stopRx() { idle(); }
  void enableWarmStandby() { setProfileStandbyWarm(true); }
  bool isReceivingPacket() override {
    const uint32_t started = DWT->CYCCNT;
    const bool receiving = CustomLR1110Wrapper::isReceivingPacket();
    if (receiving) ++receivingTrueChecks;
    receivingCycles += DWT->CYCCNT - started;
    ++receivingCount;
    return receiving;
  }
  bool isChipBusy() override {
    const bool busy = CustomLR1110Wrapper::isChipBusy();
    if (busy) ++busyHighChecks;
    return busy;
  }
protected:
  bool applyParams(float freq, float bw, uint8_t sf, uint8_t cr) override {
    const uint32_t started = DWT->CYCCNT;
    const bool rc = CustomLR1110Wrapper::applyParams(freq, bw, sf, cr);
    applyCycles += DWT->CYCCNT - started;
    ++applyCount;
    return rc;
  }
  int startReceiveMode() override {
    const uint32_t started = DWT->CYCCNT;
    const int rc = CustomLR1110Wrapper::startReceiveMode();
    startModeCycles += DWT->CYCCNT - started;
    ++startModeCount;
    return rc;
  }
} driver(chip, board);

static const uint32_t switchPins[Module::RFSWITCH_MAX_PINS] = {
  RADIOLIB_LR11X0_DIO5, RADIOLIB_LR11X0_DIO6,
  RADIOLIB_LR11X0_DIO7, RADIOLIB_LR11X0_DIO8, RADIOLIB_NC,
};
static const Module::RfSwitchMode_t switchModes[] = {
  {LR11x0::MODE_STBY, {LOW, LOW, LOW, LOW}},
  {LR11x0::MODE_RX, {HIGH, LOW, LOW, HIGH}},
  {LR11x0::MODE_TX, {HIGH, HIGH, LOW, HIGH}},
  {LR11x0::MODE_TX_HP, {LOW, HIGH, LOW, HIGH}},
  {LR11x0::MODE_TX_HF, {LOW, LOW, LOW, LOW}},
  {LR11x0::MODE_GNSS, {LOW, LOW, HIGH, LOW}},
  {LR11x0::MODE_WIFI, {LOW, LOW, LOW, LOW}},
  END_OF_MODE_TABLE,
};

struct Timing {
  uint32_t n = 0, minCycles = UINT32_MAX, maxCycles = 0;
  uint64_t sumCycles = 0;
  void add(uint32_t cycles) {
    ++n;
    if (cycles < minCycles) minCycles = cycles;
    if (cycles > maxCycles) maxCycles = cycles;
    sumCycles += cycles;
  }
  double toUs(uint32_t cycles) const { return double(cycles) * 1000000.0 / SystemCoreClock; }
  double minUs() const { return n ? toUs(minCycles) : 0; }
  double maxUs() const { return n ? toUs(maxCycles) : 0; }
  double meanUs() const { return n ? double(sumCycles) * 1000000.0 / n / SystemCoreClock : 0; }
};

bool ready = false;
int initError = 0;
uint32_t nextSequence = 0;
uint32_t configuredTcxoUs = MC_TCXO_DELAY_US;
bool injectTimeoutNextRun = false;

bool waitBusy(uint32_t limitUs = 20000) {
  const uint32_t start = micros();
  while (chip.isChipBusy()) {
    if (uint32_t(micros() - start) >= limitUs) return false;
  }
  return true;
}

void run(const char* mode, unsigned count, uint32_t sequence) {
  const bool modulation = !strcmp(mode, "mod") || !strcmp(mode, "both");
  const bool frequency = !strcmp(mode, "freq") || !strcmp(mode, "both");
  const bool preambleChange = !strcmp(mode, "preamble");
  if (!ready || !sequence || sequence <= nextSequence || count < 16 || count > 1000) {
    Serial.println("{\"error\":\"invalid or repeated run\"}");
    return;
  }
  if (!modulation && !frequency && !preambleChange) {
    Serial.println("{\"error\":\"mode must be mod, freq, both, or preamble\"}");
    return;
  }
  nextSequence = sequence;
  if (!waitBusy()) { Serial.println("{\"error\":\"initial BUSY timeout\"}"); return; }

  mesh::RadioProfileParams primary;
  primary.freq = 909.5f; primary.bw = 125; primary.sf = 7; primary.cr = 5;
  primary.preamble = 32;
  mesh::RadioProfileConfig secondary;
  secondary.params = primary;
  if (modulation) secondary.params.sf = 8;
  if (frequency) secondary.params.freq = 909.75f;
  if (preambleChange) secondary.params.preamble = 48;
  secondary.mode = mesh::RadioProfileMode::Rx;
  uint32_t primaryBusyDeferrals = 0;
  uint32_t drainedPackets = 0, drainCalls = 0;
  auto drainPendingRx = [&]() {
    uint8_t packet[RADIOLIB_LR11X0_MAX_PACKET_LENGTH] = {};
    const int length = driver.recvRaw(packet, sizeof(packet));
    ++drainCalls;
    if (length > 0) ++drainedPackets;
  };
  auto primaryResult = driver.trySetPrimaryParams(primary, true);
  while (primaryResult == mesh::RadioParamApplyResult::BUSY && primaryBusyDeferrals < 20) {
    ++primaryBusyDeferrals;
    drainPendingRx();
    delay(20);  // An unrelated packet can briefly own RX at setup.
    primaryResult = driver.trySetPrimaryParams(primary, true);
  }
  if (primaryResult != mesh::RadioParamApplyResult::APPLIED) {
    Serial.printf("{\"error\":\"primary rejected\",\"result\":%u,"
                  "\"busy_deferrals\":%lu}\n",
                  unsigned(primaryResult), (unsigned long)primaryBusyDeferrals);
    return;
  }
  driver.profiles()->setSecondary(secondary, true);
  driver.startRx();
  if (!driver.isInRecvMode() || !waitBusy()) {
    Serial.println("{\"error\":\"RX did not start\"}"); return;
  }
  chip.hilInjectRxTimeout = injectTimeoutNextRun;
  injectTimeoutNextRun = false;

  Timing spot[2], total[2];
  uint32_t failures = 0, busyDeferrals = 0, rxErrors = 0, cacheErrors = 0;
  uint32_t busyTimeouts = 0, rssiErrors = 0;
  const uint32_t rfBefore = radioHal.rfWrites;
  const uint32_t modBefore = radioHal.modulationWrites;
  const uint32_t packetBefore = radioHal.packetWrites;
  const uint32_t standbyBefore = radioHal.standbyWrites;
  const uint32_t rxBefore = radioHal.rxWrites;
  const uint32_t dioBefore = radioHal.dioWrites;
  const uint32_t packetTypeBefore = radioHal.packetTypeReads;
  const uint32_t clearBefore = radioHal.clearWrites;
  const uint32_t fsBefore = radioHal.fsWrites;
  const uint32_t pendingBefore = chip.hilPendingIrqSnapshots;
  const uint32_t fastBefore = chip.getOptimizedProfileSwitches();
  const uint64_t standbyCyclesBefore = chip.standbyCycles, startRxCyclesBefore = chip.startRxCycles;
  const uint64_t applyCyclesBefore = driver.applyCycles, receivingCyclesBefore = driver.receivingCycles;
  const uint64_t startModeCyclesBefore = driver.startModeCycles;
  const uint32_t standbyCountBefore = chip.standbyCount, startRxCountBefore = chip.startRxCount;
  const uint32_t applyCountBefore = driver.applyCount, receivingCountBefore = driver.receivingCount;
  const uint32_t startModeCountBefore = driver.startModeCount;
  const uint32_t busyHighBefore = driver.busyHighChecks;
  const uint32_t receivingTrueBefore = driver.receivingTrueChecks;
  const uint64_t clearCyclesBefore = chip.fastClearCycles;
  const uint64_t packetCyclesBefore = chip.fastPacketCycles;
  const uint64_t setRxCyclesBefore = chip.fastSetRxCycles;
  unsigned completed = 0;
  uint32_t spotBudgetUs = 0;
  for (unsigned attempt = 0; completed < count + 8 && attempt < (count + 8) * 4; ++attempt) {
    if (!waitBusy()) { ++busyTimeouts; break; }
    if (completed >= 8) delay(10);  // The first eight hops match the rapid startup self-test.
    const uint8_t target = driver.receiveProfile() ^ 1;
    const uint32_t started = DWT->CYCCNT;
    const auto result = driver.hop(target);
    if (result == mesh::RadioParamApplyResult::BUSY) {
      ++busyDeferrals;
      drainPendingRx();
      continue;
    }
    if (result != mesh::RadioParamApplyResult::APPLIED) { ++failures; break; }
    if (!waitBusy()) { ++busyTimeouts; break; }
    const uint32_t elapsed = DWT->CYCCNT - started;
    if (!driver.isInRecvMode()) ++rxErrors;
    if (chip.getSpreadingFactor() != (target && modulation ? 8 : 7)
        || chip.getFreqMHz() != (target && frequency ? 909.75f : 909.5f)
        || chip.preambleSymbols() != (target && preambleChange ? 48 : 32)) ++cacheErrors;
    if (completed < 8) spot[target].add(elapsed);
    else {
      delay(7);  // RSSI needs a settled receiver; this is outside switch timing.
      float rssi = 0;
      if (chip.getRssiInst(&rssi) != RADIOLIB_ERR_NONE || !isfinite(rssi)
          || rssi < -180.0f || rssi > 20.0f) ++rssiErrors;
      total[target].add(elapsed);
    }
    ++completed;
    if (completed == 8) spotBudgetUs = driver.profiles()->switchBudgetUs();
  }

  const uint32_t longBudgetUs = driver.profiles()->switchBudgetUs();
  driver.profiles()->setSecondary({}, true);
  auto restored = driver.hop(0);
  for (unsigned retry = 0; restored == mesh::RadioParamApplyResult::BUSY && retry < 20; ++retry) {
    drainPendingRx();
    delay(20);
    restored = driver.hop(0);
  }
  const bool safe = restored == mesh::RadioParamApplyResult::APPLIED
      && chip.getFreqMHz() == 909.5f && driver.isInRecvMode();
  recordBenchResult(lastRunResult, sequence,
      "{\"result\":%lu,\"board\":\"Seeed T1000-E LR1110\",\"mode\":\"%s\","
      "\"primary\":\"909.5,125,7,5\",\"secondary_freq_mhz\":%.2f,\"secondary_sf\":%u,"
      "\"secondary_preamble\":%u,"
      "\"tcxo_us\":%lu,\"spi_hz\":%lu,\"bulk_spi\":%s,"
      "\"requested\":%u,\"completed\":%u,\"spot_budget_us\":%lu,\"long_budget_us\":%lu,"
      "\"primary_busy_deferrals\":%lu,"
      "\"busy_deferrals\":%lu,\"drain_calls\":%lu,\"drained_packets\":%lu,"
      "\"busy_high_checks\":%lu,"
      "\"receiving_true_checks\":%lu,\"failures\":%lu,"
      "\"busy_timeouts\":%lu,\"rx_errors\":%lu,\"cache_errors\":%lu,\"rssi_errors\":%lu,"
      "\"rf_commands\":%lu,\"modulation_commands\":%lu,\"packet_commands\":%lu,"
      "\"standby_commands\":%lu,\"rx_commands\":%lu,\"dio_commands\":%lu,"
      "\"packet_type_reads\":%lu,\"clear_commands\":%lu,\"fs_commands\":%lu,"
      "\"pending_irq_snapshots\":%lu,\"fast_resumes\":%lu,\"restored\":%s,"
      "\"standby_mean_us\":%.3f,\"apply_mean_us\":%.3f,\"receiving_check_mean_us\":%.3f,"
      "\"start_mode_mean_us\":%.3f,\"start_rx_mean_us\":%.3f,"
      "\"clear_mean_us\":%.3f,\"packet_mean_us\":%.3f,\"set_rx_mean_us\":%.3f,"
      "\"spot_to_primary\":{\"n\":%lu,\"min_us\":%.3f,\"mean_us\":%.3f,\"max_us\":%.3f},"
      "\"spot_to_secondary\":{\"n\":%lu,\"min_us\":%.3f,\"mean_us\":%.3f,\"max_us\":%.3f},"
      "\"to_primary\":{\"n\":%lu,\"min_us\":%.3f,\"mean_us\":%.3f,\"max_us\":%.3f},"
      "\"to_secondary\":{\"n\":%lu,\"min_us\":%.3f,\"mean_us\":%.3f,\"max_us\":%.3f}}\n",
      (unsigned long)sequence, mode, double(secondary.params.freq), unsigned(secondary.params.sf),
      unsigned(secondary.params.preamble),
      (unsigned long)configuredTcxoUs, (unsigned long)radioHal.spiHz,
      radioHal.bulkTransfer ? "true" : "false",
      count, completed, (unsigned long)spotBudgetUs, (unsigned long)longBudgetUs,
      (unsigned long)primaryBusyDeferrals, (unsigned long)busyDeferrals,
      (unsigned long)drainCalls, (unsigned long)drainedPackets,
      (unsigned long)(driver.busyHighChecks - busyHighBefore),
      (unsigned long)(driver.receivingTrueChecks - receivingTrueBefore),
      (unsigned long)failures, (unsigned long)busyTimeouts, (unsigned long)rxErrors,
      (unsigned long)cacheErrors, (unsigned long)rssiErrors,
      (unsigned long)(radioHal.rfWrites - rfBefore),
      (unsigned long)(radioHal.modulationWrites - modBefore),
      (unsigned long)(radioHal.packetWrites - packetBefore),
      (unsigned long)(radioHal.standbyWrites - standbyBefore),
      (unsigned long)(radioHal.rxWrites - rxBefore),
      (unsigned long)(radioHal.dioWrites - dioBefore),
      (unsigned long)(radioHal.packetTypeReads - packetTypeBefore),
      (unsigned long)(radioHal.clearWrites - clearBefore),
      (unsigned long)(radioHal.fsWrites - fsBefore),
      (unsigned long)(chip.hilPendingIrqSnapshots - pendingBefore),
      (unsigned long)(chip.getOptimizedProfileSwitches() - fastBefore), safe ? "true" : "false",
      chip.standbyCount == standbyCountBefore ? 0.0 : double(chip.standbyCycles - standbyCyclesBefore)
          * 1000000.0 / (chip.standbyCount - standbyCountBefore) / SystemCoreClock,
      driver.applyCount == applyCountBefore ? 0.0 : double(driver.applyCycles - applyCyclesBefore)
          * 1000000.0 / (driver.applyCount - applyCountBefore) / SystemCoreClock,
      driver.receivingCount == receivingCountBefore ? 0.0 : double(driver.receivingCycles - receivingCyclesBefore)
          * 1000000.0 / (driver.receivingCount - receivingCountBefore) / SystemCoreClock,
      driver.startModeCount == startModeCountBefore ? 0.0 : double(driver.startModeCycles - startModeCyclesBefore)
          * 1000000.0 / (driver.startModeCount - startModeCountBefore) / SystemCoreClock,
      chip.startRxCount == startRxCountBefore ? 0.0 : double(chip.startRxCycles - startRxCyclesBefore)
          * 1000000.0 / (chip.startRxCount - startRxCountBefore) / SystemCoreClock,
      chip.getOptimizedProfileSwitches() == fastBefore ? 0.0
          : double(chip.fastClearCycles - clearCyclesBefore) * 1000000.0
              / (chip.getOptimizedProfileSwitches() - fastBefore) / SystemCoreClock,
      chip.getOptimizedProfileSwitches() == fastBefore ? 0.0
          : double(chip.fastPacketCycles - packetCyclesBefore) * 1000000.0
              / (chip.getOptimizedProfileSwitches() - fastBefore) / SystemCoreClock,
      chip.getOptimizedProfileSwitches() == fastBefore ? 0.0
          : double(chip.fastSetRxCycles - setRxCyclesBefore) * 1000000.0
              / (chip.getOptimizedProfileSwitches() - fastBefore) / SystemCoreClock,
      (unsigned long)spot[0].n, spot[0].minUs(), spot[0].meanUs(), spot[0].maxUs(),
      (unsigned long)spot[1].n, spot[1].minUs(), spot[1].meanUs(), spot[1].maxUs(),
      (unsigned long)total[0].n, total[0].minUs(), total[0].meanUs(), total[0].maxUs(),
      (unsigned long)total[1].n, total[1].minUs(), total[1].meanUs(), total[1].maxUs());
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  NRF_POWER->DCDCEN = 1;
  SPI.setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI);
  SPI.begin();
  initError = chip.begin(909.5f, 125, 7, 5,
                         RADIOLIB_LR11X0_LORA_SYNC_WORD_PRIVATE, 0, 32, 1.6f);
  if (!initError) initError = chip.setCRC(2);
  if (!initError) initError = chip.explicitHeader();
  if (!initError) chip.setRfSwitchTable(switchPins, switchModes);
  if (!initError) initError = chip.setRxBoostedGainMode(true);
  const uint32_t cycles = DWT->CYCCNT;
  delayMicroseconds(100);
  ready = initError == RADIOLIB_ERR_NONE && DWT->CYCCNT != cycles;
  if (ready) { driver.begin(); driver.startRx(); driver.enableWarmStandby(); }
}

void loop() {
  if (NRF_WDT->RUNSTATUS)
    for (unsigned i = 0; i < 8; ++i) NRF_WDT->RR[i] = 0x6E524635;
  static char line[96];
  static size_t used = 0;
  static bool overflow = false;
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (overflow) { used = 0; overflow = false; Serial.println("{\"error\":\"command too long\"}"); continue; }
      if (!used) continue;
      line[used] = 0; used = 0;
      unsigned count; unsigned long seq; char trailing; char mode[16];
      if (!strcmp(line, "reboot")) {
        Serial.println("{\"rebooting\":true}");
        Serial.flush();
        delay(20);
        NVIC_SystemReset();
      } else if (!strcmp(line, "info")) {
        char info[384];
        snprintf(info, sizeof(info),
                      "{\"bench\":\"t1000-lr1110-production-retune-v1\",\"ready\":%s,"
                      "\"init_rc\":%d,\"board\":\"Seeed T1000-E LR1110\","
                      "\"rx_frequencies_mhz\":[909.5,909.75],\"autonomous_tx\":false,\"tx_command\":false,"
                      "\"sd_fwid\":%u,\"app_base\":%u,\"cycle_hz\":%lu,\"tcxo_us\":%lu,"
                      "\"spi_hz\":%lu,\"bulk_spi\":%s,\"last_sequence\":%lu}\n",
                      ready ? "true" : "false", initError,
                      unsigned(*reinterpret_cast<volatile uint16_t*>(0x300c)),
                      unsigned(*reinterpret_cast<volatile uint32_t*>(0x3008)),
                      (unsigned long)SystemCoreClock, (unsigned long)configuredTcxoUs,
                      (unsigned long)radioHal.spiHz, radioHal.bulkTransfer ? "true" : "false",
                      (unsigned long)nextSequence);
        Serial.print(info);
      } else if (sscanf(line, "fs %u %c", &count, &trailing) == 1 && count <= 1) {
        chip.hilUseFsOnRetune = count;
        Serial.printf("{\"fs_retune\":%s}\n", count ? "true" : "false");
      } else if (sscanf(line, "clear %u %c", &count, &trailing) == 1 && count <= 2) {
        chip.hilFastClearMode = count;
        Serial.printf("{\"fast_clear_mode\":%u}\n", count);
      } else if (sscanf(line, "inject %u %c", &count, &trailing) == 1 && count <= 1) {
        injectTimeoutNextRun = count;
        Serial.printf("{\"inject_timeout_next_run\":%s}\n", count ? "true" : "false");
      } else if (sscanf(line, "bulk %u %c", &count, &trailing) == 1 && count <= 1) {
        radioHal.bulkTransfer = count;
        Serial.printf("{\"bulk_spi\":%s}\n", radioHal.bulkTransfer ? "true" : "false");
      } else if (sscanf(line, "spihz %u %c", &count, &trailing) == 1
                 && (count == 2000000 || count == 8000000 || count == 16000000)) {
        radioHal.setSpiHz(count);
        Serial.printf("{\"spi_hz\":%lu}\n", (unsigned long)radioHal.spiHz);
      } else if (sscanf(line, "tcxo %u %c", &count, &trailing) == 1) {
        if ((!ready && count != 1600) || (count != 0 && count != 50 && count != 100
            && (count < 150 || count > 200 || count % 10 != 0)
            && count != 400 && count != 800 && count != 1600)) {
          Serial.println("{\"error\":\"unsupported tcxo delay\"}");
        } else {
          driver.stopRx();
          int rc = chip.setTCXO(1.6f, count);
          if (!rc) rc = chip.calibrate(0x3F);
          delay(50);
          driver.startRx();
          if (!rc && driver.isInRecvMode() && waitBusy()) {
            configuredTcxoUs = count;
            driver.enableWarmStandby();
            Serial.printf("{\"tcxo_us\":%lu,\"ready\":true}\n", (unsigned long)configuredTcxoUs);
          } else {
            ready = false;
            Serial.printf("{\"error\":\"tcxo reconfigure failed\",\"rc\":%d}\n", rc);
          }
        }
      } else if (sscanf(line, "run %15s %u %lu %c", mode, &count, &seq, &trailing) == 3)
        run(mode, count, seq);
      else if (sscanf(line, "result result %lu %c", &seq, &trailing) == 1)
        replayBenchResult("result", seq);
      else Serial.println("{\"error\":\"unsupported command\"}");
    } else if (!overflow) {
      if (used < sizeof(line) - 1) line[used++] = c;
      else overflow = true;
    }
  }
  delay(1);
}
