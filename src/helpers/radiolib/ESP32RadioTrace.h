#pragma once

#if defined(ESP32_PLATFORM) && defined(MESH_HIL_RADIO_TRACE) && MESH_HIL_RADIO_TRACE
#define MESH_RADIO_TRACE_ENABLED 1

#include <stddef.h>
#include <stdint.h>
#include "RadioAirtime.h"

namespace mesh {
namespace radiotrace {

// Explicit lab builds only. No I/O, allocation, extra radio polling, or policy
// decisions occur here. Times are microseconds relative to a bounded capture.
enum class State : uint8_t { Off = 0, Armed = 1, Capturing = 2, Frozen = 3 };
enum Reason : uint8_t {
  None = 0, HeaderError = 1, PreambleExpired = 2, PayloadExpired = 3,
  FlagsCleared = 4, Reconfigure = 5, ReceiveRead = 6, Rearm = 7,
  Transmit = 8, Recovery = 9, CaptureEnded = 10, SpiFailure = 11,
};
enum Flag : uint8_t {
  Invalid = 1, SpiError = 2, MissingBound = 4, Interrupted = 8,
  Clipped = 16, NotConfirmed = 32, RearmFailed = 64, ReadError = 128,
};

static constexpr uint32_t MaxCaptureMs = 180000;
static constexpr uint32_t MaxDelayMs = 180000;
static constexpr uint32_t BucketWidthUs = 3000000;
static constexpr uint16_t NoIndex = UINT16_MAX;
static constexpr uint32_t NoTime = UINT32_MAX;
#if defined(MESH_HIL_RADIO_TRACE_TX_ONLY) && MESH_HIL_RADIO_TRACE_TX_ONLY
static constexpr size_t SpanCapacity = 0, RxCapacity = 0, BucketCapacity = 0;
static constexpr const char* Marker = "MCRFTRACE1:TXONLY40";
#else
static constexpr size_t SpanCapacity = 192, RxCapacity = 192, BucketCapacity = 64;
static constexpr const char* Marker = "MCRFTRACE1:RX192:TX40";
#endif
static constexpr size_t TxCapacity = 40;

struct Span {
  uint32_t begin_us, header_us, end_us;
  uint16_t rx_index;
  uint8_t profile, reason;
};
struct RxRecord {
  uint32_t done_lo_us, done_hi_us, read_begin_us, rearm_end_us;
  uint64_t hash64;
  uint16_t len, span_index;
  uint8_t profile, flags;
  uint16_t reserved;
};
struct TxRecord {
  uint64_t hash64;
  uint32_t launch_lo_us, launch_hi_us, busy_low_us;
  uint32_t done_lo_us, done_hi_us, toa_us;
  uint16_t len;
  uint8_t profile, flags;
  uint32_t spi_errors;
};
struct Bucket {
  uint32_t start_us, max_scan_gap_us, max_visit_overdue_us;
  uint32_t max_scan_gap_end_us, max_visit_overdue_end_us;
};
static_assert(sizeof(Span) == 16, "Radio trace span layout changed");
static_assert(sizeof(RxRecord) == 32, "Radio trace RX layout changed");
static_assert(sizeof(TxRecord) == 40, "Radio trace TX layout changed");

class Trace {
  const void* _phy = nullptr;
  uint8_t _profile = 0;
  uint32_t _generation = 0;
  uint32_t _seen_generation[2] = {};
  uint8_t _seen_profiles = 0;
  uint16_t _hold = NoIndex, _reading = NoIndex, _sending = NoIndex;
  uint16_t _last_hold = NoIndex;
  bool _rx_mode = false, _rx_lo_valid = false, _rx_done = false;
  bool _tx_lo_valid = false, _tx_done = false;
  uint32_t _rx_lo = 0, _rx_hi = 0, _tx_lo = 0, _last_scan = NoTime;

  bool owner(const void* phy) const { return phy != nullptr && phy == _phy; }
  uint32_t relative(uint64_t now) const {
    return now <= epoch_us ? 0 : static_cast<uint32_t>(now - epoch_us);
  }
  void resetCounters() {
    span_count = rx_count = tx_count = bucket_count = 0;
    span_dropped = rx_dropped = tx_dropped = spi_errors = invalidations = 0;
    context_errors = max_scan_gap_us = max_visit_overdue_us = max_blackout_us = 0;
    flags = 0;
    _hold = _reading = _sending = _last_hold = NoIndex;
    _rx_mode = false;
    _rx_lo_valid = _rx_done = _tx_lo_valid = _tx_done = false;
    _last_scan = NoTime;
    _seen_profiles = 0;
  }
  void invalidate(uint8_t detail) {
    ++invalidations;
    flags |= uint32_t(Invalid | detail);
    if (_reading != NoIndex) rx[_reading].flags |= uint8_t(Invalid | detail);
    if (_sending != NoIndex) tx[_sending].flags |= uint8_t(Invalid | detail);
  }
  void closeHold(uint32_t when, Reason reason) {
    if (_hold == NoIndex) return;
    Span& span = spans[_hold];
    span.end_us = when < span.begin_us ? span.begin_us : when;
    span.reason = reason;
    _last_hold = _hold;
    _hold = NoIndex;
  }
  void freeze(uint64_t when) {
    const uint32_t stamp = relative(when > end_us ? end_us : when);
    closeHold(stamp, CaptureEnded);
    if (_sending != NoIndex) {
      tx[_sending].flags |= uint8_t(Invalid | Clipped | NotConfirmed);
      _sending = NoIndex;
      invalidate(Clipped);
    }
    if (_reading != NoIndex) {
      rx[_reading].flags |= uint8_t(Invalid | Clipped);
      _reading = NoIndex;
      invalidate(Clipped);
    }
    state = State::Frozen;
  }

public:
  State state = State::Off;
  uint32_t session = 0;
  uint64_t epoch_us = 0, end_us = 0;
  uint32_t span_count = 0, rx_count = 0, tx_count = 0, bucket_count = 0;
  uint32_t span_dropped = 0, rx_dropped = 0, tx_dropped = 0;
  uint32_t spi_errors = 0, invalidations = 0, context_errors = 0;
  uint32_t max_scan_gap_us = 0, max_visit_overdue_us = 0, max_blackout_us = 0;
  uint32_t flags = 0;
  Span spans[SpanCapacity ? SpanCapacity : 1] = {};
  RxRecord rx[RxCapacity ? RxCapacity : 1] = {};
  TxRecord tx[TxCapacity] = {};
  Bucket buckets[BucketCapacity ? BucketCapacity : 1] = {};

  bool pending() const { return state == State::Armed || state == State::Capturing; }
  bool arm(uint64_t now, uint32_t delay_ms, uint32_t duration_ms) {
    if (pending() || delay_ms > MaxDelayMs || !duration_ms || duration_ms > MaxCaptureMs)
      return false;
    resetCounters();
    ++session;
    if (!session) ++session;
    epoch_us = now + uint64_t(delay_ms) * 1000;
    end_us = epoch_us + uint64_t(duration_ms) * 1000;
    state = State::Armed;
    return true;
  }
  void stop(uint64_t now) { if (pending()) freeze(now); }
  bool tick(uint64_t now) {
    if (!pending()) return false;
    if (now >= end_us) { freeze(end_us); return false; }
    if (now < epoch_us) return false;
    if (state == State::Armed) {
      state = State::Capturing;
      // Existing activity at the boundary has no observed beginning. Subsequent
      // true polls can establish a conservative, shorter ownership span.
      _rx_lo_valid = _rx_done = _tx_lo_valid = _tx_done = false;
    }
    return true;
  }
  void context(const void* phy, uint8_t profile, uint32_t generation) {
    if (!_phy || !pending()) _phy = phy;
    if (!owner(phy)) {
      if (pending()) { ++context_errors; invalidate(Interrupted); }
      return;
    }
    if (profile > 1) {
      if (pending()) { ++context_errors; invalidate(Interrupted); }
      return;
    }
    if (pending()) {
      const uint8_t bit = uint8_t(1U << profile);
      // The sender-only experiment deliberately replaces its single physical
      // tuple between completed transmissions. Receiver captures require a
      // stable pair; neither mode permits changing an in-flight TX epoch.
      if (RxCapacity && state == State::Capturing && (_seen_profiles & bit)
          && _seen_generation[profile] != generation) invalidate(Interrupted);
      _seen_generation[profile] = generation;
      _seen_profiles |= bit;
    }
    if (state == State::Capturing && (profile != _profile || generation != _generation)) {
      if (_hold != NoIndex) invalidate(Interrupted);
      if (_sending != NoIndex) {
        invalidate(Interrupted);
        _tx_lo_valid = _tx_done = false;
      }
      closeHold(_last_scan == NoTime ? 0 : _last_scan, Reconfigure);
      _rx_lo_valid = _rx_done = false;
    }
    _profile = profile;
    _generation = generation;
  }
  void operation(const void* phy, uint64_t now, Reason reason) {
    if (!owner(phy) || !pending()) return;
    const bool capturing = tick(now);
    _rx_mode = false;
    _rx_lo_valid = _rx_done = false;
    if (capturing) closeHold(relative(now), reason);
  }
  void receiveEntry(const void* phy, uint64_t before, uint64_t after, int16_t result) {
    if (!owner(phy) || !pending()) return;
    const bool capturing = tick(after);
    _rx_mode = result == 0;
    _rx_lo_valid = _rx_done = false;
    if (!capturing) return;
    closeHold(relative(before), Rearm);
    if (_reading != NoIndex) {
      RxRecord& record = rx[_reading];
      record.rearm_end_us = relative(after);
      const uint32_t elapsed = record.rearm_end_us - record.read_begin_us;
      if (elapsed > max_blackout_us) max_blackout_us = elapsed;
      if (result != 0) record.flags |= uint8_t(Invalid | RearmFailed);
    }
  }
  void dio(const void* phy, uint64_t before, uint64_t after, bool high) {
    if (!owner(phy) || !tick(after) || high || before < epoch_us) return;
    if (_sending != NoIndex && tx[_sending].launch_lo_us != NoTime && !_tx_done) {
      _tx_lo = relative(before); _tx_lo_valid = true;
    }
    if (_rx_mode && !_rx_done) { _rx_lo = relative(before); _rx_lo_valid = true; }
  }
  void irq(const void* phy, uint64_t before, uint64_t after, int16_t result, uint32_t irq) {
    if (!owner(phy) || !tick(after)) return;
    if (result != 0) {
      ++spi_errors;
      invalidate(SpiError);
      closeHold(relative(before), SpiFailure);
      _rx_lo_valid = _tx_lo_valid = false;
      return;
    }
    if (_sending != NoIndex && tx[_sending].launch_lo_us != NoTime && !_tx_done) {
      if (irq & 1U) {
        TxRecord& record = tx[_sending];
        record.done_lo_us = _tx_lo;
        record.done_hi_us = relative(after);
        if (!_tx_lo_valid) record.flags |= MissingBound;
        _tx_done = true;
      } else if (before >= epoch_us) {
        _tx_lo = relative(before);
        _tx_lo_valid = true;
      }
    }
    if (_rx_mode && !_rx_done) {
      if (irq & 2U) { _rx_hi = relative(after); _rx_done = true; }
      else if (before >= epoch_us) { _rx_lo = relative(before); _rx_lo_valid = true; }
    }
  }
  void activity(const void* phy, uint64_t before, uint64_t after, bool held,
                bool header, Reason reason) {
    if (!owner(phy) || !tick(after) || !SpanCapacity) return;
    if (!held) { closeHold(relative(before), reason); return; }
    if (_hold == NoIndex) {
      if (span_count >= SpanCapacity) { ++span_dropped; invalidate(Clipped); return; }
      _hold = static_cast<uint16_t>(span_count++);
      spans[_hold] = {relative(after), header ? relative(after) : NoTime,
                     NoTime, NoIndex, _profile, None};
    } else if (header && spans[_hold].header_us == NoTime) {
      spans[_hold].header_us = relative(after);
    }
  }
  uint16_t receiveRead(const void* phy, uint64_t now) {
    if (!owner(phy) || !tick(now) || !RxCapacity) return NoIndex;
    closeHold(relative(now), ReceiveRead);
    if (rx_count >= RxCapacity) { ++rx_dropped; invalidate(Clipped); return NoIndex; }
    if (_reading != NoIndex) { invalidate(Interrupted); _reading = NoIndex; }
    const uint16_t index = static_cast<uint16_t>(rx_count++);
    _reading = index;
    const uint16_t span = _last_hold != NoIndex && spans[_last_hold].reason == ReceiveRead
        ? _last_hold : NoIndex;
    rx[index] = {_rx_lo, _rx_hi, relative(now), NoTime, 0, 0, span, _profile, 0, 0};
    if (!_rx_done || !_rx_lo_valid) rx[index].flags |= MissingBound;
    if (_last_hold != NoIndex && spans[_last_hold].reason == ReceiveRead)
      spans[_last_hold].rx_index = index;
    _last_hold = NoIndex;
    _rx_lo_valid = _rx_done = false;
    return index;
  }
  void receiveFinish(const void* phy, uint16_t index, uint64_t now,
                     uint64_t hash, uint16_t length, bool good) {
    if (!owner(phy) || !tick(now) || index == NoIndex || index >= rx_count) return;
    RxRecord& record = rx[index];
    record.hash64 = hash;
    record.len = length;
    if (!good) record.flags |= uint8_t(Invalid | ReadError);
    if (record.rearm_end_us == NoTime) record.flags |= uint8_t(Invalid | RearmFailed);
    if (_reading == index) _reading = NoIndex;
  }
  void transmitPrepare(const void* phy, uint64_t now, uint64_t hash,
                       uint16_t length, uint32_t toa) {
    if (!owner(phy) || !tick(now)) return;
    closeHold(relative(now), Transmit);
    _rx_mode = _rx_lo_valid = _rx_done = false;
    if (_sending != NoIndex) { invalidate(Interrupted); _sending = NoIndex; }
    if (tx_count >= TxCapacity) { ++tx_dropped; invalidate(Clipped); return; }
    _sending = static_cast<uint16_t>(tx_count++);
    tx[_sending] = {hash, NoTime, NoTime, NoTime, NoTime, NoTime,
                    toa, length, _profile, NotConfirmed, spi_errors};
    if (!toa || isEncodedRadioLibAirtimeError(toa)) {
      tx[_sending].toa_us = NoTime;
      tx[_sending].flags |= Invalid;
    }
    _tx_lo_valid = _tx_done = false;
  }
  void transmitLaunch(const void* phy, uint64_t before, uint64_t after,
                      uint64_t busy_low, int16_t result) {
    if (!owner(phy) || !tick(busy_low > after ? busy_low : after) || _sending == NoIndex) return;
    TxRecord& record = tx[_sending];
    record.launch_lo_us = relative(before);
    record.launch_hi_us = relative(after);
    record.busy_low_us = busy_low ? relative(busy_low) : NoTime;
    if (result != 0) record.flags |= uint8_t(Invalid | Interrupted);
  }
  void transmitLaunchBegin(const void* phy, uint64_t now) {
    if (!owner(phy) || !tick(now) || _sending == NoIndex) return;
    tx[_sending].launch_lo_us = relative(now);
    _tx_lo_valid = _tx_done = false;
  }
  void transmitFinish(const void* phy, uint64_t now, bool confirmed) {
    if (!owner(phy) || !tick(now) || _sending == NoIndex) return;
    TxRecord& record = tx[_sending];
    record.spi_errors = spi_errors - record.spi_errors;
    if (confirmed && _tx_done) record.flags &= uint8_t(~NotConfirmed);
    else record.flags |= uint8_t(Invalid | Interrupted | NotConfirmed);
    if (!_tx_lo_valid || !_tx_done || record.launch_lo_us == NoTime
        || record.launch_hi_us == NoTime || record.busy_low_us == NoTime)
      record.flags |= MissingBound;
    _sending = NoIndex;
    _tx_lo_valid = _tx_done = false;
  }
  void scan(const void* phy, uint64_t now, uint64_t visit_started, uint32_t visit_us,
            bool gap_sample = true) {
    if (!owner(phy) || !tick(now)) return;
    const uint32_t stamp = relative(now);
    const uint32_t gap = !gap_sample || _last_scan == NoTime ? 0 : stamp - _last_scan;
    if (gap_sample) _last_scan = stamp;
    const uint64_t elapsed = now >= visit_started ? now - visit_started : 0;
    const uint64_t late = visit_us && elapsed > visit_us ? elapsed - visit_us : 0;
    const uint32_t overdue = late > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(late);
    if (gap > max_scan_gap_us) max_scan_gap_us = gap;
    if (overdue > max_visit_overdue_us) max_visit_overdue_us = overdue;
    if (!BucketCapacity) return;
    const uint32_t index = stamp / BucketWidthUs;
    if (index >= BucketCapacity) { invalidate(Clipped); return; }
    while (bucket_count <= index) {
      buckets[bucket_count] = {bucket_count * BucketWidthUs, 0, 0, 0, 0};
      ++bucket_count;
    }
    Bucket& bucket = buckets[index];
    if (gap > bucket.max_scan_gap_us) {
      bucket.max_scan_gap_us = gap;
      bucket.max_scan_gap_end_us = stamp;
    }
    if (overdue > bucket.max_visit_overdue_us) {
      bucket.max_visit_overdue_us = overdue;
      bucket.max_visit_overdue_end_us = stamp;
    }
  }
};

static_assert(sizeof(Trace) <= 12 * 1024, "Optional radio trace exceeded its RAM budget");
#if defined(MESH_HIL_RADIO_TRACE_TX_ONLY) && MESH_HIL_RADIO_TRACE_TX_ONLY
static_assert(sizeof(Trace) <= 3 * 1024, "Sender-only trace exceeded its RAM budget");
#endif

Trace& trace();
uint64_t nowUs();
uint64_t fingerprint(const uint8_t* bytes, size_t length);

}  // namespace radiotrace
}  // namespace mesh

#else
#define MESH_RADIO_TRACE_ENABLED 0
#endif
