#include <helpers/radiolib/RxpsPulse.h>

#include <algorithm>
#include <array>
#include <cassert>
#include <cstring>
#include <iostream>
#include <string>

static void begin(RxpsPulse& pulse, uint32_t now = 1000, uint32_t timeout = 500000) {
  if(!pulse.sleep_us) assert(pulse.configure(4000,2600));
  pulse.cadence(60000);
  pulse.request(now);
  assert(!pulse.pending && !pulse.owns());
  pulse.admitted(now + 10);
  assert(pulse.pending && pulse.owns() && pulse.phase == RxpsPulse::Phase::Arm);
  pulse.armed(now + 20, timeout);
  assert(pulse.waiting() && pulse.owns());
}

static uint32_t witness(RxpsPulse& pulse, uint32_t span = 1000) {
  pulse.sample(pulse.armed_at + 1, false, false);
  pulse.sample(pulse.armed_at + pulse.rx_us, true, false);
  assert(pulse.phase == RxpsPulse::Phase::Sleep && pulse.owns());
  pulse.sample(pulse.armed_at + pulse.rx_us + span, false, false);
  return pulse.armed_at + pulse.rx_us + span;
}

static void recovered(RxpsPulse& pulse, uint32_t now, RxpsPulse::Result expected) {
  pulse.rearming();
  pulse.receiveEntry(0, now);
  assert(!pulse.pending && !pulse.owns() && pulse.phase == RxpsPulse::Phase::Idle);
  assert(pulse.takeCompletion() == expected);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);
}

static void success() {
  RxpsPulse pulse;
  pulse.sample(1, true, true);
  assert(pulse.high == 0 && pulse.aborts == 0);
  begin(pulse);
  const uint32_t end = witness(pulse);
  assert(pulse.had_cycle && !pulse.failed);
  assert(pulse.cycles == 1 && pulse.high == 1 && pulse.wake == 1);
  assert(pulse.busy_high.n == 1 && pulse.busy_high.last == 1000);
  assert(pulse.total.n == 0 && pulse.ok == 0);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);
  // Seeing the GPIO wake is insufficient until the caller completes its
  // checked stop/RTC cleanup and ordinary receive-entry path.
  pulse.receiveEntry(0, end);
  assert(pulse.pending && pulse.ok == 0);
  recovered(pulse, end + 100, RxpsPulse::Result::Applied);
  assert(pulse.req == 1 && pulse.admit == 1 && pulse.ok == 1);
  assert(pulse.fail == 0 && pulse.defer == 0 && pulse.rearm_fail == 0);
  assert(pulse.total.n == 1 && pulse.total.last == end + 100 - pulse.started);
  assert(pulse.recovery.n == 0);
}

static void missingLow() {
  RxpsPulse pulse;
  begin(pulse);
  pulse.sample(pulse.armed_at + pulse.rx_us, true, false);
  assert(pulse.pending && pulse.owns());
  assert(!pulse.had_cycle && pulse.cycles == 0);
  pulse.sample(pulse.armed_at + pulse.rx_us + 1000, false, false);
  assert(!pulse.failed && pulse.error == 0);
  assert(!pulse.had_cycle && pulse.cycles == 0);
  recovered(pulse, pulse.armed_at + pulse.rx_us + 1100, RxpsPulse::Result::Deferred);
  assert(pulse.ok == 0 && pulse.fail == 0 && pulse.defer == 1);
}

static void earlyHigh() {
  RxpsPulse pulse;
  begin(pulse);
  pulse.sample(pulse.armed_at + 1, false, false);
  pulse.sample(pulse.armed_at + pulse.rx_us - 2002, true, false);
  assert(pulse.pending && pulse.owns());
  assert(!pulse.had_cycle && pulse.cycles == 0);
  // An invalid early high still owns cleanup, and cannot request SPI while
  // the GPIO remains high. A later long high cannot rehabilitate this pulse.
  pulse.sample(pulse.armed_at + pulse.rx_us + 1000, true, false);
  assert(pulse.pending && pulse.owns() && !pulse.had_cycle);
  pulse.sample(pulse.armed_at + pulse.rx_us + 1100, false, false);
  assert(!pulse.failed && pulse.failed_step == RxpsPulse::Step::None);
  assert(!pulse.had_cycle && pulse.cycles == 0);
  recovered(pulse, pulse.armed_at + pulse.rx_us + 1200, RxpsPulse::Result::Deferred);
}

static void shortHigh() {
  RxpsPulse pulse;
  begin(pulse);
  const uint32_t end = witness(pulse, 249);
  assert(!pulse.failed && pulse.error == 0);
  assert(!pulse.had_cycle && pulse.cycles == 0);
  recovered(pulse, end + 1, RxpsPulse::Result::Deferred);
}

static void witnessBoundaries() {
  RxpsPulse pulse;
  begin(pulse);
  pulse.sample(pulse.armed_at + 1, false, false);
  const uint32_t high = pulse.armed_at + pulse.rx_us - 2001;
  pulse.sample(high, true, false);
  pulse.sample(high + 250, false, false);
  assert(pulse.had_cycle && !pulse.failed && pulse.cycles == 1);
  recovered(pulse, high + 350, RxpsPulse::Result::Applied);
}

static void highUpperBoundary() {
  for (uint32_t extra : {0U, 1U}) {
    RxpsPulse pulse;
    begin(pulse);
    const uint32_t end = witness(pulse, pulse.sleep_us + 2000 + extra);
    if (extra) {
      assert(!pulse.failed && pulse.error == 0);
      assert(!pulse.had_cycle && pulse.cycles == 0);
      recovered(pulse, end + 100, RxpsPulse::Result::Deferred);
    } else {
      assert(pulse.had_cycle && !pulse.failed && pulse.cycles == 1);
      recovered(pulse, end + 100, RxpsPulse::Result::Applied);
    }
  }
}

static void lateWake() {
  for (uint32_t delta : {0U, 1U, 20000U}) {
    RxpsPulse pulse;
    begin(pulse, 1000, 120000);
    pulse.sample(pulse.armed_at + 1, false, false);
    pulse.sample(pulse.armed_at + pulse.rx_us, true, false);
    pulse.sample(pulse.armed_at + 120000 + delta, false, false);
    assert(!pulse.failed && pulse.error == 0);
    assert(pulse.missed == 1 && !pulse.had_cycle && pulse.cycles == 0);
    recovered(pulse, pulse.armed_at + 120100 + delta, RxpsPulse::Result::Deferred);
    assert(pulse.ok == 0 && pulse.fail == 0 && pulse.defer == 1);
  }
}

static void failureRecovery() {
  RxpsPulse pulse;
  begin(pulse, 1000, 120000);
  pulse.sample(pulse.armed_at + 1, false, false);
  pulse.sample(pulse.armed_at + 120000, false, false);
  assert(!pulse.failed && pulse.error == 0);
  assert(pulse.pending && pulse.owns());
  pulse.noteError(-705, RxpsPulse::Step::RtcRead);
  assert(pulse.error == -705);
  assert(pulse.failed_step == RxpsPulse::Step::RtcRead);
  pulse.rearming();
  pulse.receiveEntry(-707, pulse.armed_at + 120010);
  assert(pulse.rearm_fail == 1 && pulse.pending && pulse.owns());
  assert(pulse.phase == RxpsPulse::Phase::Recovery);
  assert(pulse.error == -705);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);
  pulse.recovering(pulse.armed_at + 120020);
  assert(pulse.retry_at == pulse.armed_at + 120020);
  pulse.receiveEntry(0, pulse.armed_at + 121000);
  assert(!pulse.pending && pulse.fail == 1 && pulse.ok == 0);
  assert(pulse.takeCompletion() == RxpsPulse::Result::Failed);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);
  assert(pulse.recovery.n == 1 && pulse.total.n == 0);
  // A new attempt can retain its own first failing command, rather than a
  // later success or recovery replacing the evidence from that attempt.
  begin(pulse, 500000);
  pulse.noteError(-706, RxpsPulse::Step::Stage);
  pulse.noteError(-707, RxpsPulse::Step::Arm);
  recovered(pulse, 500100, RxpsPulse::Result::Failed);
  assert(pulse.error == -706 && pulse.failed_step == RxpsPulse::Step::Stage);
}

static void wrap() {
  RxpsPulse pulse;
  begin(pulse, UINT32_MAX - 60000);
  const uint32_t end = witness(pulse, 1400);
  assert(end < pulse.armed_at && pulse.had_cycle && !pulse.failed);
  recovered(pulse, end + 50, RxpsPulse::Result::Applied);
  assert(pulse.busy_high.last == 1400);
  assert(pulse.total.last == 20 + pulse.rx_us + 1400 + 50 - 10);
  RxpsPulse late;
  begin(late, UINT32_MAX - 60000, 120000);
  late.sample(late.armed_at + 1, false, false);
  late.sample(late.armed_at + late.rx_us, true, false);
  late.sample(late.armed_at + 120001, false, false);
  assert(!late.failed && late.error == 0 && !late.had_cycle);
}

static void packet() {
  RxpsPulse pulse;
  begin(pulse);
  pulse.sample(pulse.armed_at + 500, false, true);
  assert(pulse.packet && pulse.aborts == 1 && !pulse.had_cycle);
  assert(pulse.owns() && pulse.pending && pulse.phase == RxpsPulse::Phase::Stop);
  pulse.packetOwned();
  assert(!pulse.owns() && pulse.pending && pulse.phase == RxpsPulse::Phase::Packet);
  pulse.sample(pulse.armed_at + 1000, true, true);
  assert(pulse.high == 0 && pulse.aborts == 1);
  pulse.receiveEntry(0, pulse.armed_at + 2000);
  assert(!pulse.pending && !pulse.owns() && pulse.defer == 1);
  assert(pulse.ok == 0 && pulse.fail == 0 && pulse.cycles == 0);
  assert(pulse.takeCompletion() == RxpsPulse::Result::Deferred);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);
}

static void cancel() {
  RxpsPulse pulse;
  pulse.cancelRequest();
  assert(pulse.cancelled == 0);
  begin(pulse);
  pulse.cancelRequest();
  pulse.cancelRequest();
  assert(pulse.cancel && pulse.cancelled == 1 && pulse.pending && pulse.owns());
  const uint32_t end = witness(pulse);
  assert(pulse.had_cycle);
  recovered(pulse, end + 100, RxpsPulse::Result::Deferred);
  assert(pulse.ok == 0 && pulse.defer == 1 && pulse.fail == 0);
  begin(pulse, 1000000);
  assert(!pulse.cancel);
  pulse.cancelRequest();
  pulse.noteError(-705, RxpsPulse::Step::Standby);
  recovered(pulse, 1001000, RxpsPulse::Result::Failed);
  assert(pulse.cancelled == 2 && pulse.fail == 1);
}

static void abandon() {
  RxpsPulse pulse;
  begin(pulse);
  pulse.abandon(2000);
  assert(pulse.fail == 1 && !pulse.pending && !pulse.owns());
  assert(pulse.error == RxpsPulse::WitnessError);
  assert(pulse.failed_step == RxpsPulse::Step::Recovery);
  assert(pulse.recovery.n == 1 && pulse.total.n == 0);
  pulse.abandon(3000);
  pulse.receiveEntry(0, 4000);
  assert(pulse.fail == 1 && pulse.recovery.n == 1);
  assert(pulse.takeCompletion() == RxpsPulse::Result::Failed);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);
}

static void configure() {
  RxpsPulse pulse;
  for (uint32_t sleep : {6554U, 4000U, 3000U}) {
    assert(pulse.configure(sleep, 2600));
    assert(pulse.sleep_us == sleep);
    assert(pulse.sleep_ticks == (sleep == 6554 ? 253U : sleep == 4000 ? 89U : 25U));
  }
  const uint32_t before = pulse.sleep_us, ticks = pulse.sleep_ticks;
  for (uint32_t invalid : {0U, 2599U, 2600U, 2615U, UINT32_MAX}) {
    assert(!pulse.configure(invalid, 2600));
    assert(pulse.sleep_us == before && pulse.sleep_ticks == ticks);
  }
  begin(pulse);
  assert(!pulse.configure(6554, 2600));
  assert(pulse.sleep_us == before && pulse.sleep_ticks == ticks);
}

static void cadence() {
  RxpsPulse pulse;
  begin(pulse);
  pulse.cadence(60000);
  assert(!pulse.cancel && pulse.cancelled == 0);
  pulse.cadence(1000);
  assert(pulse.cancel && pulse.cancelled == 1 && pulse.pending && pulse.owns());
  const uint32_t end = witness(pulse);
  pulse.rearming();
  pulse.receiveEntry(0, end + 100);
  assert(!pulse.pending && pulse.defer == 1 && pulse.ok == 0);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);

  begin(pulse, 1000000);
  pulse.noteError(-705, RxpsPulse::Step::RtcStop);
  pulse.cadence(0);
  assert(pulse.pending && pulse.owns());
  pulse.rearming();
  pulse.receiveEntry(0, 1000100);
  assert(pulse.fail == 1 && !pulse.pending);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);

  begin(pulse, 2000000);
  pulse.cadence(0);
  pulse.abandon(2000100);
  assert(pulse.fail == 2 && !pulse.pending);
  assert(pulse.takeCompletion() == RxpsPulse::Result::None);
}

static void statistics() {
  RxpsPulse pulse;
  for (uint32_t span : {900U, 1400U, 1000U}) {
    const uint32_t start = pulse.req * 1000000 + 1000;
    begin(pulse, start);
    const uint32_t end = witness(pulse, span);
    recovered(pulse, end + 100, RxpsPulse::Result::Applied);
  }
  assert(pulse.total.n == 3 && pulse.ok == 3 && pulse.cycles == 3);
  assert(pulse.busy_high.n == 3 && pulse.busy_high.last == 1000);
  assert(pulse.busy_high.minimum == 900 && pulse.busy_high.maximum == 1400);
  assert(pulse.total.minimum < pulse.total.maximum);
  begin(pulse, 4000000);
  pulse.noteError(-707, RxpsPulse::Step::RtcEvent);
  recovered(pulse, 4001000, RxpsPulse::Result::Failed);
  assert(pulse.recovery.n == 1 && pulse.fail == 1);
  assert(pulse.total.n == 3 && pulse.ok == 3 && pulse.cycles == 3);
}


static uint32_t coarseTime(uint32_t physical) {
  return static_cast<uint32_t>((uint64_t(physical)*1024/1000000)*1000000/1024);
}

static void clockUncertainty() {
  for(uint32_t rx : {46094U,185094U}) {
    // Sweep every physical microsecond within a full coarse tick, with arm
    // return latency independent of the actual duty-cycle start at command.
    for(uint32_t phase=0;phase<977;++phase) {
      for(uint32_t latency : {0U,500U,1954U}) {
        const uint32_t origin=1000000+phase;
        const uint32_t returned=coarseTime(origin+latency);
        RxpsPulse pulse;
        pulse.rx_us=rx;begin(pulse);
        pulse.armed(returned,500000,returned-coarseTime(origin),1000);
        pulse.sample(coarseTime(origin+latency+1000),false,false);
        // Also allow the existing 2 ms radio/observer tolerance. Quantization
        // and measured arm latency must not manufacture an early-edge error.
        const uint32_t high=coarseTime(origin+rx-1000);
        pulse.sample(high,true,false);
        pulse.sample(coarseTime(origin+rx+1000),false,false);
        assert(pulse.had_cycle && !pulse.failed && pulse.missed==0);
        recovered(pulse,coarseTime(origin+rx+1500),RxpsPulse::Result::Applied);
      }
    }
  }
  // Actual retained timestamps from the RAK3401 SF12 observation.
  RxpsPulse measured;
  measured.rx_us=185094;begin(measured);
  measured.armed(1000000,500000,0,1000);
  measured.sample(1001000,false,false);
  measured.sample(1182617,true,false);
  measured.sample(1183594,false,false);
  assert(measured.had_cycle && !measured.failed);
  recovered(measured,1184000,RxpsPulse::Result::Applied);

  // A delay consuming most of the listening period cannot prove which radio
  // cycle was observed. Restore RX and silently discard it instead.
  RxpsPulse delayed;
  delayed.rx_us=1000;begin(delayed);
  delayed.armed(1000000,500000,501,1000);
  const uint32_t end=witness(delayed);
  assert(!delayed.had_cycle && !delayed.failed && delayed.error==0);
  recovered(delayed,end+100,RxpsPulse::Result::Deferred);
}

static void quietDiscard() {
  RxpsPulse pulse;begin(pulse);
  pulse.sample(pulse.armed_at+1,false,false);
  pulse.sample(pulse.armed_at+10,true,false);
  pulse.sample(pulse.armed_at+1000,false,false);
  recovered(pulse,pulse.armed_at+1100,RxpsPulse::Result::Deferred);
  assert(!pulse.ok && !pulse.fail && !pulse.error && pulse.missed==1);
  char page[160];pulse.format(0,16,page,sizeof(page));
  assert(std::strstr(page,"fail=0,ok=0,err=0"));
  // The next attempt remains eligible; a previous unconfirmed observer must
  // neither poison success nor hide a subsequent real command error.
  begin(pulse,1000000);const uint32_t end=witness(pulse);
  recovered(pulse,end+100,RxpsPulse::Result::Applied);
  assert(pulse.ok==1 && pulse.fail==0 && pulse.error==0);
  begin(pulse,2000000);
  pulse.sample(pulse.armed_at+500000,false,false);
  pulse.noteError(-705,RxpsPulse::Step::RtcStop);
  recovered(pulse,2501000,RxpsPulse::Result::Failed);
  assert(pulse.fail==1 && pulse.error==-705);
}

static void format() {
  RxpsPulse pulse;
  pulse.req = pulse.admit = pulse.defer = pulse.fail = pulse.ok = UINT32_MAX;
  pulse.cycles = pulse.aborts = pulse.missed = pulse.rearm_fail = UINT32_MAX;
  pulse.rx_us = pulse.sleep_us = pulse.rx_ticks = pulse.sleep_ticks = UINT32_MAX;
  pulse.high = pulse.wake = pulse.cancelled = pulse.forced_wake = UINT32_MAX;
  pulse.low = pulse.early = pulse.rtc = pulse.verified = pulse.irq_reads = UINT32_MAX;
  pulse.total.n = pulse.total.last = pulse.total.minimum = pulse.total.maximum = UINT32_MAX;
  pulse.busy_high = pulse.recovery = pulse.total;
  pulse.error = INT16_MIN;
  pulse.failed_step = RxpsPulse::Step::Recovery;
  pulse.pending = true;
  pulse.phase = RxpsPulse::Phase::Recovery;
  for (uint8_t page = 0; page <= 5; ++page) {
    char full[512];
    pulse.format(page, UINT32_MAX, full, sizeof(full));
    assert(std::strlen(full) < 160);
    assert(std::strncmp(full, "> v=", 4) == 0);
    for (size_t cap = 0; cap <= 160; ++cap) {
      std::array<unsigned char, 192> bytes;
      bytes.fill(0xA5);
      char* out = reinterpret_cast<char*>(bytes.data() + 16);
      pulse.format(page, UINT32_MAX, out, cap);
      for (size_t i = 0; i < 16; ++i) assert(bytes[i] == 0xA5);
      for (size_t i = 16 + cap; i < bytes.size(); ++i) assert(bytes[i] == 0xA5);
      if (cap) {
        assert(std::strlen(out) == std::min(std::strlen(full), cap - 1));
        assert(std::strncmp(out, full, cap - 1) == 0);
      }
    }
    pulse.format(page, 60, nullptr, 0);
  }
  char base[160];
  pulse.format(0, 60, base, sizeof(base));
  assert(std::strstr(base, "method=rxps,interval=60,") != nullptr);
  char failure[160];
  pulse.format(2, 60, failure, sizeof(failure));
  assert(std::strstr(failure, "step=recovery") != nullptr);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string name = argv[1];
  if (name == "clock_uncertainty") clockUncertainty();
  else if (name == "quiet_discard") quietDiscard();
  else if (name == "success") success();
  else if (name == "missing_low") missingLow();
  else if (name == "early_high") earlyHigh();
  else if (name == "short_high") shortHigh();
  else if (name == "witness_boundaries") witnessBoundaries();
  else if (name == "high_upper_boundary") highUpperBoundary();
  else if (name == "late_wake") lateWake();
  else if (name == "failure_recovery") failureRecovery();
  else if (name == "wrap") wrap();
  else if (name == "packet") packet();
  else if (name == "cancel") cancel();
  else if (name == "abandon") abandon();
  else if (name == "configure") configure();
  else if (name == "cadence") cadence();
  else if (name == "statistics") statistics();
  else if (name == "format") format();
  else assert(false);
  std::cout << "PASS " << name << '\n';
}
