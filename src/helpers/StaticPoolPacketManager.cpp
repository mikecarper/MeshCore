#include "StaticPoolPacketManager.h"

PacketQueue::PacketQueue(int max_entries) {
  _table = new mesh::Packet*[max_entries];
  _pri_table = new uint8_t[max_entries];
  _schedule_table = new uint32_t[max_entries];
  _size = max_entries;
  _num = 0;
  _next_schedule = 0;
}

int PacketQueue::countBefore(uint32_t now) const {
  if (now == 0xFFFFFFFF) return _num;  // sentinel: count all entries regardless of schedule

  int n = 0;
  for (int j = 0; j < _num; j++) {
    if ((int32_t)(_schedule_table[j] - now) > 0) continue;   // scheduled for future... ignore for now
    n++;
  }
  return n;
}

bool PacketQueue::getNextTime(uint32_t now, uint32_t& scheduled_for) const {
  if (_num == 0) return false;
  scheduled_for = (int32_t)(_next_schedule - now) <= 0 ? now : _next_schedule;
  return true;
}

void PacketQueue::rebuildNextTime() {
  if (_num == 0) {
    _next_schedule = 0;
    return;
  }
  _next_schedule = _schedule_table[0];
  for (int i = 1; i < _num; i++) {
    if ((int32_t)(_schedule_table[i] - _next_schedule) < 0) {
      _next_schedule = _schedule_table[i];
    }
  }
}

mesh::Packet* PacketQueue::get(uint32_t now, bool resolve_flood_scope,
                               FloodScopePreference scope_preference,
                               void* scope_preference_context) {
  uint8_t min_pri = 0xFF;
  int best_idx = -1;
  for (int j = 0; j < _num; j++) {
    if ((int32_t)(_schedule_table[j] - now) > 0) continue;   // scheduled for future... ignore for now
    if (_pri_table[j] < min_pri) {  // select most important priority amongst non-future entries
      min_pri = _pri_table[j];
      best_idx = j;
    }
  }
  if (best_idx < 0) return NULL;   // empty, or all items are still in the future

  if (resolve_flood_scope) {
    applyBestFloodTransportScope(_table[best_idx], scope_preference,
                                 scope_preference_context);
  }
  return removeByIdx(best_idx);
}

mesh::Packet* PacketQueue::peek(uint32_t now) const {
  uint8_t min_pri = 0xFF;
  int best_idx = -1;
  for (int j = 0; j < _num; j++) {
    if ((int32_t)(_schedule_table[j] - now) > 0) continue;
    if (_pri_table[j] < min_pri) {
      min_pri = _pri_table[j];
      best_idx = j;
    }
  }
  return best_idx < 0 ? NULL : _table[best_idx];
}

mesh::Packet* PacketQueue::removeByIdx(int i) {
  if (i < 0 || i >= _num) return NULL;  // invalid index

  mesh::Packet* item = _table[i];
  uint32_t removed_schedule = _schedule_table[i];
  _num--;
  while (i < _num) {
    _table[i] = _table[i+1];
    _pri_table[i] = _pri_table[i+1];
    _schedule_table[i] = _schedule_table[i+1];
    i++;
  }
  if (_num == 0 || removed_schedule == _next_schedule) rebuildNextTime();
  return item;
}

bool PacketQueue::add(mesh::Packet* packet, uint8_t priority, uint32_t scheduled_for) {
  if (_num == _size) {
    return false;
  }
  _table[_num] = packet;
  _pri_table[_num] = priority;
  _schedule_table[_num] = scheduled_for;
  if (_num == 0 || (int32_t)(scheduled_for - _next_schedule) < 0) {
    _next_schedule = scheduled_for;
  }
  _num++;
  return true;
}

void PacketQueue::applyBestFloodTransportScope(mesh::Packet* packet,
                                               FloodScopePreference scope_preference,
                                               void* scope_preference_context) const {
  // Scope validity is application-specific. Without a validator there is no
  // safe way to distinguish a locally-allowed scope from arbitrary codes.
  if (packet == NULL || !packet->isRouteFlood()
      || packet->getPayloadType() == PAYLOAD_TYPE_TRACE
      || scope_preference == NULL) return;

  uint8_t packet_hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(packet_hash);

  const mesh::Packet* best = NULL;
  uint8_t best_hops = 0xFF;
  uint8_t best_preference = 0;
  for (int i = 0; i < _num; i++) {
    const mesh::Packet* candidate = _table[i];
    if (candidate == NULL || candidate->getRouteType() != ROUTE_TYPE_TRANSPORT_FLOOD) continue;
    uint8_t candidate_hash[MAX_HASH_SIZE];
    candidate->calculatePacketHash(candidate_hash);
    if (memcmp(packet_hash, candidate_hash, sizeof(packet_hash)) != 0) continue;

    uint8_t hops = candidate->getPathHashCount();
    uint8_t preference = scope_preference(candidate, scope_preference_context);
    if (preference == 0) continue;  // unknown, denied, or otherwise unusable here
    if (best == NULL || hops < best_hops
        || (hops == best_hops && preference > best_preference)) {
      best = candidate;
      best_hops = hops;
      best_preference = preference;
    }
  }
  if (best == NULL) return;

  // Keep the receive-quality winner's path, SNR, and schedule, but give it the
  // scope carried by the shortest equivalent scoped copy. Equal paths prefer
  // the most specific locally-recognized scope supplied by the application.
  packet->header = (packet->header & (uint8_t)~PH_ROUTE_MASK) | ROUTE_TYPE_TRANSPORT_FLOOD;
  if (packet != best) {
    memcpy(packet->transport_codes, best->transport_codes, sizeof(packet->transport_codes));
  }
}

StaticPoolPacketManager::StaticPoolPacketManager(int pool_size)
    : unused(pool_size), send_queue(pool_size), rx_queue(pool_size),
      flood_scope_preference(NULL), flood_scope_preference_context(NULL) {
  // load up our unusued Packet pool
  for (int i = 0; i < pool_size; i++) {
    unused.add(new mesh::Packet(), 0, 0);
  }
}

mesh::Packet* StaticPoolPacketManager::allocNew() {
  return unused.removeByIdx(0);  // just get first one (returns NULL if empty)
}

void StaticPoolPacketManager::free(mesh::Packet* packet) {
  unused.add(packet, 0, 0);
}

bool StaticPoolPacketManager::queueOutbound(mesh::Packet* packet, uint8_t priority, uint32_t scheduled_for) {
  if (!send_queue.add(packet, priority, scheduled_for)) {
    MESH_DEBUG_PRINTLN("queueOutbound: send queue full, dropping packet");
    return false;
  }
  return true;
}

mesh::Packet* StaticPoolPacketManager::getNextOutbound(uint32_t now) {
  //send_queue.sort();   // sort by scheduled_for/priority first
  return send_queue.get(now);
}

mesh::Packet* StaticPoolPacketManager::peekNextOutbound(uint32_t now) {
  return send_queue.peek(now);
}

int  StaticPoolPacketManager::getOutboundCount(uint32_t now) const {
  return send_queue.countBefore(now);
}

int  StaticPoolPacketManager::getOutboundTotal() const {
  return send_queue.count();
}

bool StaticPoolPacketManager::getNextOutboundTime(uint32_t now, uint32_t& scheduled_for) const {
  return send_queue.getNextTime(now, scheduled_for);
}

int StaticPoolPacketManager::getFreeCount() const {
  return unused.count();
}

mesh::Packet* StaticPoolPacketManager::getOutboundByIdx(int i) {
  return send_queue.itemAt(i);
}
mesh::Packet* StaticPoolPacketManager::removeOutboundByIdx(int i) {
  return send_queue.removeByIdx(i);
}

void StaticPoolPacketManager::queueInbound(mesh::Packet* packet, uint32_t scheduled_for) {
  if (!rx_queue.add(packet, 0, scheduled_for)) {
    MESH_DEBUG_PRINTLN("queueInbound: rx queue full, dropping packet");
    free(packet);
  }
}
mesh::Packet* StaticPoolPacketManager::getNextInbound(uint32_t now) {
  return rx_queue.get(now, true, flood_scope_preference,
                      flood_scope_preference_context);
}

bool StaticPoolPacketManager::getNextInboundTime(uint32_t now, uint32_t& scheduled_for) const {
  return rx_queue.getNextTime(now, scheduled_for);
}
