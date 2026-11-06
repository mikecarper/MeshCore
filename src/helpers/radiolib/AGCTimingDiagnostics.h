#pragma once

#include <stdint.h>
#include <stddef.h>
#include <stdio.h>

// Lab-only software timing. Admission and command acknowledgements do not
// measure analog readiness or prove that a sleep command powered down RX.
class AGCTimingDiagnostics {
public:
  enum class Skip : uint8_t { None, Busy, Beacon, Carrier, Packet };
  enum class Step : uint8_t { None, Sleep, Standby, Calibrate, Image, Dio2, Gain, ReadPatch, WritePatch, Poll };
  struct Samples {
    uint32_t count = 0, last = 0, minimum = 0, maximum = 0;
    void add(uint32_t value) {
      last = value;
      if (count == 0 || value < minimum) minimum = value;
      if (count == 0 || value > maximum) maximum = value;
      ++count;
    }
  };
  uint32_t periodic_requests = 0, periodic_admitted = 0, periodic_skipped = 0;
  uint32_t shared_requests = 0, shared_admitted = 0, shared_skipped = 0;
  uint32_t rearm_failures = 0, command_failures = 0, failed_sequences = 0;
  uint32_t poll_timeouts = 0, superseded_pending = 0;
  Samples helper_us, rx_gap_us;
  bool pending = false, last_periodic = false;
  Skip last_skip = Skip::None;
  Step last_error_step = Step::None;
  int16_t last_error = 0;

private:
  bool _periodic = false, _resolved = false, _sequence_failed = false;
  uint32_t _started_us = 0;

public:
  class PeriodicRequest {
    AGCTimingDiagnostics& _owner;
  public:
    explicit PeriodicRequest(AGCTimingDiagnostics& owner) : _owner(owner) {
      ++owner.periodic_requests;
      owner._periodic = true;
      owner._resolved = false;
    }
    ~PeriodicRequest() {
      // tryResetAGC can reject BUSY before reaching the common owner guard.
      if (!_owner._resolved) _owner.reject(Skip::Busy);
      _owner._periodic = false;
    }
  };
  void sequenceRequest() {
    if (!_periodic) ++shared_requests;
  }
  void reject(Skip reason) {
    if (_periodic) ++periodic_skipped;
    else ++shared_skipped;
    _resolved = true;
    last_skip = reason;
    last_periodic = _periodic;
  }
  void admit(uint32_t now_us) {
    if (_periodic) ++periodic_admitted;
    else ++shared_admitted;
    _resolved = true;
    last_periodic = _periodic;
    last_skip = Skip::None;
    if (pending) ++superseded_pending;
    pending = true;
    _started_us = now_us;
    _sequence_failed = false;
    last_error = 0;
    last_error_step = Step::None;
  }
  void commandResult(Step step, int16_t result) {
    if (result == 0) return;
    ++command_failures;
    if (!_sequence_failed) { last_error = result; last_error_step = step; }
    _sequence_failed = true;
  }
  void pollTimeout() {
    ++poll_timeouts;
    if (!_sequence_failed) last_error_step = Step::Poll;
    _sequence_failed = true;
  }
  void helperFinished(uint32_t now_us) {
    helper_us.add(now_us - _started_us);
    if (_sequence_failed) ++failed_sequences;
  }
  void receiveEntry(int16_t result, uint32_t now_us) {
    if (!pending) return;
    if (result != 0) { ++rearm_failures; return; }
    // Unsigned subtraction handles one micros() wrap. These lab intervals
    // must be shorter than 2^32 us (about 71 minutes).
    rx_gap_us.add(now_us - _started_us);
    pending = false;
  }
  static const char* skipName(Skip value) {
    switch (value) {
      case Skip::Busy: return "busy";
      case Skip::Beacon: return "beacon";
      case Skip::Carrier: return "carrier";
      case Skip::Packet: return "packet";
      default: return "none";
    }
  }
  static const char* stepName(Step value) {
    switch (value) {
      case Step::Sleep: return "sleep";
      case Step::Standby: return "standby";
      case Step::Calibrate: return "calibrate";
      case Step::Image: return "image";
      case Step::Dio2: return "dio2";
      case Step::Gain: return "gain";
      case Step::ReadPatch: return "readpatch";
      case Step::WritePatch: return "writepatch";
      case Step::Poll: return "poll";
      default: return "none";
    }
  }
  void format(uint8_t page, char* reply, size_t capacity) const {
    if (page == 1) {
      snprintf(reply, capacity,
          "> v=1,h_n=%lu,h_us=%lu,h_min=%lu,h_max=%lu,g_n=%lu,g_us=%lu,g_min=%lu,g_max=%lu",
          (unsigned long)helper_us.count, (unsigned long)helper_us.last,
          (unsigned long)helper_us.minimum, (unsigned long)helper_us.maximum,
          (unsigned long)rx_gap_us.count, (unsigned long)rx_gap_us.last,
          (unsigned long)rx_gap_us.minimum, (unsigned long)rx_gap_us.maximum);
    } else if (page == 2) {
      snprintf(reply, capacity,
          "> v=1,cmdfail=%lu,seqfail=%lu,rc=%d,step=%s,poll=%lu,overlap=%lu,skip=%s",
          (unsigned long)command_failures, (unsigned long)failed_sequences,
          (int)last_error, stepName(last_error_step), (unsigned long)poll_timeouts,
          (unsigned long)superseded_pending, skipName(last_skip));
    } else {
      snprintf(reply, capacity,
          "> v=1,pr=%lu,pa=%lu,ps=%lu,sr=%lu,sa=%lu,ss=%lu,pending=%u,origin=%s,rf=%lu",
          (unsigned long)periodic_requests, (unsigned long)periodic_admitted,
          (unsigned long)periodic_skipped, (unsigned long)shared_requests,
          (unsigned long)shared_admitted, (unsigned long)shared_skipped,
          pending ? 1U : 0U, last_periodic ? "periodic" : "shared",
          (unsigned long)rearm_failures);
    }
  }
};
