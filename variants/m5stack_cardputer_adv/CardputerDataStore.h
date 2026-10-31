#pragma once

#include <DataStore.h>
#include "globals.h"
#include "RingBuffer.h"
#include "HistoryLine.h"

//** Custom node preferences, persisted to file. Power saving is the
//   companion's own powersaving_enabled pref (shared with the CLI). */
struct __attribute__((packed)) CustomNodePrefs {
  float battery_correction;
  uint8_t brightness;
  uint8_t text_level;  // CardputerDisplay::TextLevel
};

// HistoryMessage / MessageMeta and the SD line codec live in HistoryLine.h.
using ChatHistory = RingBuffer<HistoryMessage, CHAT_HISTORY_RINGBUF_SIZE>;

// Contacts, channels and node prefs stay on the primary (internal) FS managed
// by DataStore. Cardputer UI prefs live there too; per-chat history is kept
// only when a microSD card is mounted.
// Channel identities include the complete name/protocol alias and 32-byte key.
// Legacy /history/chan PSK-only logs remain on SD for manual recovery; they
// cannot be assigned safely to one channel and are never silently imported.
class CardputerDataStore : public DataStore {
private:
  FILESYSTEM *_fs;
  FILESYSTEM *_history_fs = nullptr;
  bool _custom_prefs_write_blocked = false;

public:
  CardputerDataStore(FILESYSTEM &fs, mesh::RTCClock &clock) : DataStore(fs, clock), _fs(&fs) {}

  // Call after the SD card is mounted (or not); nullptr disables history.
  void beginCustom(FILESYSTEM *history_fs);
  bool hasHistory() const { return _history_fs != nullptr; }
  bool loadCustomPrefs(CustomNodePrefs &prefs);
  bool saveCustomPrefs(const CustomNodePrefs &prefs);
  // For channels, pkey is the 32-byte channelHistoryId, not the wire PSK.
  // Received messages with meta->has persist as v2 lines; own messages persist text only.
  void storeMessage(const uint8_t pkey[PUB_KEY_SIZE], const char *text, bool is_sent, bool is_channel,
                    const MessageMeta *meta = nullptr);
  void loadMessages(const uint8_t pkey[PUB_KEY_SIZE], bool is_channel, ChatHistory &history);
};
