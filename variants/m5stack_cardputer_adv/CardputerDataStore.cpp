#include "CardputerDataStore.h"
#include <SD.h>  // Keep optional framework-SD discovery local to this variant.

#include "globals.h"

static void buf_to_hex_fast(const unsigned char *src, size_t src_len, char *dst) {
  static const char hex_digits[] = "0123456789abcdef";

  for (size_t i = 0; i < src_len; i++) {
    dst[i * 2] = hex_digits[(src[i] >> 4) & 0x0F]; // High nibble
    dst[i * 2 + 1] = hex_digits[src[i] & 0x0F];    // Low nibble
  }
  dst[src_len * 2] = '\0';
}

void CardputerDataStore::beginCustom(FILESYSTEM *history_fs) {
  _history_fs = history_fs;
  if (!_history_fs) {
    return;
  }
  _history_fs->mkdir(CUSTOM_DATA_DIR);
  _history_fs->mkdir(HISTORY_DIR);
  _history_fs->mkdir(HISTORY_DIR_CHANNEL);
  _history_fs->mkdir(HISTORY_DIR_DIRECT);
}

void CardputerDataStore::loadCustomPrefs(CustomNodePrefs &prefs) {
  if (!_fs->exists(CUSTOM_PREFS_FILE)) {
    return;
  }

  File file = _fs->open(CUSTOM_PREFS_FILE, FILE_READ);

  // Read file only if it exactly matches struct size, otherwise keep default values
  if (file.size() == sizeof(prefs)) {
    file.read((uint8_t *)&prefs, sizeof(prefs));
  }

  file.close();
}

void CardputerDataStore::saveCustomPrefs(const CustomNodePrefs &prefs) {
  File file = _fs->open(CUSTOM_PREFS_FILE, FILE_WRITE);

  if (!file) {
    return;
  }

  file.write((uint8_t *)&prefs, sizeof(prefs));
  file.close();
}

void CardputerDataStore::storeMessage(const uint8_t pkey[PUB_KEY_SIZE], const char *text, bool is_sent,
                                      bool is_channel, const MessageMeta *meta) {
  if (!_history_fs) {
    return;
  }
  char path[128];
  // "<< " + v2 prefix (<= 78 chars) + text; the codec swaps newlines for spaces.
  char line[MAX_MESSAGE_LENGTH + 96];
  char pkey_hex[PUB_KEY_SIZE * 2 + 1];
  uint8_t key_len = is_channel ? (PUB_KEY_SIZE / 2) : PUB_KEY_SIZE;

  buf_to_hex_fast(pkey, key_len, pkey_hex);

  // Own messages keep text only: their delivery details are session-only.
  encodeHistoryLineParts(is_sent, is_sent ? nullptr : meta, text, MAX_MESSAGE_LENGTH, line, sizeof(line));

  snprintf(path, sizeof(path), "%s/%s", is_channel ? HISTORY_DIR_CHANNEL : HISTORY_DIR_DIRECT, pkey_hex);

  File file = _history_fs->open(path, FILE_APPEND, true);

  if (!file) {
    return;
  }

  file.println(line);
  file.close();
}

static void push_history_line(ChatHistory &history, char *buf) {
  if (!isHistoryLine(buf)) {
    return;
  }
  // Legacy and v2 lines; a legacy text starting with '{' stays text.
  decodeHistoryLine(buf, *history.push_ref());
}

void CardputerDataStore::loadMessages(const uint8_t pkey[PUB_KEY_SIZE], bool is_channel,
                                      ChatHistory &history) {
  history.clear();

  if (!_history_fs) {
    return;
  }
  char path[128];
  char pkey_hex[PUB_KEY_SIZE * 2 + 1];
  char line_buf[256] = { 0 };
  uint8_t file_read_buf[1024];

  uint8_t key_len = is_channel ? (PUB_KEY_SIZE / 2) : PUB_KEY_SIZE;

  buf_to_hex_fast(pkey, key_len, pkey_hex);

  snprintf(path, sizeof(path), "%s/%s", is_channel ? HISTORY_DIR_CHANNEL : HISTORY_DIR_DIRECT, pkey_hex);

  if (!_history_fs->exists(path)) {
    return;
  }

  File file = _history_fs->open(path, FILE_READ, false);

  if (!file) {
    return;
  }

  size_t line_idx = 0;

  while (file.available()) {
    int bytesRead = file.read(file_read_buf, sizeof(file_read_buf));
    if (bytesRead <= 0) {
      break;
    }

    for (int b = 0; b < bytesRead; b++) {
      char c = (char)file_read_buf[b];

      if (c == '\n' || line_idx >= sizeof(line_buf) - 1) {
        line_buf[line_idx] = '\0';
        line_idx = 0;
        push_history_line(history, line_buf);
      } else if (c != '\r') {
        line_buf[line_idx++] = c;
      }
    }
  }

  // Handle any remaining text if the file didn't end with a trailing LF
  if (line_idx > 0) {
    line_buf[line_idx] = '\0';
    push_history_line(history, line_buf);
  }

  file.close();
}
