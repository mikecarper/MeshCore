#pragma once

#include <Dispatcher.h>
#include <helpers/IdentityStore.h>
#include <helpers/CLICommandUtils.h>

namespace mesh {

// Shared by infrastructure, Companion and standalone roles. Settings use a
// separate versioned image so existing preference offsets stay compatible.
class RadioProfileCLI {
 public:
  struct Schedule {
    RadioProfileConfig config;
    uint32_t start = 0, end = 0;
    uint32_t remaining_ms = 0;  // monotonic upper bound, including start delay
    bool active = false, temporary = false, started = false;
  };
 private:
  FILESYSTEM* fs_ = nullptr;
  Radio* radio_ = nullptr;
  RTCClock* rtc_ = nullptr;
  RadioProfileConfig saved_;
  RadioProfileConfig temporary_;
  RadioProfileConfig pending_temporary_;
  uint16_t primary_preamble_ = 0;
  RadioCrossMode cross_ = RadioCrossMode::Auto;
  Schedule schedules_[8];
  uint32_t temp_remaining_ms_ = 0;
  uint32_t temp_start_ms_ = 0;
  uint32_t pending_duration_ms_ = 0;
  uint32_t publish_after_ms_ = 0;
  uint32_t last_ms_ = 0;
  bool temp_pending_ = false, temp_active_ = false;
  bool hold_ = false;
  bool publish_pending_ = false;
  bool save(const RadioProfileConfig& config, uint16_t preamble, RadioCrossMode cross);
  bool readImage(const char* path, uint8_t* bytes, size_t size);
  bool writeImage(const char* path, const uint8_t* bytes, size_t size);
  void publish();
  void formatConfig(char* reply, size_t capacity, const RadioProfileConfig& config,
                    bool temporary, uint32_t remaining_ms) const;
 public:
  void begin(FILESYSTEM* fs, Radio* radio, RTCClock* rtc);
  void loop();
  bool handle(const char* command, char* reply, size_t capacity = 160);
  uint16_t primaryPreamble() const { return primary_preamble_; }
  bool savePrimaryPreamble(uint16_t symbols);
  bool acceptsPrimary(float freq, float bw, uint8_t sf, uint8_t cr, uint16_t preamble) const;
  RadioParamApplyResult applyPrimary(float freq, float bw, uint8_t sf, uint8_t cr,
      bool temporary, uint16_t preamble, const uint32_t* timings = nullptr) {
    if (!radio_) return RadioParamApplyResult::FAILED;
    RadioProfileParams p;
    p.freq = freq; p.bw = bw; p.sf = sf; p.cr = cr; p.preamble = preamble;
    return radio_->trySetPrimaryParams(p, temporary, timings);
  }
  void stagePrimary(uint16_t preamble, bool temporary) {
    if (!radio_ || !radio_->profiles()) return;
    auto p = radio_->profiles()->primary;
    p.preamble = preamble;
    radio_->profiles()->setPrimary(p, temporary);
  }
  bool secondaryTemporary() const { return temp_active_; }
  uint32_t secondaryRemainingSeconds() const { return (temp_remaining_ms_ + 999) / 1000; }
  void appendPreamble(char* reply, size_t capacity, uint8_t profile = 0) const;
  void appendSavedPreamble(char* reply, size_t capacity, uint8_t sf, float bw) const;
  // Strip exactly one optional trailing preamble before a legacy primary
  // parser runs. Missing means auto. Reject extra fields and malformed input.
  static bool parseSuffix(const char* input, unsigned fields, char* legacy,
                          size_t capacity, uint16_t& preamble);
};

}  // namespace mesh
