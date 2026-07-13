#pragma once

#include <Dispatcher.h>

class PacketQueue {
  mesh::Packet** _table;
  uint8_t* _pri_table;
  uint32_t* _schedule_table;
  int _size, _num;
  uint32_t _next_schedule;

  void rebuildNextTime();

public:
  PacketQueue(int max_entries);
  mesh::Packet* get(uint32_t now);
  mesh::Packet* peek(uint32_t now) const;
  bool add(mesh::Packet* packet, uint8_t priority, uint32_t scheduled_for);
  int count() const { return _num; }
  int countBefore(uint32_t now) const;
  bool getNextTime(uint32_t now, uint32_t& scheduled_for) const;
  mesh::Packet* itemAt(int i) const { return _table[i]; }
  mesh::Packet* removeByIdx(int i);
};

class StaticPoolPacketManager : public mesh::PacketManager {
  PacketQueue unused, send_queue, rx_queue;

public:
  StaticPoolPacketManager(int pool_size);

  mesh::Packet* allocNew() override;
  void free(mesh::Packet* packet) override;
  bool queueOutbound(mesh::Packet* packet, uint8_t priority, uint32_t scheduled_for) override;
  mesh::Packet* getNextOutbound(uint32_t now) override;
  mesh::Packet* peekNextOutbound(uint32_t now) override;
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
