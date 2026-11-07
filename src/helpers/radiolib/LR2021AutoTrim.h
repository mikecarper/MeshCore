#pragma once

// Hardware-independent calibration policy. Callers must bind each observation
// to the actual received packet and verify the sender's signature.
#include <stdint.h>
#include <string.h>
#include "LR2021CalibrationPolicy.h"

namespace mesh { namespace lr2021 {

struct AutoTrimSample {
  const uint8_t* sender_key = nullptr; // full 32-byte trusted identity
  int32_t offset_hz = 0;
  uint32_t frequency_hz = 0;
  uint32_t signed_time = 0, utc_now = 0;
  uint32_t captured_at_ms = 0, now_ms = 0;
  bool verified = false, packet_bound = false, direct_rf = false;
};

// At most five reference batches plus an independent verification batch. The
// caller applies candidates only in RAM and must restore the original on every
// failure, timeout, cancellation or reboot. Commit is an explicit final action.
class AutoTrim {
 public:
  enum class Action : uint8_t { Idle, Collect, Apply, Restore, Commit, Complete };
  static constexpr uint8_t SampleCount = 1;
  static constexpr uint8_t PeerCount = 3;
  static constexpr uint32_t PreferredPeersWaitMs = 20000;

  bool begin(const XoscTrimSelection& original, uint32_t frequency_hz, uint32_t now_ms, uint32_t timeout_ms,
             uint32_t window_ms, uint32_t max_spread_hz, uint32_t min_improvement_hz) {
    if (action_ != Action::Idle || !validXoscTrim(original.a, original.b)
        || frequency_hz < 150000000UL || frequency_hz > 2500000000UL
        || timeout_ms < 1000 || timeout_ms >= 0x80000000UL
        || window_ms < 1000 || window_ms >= timeout_ms
        || max_spread_hz == 0 || max_spread_hz > 100000UL
        || min_improvement_hz == 0 || min_improvement_hz > 100000UL) return false;
    memset(peers_, 0, sizeof(peers_));
    peer_count_ = 0; cohort_locked_ = false; first_sample_at_ = 0;
    original_ = current_ = best_ = original;
    frequency_hz_ = frequency_hz; started_ = now_ms; timeout_ = timeout_ms;
    max_spread_ = max_spread_hz; min_improvement_ = min_improvement_hz;
    sample_started_ = current_now_ = now_ms; window_ = window_ms;
    baseline_known_ = digital_only_ = false;
    digital_verified_ = false;
    baseline_signed_ = best_signed_ = verified_offset_hz_ = 0;
    count_ = round_ = 0; baseline_abs_ = best_abs_ = UINT32_MAX;
    trial3_abs_ = trial8_abs_ = UINT32_MAX;
    action_ = Action::Collect;
    return true;
  }

  Action action() const { return action_; }
  const XoscTrimSelection& target() const { return target_; }
  const XoscTrimSelection& current() const { return current_; }
  uint8_t sampleCount() const { return count_; }
  uint8_t peerCount() const { return peer_count_; }
  uint8_t requiredSampleCount() const { return SampleCount * (cohort_locked_ ? peer_count_ : PeerCount); }
  bool cohortLocked() const { return cohort_locked_; }
  bool peerStatus(uint8_t index, const uint8_t*& key, uint32_t& signed_time, uint8_t& samples) const {
    key=nullptr; signed_time=0; samples=0;
    if (index>=peer_count_) return false;
    key=peers_[index].key; signed_time=peers_[index].last_signed_time; samples=peers_[index].count;
    return true;
  }
  const char* confidenceName() const {
    return !cohort_locked_ ? "pending" : peer_count_ == 3 ? "three-peer" : peer_count_ == 2 ? "two-peer" : "single-peer";
  }
  uint8_t round() const { return round_; }
  bool baselineKnown() const { return baseline_known_; }
  int32_t verifiedOffsetHz() const { return verified_offset_hz_; }
  bool digitalVerified() const { return digital_verified_; }
  void beginDigitalVerification(uint32_t now_ms) {
    if (action_ != Action::Commit) return;
    round_ = 6; sample_started_ = current_now_ = now_ms; clearBatch(); action_ = Action::Collect;
  }
  void skipDigitalCorrection() {
    if (action_ == Action::Commit && magnitude(verified_offset_hz_) < 400U) digital_verified_ = true;
  }

  void tick(uint32_t now_ms) {
    current_now_ = now_ms;
    if ((action_ == Action::Collect || action_ == Action::Apply || action_ == Action::Commit)
        && uint32_t(now_ms - started_) >= timeout_) { abort(); return; }
    // Prefer three peers, but a usable local reference is better than no
    // calibration. Bound that preference wait from the first valid sample,
    // without shortening a completely silent collection window. Lock the
    // same identities for every candidate and independent verification batch.
    if (action_ == Action::Collect && !cohort_locked_ && count_
        && (uint32_t(now_ms - first_sample_at_) >= PreferredPeersWaitMs
            || uint32_t(now_ms - sample_started_) >= window_)) {
      cohort_locked_ = true;
      finishBatch();
      return;
    }
    if (action_ == Action::Collect && uint32_t(now_ms - sample_started_) >= window_) {
      // A silent or incomplete window is unknown, never a zero error.
      if (round_ == 0) { round_ = 1; request(code(3)); }
      else if (round_ == 1) { round_ = 2; request(code(8)); }
      else if (round_ == 2) searchNeighbours();
      else if (round_ == 3) { round_ = 4; request(code(upper_)); }
      else if (round_ == 4) chooseBest();
      else abort();
    }
  }

  void cancel() { if (action_ != Action::Idle && action_ != Action::Complete) abort(); }
  // Used only when RX is deliberately disabled and the hardware cannot be
  // safely reconfigured. The caller must expose that restoration is deferred.
  void abandon() { target_ = original_; action_ = Action::Complete; count_ = 0; }

  // The caller reports whether the requested temporary hardware operation
  // completed, including successful RX restart. False always requests restore.
  void applied(bool success, uint32_t now_ms) {
    if (action_ == Action::Apply) {
      if (!success) { abort(); return; }
      current_ = target_;
      sample_started_ = now_ms;
      clearBatch();
      action_ = Action::Collect;
    } else if (action_ == Action::Restore && success) {
      current_ = original_;
      action_ = Action::Complete;
    }
  }

  // A failed persistence/verification must restore the original selection.
  void committed(bool success) {
    if (action_ != Action::Commit) return;
    if (success && !digital_verified_) return;
    if (success) action_ = Action::Complete;
    else abort();
  }
  void reset() { if (action_ == Action::Complete) action_ = Action::Idle; }

  // Signed adverts must be fresh on a trustworthy UTC clock, strictly newer
  // than prior accepted samples, from the locked identities, and local RF.
  // A zero-length route is unsigned and does not prove the transmitter was
  // physically direct. The sender/measurement binding is the caller's responsibility.
  bool observe(const AutoTrimSample& sample) {
    tick(sample.now_ms);
    if (action_ != Action::Collect || !sample.sender_key || !sample.verified
        || !sample.packet_bound || !sample.direct_rf
        || sample.frequency_hz != frequency_hz_ || sample.utc_now < 1600000000UL
        || uint32_t(sample.now_ms - sample.captured_at_ms) > 30000UL
        || sample.captured_at_ms == sample_started_
        || uint32_t(sample.captured_at_ms - sample_started_) >= 0x80000000UL
        || uint64_t(sample.signed_time) + 120ULL < sample.utc_now
        || uint64_t(sample.signed_time) > uint64_t(sample.utc_now) + 10ULL
        || sample.offset_hz < -100000 || sample.offset_hz > 100000) return false;
    unsigned peer = 0;
    while (peer < peer_count_ && memcmp(peers_[peer].key, sample.sender_key, 32)) ++peer;
    if (peer == peer_count_) {
      uint8_t any = 0; for (unsigned i = 0; i < 32; ++i) any |= sample.sender_key[i];
      if (!any || cohort_locked_ || peer_count_ == PeerCount) return false;
      memcpy(peers_[peer].key, sample.sender_key, 32); ++peer_count_;
    }
    Peer& p = peers_[peer];
    if (sample.signed_time <= p.last_signed_time || p.count == SampleCount) return false;
    current_now_ = sample.now_ms;
    p.last_signed_time = sample.signed_time;
    p.samples[p.count++] = sample.offset_hz;
    if (!count_) first_sample_at_ = sample.now_ms;
    ++count_;
    if (count_ == requiredSampleCount()) { cohort_locked_ = true; finishBatch(); }
    return true;
  }

 private:
  static uint32_t magnitude(int32_t value) {
    return value < 0 ? uint32_t(-int64_t(value)) : uint32_t(value);
  }
  static XoscTrimSelection code(uint8_t value) {
    XoscTrimSelection selection; selection.enabled = true;
    selection.a = selection.b = value; return selection;
  }
  void abort() { target_ = original_; action_ = Action::Restore; count_ = 0; }
  void request(const XoscTrimSelection& selection) {
    target_ = selection;
    if (sameXoscTrim(current_, selection)) { clearBatch(); sample_started_ = current_now_; action_ = Action::Collect; }
    else action_ = Action::Apply;
  }
  void searchNeighbours() {
    const uint8_t centre = trial3_abs_ <= trial8_abs_ ? 3 : 8;
    lower_ = uint8_t(centre - 1); upper_ = uint8_t(centre + 1);
    round_ = 3; request(code(lower_));
  }
  void chooseBest() {
    if (baseline_known_ && (sameXoscTrim(best_, original_)
        || baseline_abs_ < best_abs_ || baseline_abs_ - best_abs_ < min_improvement_)) {
      // No candidate improved the original enough to justify a crystal change.
      // Its independently verified residual can still support digital correction.
      best_ = original_; best_abs_ = baseline_abs_; best_signed_ = baseline_signed_;
      digital_only_ = true; round_ = 5; request(original_); return;
    }
    if (best_abs_ == UINT32_MAX || sameXoscTrim(best_, original_)) { abort(); return; }
    round_ = 5; request(best_);
  }
  void finishBatch() {
    int32_t medians[PeerCount];
    for (unsigned peer = 0; peer < peer_count_; ++peer) {
      int32_t sorted[SampleCount]; memcpy(sorted, peers_[peer].samples, sizeof(sorted));
      for (unsigned i = 1; i < SampleCount; ++i) {
        const int32_t v = sorted[i]; unsigned j = i;
        while (j && sorted[j - 1] > v) { sorted[j] = sorted[j - 1]; --j; }
        sorted[j] = v;
      }
      if (uint32_t(int64_t(sorted[SampleCount - 1]) - sorted[0]) > max_spread_) {
        abort(); return;
      }
      medians[peer] = sorted[SampleCount / 2];
    }
    for (unsigned i = 1; i < peer_count_; ++i) {
      const int32_t v = medians[i]; unsigned j = i;
      while (j && medians[j - 1] > v) { medians[j] = medians[j - 1]; --j; }
      medians[j] = v;
    }
    if (uint32_t(int64_t(medians[peer_count_ - 1]) - medians[0]) > max_spread_) {
      abort(); return;
    }
    const int32_t median = medians[peer_count_ / 2];
    const uint32_t error = magnitude(median);
    if (round_ == 6) {
      // The requested correction removes 75% of the residual. Allow bounded
      // estimator/quantization noise, but require meaningful improvement.
      const uint32_t before = magnitude(verified_offset_hz_);
      const int32_t expected = verified_offset_hz_ / 4;
      const uint32_t tolerance = before / 2 > 250U ? before / 2 : 250U;
      const uint32_t deviation = magnitude(median - expected);
      if (error > before * 3 / 4 || deviation > tolerance) {
        abort(); return;
      }
      digital_verified_ = true; action_ = Action::Commit; return;
    }
    if (round_ == 1) trial3_abs_ = error;
    if (round_ == 2) trial8_abs_ = error;
    if (round_ == 0) {
      baseline_known_ = true;
      baseline_abs_ = best_abs_ = error;
      baseline_signed_ = best_signed_ = median;
      // Near zero, avoid making a correction smaller than the radio's
      // measurement/programming precision. Otherwise independently verify
      // the existing trim so digital correction can work without a sweep.
      if (error < 400U) { target_ = original_; action_ = Action::Complete; return; }
      if (error <= min_improvement_) {
        digital_only_ = true; round_ = 5; request(original_); return;
      }
    }
    else if (round_ <= 4 && error < best_abs_) { best_abs_ = error; best_ = current_; best_signed_ = median; }
    if (round_ == 0) { round_ = 1; request(code(3)); return; }
    if (round_ == 1) { round_ = 2; request(code(8)); return; }
    if (round_ == 2) { searchNeighbours(); return; }
    if (round_ == 3) { round_ = 4; request(code(upper_)); return; }
    if (round_ == 4) { chooseBest(); return; }
    // Verify the selected setting using another independent batch; reject a
    // changed or transient optimum and keep the original saved preference.
    const uint32_t verification_spread = max_spread_ < 1000U ? max_spread_ : 1000U;
    if ((!baseline_known_ && error > 2000U)
        || (baseline_known_ && !digital_only_ && (error >= baseline_abs_ || baseline_abs_ - error < min_improvement_))
        || (median > best_signed_ ? int64_t(median) - best_signed_ : int64_t(best_signed_) - median) > verification_spread) {
      abort(); return;
    }
    target_ = best_;
    verified_offset_hz_ = median;
    action_ = Action::Commit;
  }

  struct Peer { uint8_t key[32]; uint32_t last_signed_time; int32_t samples[SampleCount]; uint8_t count; };
  void clearBatch() { count_ = 0; for (unsigned i = 0; i < peer_count_; ++i) peers_[i].count = 0; }
  Peer peers_[PeerCount] = {};
  uint8_t peer_count_ = 0;
  XoscTrimSelection original_, current_, best_, target_;
  uint32_t frequency_hz_ = 0, started_ = 0, timeout_ = 0, sample_started_ = 0;
  uint32_t window_ = 0, current_now_ = 0, first_sample_at_ = 0;
  uint32_t max_spread_ = 0, min_improvement_ = 0;
  uint32_t baseline_abs_ = 0, best_abs_ = 0;
  uint32_t trial3_abs_ = 0, trial8_abs_ = 0;
  int32_t baseline_signed_ = 0, best_signed_ = 0, verified_offset_hz_ = 0;
  uint8_t count_ = 0, round_ = 0, lower_ = 0, upper_ = 0;
  bool baseline_known_ = false, digital_only_ = false;
  bool digital_verified_ = false, cohort_locked_ = false;
  Action action_ = Action::Idle;
};

}} // namespace mesh::lr2021
