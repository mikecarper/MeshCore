// Receive-only, RAM-only, same-image A/B harness for the production T-Deck
// display and production radio2 scheduler. There is deliberately no TX path.
#include <Arduino.h>
#include <SPI.h>
#include <stdarg.h>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include <helpers/radiolib/ESP32BufferedRadioHal.h>
#include "TDeckRadioHal.h"
#include <helpers/radiolib/ESP32RetunePriority.h>
#include <helpers/ui/ST7789LCDDisplay.h>

namespace {
// Exact min/mean/max plus bounded histogram percentiles. The percentile field
// is the upper edge of a bucket, not an interpolated/exact percentile.
const uint32_t timingEdges[] = {20, 50, 100, 200, 500, 750, 1000, 1500,
    2000, 3000, 5000, 7500, 10000, 20000, 40000, 80000, 160000, UINT32_MAX};
struct Timing {
  uint32_t n = 0, lo = UINT32_MAX, hi = 0;
  uint64_t sum = 0;
  uint32_t buckets[sizeof(timingEdges) / sizeof(timingEdges[0])] = {};
  void add(uint32_t us) {
    ++n;
    if (us < lo) lo = us;
    if (us > hi) hi = us;
    sum += us;
    for (unsigned i = 0; i < sizeof(buckets) / sizeof(buckets[0]); ++i) {
      if (us <= timingEdges[i]) { ++buckets[i]; break; }
    }
  }
  uint32_t percentileUpper(unsigned percent) const {
    if (!n) return 0;
    const uint32_t target = (uint64_t(n) * percent + 99) / 100;
    uint32_t cumulative = 0;
    for (unsigned i = 0; i < sizeof(buckets) / sizeof(buckets[0]); ++i) {
      cumulative += buckets[i];
      if (cumulative >= target) return timingEdges[i];
    }
    return hi;
  }
};

class BenchBoard : public mesh::MainBoard {
 public:
  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override { return "T-Deck display timing HIL"; }
  void reboot() override { ESP.restart(); }
  uint8_t getStartupReason() const override { return 0; }
} board;

// Default construction matches production's HSPI selection. The display has
// its own SPIClass(HSPI) handle and the same physical SCLK/MOSI pins.
SPIClass radioSpi;
class BenchHal : public TDeckRadioHal {
 public:
  bool bulkTransfer = false;
  uint32_t transfers = 0, bytes = 0, modemQueries = 0, actualHz = 0;
  explicit BenchHal(SPIClass& bus) : TDeckRadioHal(bus, 2000000) {}
  void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
    ++transfers; bytes += len;
    actualHz = spiClockDivToFrequency(spi->getClockDivider());
    if (len && out[0] == RADIOLIB_SX126X_CMD_GET_PACKET_TYPE) ++modemQueries;
    if (bulkTransfer) ESP32BufferedRadioHal::spiTransfer(out, len, in);
    else ArduinoHal::spiTransfer(out, len, in);
  }
} radioHal(radioSpi);
CustomSX1262 chip = new Module(&radioHal, P_LORA_NSS, P_LORA_DIO_1,
                              P_LORA_RESET, P_LORA_BUSY);

class BenchWrapper : public CustomSX1262Wrapper {
  uint32_t retuneStarted = 0, visitStarted = 0, dueAfterUs = 0;
  uint8_t retuneFrom = 0;
 public:
  ESP32RetunePriority retunePriority;
  using CustomSX1262Wrapper::CustomSX1262Wrapper;
  bool measuring = false;
  Timing retune[2], visit[2], late[2];
  uint32_t successfulHops = 0, failedRetunes = 0;
  unsigned actualRetunePriority = 0;
  void resetMeasurements() {
    for (unsigned i = 0; i < 2; ++i) { retune[i] = {}; visit[i] = {}; late[i] = {}; }
    successfulHops = failedRetunes = 0;
    actualRetunePriority = 0;
  }
  bool floorCalibrationPending() const { return _nf_refresh_requested; }
  uint32_t nominalVisitUs(uint8_t profile) const {
    return _profiles.listenUs(profile, profilePreamble(_profiles.slowerProfile()));
  }
 protected:
  void beginProfileRetune(bool continuousRx) override {
    retuneStarted = micros();
    retunePriority.begin(continuousRx);
    if (unsigned(uxTaskPriorityGet(nullptr)) > actualRetunePriority)
      actualRetunePriority = uxTaskPriorityGet(nullptr);
    retuneFrom = _active_profile;
    visitStarted = _profile_visit_stamp;
    dueAfterUs = nominalVisitUs(retuneFrom);
    // Match serviceProfileScan's temporary noise-floor sample extension.
    // A packet can legitimately extend an RX visit further; report received
    // packets alongside lateness so that a busy-channel run is identifiable.
    if (_nf_refresh_requested && profileFloorEstimator(retuneFrom).ready(uint32_t(millis()))
        && dueAfterUs < NoiseFloorSettleMillis * 1000UL)
      dueAfterUs = NoiseFloorSettleMillis * 1000UL;
    CustomSX1262Wrapper::beginProfileRetune(continuousRx);
  }
  void endProfileRetune(bool success) override {
    CustomSX1262Wrapper::endProfileRetune(success);
    retunePriority.end();
    const uint32_t done = micros();
    if (!measuring) return;
    if (!success) { ++failedRetunes; return; }
    if (retuneFrom == _active_profile) return;
    ++successfulHops;
    retune[_active_profile].add(done - retuneStarted);
    const uint32_t dwell = retuneStarted - visitStarted;
    visit[retuneFrom].add(dwell);
    late[retuneFrom].add(dwell > dueAfterUs ? dwell - dueAfterUs : 0);
  }
} driver(chip, board);

ST7789LCDDisplay display;
bool ready = false, measuringService = false;
uint32_t lastServiceUs = 0, receivePackets = 0;
Timing serviceGap, serviceDuration, frameDuration;
char savedResult[6144] = {};
unsigned savedSequence = 0;
size_t resultUsed = 0;
std::atomic<bool> stressEnabled{false};
std::atomic<uint32_t> stressRounds{0};
bool stressTaskReady = false;
unsigned setupStage = 0;
int setupApplyResult = -1;
bool exactSpiClock = false;
uint32_t clockSettingHz = 8000000;
void configureRadioClock(unsigned spiMHz) {
  clockSettingHz = spiMHz * 1000000UL;
  if (exactSpiClock)
    clockSettingHz = spiClockDivToFrequency(spiFrequencyToClockDiv(clockSettingHz));
  radioHal.spiSettings = SPISettings(clockSettingHz, MSBFIRST, SPI_MODE0);
}
// A bounded, same-core priority-1 CPU competitor. It never accesses the radio,
// display, or SPI and sleeps between 250-us bursts. Report it separately from
// the unloaded measurements; it is not a simulation of Bluetooth's stack.
void stressTask(void*) {
  for (;;) {
    if (stressEnabled.load(std::memory_order_relaxed)) {
      const uint32_t started = micros();
      while (uint32_t(micros() - started) < 250) {}
      stressRounds.fetch_add(1, std::memory_order_relaxed);
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

bool appendResult(const char* format, ...) {
  if (resultUsed >= sizeof(savedResult)) return false;
  va_list args;
  va_start(args, format);
  const int count = vsnprintf(savedResult + resultUsed, sizeof(savedResult) - resultUsed,
                              format, args);
  va_end(args);
  if (count < 0 || size_t(count) >= sizeof(savedResult) - resultUsed) {
    resultUsed = sizeof(savedResult);
    return false;
  }
  resultUsed += count;
  return true;
}

void appendTiming(const char* name, const Timing& timing) {
  appendResult("\"%s\":{\"n\":%u,\"min_us\":%u,\"mean_us\":%.3f,\"max_us\":%u,"
               "\"p95_upper_us\":%u,\"p99_upper_us\":%u}", name, timing.n,
               timing.n ? timing.lo : 0, timing.n ? double(timing.sum) / timing.n : 0,
               timing.hi, timing.percentileUpper(95), timing.percentileUpper(99));
}

bool waitBusy() {
  const uint32_t started = micros();
  while (chip.isChipBusy()) {
    if (uint32_t(micros() - started) > 20000) return false;
  }
  return true;
}

void serviceRadio() {
  const uint32_t started = micros();
  if (measuringService) {
    if (lastServiceUs) serviceGap.add(started - lastServiceUs);
    lastServiceUs = started;
  }
  // Keep the complete production scheduling/calibration/receive guards. The
  // display callback is called only after its SPI transaction has ended.
  driver.loop();
  uint8_t packet[256];
  const int received = driver.recvRaw(packet, sizeof(packet));
  if (received > 0) {
    ++receivePackets;
    driver.onReceiveProcessed();
  }
  if (measuringService) serviceDuration.add(micros() - started);
}

// Repeatable representative full UI redraw. Content and geometry are exactly
// the same for direct and buffered paths. Only the counter/bar change after
// the initial frame. Both paths redraw the full screen; the optimized path
// permits radio service between short, completed display transactions.
void drawFrame(unsigned mode, uint32_t frame) {
  const uint32_t started = micros();
  display.startFrame(UIColor::window_bkg);
  display.setColor(UIColor::title_bkg);
  display.fillRect(0, 0, display.width(), 28);
  display.setColor(UIColor::title_txt);
  display.setTextSize(2);
  display.setCursor(10, 6);
  display.print("T-Deck radio2 HIL");
  display.setTextSize(1);
  display.setColor(UIColor::primary_txt);
  display.setCursor(12, 46);
  display.print("Receive-only production profile scheduler");
  display.setCursor(12, 68);
  display.print("Primary: 909.5 MHz SF7 / BW62.5");
  display.setCursor(12, 88);
  display.print("Radio2:  910.5 MHz SF8 / BW500");
  display.setColor(UIColor::corp_blue);
  display.drawRect(12, 116, display.width() - 24, 28);
  display.fillRect(16, 120, (frame % 20) * (display.width() - 32) / 20, 20);
  char text[72];
  snprintf(text, sizeof(text), "Frame %06lu  render %s", (unsigned long)frame,
           mode == 1 ? "direct" : "buffered");
  display.setColor(UIColor::primary_txt);
  display.setCursor(12, 168);
  display.print(text);
  display.setCursor(12, 200);
  display.print("Native USB commands; no autonomous TX");
  display.endFrame();
  if (measuringService) frameDuration.add(micros() - started);
}

bool setProfiles(unsigned secondaryPreamble = 32) {
  setupStage = 1; setupApplyResult = -1;
  mesh::RadioProfileConfig secondary;
  secondary.mode = mesh::RadioProfileMode::Off;
  driver.profiles()->setSecondary(secondary, true);
  for (unsigned i = 0; i < 4; ++i) { serviceRadio(); if (!waitBusy()) return false; }
  mesh::RadioProfileParams primary;
  primary.freq = 909.5f; primary.bw = 62.5f; primary.sf = 7;
  primary.cr = 5; primary.preamble = 32;
  setupStage = 2;
  setupApplyResult = int(driver.trySetPrimaryParams(primary, true));
  if (setupApplyResult != int(mesh::RadioParamApplyResult::APPLIED))
    return false;
  setupStage = 3;
  secondary.params.freq = 910.5f; secondary.params.bw = 500;
  secondary.params.sf = 8; secondary.params.cr = 5;
  secondary.params.preamble = secondaryPreamble;
  secondary.mode = mesh::RadioProfileMode::Rx;
  driver.profiles()->setSecondary(secondary, true);
  const uint32_t started = millis();
  while (uint32_t(millis() - started) < 300) {
    serviceRadio();
    delay(0);
  }
  setupStage = 4;
  return driver.profiles()->switchTestReady() && driver.isInRecvMode() && waitBusy();
}

void printInfo() {
  Serial.printf("{\"bench\":\"tdeck-display-radio2-v2\",\"ready\":%s,"
                "\"autonomous_tx\":false,\"psram_bytes\":%u,\"free_psram_bytes\":%u,"
                "\"free_heap_bytes\":%u,\"buffered\":%s,\"width\":%d,\"height\":%d,"
                "\"stress_task_ready\":%s,\"loop_priority\":%u,\"core\":%u,"
                "\"clock_fix_supported\":%s,"
                "\"command\":\"trial <display:0,1,2> <MHz:8..16> <modemcache:0,1> "
                "<iqcache:0,1> <priority:0,3> <load:0,1> <preamble:32,40> "
                "<seconds:1..10> <fps:1..30> <seq>\"}\n",
                ready ? "true" : "false", ESP.getPsramSize(), ESP.getFreePsram(),
                ESP.getFreeHeap(), display.isBufferedRendering() ? "true" : "false",
                display.width(), display.height(), stressTaskReady ? "true" : "false",
                unsigned(uxTaskPriorityGet(nullptr)), unsigned(xPortGetCoreID()),
                TDeckRadioHal::supportsClockTimingCorrection() ? "true" : "false");
}

// Read-only link diagnostic, outside timed trials. Put the chip in standby
// at the known 8 MHz clock before changing the transfer policy; recover at
// 8 MHz afterwards. No identity/settings writes or RF transmission.
void inspectSpi(unsigned spiMHz, unsigned bulk, unsigned count = 100) {
  radioHal.spiSettings = SPISettings(8000000, MSBFIRST, SPI_MODE0);
  radioHal.bulkTransfer = true;
  const int16_t standby = chip.standby();
  uint8_t reference[2] = {};
  const int16_t referenceRc = chip.readRegister(0x0740, reference, 2);
  configureRadioClock(spiMHz);
  radioHal.bulkTransfer = bulk;
  unsigned correct = 0, bad = 0, firstBad = 0, syncBad = 0, readErrors = 0;
  int firstReadError = 0;
  for (unsigned i = 0; i < count; ++i) {
    uint8_t modem = 0xff, sync[2] = {};
    const int16_t modemRc = chip.getMod()->SPIreadStream(
        RADIOLIB_SX126X_CMD_GET_PACKET_TYPE, &modem, 1);
    const int16_t syncRc = chip.readRegister(0x0740, sync, 2);
    if (modemRc != RADIOLIB_ERR_NONE || syncRc != RADIOLIB_ERR_NONE) {
      if (!readErrors) firstReadError = modemRc ? modemRc : syncRc;
      ++readErrors;
    }
    if (modemRc == RADIOLIB_ERR_NONE && modem == RADIOLIB_SX126X_PACKET_TYPE_LORA) ++correct;
    else { if (!bad) firstBad = modem; ++bad; }
    if (syncRc != RADIOLIB_ERR_NONE || memcmp(sync, reference, sizeof(sync))) ++syncBad;
  }
  const uint32_t actualHz = radioHal.actualHz;
  const uint32_t originalClock = radioHal.getOriginalClockTiming();
  const uint32_t correctedClock = radioHal.getCorrectedClockTiming();
  radioHal.spiSettings = SPISettings(8000000, MSBFIRST, SPI_MODE0);
  radioHal.bulkTransfer = true;
  const unsigned recoveryModem = chip.getPacketType();
  const unsigned errors = chip.getDeviceErrors();
  const int16_t resumed = chip.startReceive();
  Serial.printf("{\"spi_inspection\":true,\"spi_mhz\":%u,\"bulk\":%s,"
                "\"actual_spi_hz\":%u,\"read_pairs\":%u,\"exact_clock\":%s,"
                "\"clock_setting_hz\":%u,\"clock_timing_corrected\":%s,"
                "\"spi_clock_original\":%u,\"spi_clock_corrected\":%u,"
                "\"standby_result\":%d,\"reference_result\":%d,"
                "\"reference_sync\":[%u,%u],\"correct_modem_reads\":%u,"
                "\"bad_modem_reads\":%u,\"first_bad_modem\":%u,"
                "\"bad_sync_reads\":%u,\"read_errors\":%u,\"first_read_error\":%d,"
                "\"recovery_modem\":%u,\"chip_errors_at_8mhz\":%u,"
                "\"rx_resume_at_8mhz\":%d}\n", spiMHz, bulk ? "true" : "false",
                actualHz, count, exactSpiClock ? "true" : "false", clockSettingHz,
                radioHal.isClockTimingCorrectionActive() ? "true" : "false",
                originalClock, correctedClock,
                standby, referenceRc, reference[0], reference[1], correct,
                bad, firstBad, syncBad, readErrors, firstReadError, recoveryModem, errors, resumed);
}

void run(unsigned mode, unsigned spiMHz, unsigned bulk, unsigned seconds,
         unsigned fps, unsigned sequence, unsigned modemCache = 0,
         unsigned iqCache = 0, unsigned priority = 0, unsigned load = 0,
         unsigned secondaryPreamble = 32) {
  if (!ready || !waitBusy()) { Serial.println("{\"error\":\"radio not ready\"}"); return; }
  measuringService = driver.measuring = false;
  radioHal.bulkTransfer = bulk;
  configureRadioClock(spiMHz);
  chip.hilUseFsOnRetune = true;
  chip.hilSkipUnchangedPacket = true;
  chip.hilSkipConfirmedModemQuery = modemCache;
  chip.hilSkipUnchangedIq = iqCache;
  driver.retunePriority.setPriority(priority);
  display.setBufferedRendering(mode == 2);
  display.setRenderYieldCallback(mode == 2 ? serviceRadio : nullptr);
  if (mode == 2 && !display.isBufferedRendering()) {
    Serial.println("{\"error\":\"display buffer unavailable\"}"); return;
  }
  if (!setProfiles(secondaryPreamble)) {
    Serial.printf("{\"error\":\"profile setup failed\",\"setup_stage\":%u,"
                  "\"primary_apply_result\":%d,\"switch_failures\":%u,"
                  "\"spi_mhz\":%u,\"actual_spi_hz\":%u,\"chip_errors\":%u,"
                  "\"rx_mode\":%d}\n", setupStage, setupApplyResult,
                  driver.profiles()->switch_failures, spiMHz, radioHal.actualHz,
                  chip.getDeviceErrors(), sx126xReceiveMode(&chip));
    return;
  }
  // Prime the frame buffer before measuring. Both redraw paths start from
  // an already visible frame; no-display leaves that static screen in place.
  if (mode) drawFrame(mode, 0);
  if (!waitBusy()) { Serial.println("{\"error\":\"warmup busy timeout\"}"); return; }
  const uint16_t chipErrorsBefore = chip.getDeviceErrors();
  const uint32_t switchesBefore = driver.profiles()->switches;
  const uint32_t failuresBefore = driver.profiles()->switch_failures;
  const uint32_t optimizedBefore = chip.getOptimizedProfileSwitches();
  const uint32_t modemSkipsBefore = chip.hilModemQuerySkips;
  const uint32_t iqSkipsBefore = chip.hilIqWorkaroundSkips;
  const uint32_t transfersBefore = radioHal.transfers, bytesBefore = radioHal.bytes;
  const uint32_t modemQueriesBefore = radioHal.modemQueries;
  const uint32_t stressBefore = stressRounds.load(std::memory_order_relaxed);
  const unsigned originalPriority = uxTaskPriorityGet(nullptr);
  stressEnabled.store(load != 0, std::memory_order_relaxed);
  driver.resetMeasurements();
  serviceGap = {}; serviceDuration = {}; frameDuration = {};
  receivePackets = lastServiceUs = 0;
  measuringService = driver.measuring = true;
  const uint32_t started = micros();
  uint32_t nextFrame = started, frames = 0;
  const uint32_t intervalUs = 1000000UL / fps;
  while (uint32_t(micros() - started) < seconds * 1000000UL) {
    serviceRadio();
    const uint32_t now = micros();
    if (mode && int32_t(now - nextFrame) >= 0) {
      drawFrame(mode, ++frames);
      // Do not pile up overdue frames and starve the rest of the application.
      nextFrame = now + intervalUs;
    }
    delay(0);
  }
  const uint32_t elapsedUs = micros() - started;
  stressEnabled.store(false, std::memory_order_relaxed);
  measuringService = driver.measuring = false;
  display.setRenderYieldCallback(nullptr);
  const bool idle = waitBusy();
  const uint16_t chipErrorsAfter = chip.getDeviceErrors();
  const int receiveMode = sx126xReceiveMode(&chip);
  const unsigned active = driver.receiveProfile();
  const auto& expected = driver.profiles()->params(active);
  const bool cacheCorrect = chip.spreadingFactor == expected.sf
      && chip.bandwidthKhz == expected.bw && chip.codingRate == expected.cr - 4;
  resultUsed = 0;
  savedSequence = sequence;
  savedResult[0] = 0;
  appendResult("{\"result\":%u,\"bench\":\"tdeck-display-radio2-v2\",\"display_mode\":%u,"
               "\"spi_mhz\":%u,\"bulk\":%s,\"seconds\":%u,\"requested_fps\":%u,"
               "\"elapsed_us\":%u,\"frames\":%u,\"switches\":%u,\"switch_failures\":%u,"
               "\"hook_hops\":%u,\"failed_retunes\":%u,\"optimized_rx_resumes\":%u,"
               "\"received_packets\":%u,\"chip_errors_before\":%u,\"chip_errors_after\":%u,"
               "\"busy_timeout\":%s,\"rx_mode\":%d,\"cache_correct\":%s,"
               "\"floor_calibration_pending\":%s,\"nominal_visit_us\":[%u,%u],",
               sequence, mode, spiMHz, bulk ? "true" : "false", seconds, fps,
               elapsedUs, frames, driver.profiles()->switches - switchesBefore,
               driver.profiles()->switch_failures - failuresBefore, driver.successfulHops,
               driver.failedRetunes, chip.getOptimizedProfileSwitches() - optimizedBefore,
               receivePackets, chipErrorsBefore, chipErrorsAfter, idle ? "false" : "true",
               receiveMode, cacheCorrect ? "true" : "false",
               driver.floorCalibrationPending() ? "true" : "false",
               driver.nominalVisitUs(0), driver.nominalVisitUs(1));
  appendResult("\"modem_cache\":%s,\"iq_cache\":%s,\"retune_priority\":%u,"
               "\"synthetic_load\":%s,\"secondary_preamble\":%u,"
               "\"modem_query_skips\":%u,\"iq_workaround_skips\":%u,\"actual_spi_hz\":%u,"
               "\"spi_transfers\":%u,\"spi_bytes\":%u,\"modem_queries\":%u,"
               "\"stress_rounds\":%u,\"original_priority\":%u,\"final_priority\":%u,"
               "\"actual_retune_priority\":%u,\"core\":%u,\"exact_clock\":%s,"
               "\"clock_setting_hz\":%u,\"clock_timing_corrected\":%s,"
               "\"spi_clock_original\":%u,\"spi_clock_corrected\":%u,",
               modemCache ? "true" : "false", iqCache ? "true" : "false", priority,
               load ? "true" : "false", secondaryPreamble,
               chip.hilModemQuerySkips - modemSkipsBefore,
               chip.hilIqWorkaroundSkips - iqSkipsBefore,
               radioHal.actualHz,
               radioHal.transfers - transfersBefore, radioHal.bytes - bytesBefore,
               radioHal.modemQueries - modemQueriesBefore,
               stressRounds.load(std::memory_order_relaxed) - stressBefore,
               originalPriority, unsigned(uxTaskPriorityGet(nullptr)),
               driver.actualRetunePriority, unsigned(xPortGetCoreID()),
               exactSpiClock ? "true" : "false", clockSettingHz,
               radioHal.isClockTimingCorrectionActive() ? "true" : "false",
               radioHal.getOriginalClockTiming(), radioHal.getCorrectedClockTiming());
  appendTiming("service_gap", serviceGap); appendResult(",");
  appendTiming("service_duration", serviceDuration); appendResult(",");
  appendTiming("frame", frameDuration); appendResult(",\"directions\":[");
  for (unsigned i = 0; i < 2; ++i) {
    if (i) appendResult(",");
    appendResult("{\"profile\":%u,", i);
    appendTiming("retune_to", driver.retune[i]); appendResult(",");
    appendTiming("visit_before_departure", driver.visit[i]); appendResult(",");
    appendTiming("deadline_lateness", driver.late[i]); appendResult("}");
  }
  // ESP-IDF reports the high-water mark in bytes. This includes synchronous
  // nesting of the display's stack strip buffer and radio-service callback.
  appendResult("],\"free_heap_bytes\":%u,\"free_psram_bytes\":%u,"
               "\"loop_stack_min_free_bytes\":%u}",
               ESP.getFreeHeap(), ESP.getFreePsram(),
               unsigned(uxTaskGetStackHighWaterMark(nullptr)));
  if (resultUsed >= sizeof(savedResult)) {
    savedSequence = 0;
    Serial.println("{\"error\":\"result overflow\"}");
  } else Serial.println(savedResult);
}
} // namespace

void setup() {
  Serial.setTxBufferSize(8192);
  Serial.setTxTimeoutMs(250);
  Serial.begin(115200);
  // Shared peripheral rail, TFT and SD deselected before touching the bus.
  pinMode(PIN_PERF_POWERON, OUTPUT); digitalWrite(PIN_PERF_POWERON, HIGH);
  pinMode(PIN_TFT_CS, OUTPUT); digitalWrite(PIN_TFT_CS, HIGH);
  pinMode(39, OUTPUT); digitalWrite(39, HIGH);
  pinMode(P_LORA_NSS, OUTPUT); digitalWrite(P_LORA_NSS, HIGH);
  delay(100);
  ready = chip.std_init(&radioSpi);
  if (ready) {
    driver.begin();
    ready = driver.setRxPowerSaving(false, 65625, 60000);
  }
  const bool displayReady = display.begin();
  display.setBufferedRendering(false);
  ready = ready && displayReady && waitBusy();
  stressTaskReady = xTaskCreatePinnedToCore(stressTask, "hilCpuLoad", 2048,
      nullptr, 1, nullptr, xPortGetCoreID()) == pdPASS;
  printInfo();
}

void loop() {
  static char line[112];
  static size_t used = 0;
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (!used) continue;
      line[used] = 0; used = 0;
      unsigned mode, spiMHz, bulk, seconds, fps, sequence;
      unsigned modemCache, iqCache, priority, load, preamble, count;
      char trailing;
      if (!strcmp(line, "info")) printInfo();
      else if (!strcmp(line, "reboot")) ESP.restart();
      else if (sscanf(line, "clockfix %u %c", &bulk, &trailing) == 1 && bulk <= 1) {
        const bool supported = radioHal.setClockTimingCorrection(bulk);
        Serial.printf("{\"clock_fix\":%s,\"supported\":%s}\n",
                      radioHal.isClockTimingCorrectionActive() ? "true" : "false",
                      supported ? "true" : "false");
      }
      else if (sscanf(line, "exactclock %u %c", &bulk, &trailing) == 1 && bulk <= 1) {
        exactSpiClock = bulk;
        Serial.printf("{\"exact_spi_clock\":%s}\n", exactSpiClock ? "true" : "false");
      } else if (sscanf(line, "inspect %u %u %u %c", &spiMHz, &bulk, &count, &trailing) == 3
                 && spiMHz >= 8 && spiMHz <= 16 && bulk <= 1 && count >= 1 && count <= 10000) {
        inspectSpi(spiMHz, bulk, count);
      }
      else if (sscanf(line, "inspect %u %u %c", &spiMHz, &bulk, &trailing) == 2
               && spiMHz >= 8 && spiMHz <= 16 && bulk <= 1) {
        inspectSpi(spiMHz, bulk);
      }
      else if (sscanf(line, "result %u %c", &sequence, &trailing) == 1) {
        if (sequence == savedSequence && savedSequence) Serial.println(savedResult);
        else Serial.println("{\"error\":\"result not retained\"}");
      } else if (sscanf(line, "trial %u %u %u %u %u %u %u %u %u %u %c",
                        &mode, &spiMHz, &modemCache, &iqCache, &priority, &load,
                        &preamble, &seconds, &fps, &sequence, &trailing) == 10
                 && mode <= 2 && spiMHz >= 8 && spiMHz <= 16
                 && modemCache <= 1 && iqCache <= 1 && (priority == 0 || priority == 3)
                 && load <= 1 && (!load || stressTaskReady)
                 && (preamble == 32 || preamble == 40)
                 && seconds >= 1 && seconds <= 10 && fps >= 1 && fps <= 30 && sequence) {
        run(mode, spiMHz, 1, seconds, fps, sequence, modemCache, iqCache,
            priority, load, preamble);
      } else if (sscanf(line, "run %u %u %u %u %u %u %c", &mode, &spiMHz, &bulk,
                        &seconds, &fps, &sequence, &trailing) == 6
                 && mode <= 2 && (spiMHz == 2 || spiMHz == 8) && bulk <= 1
                 && seconds >= 1 && seconds <= 10 && fps >= 1 && fps <= 30 && sequence) {
        run(mode, spiMHz, bulk, seconds, fps, sequence);
      } else Serial.println("{\"error\":\"invalid command; use info\"}");
    } else if (used + 1 < sizeof(line)) line[used++] = c;
  }
  if (ready) serviceRadio();
  delay(0);
}
