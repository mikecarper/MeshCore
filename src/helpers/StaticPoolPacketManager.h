#pragma once

#include <Dispatcher.h>

// Zero rejects a candidate as unknown/denied; positive values rank eligible
// scopes, with larger values preferred when received path lengths are equal.
typedef uint8_t (*FloodScopePreference)(const mesh::Packet* packet, void* context);

class PacketQueue {
  mesh::Packet** _table;
  uint8_t* _pri_table;
  uint32_t* _schedule_table;
  int _size, _num;
  uint32_t _next_schedule;

  void rebuildNextTime();
  void applyBestFloodTransportScope(mesh::Packet* packet,
                                    FloodScopePreference scope_preference,
                                    void* scope_preference_context) const;

public:
  PacketQueue(int max_entries);
  mesh::Packet* get(uint32_t now, bool resolve_flood_scope = false,
                    FloodScopePreference scope_preference = NULL,
                    void* scope_preference_context = NULL
#if defined(MESH_W12_PRAM_SEQ_TX_LAB) && MESH_W12_PRAM_SEQ_TX_LAB
                    , uint8_t* reserved_priority = NULL, uint32_t* reserved_schedule = NULL
#endif
                    );
  mesh::Packet* peek(uint32_t now) const;
  bool add(mesh::Packet* packet, uint8_t priority, uint32_t scheduled_for);
  int count() const { return _num; }
#if defined(MESH_W12_PRAM_SEQ_TX_LAB) && MESH_W12_PRAM_SEQ_TX_LAB
  int capacity() const { return _size; }
#endif
  int countBefore(uint32_t now) const;
  bool getNextTime(uint32_t now, uint32_t& scheduled_for) const;
  mesh::Packet* itemAt(int i) const {
    if (i < 0 || i >= _num) return NULL;
    return _table[i];
  }
  mesh::Packet* removeByIdx(int i);
  bool reschedule(mesh::Packet* packet, uint32_t scheduled_for);
};

class StaticPoolPacketManager : public mesh::PacketManager {
  PacketQueue unused, send_queue, rx_queue;
  FloodScopePreference flood_scope_preference;
  void* flood_scope_preference_context;
#if defined(MESH_W12_PRAM_SEQ_TX_LAB) && MESH_W12_PRAM_SEQ_TX_LAB
  mesh::Packet* reserved_outbound = nullptr;
  uint32_t reservation_generation = 0;
  uint32_t reserved_schedule = 0;
  uint8_t reserved_priority = 0;
  bool reservationMatches(const mesh::OutboundReservation& reservation) const;
#endif

public:
  StaticPoolPacketManager(int pool_size);

  void setFloodScopePreference(FloodScopePreference preference, void* context) {
    flood_scope_preference = preference;
    flood_scope_preference_context = context;
  }

  mesh::Packet* allocNew() override;
  void free(mesh::Packet* packet) override;
  bool queueOutbound(mesh::Packet* packet, uint8_t priority, uint32_t scheduled_for) override;
  mesh::Packet* getNextOutbound(uint32_t now) override;
#if defined(MESH_W12_PRAM_SEQ_TX_LAB) && MESH_W12_PRAM_SEQ_TX_LAB
  bool supportsOutboundReservation() const override { return true; }
  bool reserveNextOutbound(uint32_t now, mesh::OutboundReservation& reservation) override;
  bool deferReservedOutbound(mesh::OutboundReservation& reservation, uint32_t retry_at) override;
  bool releaseOutboundReservation(mesh::OutboundReservation& reservation) override;
#endif
  mesh::Packet* peekNextOutbound(uint32_t now) override;
  bool deferOutbound(mesh::Packet* packet, uint32_t scheduled_for) override {
    return send_queue.reschedule(packet, scheduled_for);
  }
  int getOutboundCount(uint32_t now) const override;
  int getOutboundTotal() const override;
  bool getNextOutboundTime(uint32_t now, uint32_t& scheduled_for) const override;
  int getFreeCount() const override;
  mesh::Packet* getOutboundByIdx(int i) override;
  mesh::Packet* removeOutboundByIdx(int i) override;
  void queueInbound(mesh::Packet* packet, uint32_t scheduled_for) override;
  mesh::Packet* getNextInbound(uint32_t now) override;
  bool getNextInboundTime(uint32_t now, uint32_t& scheduled_for) const override;
};
