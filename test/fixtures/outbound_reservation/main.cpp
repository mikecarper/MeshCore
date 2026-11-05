#include <cassert>
#include <helpers/RxReservePacketManager.h>

static void test_priority_and_stale_tokens() {
  StaticPoolPacketManager manager(4), other(1);
  assert(manager.supportsOutboundReservation());
  auto* low=manager.allocNew(); auto* high=manager.allocNew(); auto* later=manager.allocNew();
  assert(low && high && later);
  assert(manager.queueOutbound(low, 5, 100));
  assert(manager.queueOutbound(high, 1, 100));
  assert(manager.queueOutbound(later, 0, 1000));
  mesh::OutboundReservation r={};
  assert(manager.reserveNextOutbound(100,r));
  assert(r.packet==high && r.owner==&manager && r.priority==1 && r.scheduled_for==100 && r.generation);
  auto copied=r;
  auto corrupted=r; ++corrupted.priority;
  assert(!manager.deferReservedOutbound(corrupted, 200));
  corrupted=r; ++corrupted.scheduled_for;
  assert(!manager.releaseOutboundReservation(corrupted));
  mesh::OutboundReservation second={};
  assert(!manager.reserveNextOutbound(100,second));
  assert(!other.deferReservedOutbound(r,200));
  assert(!manager.queueOutbound(high,0,100));
  assert(manager.deferReservedOutbound(r,200));
  assert(!r.packet && !r.owner && !manager.deferReservedOutbound(copied,200));
  assert(manager.getNextOutbound(100)==low);
  assert(manager.getNextOutbound(199)==nullptr);
  assert(manager.reserveNextOutbound(200,r) && r.packet==high && r.priority==1 && r.scheduled_for==200);
  assert(r.generation!=copied.generation);
  auto* owned=r.packet;
  assert(manager.releaseOutboundReservation(r) && !r.packet);
  assert(!manager.releaseOutboundReservation(copied));
  manager.free(owned); manager.free(low);
  assert(manager.getNextOutbound(1000)==later); manager.free(later);
}

static void test_age_and_wrap() {
  RxReservePacketManager manager(3,0);
  auto* p=manager.allocNew(); assert(p && manager.queueOutbound(p,1,100));
  mesh::OutboundReservation r={}; assert(manager.reserveNextOutbound(100,r));
  assert(manager.deferReservedOutbound(r,1000));
  // Retry must not refresh the original age, even though schedule changes.
  assert(!manager.peekNextOutbound(30101));
  assert(manager.getNextDroppedOutbound()==p); manager.free(p);

  p=manager.allocNew(); assert(p && manager.queueOutbound(p,1,0xfffffff0u));
  assert(manager.reserveNextOutbound(0xfffffff0u,r));
  assert(manager.deferReservedOutbound(r,0x10u));
  assert(!manager.getNextOutbound(0x0fu));
  assert(manager.getNextOutbound(0x10u)==p); manager.free(p);
}

static void test_failure_retains_ownership() {
  StaticPoolPacketManager manager(2);
  auto* a=manager.allocNew(); auto* b=manager.allocNew(); mesh::Packet extra;
  assert(a && b && manager.queueOutbound(a,1,5));
  mesh::OutboundReservation r={}; assert(manager.reserveNextOutbound(5,r));
  assert(manager.queueOutbound(b,1,5));
  // External packets cannot consume the rollback slot while a is reserved.
  assert(!manager.queueOutbound(&extra,1,5));
  assert(manager.deferReservedOutbound(r,20));
  assert(manager.getNextOutbound(5)==b); manager.free(b);
  assert(manager.getNextOutbound(20)==a); manager.free(a);
}

static void test_freed_reservation_cannot_be_replayed() {
  StaticPoolPacketManager manager(1); mesh::OutboundReservation r={};
  auto* p=manager.allocNew(); assert(manager.queueOutbound(p,0,1));
  assert(manager.reserveNextOutbound(1,r));
  manager.free(p);
  assert(!manager.deferReservedOutbound(r,2));
  assert(!manager.releaseOutboundReservation(r));
}
int main(){test_priority_and_stale_tokens();test_age_and_wrap();test_failure_retains_ownership();test_freed_reservation_cannot_be_replayed();}
