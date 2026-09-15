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
    uint32_t end_ms = 0;  // monotonic deadline anchored when the command is accepted
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
  uint8_t reply_setting_ = 0;  // reserved image byte: 0 = role default
  bool infrastructure_replies_ = false;
  Schedule schedules_[8];
  uint32_t temp_remaining_ms_ = 0;
  uint32_t temp_start_ms_ = 0;
  uint32_t pending_duration_ms_ = 0;
  uint32_t publish_after_ms_ = 0;
  uint32_t last_ms_ = 0;
  uint32_t schedule_retry_ms_ = 0;
  bool temp_pending_ = false, temp_active_ = false;
  bool hold_ = false;
  bool publish_pending_ = false;
  enum class RemoteMutation : uint8_t { None, Saved, Temporary, Off, TempOff, DeleteTemp };
  RemoteMutation remote_mutation_ = RemoteMutation::None;
  RadioProfileConfig remote_config_;
  uint32_t remote_duration_ms_ = 0, remote_start_ms_ = 0;
  uint32_t remote_commit_retry_ms_ = 0;
  uint32_t remote_generation_ = 0;
  uint8_t remote_delete_mask_ = 0;
  bool remote_command_ = false, remote_delivered_ = false;
  bool stageRemoteMutation(RemoteMutation kind, const RadioProfileConfig& config = {},
                           uint32_t duration_ms = 0, uint8_t delete_mask = 0);
  bool save(const RadioProfileConfig& config, uint16_t preamble, RadioCrossMode cross);
  bool prepareSavedImage(const char* path, const RadioProfileConfig& config,
                         uint16_t preamble, RadioCrossMode cross);
  bool commitSavedImage(const char* path);
  bool applyReplyMutation();
  bool readImage(const char* path, uint8_t* bytes, size_t size);
  bool writeImage(const char* path, const uint8_t* bytes, size_t size);
  void publish();
  void formatConfig(char* reply, size_t capacity, const RadioProfileConfig& config,
                    bool temporary, uint32_t remaining_ms) const;
 public:
  void begin(FILESYSTEM* fs, Radio* radio, RTCClock* rtc, bool infrastructure_replies = false);
  void loop();
  bool handle(const char* command, char* reply, size_t capacity = 160, bool remote_origin = false);
  // Remote infrastructure commands must keep both RX-origin generations alive
  // until their exact response packets finish, not merely wait a fixed delay.
  void beginReplyCommand() { remote_command_ = true; }
  void endReplyCommand() { remote_command_ = false; }
  bool hasReplyMutation() const { return remote_mutation_ != RemoteMutation::None; }
  uint32_t replyMutationGeneration() const { return remote_generation_; }
  bool finishReplyMutation(bool delivered);
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
  static void appendChirpWarning(char* reply, size_t capacity, const RadioProfiles& preview);
  void appendPrimaryChirpWarning(char* reply, size_t capacity, uint8_t sf, float bw,
                                uint16_t preamble) const;
  // Strip exactly one optional trailing preamble before a legacy primary
  // parser runs. Missing means auto. Reject extra fields and malformed input.
  static bool parseSuffix(const char* input, unsigned fields, char* legacy,
                          size_t capacity, uint16_t& preamble);
};

}  // namespace mesh
