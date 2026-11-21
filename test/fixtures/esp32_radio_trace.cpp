#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <helpers/radiolib/ESP32RadioTrace.h>

using namespace mesh::radiotrace;
static int radio;
static constexpr uint64_t Base = UINT64_C(0x100000000) + 100000;

static void begin(Trace& t) {
  t.context(&radio, 0, 7);
  assert(t.arm(Base, 0, MaxCaptureMs));
  assert(t.tick(Base));
}

static void lifetime() {
  Trace t;
  assert(!t.pending() && !t.tick(Base));
  assert(!t.arm(Base, 0, 0));
  assert(!t.arm(Base, MaxDelayMs + 1, 1));
  assert(!t.arm(Base, 0, MaxCaptureMs + 1));
  assert(t.arm(Base, 10, 20));
  assert(t.session == 1 && t.state == State::Armed);
  assert(!t.arm(Base, 0, 1));
  assert(!t.tick(Base + 9999));
  assert(t.tick(Base + 10000));
  assert(t.state == State::Capturing);
  assert(!t.tick(Base + 30000));
  assert(t.state == State::Frozen && !t.pending());
  assert(t.arm(Base + 40000, 0, 1) && t.session == 2);
  t.stop(Base + 40500);
  assert(t.state == State::Frozen);
  assert(t.arm(Base, 10, 10));
  t.stop(Base);
  assert(t.state == State::Frozen && !t.span_count && !t.flags);
}

static void holdsAndReceipts() {
  Trace t; begin(t);
  t.receiveEntry(&radio, Base, Base + 1, 0);
  t.irq(&radio, Base + 10, Base + 11, 0, 0);
  t.activity(&radio, Base + 20, Base + 21, true, false, None);
  t.activity(&radio, Base + 30, Base + 31, true, false, None);
  t.activity(&radio, Base + 40, Base + 41, true, true, None);
  t.activity(&radio, Base + 50, Base + 51, true, true, None);
  assert(t.span_count == 1 && t.spans[0].begin_us == 21);
  assert(t.spans[0].header_us == 41 && t.spans[0].end_us == NoTime);
  t.irq(&radio, Base + 60, Base + 61, 0, 2);
  auto i = t.receiveRead(&radio, Base + 70);
  assert(i == 0 && t.rx_count == 1);
  assert(t.rx[0].done_lo_us == 10 && t.rx[0].done_hi_us == 61);
  assert(t.rx[0].span_index == 0 && t.spans[0].rx_index == 0);
  assert(t.spans[0].end_us == 70 && t.spans[0].reason == ReceiveRead);
  t.operation(&radio, Base + 80, Rearm);
  t.receiveEntry(&radio, Base + 80, Base + 90, 0);
  t.receiveFinish(&radio, i, Base + 100, UINT64_C(0xaabbccddeeff1234), 101, true);
  assert(t.rx[0].hash64 == UINT64_C(0xaabbccddeeff1234));
  assert(t.rx[0].len == 101 && !t.rx[0].flags);
  assert(t.rx[0].rearm_end_us == 90 && t.max_blackout_us == 20);
  t.activity(&radio, Base + 110, Base + 111, true, false, None);
  t.activity(&radio, Base + 210, Base + 211, false, false, PreambleExpired);
  assert(t.spans[1].header_us == NoTime && t.spans[1].end_us == 210);
  assert(t.spans[1].reason == PreambleExpired);
  t.irq(&radio, Base + 220, Base + 221, 0, 2);
  i = t.receiveRead(&radio, Base + 230);
  assert(t.rx[i].span_index == NoIndex); // no stale abandoned-span link
  t.receiveEntry(&radio, Base + 231, Base + 235, -1);
  t.receiveFinish(&radio, i, Base + 240, 0, 0, false);
  assert((t.rx[i].flags & (Invalid | RearmFailed | ReadError)) == (Invalid | RearmFailed | ReadError));
}

static void transmitBounds() {
  Trace t; begin(t);
  t.transmitPrepare(&radio, Base + 10, 0x1234, 101, 397824);
  t.transmitLaunch(&radio, Base + 20, Base + 30, Base + 30, 0);
  t.dio(&radio, Base + 400000, Base + 400001, false);
  t.irq(&radio, Base + 400010, Base + 400020, 0, 0);
  t.irq(&radio, Base + 400030, Base + 400040, 0, 1);
  t.dio(&radio, Base + 400050, Base + 400051, false); // cannot move a latched bound
  t.irq(&radio, Base + 400060, Base + 400070, 0, 1); // retain first positive
  t.transmitFinish(&radio, Base + 400080, true);
  auto& r = t.tx[0];
  assert(t.tx_count == 1 && !r.flags && r.hash64 == 0x1234);
  assert(r.done_lo_us == 400010 && r.done_hi_us == 400040);
  assert(r.launch_lo_us == 20 && r.launch_hi_us == 30 && r.busy_low_us == 30);
  assert(r.toa_us == 397824 && !r.spi_errors);
  t.transmitFinish(&radio, Base + 400090, false); // normal finish cleanup is inert
  assert(!r.flags);
  t.transmitPrepare(&radio, Base + 500000, 0x5678, 101, 652288);
  t.transmitLaunch(&radio, Base + 500001, Base + 500002, Base + 500003, 0);
  t.irq(&radio, Base + 500010, Base + 500020, 0, 1);
  t.transmitFinish(&radio, Base + 500030, true);
  assert(t.tx[1].flags == MissingBound); // old TX cannot donate its lower bound
  for (uint32_t error : {0U, uint32_t(-705)}) {
    Trace fault; begin(fault);
    fault.transmitPrepare(&radio, Base + 10, 1, 101, error);
    assert(fault.tx[0].toa_us == NoTime && (fault.tx[0].flags & Invalid));
  }
}

static void spiFailure() {
  Trace t; begin(t);
  t.transmitPrepare(&radio, Base + 10, 1, 101, 100);
  t.transmitLaunch(&radio, Base + 11, Base + 12, Base + 13, 0);
  t.irq(&radio, Base + 20, Base + 21, -707, 0);
  t.irq(&radio, Base + 30, Base + 31, 0, 1);
  t.transmitFinish(&radio, Base + 40, true);
  assert(t.spi_errors == 1 && t.invalidations == 1);
  assert((t.tx[0].flags & (Invalid | SpiError | MissingBound)) == (Invalid | SpiError | MissingBound));
  assert(t.tx[0].spi_errors == 1);
  Trace r; begin(r);
  r.receiveEntry(&radio, Base, Base + 1, 0);
  r.activity(&radio, Base + 10, Base + 11, true, false, None);
  r.irq(&radio, Base + 20, Base + 21, -707, 0);
  assert(r.spans[0].reason == SpiFailure && r.spans[0].end_us == 20);
  r.irq(&radio, Base + 30, Base + 31, 0, 2);
  auto i = r.receiveRead(&radio, Base + 40);
  assert(r.rx[i].flags & MissingBound);
}

static void interruptionsAndFreeze() {
  Trace t; begin(t);
  t.activity(&radio, Base + 10, Base + 11, true, true, None);
  t.operation(&radio, Base + 20, Reconfigure);
  assert(t.spans[0].end_us == 20 && t.spans[0].reason == Reconfigure);
  t.transmitPrepare(&radio, Base + 30, 1, 101, 100);
  t.stop(Base + 40);
  assert(t.state == State::Frozen && (t.flags & Invalid));
  assert((t.tx[0].flags & (Invalid | Clipped | NotConfirmed)) == (Invalid | Clipped | NotConfirmed));
  const auto frozen = t;
  t.activity(&radio, Base + 50, Base + 51, true, false, None);
  t.irq(&radio, Base + 60, Base + 61, 0, 1);
  t.transmitFinish(&radio, Base + 70, true);
  assert(!std::memcmp(&frozen, &t, sizeof(t)));
  Trace r; begin(r);
  r.receiveEntry(&radio, Base, Base + 1, 0);
  r.irq(&radio, Base + 10, Base + 11, 0, 2);
  r.receiveRead(&radio, Base + 20);
  r.tick(r.end_us);
  assert(r.state == State::Frozen && (r.rx[0].flags & Clipped));
}

static void overflowAndOwnership() {
  Trace t; begin(t);
  for (size_t i = 0; i < SpanCapacity + 2; ++i) {
    t.activity(&radio, Base + i * 10 + 1, Base + i * 10 + 2, true, false, None);
    t.activity(&radio, Base + i * 10 + 3, Base + i * 10 + 4, false, false, HeaderError);
  }
  assert(t.span_count == SpanCapacity && t.span_dropped == 2);
  assert(t.spans[0].begin_us == 2 && (t.flags & Invalid));
  Trace r; begin(r);
  for (size_t i = 0; i < RxCapacity + 1; ++i) {
    const auto record = r.receiveRead(&radio, Base + i * 10 + 1);
    r.receiveEntry(&radio, Base + i * 10 + 2, Base + i * 10 + 3, 0);
    r.receiveFinish(&radio, record, Base + i * 10 + 4, i, 101, true);
  }
  assert(r.rx_count == RxCapacity && r.rx_dropped == 1);
  Trace s; begin(s);
  for (size_t i = 0; i < TxCapacity + 1; ++i) {
    s.transmitPrepare(&radio, Base + i * 10 + 1, i, 101, 100);
    s.transmitFinish(&radio, Base + i * 10 + 2, false);
  }
  assert(s.tx_count == TxCapacity && s.tx_dropped == 1);
  int other;
  s.context(&other, 1, 9);
  assert(s.context_errors == 1);
  const auto count = s.tx_count;
  s.transmitPrepare(&other, Base + 10000, 0, 101, 100);
  assert(s.tx_count == count);
}

static void scanTiming() {
  Trace t; begin(t);
  t.scan(&radio, Base + 10, Base, 100);
  t.scan(&radio, Base + 1000, Base, 100, false);
  t.scan(&radio, Base + 2010, Base + 2000, 100);
  assert(t.max_scan_gap_us == 2000 && t.max_visit_overdue_us == 900);
  assert(t.buckets[0].max_scan_gap_us == 2000);
  assert(t.buckets[0].max_scan_gap_end_us == 2010);
  assert(t.buckets[0].max_visit_overdue_end_us == 1000);
  t.scan(&radio, Base + BucketWidthUs + 10, Base + BucketWidthUs, 100);
  assert(t.bucket_count == 2 && t.buckets[1].start_us == BucketWidthUs);
  assert(t.buckets[0].max_scan_gap_end_us == 2010);
  // Expiry is exclusive, so the final valid bucket is 59 for 180 seconds.
  t.scan(&radio, Base + uint64_t(MaxCaptureMs) * 1000 - 1, 0, 0);
  assert(t.bucket_count == 60 && !(t.flags & Invalid));
}

static void epochsAndMissingLaunch() {
  Trace t; begin(t);
  t.transmitPrepare(&radio, Base + 10, 1, 101, 100);
  t.dio(&radio, Base + 11, Base + 12, false);
  t.irq(&radio, Base + 13, Base + 14, 0, 1); // previous epoch, before SetTx
  t.transmitLaunchBegin(&radio, Base + 20);
  t.transmitLaunch(&radio, Base + 20, Base + 21, Base + 22, 0);
  t.irq(&radio, Base + 120, Base + 121, 0, 1);
  t.transmitFinish(&radio, Base + 122, true);
  assert(t.tx[0].done_hi_us == 121 && t.tx[0].flags == MissingBound);
  t.transmitPrepare(&radio, Base + 200, 2, 101, 100);
  t.irq(&radio, Base + 201, Base + 202, 0, 1);
  t.transmitFinish(&radio, Base + 203, true);
  assert((t.tx[1].flags & (Invalid | MissingBound | NotConfirmed)) == (Invalid | MissingBound | NotConfirmed));

  Trace r;r.context(&radio,0,1);
  assert(r.arm(Base,10,1000));
  r.receiveEntry(&radio,Base+100,Base+101,0); // establish mode while armed
  r.irq(&radio,Base+10010,Base+10011,0,0);
  r.irq(&radio,Base+10020,Base+10021,0,2);
  auto i=r.receiveRead(&radio,Base+10030);
  assert(!(r.rx[i].flags&MissingBound));
  r.receiveEntry(&radio,Base+10031,Base+10032,0);
  r.receiveFinish(&radio,i,Base+10033,1,101,true);
  r.stop(Base+10040);
  assert(r.arm(Base+20000,0,1000)); // stale mode/bounds from old capture cannot survive
  r.irq(&radio,Base+20010,Base+20011,0,0);
  r.irq(&radio,Base+20020,Base+20021,0,2);
  i=r.receiveRead(&radio,Base+20030);
  assert(r.rx[i].flags&MissingBound);
  Trace receiver;begin(receiver);
  receiver.context(&radio,0,7);
  receiver.context(&radio,0,8);
  assert((receiver.flags&(Invalid|Interrupted))==(Invalid|Interrupted));
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!std::strcmp(argv[1], "lifetime")) lifetime();
  else if (!std::strcmp(argv[1], "holds")) holdsAndReceipts();
  else if (!std::strcmp(argv[1], "tx")) transmitBounds();
  else if (!std::strcmp(argv[1], "spi")) spiFailure();
  else if (!std::strcmp(argv[1], "freeze")) interruptionsAndFreeze();
  else if (!std::strcmp(argv[1], "overflow")) overflowAndOwnership();
  else if (!std::strcmp(argv[1], "scan")) scanTiming();
  else if (!std::strcmp(argv[1], "epochs")) epochsAndMissingLaunch();
  else assert(false);
}
