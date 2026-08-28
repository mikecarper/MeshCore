#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace espnow {

// ESP-NOW v1 payloads are limited to 250 bytes while MeshCore's serialized
// packet length is an unsigned byte and can therefore reach 255 bytes. Keep
// ordinary packets byte-for-byte compatible and use this envelope only for
// the five otherwise-unrepresentable lengths, 251 through 255.
static constexpr size_t ESPNOW_RAW_MAX_FRAME_SIZE = 250;
static constexpr size_t ESPNOW_RAW_MAX_PACKET_SIZE = 255;
static constexpr size_t ESPNOW_RAW_SOURCE_MAC_SIZE = 6;
static constexpr uint8_t ESPNOW_RAW_FRAGMENT_COUNT = 2;
static constexpr uint8_t ESPNOW_RAW_FRAGMENT_VERSION = 1;

// 0xFE has MeshCore payload-version bits 0b11. Current MeshCore receivers
// reject every such packet before parsing its route or payload, so this marker
// cannot collide with a valid serialized MeshCore packet. This deliberately
// reserves every raw frame beginning with 0xFE for fragmentation until a
// future MeshCore wire-version change revises the reservation. The following
// MCF bytes and explicit version make malformed envelopes fail closed.
static constexpr uint8_t ESPNOW_RAW_FRAGMENT_MAGIC[] = {
    0xFE, 0x4D, 0x43, 0x46};  // unsupported MeshCore version, "MCF"
static constexpr size_t ESPNOW_RAW_FRAGMENT_MAGIC_SIZE =
    sizeof(ESPNOW_RAW_FRAGMENT_MAGIC);

// Fragment header, all multi-byte fields in network byte order:
//   magic[4], version, count, index, total_len, offset, crc32[4]
// Fragment zero carries a full 237-byte body. Fragment one carries the
// remaining 14-18 bytes. Both frames therefore fit ESP-NOW's 250-byte limit.
static constexpr size_t ESPNOW_RAW_FRAGMENT_HEADER_SIZE = 13;
static constexpr size_t ESPNOW_RAW_FRAGMENT_DATA_SIZE =
    ESPNOW_RAW_MAX_FRAME_SIZE - ESPNOW_RAW_FRAGMENT_HEADER_SIZE;

static_assert(ESPNOW_RAW_FRAGMENT_DATA_SIZE < ESPNOW_RAW_MAX_FRAME_SIZE,
              "ESP-NOW fragment header must leave room for data");
static_assert(ESPNOW_RAW_FRAGMENT_DATA_SIZE * ESPNOW_RAW_FRAGMENT_COUNT >=
                  ESPNOW_RAW_MAX_PACKET_SIZE,
              "two ESP-NOW fragments must hold one maximum MeshCore packet");

struct ESPNowRawFrames {
  uint8_t count;
  uint16_t lengths[ESPNOW_RAW_FRAGMENT_COUNT];
  uint8_t data[ESPNOW_RAW_FRAGMENT_COUNT][ESPNOW_RAW_MAX_FRAME_SIZE];

  ESPNowRawFrames() : count(0), lengths{0, 0}, data{} {}
};

inline uint32_t espNowRawCrc32(const uint8_t* data, size_t len) {
  if (data == nullptr && len != 0) return 0;

  uint32_t crc = 0xFFFFFFFFUL;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      const uint32_t mask = 0U - (crc & 1U);
      crc = (crc >> 1) ^ (0xEDB88320UL & mask);
    }
  }
  return ~crc;
}

inline void writeEspNowRawUint32(uint8_t* dest, uint32_t value) {
  dest[0] = static_cast<uint8_t>(value >> 24);
  dest[1] = static_cast<uint8_t>(value >> 16);
  dest[2] = static_cast<uint8_t>(value >> 8);
  dest[3] = static_cast<uint8_t>(value);
}

inline uint32_t readEspNowRawUint32(const uint8_t* src) {
  return (static_cast<uint32_t>(src[0]) << 24)
      | (static_cast<uint32_t>(src[1]) << 16)
      | (static_cast<uint32_t>(src[2]) << 8)
      | static_cast<uint32_t>(src[3]);
}

/**
 * Encode one serialized MeshCore packet for raw ESP-NOW transport.
 *
 * Packets up to 250 bytes produce one exact, unwrapped frame. Only packets
 * from 251 through 255 bytes produce the versioned two-frame envelope.
 * `frames.count` is zero on every failure.
 */
inline bool encodeEspNowRawFrames(const uint8_t* packet, size_t packet_len,
                                  ESPNowRawFrames& frames) {
  frames.count = 0;
  frames.lengths[0] = 0;
  frames.lengths[1] = 0;

  if (packet == nullptr || packet_len < 2
      || packet_len > ESPNOW_RAW_MAX_PACKET_SIZE) {
    return false;
  }

  if (packet_len <= ESPNOW_RAW_MAX_FRAME_SIZE) {
    memcpy(frames.data[0], packet, packet_len);
    frames.lengths[0] = static_cast<uint16_t>(packet_len);
    frames.count = 1;
    return true;
  }

  const uint32_t checksum = espNowRawCrc32(packet, packet_len);
  for (uint8_t index = 0; index < ESPNOW_RAW_FRAGMENT_COUNT; ++index) {
    const size_t offset = index == 0 ? 0 : ESPNOW_RAW_FRAGMENT_DATA_SIZE;
    const size_t remaining = packet_len - offset;
    const size_t body_len = remaining < ESPNOW_RAW_FRAGMENT_DATA_SIZE
        ? remaining : ESPNOW_RAW_FRAGMENT_DATA_SIZE;
    uint8_t* frame = frames.data[index];

    memcpy(frame, ESPNOW_RAW_FRAGMENT_MAGIC,
           ESPNOW_RAW_FRAGMENT_MAGIC_SIZE);
    frame[4] = ESPNOW_RAW_FRAGMENT_VERSION;
    frame[5] = ESPNOW_RAW_FRAGMENT_COUNT;
    frame[6] = index;
    frame[7] = static_cast<uint8_t>(packet_len);
    frame[8] = static_cast<uint8_t>(offset);
    writeEspNowRawUint32(frame + 9, checksum);
    memcpy(frame + ESPNOW_RAW_FRAGMENT_HEADER_SIZE, packet + offset,
           body_len);
    frames.lengths[index] = static_cast<uint16_t>(
        ESPNOW_RAW_FRAGMENT_HEADER_SIZE + body_len);
  }
  frames.count = ESPNOW_RAW_FRAGMENT_COUNT;
  return true;
}

enum class ESPNowRawReassemblyResult : uint8_t {
  PASSTHROUGH,
  FRAGMENT_STORED,
  PACKET_COMPLETE,
  DUPLICATE,
  REJECTED,
  OUTPUT_TOO_SMALL,
};

/**
 * Fixed-storage ESP-NOW raw-packet reassembler.
 *
 * Slots are keyed by source MAC, complete-packet CRC, and packet length.
 * Fragments may arrive in either order. Identical fragments are harmless;
 * conflicting duplicates and malformed envelopes are rejected without
 * replacing a valid fragment. Completed identities are remembered until the
 * same timeout while their bounded slot remains resident; under slot pressure,
 * delivered duplicate-cache entries are evicted before in-flight assemblies.
 */
template <size_t SlotCount>
class ESPNowRawReassemblerT {
 public:
  static_assert(SlotCount > 0, "ESP-NOW reassembly needs at least one slot");

  explicit ESPNowRawReassemblerT(uint32_t timeout_ms = 2000)
      : _timeout_ms(timeout_ms) {
    reset();
  }

  void reset() {
    memset(_slots, 0, sizeof(_slots));
  }

  size_t expire(uint32_t now_ms) {
    size_t expired = 0;
    for (size_t i = 0; i < SlotCount; ++i) {
      if (_slots[i].state != SLOT_EMPTY
          && elapsed(now_ms, _slots[i].updated_ms) >= _timeout_ms) {
        clearSlot(_slots[i]);
        ++expired;
      }
    }
    return expired;
  }

  ESPNowRawReassemblyResult acceptFrame(
      const uint8_t source_mac[ESPNOW_RAW_SOURCE_MAC_SIZE],
      const uint8_t* frame, size_t frame_len, uint32_t now_ms,
      uint8_t* packet_out, size_t packet_capacity, size_t& packet_len) {
    packet_len = 0;
    expire(now_ms);

    if (frame == nullptr || frame_len == 0
        || frame_len > ESPNOW_RAW_MAX_FRAME_SIZE) {
      return ESPNowRawReassemblyResult::REJECTED;
    }

    // Valid current MeshCore packets can never begin with 0xFE. Reserve that
    // entire prefix so truncated or corrupted envelopes cannot be handed to
    // the ordinary MeshCore parser as if they were legacy raw frames.
    if (frame[0] != ESPNOW_RAW_FRAGMENT_MAGIC[0]) {
      if (packet_out == nullptr || packet_capacity < frame_len) {
        return ESPNowRawReassemblyResult::OUTPUT_TOO_SMALL;
      }
      memcpy(packet_out, frame, frame_len);
      packet_len = frame_len;
      return ESPNowRawReassemblyResult::PASSTHROUGH;
    }

    FragmentInfo fragment;
    if (source_mac == nullptr || !parseFragment(frame, frame_len, fragment)) {
      return ESPNowRawReassemblyResult::REJECTED;
    }

    Slot* slot = findSlot(source_mac, fragment.total_len, fragment.checksum);
    if (slot == nullptr) {
      slot = allocateSlot(now_ms);
      initializeSlot(*slot, source_mac, fragment.total_len,
                     fragment.checksum, now_ms);
    }

    const uint8_t bit = static_cast<uint8_t>(1U << fragment.index);
    if ((slot->received_mask & bit) != 0) {
      if (memcmp(slot->packet + fragment.offset, fragment.body,
                 fragment.body_len) != 0) {
        return ESPNowRawReassemblyResult::REJECTED;
      }
      return ESPNowRawReassemblyResult::DUPLICATE;
    }
    if (slot->state == SLOT_DELIVERED) {
      // A delivered slot necessarily has both bits, but fail closed if its
      // metadata was ever corrupted rather than writing into it again.
      return ESPNowRawReassemblyResult::REJECTED;
    }

    memcpy(slot->packet + fragment.offset, fragment.body, fragment.body_len);
    slot->received_mask = static_cast<uint8_t>(slot->received_mask | bit);
    slot->updated_ms = now_ms;

    const uint8_t all_fragments =
        static_cast<uint8_t>((1U << ESPNOW_RAW_FRAGMENT_COUNT) - 1U);
    if (slot->received_mask != all_fragments) {
      return ESPNowRawReassemblyResult::FRAGMENT_STORED;
    }

    if (espNowRawCrc32(slot->packet, slot->total_len) != slot->checksum) {
      clearSlot(*slot);
      return ESPNowRawReassemblyResult::REJECTED;
    }
    if (packet_out == nullptr || packet_capacity < slot->total_len) {
      clearSlot(*slot);
      return ESPNowRawReassemblyResult::OUTPUT_TOO_SMALL;
    }

    memcpy(packet_out, slot->packet, slot->total_len);
    packet_len = slot->total_len;
    slot->state = SLOT_DELIVERED;
    slot->updated_ms = now_ms;
    return ESPNowRawReassemblyResult::PACKET_COMPLETE;
  }

 private:
  enum SlotState : uint8_t {
    SLOT_EMPTY = 0,
    SLOT_ASSEMBLING = 1,
    SLOT_DELIVERED = 2,
  };

  struct FragmentInfo {
    uint8_t index;
    uint8_t total_len;
    uint8_t offset;
    uint32_t checksum;
    const uint8_t* body;
    size_t body_len;
  };

  struct Slot {
    SlotState state;
    uint8_t source_mac[ESPNOW_RAW_SOURCE_MAC_SIZE];
    uint8_t total_len;
    uint8_t received_mask;
    uint32_t checksum;
    uint32_t updated_ms;
    uint8_t packet[ESPNOW_RAW_MAX_PACKET_SIZE];
  };

  static uint32_t elapsed(uint32_t now_ms, uint32_t then_ms) {
    return static_cast<uint32_t>(now_ms - then_ms);
  }

  static void clearSlot(Slot& slot) {
    slot.state = SLOT_EMPTY;
    slot.received_mask = 0;
  }

  static bool parseFragment(const uint8_t* frame, size_t frame_len,
                            FragmentInfo& fragment) {
    if (frame_len < ESPNOW_RAW_FRAGMENT_HEADER_SIZE
        || memcmp(frame, ESPNOW_RAW_FRAGMENT_MAGIC,
                  ESPNOW_RAW_FRAGMENT_MAGIC_SIZE) != 0
        || frame[4] != ESPNOW_RAW_FRAGMENT_VERSION
        || frame[5] != ESPNOW_RAW_FRAGMENT_COUNT
        || frame[6] >= ESPNOW_RAW_FRAGMENT_COUNT
        || frame[7] <= ESPNOW_RAW_MAX_FRAME_SIZE
        || frame[7] > ESPNOW_RAW_MAX_PACKET_SIZE) {
      return false;
    }

    fragment.index = frame[6];
    fragment.total_len = frame[7];
    fragment.offset = frame[8];
    fragment.checksum = readEspNowRawUint32(frame + 9);
    fragment.body = frame + ESPNOW_RAW_FRAGMENT_HEADER_SIZE;
    fragment.body_len = frame_len - ESPNOW_RAW_FRAGMENT_HEADER_SIZE;

    const size_t expected_offset = fragment.index == 0
        ? 0 : ESPNOW_RAW_FRAGMENT_DATA_SIZE;
    const size_t expected_body_len = fragment.index == 0
        ? ESPNOW_RAW_FRAGMENT_DATA_SIZE
        : static_cast<size_t>(fragment.total_len)
              - ESPNOW_RAW_FRAGMENT_DATA_SIZE;
    return fragment.offset == expected_offset
        && fragment.body_len == expected_body_len
        && static_cast<size_t>(fragment.offset) + fragment.body_len
               <= fragment.total_len;
  }

  Slot* findSlot(const uint8_t* source_mac, uint8_t total_len,
                 uint32_t checksum) {
    for (size_t i = 0; i < SlotCount; ++i) {
      Slot& slot = _slots[i];
      if (slot.state != SLOT_EMPTY && slot.total_len == total_len
          && slot.checksum == checksum
          && memcmp(slot.source_mac, source_mac,
                    ESPNOW_RAW_SOURCE_MAC_SIZE) == 0) {
        return &slot;
      }
    }
    return nullptr;
  }

  Slot* allocateSlot(uint32_t now_ms) {
    for (size_t i = 0; i < SlotCount; ++i) {
      if (_slots[i].state == SLOT_EMPTY) return &_slots[i];
    }

    // Delivered identities are only a duplicate-suppression cache, so evict
    // the oldest of those before sacrificing an in-flight reassembly.
    Slot* candidate = nullptr;
    uint32_t candidate_age = 0;
    for (size_t i = 0; i < SlotCount; ++i) {
      Slot& slot = _slots[i];
      if (slot.state != SLOT_DELIVERED) continue;
      const uint32_t age = elapsed(now_ms, slot.updated_ms);
      if (candidate == nullptr || age > candidate_age) {
        candidate = &slot;
        candidate_age = age;
      }
    }
    if (candidate != nullptr) return candidate;

    // If every bounded slot is assembling, retain the freshest work and
    // replace the oldest. No input can grow memory usage beyond SlotCount.
    candidate = &_slots[0];
    candidate_age = elapsed(now_ms, candidate->updated_ms);
    for (size_t i = 1; i < SlotCount; ++i) {
      const uint32_t age = elapsed(now_ms, _slots[i].updated_ms);
      if (age > candidate_age) {
        candidate = &_slots[i];
        candidate_age = age;
      }
    }
    return candidate;
  }

  static void initializeSlot(Slot& slot, const uint8_t* source_mac,
                             uint8_t total_len, uint32_t checksum,
                             uint32_t now_ms) {
    slot.state = SLOT_ASSEMBLING;
    memcpy(slot.source_mac, source_mac, ESPNOW_RAW_SOURCE_MAC_SIZE);
    slot.total_len = total_len;
    slot.received_mask = 0;
    slot.checksum = checksum;
    slot.updated_ms = now_ms;
  }

  uint32_t _timeout_ms;
  Slot _slots[SlotCount];
};

using ESPNowRawReassembler = ESPNowRawReassemblerT<4>;

}  // namespace espnow
}  // namespace mesh
