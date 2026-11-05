#pragma once
#include <stdint.h>
namespace mesh { namespace w12pram {
// RAM-only policy: a failed radio transaction falls back for this profile
// pair; changing either profile or explicitly enabling permits a new attempt.
class AutoPolicy {
 public:
  enum class Reason : uint8_t { None, Off, Unsupported, Failed, Uncertain };
  explicit AutoPolicy(bool enabled = true) : enabled_(enabled) {}
  bool enabled() const { return enabled_; }
  bool blocked() const { return reason_ == Reason::Failed || reason_ == Reason::Unsupported; }
  Reason reason() const { return !enabled_ ? Reason::Off : reason_; }
  void setEnabled(bool enabled) {
    enabled_ = enabled;
    reason_ = Reason::None;
    due_ = 0;
  }
  void observe(uint32_t primary, uint32_t secondary) {
    if (!seen_ || primary != primary_ || secondary != secondary_) {
      seen_ = true; primary_ = primary; secondary_ = secondary;
      reason_ = Reason::None; due_ = 0;
    }
  }
  bool due(uint32_t now) const {
    return enabled_ && !blocked() && (due_ == 0 || int32_t(now - due_) >= 0);
  }
  void busy(uint32_t now) { due_ = now + 1000u; if (!due_) due_ = 1; }
  void block(Reason reason) { reason_ = reason; due_ = 0; }
  void clearUnsupported() { if (reason_ == Reason::Unsupported) { reason_ = Reason::None; due_ = 0; } }
  void active() { reason_ = Reason::None; due_ = 0; }
 private:
  uint32_t primary_=0, secondary_=0, due_=0;
  Reason reason_=Reason::None;
  bool enabled_=true, seen_=false;
};
inline const char* autoReasonName(AutoPolicy::Reason reason) {
  switch (reason) {
    case AutoPolicy::Reason::Off: return "off";
    case AutoPolicy::Reason::Unsupported: return "unsupported";
    case AutoPolicy::Reason::Failed: return "failed";
    case AutoPolicy::Reason::Uncertain: return "uncertain";
    default: return "none";
  }
}
}}
