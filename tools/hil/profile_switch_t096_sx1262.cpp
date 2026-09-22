// Application-only Heltec T096 SX1262 experiment. No autonomous TX, identity,
// saved settings, display, or filesystem. RX frequencies: 909.5/909.75 MHz.
#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <SPI.h>
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include <helpers/radiolib/Nrf52BufferedRadioHal.h>
#include "ProfileSwitchUsb.h"

class BenchBoard : public mesh::MainBoard {
public:
  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override { return "Heltec T096 SX1262 bench"; }
  void reboot() override { NVIC_SystemReset(); }
  uint8_t getStartupReason() const override { return 0; }
} board;

class CountingHal : public Nrf52BufferedRadioHal {
public:
  explicit CountingHal(SPIClass& bus) : Nrf52BufferedRadioHal(bus, 8000000) {}
  uint32_t spiHz = 8000000;
  uint32_t rfWrites = 0, modWrites = 0, packetWrites = 0;
  uint32_t standbyWrites = 0, fsWrites = 0, clearWrites = 0, rxWrites = 0;
  void setSpiHz(uint32_t hz) {
    spiHz = hz;
    spiSettings = SPISettings(hz, MSBFIRST, SPI_MODE0);
  }
  void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
    if (len) {
      switch (out[0]) {
      case 0x86: ++rfWrites; break;
      case 0x8B: ++modWrites; break;
      case 0x8C: ++packetWrites; break;
      case 0x80: ++standbyWrites; break;
      case 0xC1: ++fsWrites; break;
      case 0x02: ++clearWrites; break;
      case 0x82: ++rxWrites; break;
      }
    }
    Nrf52BufferedRadioHal::spiTransfer(out, len, in);
  }
} radioHal(SPI);

CustomSX1262 chip = new Module(&radioHal, P_LORA_NSS, P_LORA_DIO_1,
                                P_LORA_RESET, P_LORA_BUSY);
class BenchWrapper : public CustomSX1262Wrapper {
public:
  using CustomSX1262Wrapper::CustomSX1262Wrapper;
  mesh::RadioParamApplyResult hop(uint8_t profile) { return tuneProfile(profile); }
  void startRx() { startRecv(); }
  void warm() { setProfileStandbyWarm(true); }
} driver(chip, board);

struct Timing {
  uint32_t n = 0, lo = UINT32_MAX, hi = 0;
  uint64_t sum = 0;
  void add(uint32_t cycles) { ++n; if (cycles < lo) lo = cycles; if (cycles > hi) hi = cycles; sum += cycles; }
  double toUs(uint64_t cycles) const { return double(cycles) * 1000000.0 / SystemCoreClock; }
  double mean() const { return n ? toUs(sum) / n : 0; }
  double min() const { return n ? toUs(lo) : 0; }
  double max() const { return n ? toUs(hi) : 0; }
};

bool ready = false, pilotReady = false;
int initError = 0;
uint32_t lastSequence = 0;

bool waitBusy(uint32_t limitUs = 20000) {
  const uint32_t start = DWT->CYCCNT;
  while (chip.isChipBusy())
    if (uint32_t(DWT->CYCCNT - start) >= uint64_t(limitUs) * SystemCoreClock / 1000000) return false;
  return true;
}

void drain() {
  uint8_t packet[256] = {};
  driver.recvRaw(packet, sizeof(packet));
}

bool configureProfiles(uint8_t sf, bool varySf) {
  drain();
  mesh::RadioProfileParams primary;
  primary.freq = 909.5f; primary.bw = 125; primary.sf = sf; primary.cr = 5;
  primary.preamble = 32;
  auto applied = driver.trySetPrimaryParams(primary, true);
  for (unsigned retry = 0; applied == mesh::RadioParamApplyResult::BUSY && retry < 20; ++retry) {
    drain(); delay(20);
    applied = driver.trySetPrimaryParams(primary, true);
  }
  if (applied != mesh::RadioParamApplyResult::APPLIED) return false;
  mesh::RadioProfileConfig secondary;
  secondary.params = primary;
  secondary.params.freq = 909.75f;
  if (varySf) secondary.params.sf = sf + 1;
  secondary.mode = mesh::RadioProfileMode::Rx;
  driver.profiles()->setSecondary(secondary, true);
  driver.startRx();
  driver.warm();
  return driver.isInRecvMode() && waitBusy();
}

void run(const char* mode, unsigned count, uint32_t sequence) {
  const bool varySf = !strcmp(mode, "both");
  if ((!varySf && strcmp(mode, "freq")) || !ready || sequence <= lastSequence
      || count < 16 || count > 1000) {
    Serial.println("{\"error\":\"invalid run\"}"); return;
  }
  lastSequence = sequence;
  pilotReady = false;
  if (!configureProfiles(7, varySf)) { Serial.println("{\"error\":\"setup failed\"}"); return; }
  const uint32_t rf0 = radioHal.rfWrites, mod0 = radioHal.modWrites;
  const uint32_t packet0 = radioHal.packetWrites, standby0 = radioHal.standbyWrites;
  const uint32_t fs0 = radioHal.fsWrites, clear0 = radioHal.clearWrites;
  const uint32_t rx0 = radioHal.rxWrites, fast0 = chip.getOptimizedProfileSwitches();
  const uint32_t skip0 = chip.hilPacketSkips, fsEntry0 = chip.hilFsEntries;
  Timing timing[2];
  uint32_t completed = 0, deferrals = 0, failures = 0, modeErrors = 0;
  uint32_t cacheErrors = 0, rssiErrors = 0, busyTimeouts = 0;
  for (unsigned attempt = 0; completed < count + 8 && attempt < (count + 8) * 4; ++attempt) {
    drain();
    if (!waitBusy()) { ++busyTimeouts; break; }
    delay(10); // host traffic and settling stay outside the timed region
    const uint8_t target = driver.receiveProfile() ^ 1;
    const uint32_t start = DWT->CYCCNT;
    const auto result = driver.hop(target);
    if (result == mesh::RadioParamApplyResult::BUSY) { ++deferrals; drain(); continue; }
    if (result != mesh::RadioParamApplyResult::APPLIED) { ++failures; break; }
    if (!waitBusy()) { ++busyTimeouts; break; }
    const uint32_t elapsed = DWT->CYCCNT - start;
    if (sx126xReceiveMode(&chip) != 1) ++modeErrors;
    if (chip.hilFrequencyMHz() != (target ? 909.75f : 909.5f)
        || driver.getSpreadingFactor() != (target && varySf ? 8 : 7)) ++cacheErrors;
    delay(7);
    const float rssi = driver.getCurrentRSSI();
    if (!isfinite(rssi) || rssi < -180 || rssi > 20) ++rssiErrors;
    if (completed++ >= 8) timing[target].add(elapsed);
  }
  driver.profiles()->setSecondary({}, true);
  drain();
  auto restored = driver.hop(0);
  for (unsigned retry = 0; restored == mesh::RadioParamApplyResult::BUSY && retry < 20; ++retry) {
    drain(); delay(20); restored = driver.hop(0);
  }
  const bool safe = restored == mesh::RadioParamApplyResult::APPLIED
      && driver.isInRecvMode() && chip.hilFrequencyMHz() == 909.5f;
  recordBenchResult(lastRunResult, sequence,
      "{\"result\":%lu,\"board\":\"Heltec T096 SX1262\",\"mode\":\"%s\","
      "\"fs\":%s,\"packet_cache\":%s,\"spi_hz\":%lu,\"requested\":%u,"
      "\"completed\":%lu,\"deferrals\":%lu,\"failures\":%lu,"
      "\"mode_errors\":%lu,\"cache_errors\":%lu,\"rssi_errors\":%lu,"
      "\"busy_timeouts\":%lu,\"fast_resumes\":%lu,\"fs_entries\":%lu,"
      "\"packet_skips\":%lu,\"rf_commands\":%lu,\"mod_commands\":%lu,"
      "\"packet_commands\":%lu,\"standby_commands\":%lu,\"fs_commands\":%lu,"
      "\"clear_commands\":%lu,\"rx_commands\":%lu,\"restored\":%s,"
      "\"to_primary\":{\"n\":%lu,\"min_us\":%.3f,\"mean_us\":%.3f,\"max_us\":%.3f},"
      "\"to_secondary\":{\"n\":%lu,\"min_us\":%.3f,\"mean_us\":%.3f,\"max_us\":%.3f}}\n",
      (unsigned long)sequence, mode, chip.hilUseFsOnRetune ? "true" : "false",
      chip.hilSkipUnchangedPacket ? "true" : "false", (unsigned long)radioHal.spiHz,
      count, (unsigned long)completed, (unsigned long)deferrals, (unsigned long)failures,
      (unsigned long)modeErrors, (unsigned long)cacheErrors, (unsigned long)rssiErrors,
      (unsigned long)busyTimeouts, (unsigned long)(chip.getOptimizedProfileSwitches() - fast0),
      (unsigned long)(chip.hilFsEntries - fsEntry0), (unsigned long)(chip.hilPacketSkips - skip0),
      (unsigned long)(radioHal.rfWrites - rf0), (unsigned long)(radioHal.modWrites - mod0),
      (unsigned long)(radioHal.packetWrites - packet0), (unsigned long)(radioHal.standbyWrites - standby0),
      (unsigned long)(radioHal.fsWrites - fs0), (unsigned long)(radioHal.clearWrites - clear0),
      (unsigned long)(radioHal.rxWrites - rx0), safe ? "true" : "false",
      (unsigned long)timing[0].n, timing[0].min(), timing[0].mean(), timing[0].max(),
      (unsigned long)timing[1].n, timing[1].min(), timing[1].mean(), timing[1].max());
}

void pilot() {
  if (!ready || !configureProfiles(10, false)) {
    pilotReady = false;
    Serial.println("{\"pilot\":false}");
    return;
  }
  pilotReady = driver.receiveProfile() == 0 && driver.isInRecvMode()
      && chip.hilFrequencyMHz() == 909.5f && driver.getSpreadingFactor() == 10;
  Serial.printf("{\"pilot\":%s,\"freq_khz\":909500,\"sf\":10,\"bw_khz\":125}\n",
                pilotReady ? "true" : "false");
}

void pilotHop(unsigned target) {
  if (!pilotReady || target > 1 || driver.receiveProfile() == target) {
    Serial.println("{\"error\":\"invalid pilot hop\"}"); return;
  }
  const uint32_t fast0 = chip.getOptimizedProfileSwitches();
  const uint32_t fs0 = chip.hilFsEntries, skip0 = chip.hilPacketSkips;
  const uint32_t started = DWT->CYCCNT;
  const auto result = driver.hop(target);
  const uint32_t cycles = DWT->CYCCNT - started;
  const bool okay = result == mesh::RadioParamApplyResult::APPLIED
      && driver.receiveProfile() == target && driver.isInRecvMode()
      && chip.hilFrequencyMHz() == (target ? 909.75f : 909.5f)
      && driver.getSpreadingFactor() == 10 && waitBusy();
  if (!okay) pilotReady = false;
  Serial.printf("{\"pilot_hop\":%u,\"ok\":%s,\"us\":%.3f,\"fast\":%lu,"
                "\"fs\":%lu,\"packet_skips\":%lu}\n", target,
                okay ? "true" : "false", double(cycles) * 1000000.0 / SystemCoreClock,
                (unsigned long)(chip.getOptimizedProfileSwitches() - fast0),
                (unsigned long)(chip.hilFsEntries - fs0),
                (unsigned long)(chip.hilPacketSkips - skip0));
}

void poll(uint32_t expected) {
  if (!pilotReady || !expected) { Serial.println("{\"error\":\"pilot not ready\"}"); return; }
  uint8_t bytes[256] = {};
  const int len = driver.recvRaw(bytes, sizeof(bytes));
  uint32_t seen = 0;
  if (len == 16 && !memcmp(bytes, "CHS1", 4)) memcpy(&seen, bytes + 4, 4);
  bool valid = len == 16 && seen == expected && bytes[8] == 0;
  for (unsigned i = 9; valid && i < 16; ++i) valid = bytes[i] == uint8_t(i ^ expected);
  Serial.printf("{\"expected\":%lu,\"received\":%lu,\"len\":%d,\"valid\":%s,\"rx_mode\":%s}\n",
                (unsigned long)expected, (unsigned long)seen, len,
                valid ? "true" : "false", driver.isInRecvMode() ? "true" : "false");
}

void setup() {
  Serial.begin(115200); delay(1500);
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  NRF_POWER->DCDCEN = 1;
  pinMode(30, OUTPUT); digitalWrite(30, HIGH); // T096 RF front-end supply
  pinMode(12, OUTPUT); digitalWrite(12, HIGH); // KCT8103L enabled
  pinMode(41, OUTPUT); digitalWrite(41, LOW); // LNA/RX path
  SPI.setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI);
  SPI.begin();
  initError = chip.begin(909.5f, 125, 7, 5,
                         RADIOLIB_SX126X_SYNC_WORD_PRIVATE, 0, 32, 1.8f);
  if (!initError) initError = chip.setCRC(true);
  if (!initError) initError = chip.explicitHeader();
  if (!initError) initError = chip.setDio2AsRfSwitch(true);
  if (!initError) initError = chip.setCurrentLimit(140);
  if (!initError) initError = chip.setRxBoostedGainMode(true);
  const uint32_t cycles = DWT->CYCCNT;
  delayMicroseconds(2);
  ready = initError == RADIOLIB_ERR_NONE && DWT->CYCCNT != cycles;
  if (ready) {
    chip.setProfileSwitchOptimization(true);
    driver.begin(); driver.startRx(); driver.warm();
    ready = driver.isInRecvMode() && waitBusy();
  }
  Serial.printf("{\"ready\":%s,\"bench\":\"t096-sx1262-fs-packet-v1\",\"init_rc\":%d}\n",
                ready ? "true" : "false", initError);
}

void loop() {
  if (NRF_WDT->RUNSTATUS)
    for (unsigned i = 0; i < 8; ++i) NRF_WDT->RR[i] = 0x6E524635;
  static char line[96];
  static size_t used = 0;
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (!used) continue;
      line[used] = 0; used = 0;
      unsigned value; unsigned long seq; char trailing, mode[16], kind[12];
      if (!strcmp(line, "info")) {
        Serial.printf("{\"ready\":%s,\"bench\":\"t096-sx1262-fs-packet-v1\","
                      "\"board\":\"Heltec T096 SX1262\",\"autonomous_tx\":false,"
                      "\"sd_fwid\":%u,\"app_base\":%lu,\"spi_hz\":%lu,"
                      "\"fs\":%s,\"packet_cache\":%s}\n",
                      ready ? "true" : "false",
                      unsigned(*reinterpret_cast<volatile uint16_t*>(0x300c)),
                      (unsigned long)*reinterpret_cast<volatile uint32_t*>(0x3008),
                      (unsigned long)radioHal.spiHz,
                      chip.hilUseFsOnRetune ? "true" : "false",
                      chip.hilSkipUnchangedPacket ? "true" : "false");
      } else if (sscanf(line, "fs %u %c", &value, &trailing) == 1 && value <= 1) {
        chip.hilUseFsOnRetune = value;
        Serial.printf("{\"fs\":%s}\n", value ? "true" : "false");
      } else if (sscanf(line, "packetcache %u %c", &value, &trailing) == 1 && value <= 1) {
        chip.hilSkipUnchangedPacket = value;
        Serial.printf("{\"packet_cache\":%s}\n", value ? "true" : "false");
      } else if (sscanf(line, "spihz %u %c", &value, &trailing) == 1
                 && (value == 8000000 || value == 16000000)) {
        radioHal.setSpiHz(value);
        Serial.printf("{\"spi_hz\":%lu}\n", (unsigned long)radioHal.spiHz);
      } else if (sscanf(line, "run %15s %u %lu %c", mode, &value, &seq, &trailing) == 3) {
        run(mode, value, seq);
      } else if (!strcmp(line, "pilot")) {
        pilot();
      } else if (sscanf(line, "pilothop %u %c", &value, &trailing) == 1) {
        pilotHop(value);
      } else if (sscanf(line, "poll %lu %c", &seq, &trailing) == 1) {
        poll(seq);
      } else if (sscanf(line, "result result %lu %c", &seq, &trailing) == 1) {
        replayBenchResult("result", seq);
      } else if (!strcmp(line, "reboot")) {
        Serial.flush(); delay(20); NVIC_SystemReset();
      } else Serial.println("{\"error\":\"unsupported command\"}");
    } else if (used + 1 < sizeof(line)) line[used++] = c;
  }
  delay(1);
}
