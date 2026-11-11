#pragma once

// Qualified recipes can move existing frame slots, never borrow or remove
// them. Unselected boards retain their original storage and capacity policy.
#ifndef COMPANION_RTC_FAST_OFFLINE_FRAMES
#define COMPANION_RTC_FAST_OFFLINE_FRAMES 0
#endif
#ifndef COMPANION_RTC_SLOW_OFFLINE_FRAMES
#define COMPANION_RTC_SLOW_OFFLINE_FRAMES 0
#endif
#if COMPANION_RTC_FAST_OFFLINE_FRAMES < 0 || COMPANION_RTC_SLOW_OFFLINE_FRAMES < 0
#error "RTC offline-frame counts must not be negative"
#endif

#if COMPANION_RTC_FAST_OFFLINE_FRAMES || COMPANION_RTC_SLOW_OFFLINE_FRAMES
#if !defined(ESP32_PLATFORM)
#error "RTC offline frames require a qualified ESP32 Companion recipe"
#endif
#if defined(BOARD_HAS_PSRAM) || ONE_KEY_DM_SHARED_OFFLINE_QUEUE
#error "RTC offline frames require the independent fixed-capacity queue"
#endif
#include <sdkconfig.h>
#include <esp_attr.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <type_traits>
#if defined(MESHADVENTURER) && defined(CONFIG_IDF_TARGET_ESP32) && \
    defined(COMPANION_RADIO_FULL) && defined(MESH_NOTIFY_RTC_SETTINGS) && \
    MESH_NOTIFY_RTC_SETTINGS && COMPANION_RTC_FAST_OFFLINE_FRAMES == 0 && \
    COMPANION_RTC_SLOW_OFFLINE_FRAMES == 4
#define COMPANION_RTC_CLASSIC_MESHADVENTURER 1
#else
#define COMPANION_RTC_CLASSIC_MESHADVENTURER 0
#endif
#if !defined(COMPANION_RADIO_FULL) && !COMPANION_RTC_CLASSIC_MESHADVENTURER
#error "RTC offline frames require a qualified ESP32 Companion recipe"
#endif
#if !defined(CONFIG_IDF_TARGET_ESP32C3) && !defined(CONFIG_IDF_TARGET_ESP32S3) && \
    !COMPANION_RTC_CLASSIC_MESHADVENTURER
#error "RTC offline frames require a qualified ESP32 memory layout"
#endif
#if COMPANION_RTC_FAST_OFFLINE_FRAMES && !CONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP
#error "RTC offline frames require the qualified byte-accessible retained RTC configuration"
#endif
#if COMPANION_RTC_SLOW_OFFLINE_FRAMES && !defined(CONFIG_IDF_TARGET_ESP32S3) && \
    !COMPANION_RTC_CLASSIC_MESHADVENTURER
#error "The RTC SLOW offline-frame bank requires a qualified board"
#endif
// Shared OTA retains only CPU-owned message frames in these same banks.
// BorrowableFrameBuffer keeps its context/workspace in ordinary DRAM on
// both qualified chips; it never hands an RTC address to an SDK/DMA owner.

namespace mesh {
namespace companion_rtc {

template<typename Frame> struct Lease {
  static const void*& owner() {
    static const void* value = nullptr;
    return value;
  }
};
} // namespace companion_rtc

// CPU-only backing for the ordinary FIFO. SDK transports copy frames into
// their own buffers; these banks are not handed to a DMA engine. The linker
// excludes both banks from its RTC heap and rejects physical-bank overflow.
// No retained message/session state is adopted after reboot or destruction.
template<typename Frame, size_t Count> class CompanionRtcQueue {
  static constexpr size_t FastCount = COMPANION_RTC_FAST_OFFLINE_FRAMES;
  static constexpr size_t SlowCount = COMPANION_RTC_SLOW_OFFLINE_FRAMES;
  static_assert(Count > FastCount + SlowCount,
                "RTC frames must leave a nonempty ordinary DRAM bank");
  static_assert(std::is_trivial<Frame>::value && std::is_standard_layout<Frame>::value,
                "RTC frame banks require trivial plain-data frames");
  static constexpr size_t MainCount = Count - FastCount - SlowCount;
  // Cardputer Full's linked RTC slow layout reserves 56 bytes outside the
  // frame bank. Permit its 36-frame bank while leaving room for board state.
#if defined(M5STACK_CARDPUTER_ADV) && defined(CONFIG_IDF_TARGET_ESP32S3)
  static constexpr size_t SlowBankLimit = 6400;
#else
  static constexpr size_t SlowBankLimit = 6144;
#endif
  static_assert(sizeof(Frame) * FastCount <= 6144 && sizeof(Frame) * SlowCount <= SlowBankLimit,
                "Leave RTC bank space for the SDK and retained board state");
  Frame* fast_frames;
  Frame* slow_frames;
  Frame main_frames[MainCount];

  static const void*& owner() {
    // Ordinary BSS resets on every boot, unlike the RTC byte arrays.
    return companion_rtc::Lease<Frame>::owner();
  }
  void requireOwner() const {
    if (owner() != this) abort();
  }

public:
  static constexpr size_t frame_count = Count;
  CompanionRtcQueue(Frame* fast, Frame* slow) : fast_frames(fast), slow_frames(slow) {
    if ((FastCount && (!fast || reinterpret_cast<uintptr_t>(fast) % 4))
        || (SlowCount && (!slow || reinterpret_cast<uintptr_t>(slow) % 4))) abort();
    // Release builds define NDEBUG. Never let a second object silently share
    // the first object's unread messages or rely on an assert for ownership.
    if (owner() != nullptr) abort();
    owner() = this;
    reset();
  }
  ~CompanionRtcQueue() {
    requireOwner();
    owner() = nullptr;
  }
  CompanionRtcQueue(const CompanionRtcQueue&) = delete;
  CompanionRtcQueue& operator=(const CompanionRtcQueue&) = delete;
  CompanionRtcQueue(CompanionRtcQueue&&) = delete;
  CompanionRtcQueue& operator=(CompanionRtcQueue&&) = delete;

  void reset() {
    requireOwner();
    // Clear in place. An aggregate assignment here would put the complete
    // queue on the loop/startup stack as a temporary.
    memset(main_frames, 0, sizeof(main_frames));
    if (FastCount) memset(fast_frames, 0, sizeof(Frame) * FastCount);
    if (SlowCount) memset(slow_frames, 0, sizeof(Frame) * SlowCount);
  }
  Frame& at(size_t physical_index) {
    requireOwner();
    if (physical_index >= Count) abort();
    if (physical_index < MainCount) return main_frames[physical_index];
    physical_index -= MainCount;
    if (physical_index < FastCount) return fast_frames[physical_index];
    return slow_frames[physical_index - FastCount];
  }
};

} // namespace mesh
#endif
