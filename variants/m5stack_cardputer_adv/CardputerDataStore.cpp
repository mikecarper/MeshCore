#include "CardputerDataStore.h"
#include <SD.h>  // Keep optional framework-SD discovery local to this variant.
#include <helpers/ContactFileTransaction.h>
#include <errno.h>
#include <math.h>
#include <sys/stat.h>

#include "globals.h"

static const char CHANNEL_HISTORY_ID_DIR[] = HISTORY_DIR "/chan2";

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
  // Leave ambiguous legacy PSK-only /chan histories intact for recovery.
  _history_fs->mkdir(CHANNEL_HISTORY_ID_DIR);
  _history_fs->mkdir(HISTORY_DIR_DIRECT);
}

static bool validCustomPrefs(const CustomNodePrefs &prefs) {
  return isfinite(prefs.battery_correction) && prefs.battery_correction >= 0.5f
      && prefs.battery_correction <= 2.0f && prefs.text_level < 3;
}

static bool customPrefsPresence(FILESYSTEM *fs, const char *path, bool &present) {
  (void)fs; // Custom preferences always use the default internal SPIFFS mount.
  char vfs_path[96];
  const int length = snprintf(vfs_path, sizeof(vfs_path), "/spiffs%s", path);
  if (length < 0 || static_cast<size_t>(length) >= sizeof(vfs_path)) return false;
  struct stat info;
  const int result = ::stat(vfs_path, &info);
  if (result != 0 && errno != ENOENT) return false;
  present = result == 0;
  return true;
}

static bool readCustomPrefs(FILESYSTEM *fs, const char *path, CustomNodePrefs &prefs) {
  File file = fs->open(path, FILE_READ);
  if (!file) return false;
  CustomNodePrefs candidate;
  const bool valid = !file.isDirectory() && file.size() == sizeof(candidate)
      && file.read(reinterpret_cast<uint8_t *>(&candidate), sizeof(candidate)) == sizeof(candidate)
      && validCustomPrefs(candidate);
  file.close();
  if (valid) prefs = candidate;
  return valid;
}

bool CardputerDataStore::loadCustomPrefs(CustomNodePrefs &prefs) {
  _custom_prefs_write_blocked = false;
  bool present = false, backup_present = false;
  if (!customPrefsPresence(_fs, CUSTOM_PREFS_FILE, present)
      || !customPrefsPresence(_fs, CUSTOM_PREFS_FILE ".bak", backup_present)) {
    _custom_prefs_write_blocked = true;
    return false;
  }
  if (present && readCustomPrefs(_fs, CUSTOM_PREFS_FILE, prefs)) return true;
  CustomNodePrefs backup;
  if (backup_present && readCustomPrefs(_fs, CUSTOM_PREFS_FILE ".bak", backup)) {
    prefs = backup;
    if (present && !_fs->remove(CUSTOM_PREFS_FILE)) {
      _custom_prefs_write_blocked = true;
      return true;
    }
    _fs->rename(CUSTOM_PREFS_FILE ".bak", CUSTOM_PREFS_FILE);
    CustomNodePrefs verified;
    _custom_prefs_write_blocked = !readCustomPrefs(_fs, CUSTOM_PREFS_FILE, verified)
        || memcmp(&verified, &backup, sizeof(backup)) != 0;
    return true;
  }
  // An unreadable existing image is not permission to replace its only copy.
  _custom_prefs_write_blocked = present || backup_present;
  return false;
}

bool CardputerDataStore::saveCustomPrefs(const CustomNodePrefs &prefs) {
  if (_custom_prefs_write_blocked || !validCustomPrefs(prefs)) return false;
  mesh::ContactFileTransaction transaction(_fs, CUSTOM_PREFS_FILE, customPrefsPresence);
  bool valid = transaction && transaction.write(reinterpret_cast<const uint8_t *>(&prefs), sizeof(prefs)) == sizeof(prefs);
  mesh::ContactFileTransaction::CommitProgress progress;
  do {
    progress = transaction.serviceCommit(valid, 1, true);
  } while (progress == mesh::ContactFileTransaction::CommitProgress::Pending);
  if (progress != mesh::ContactFileTransaction::CommitProgress::Succeeded) {
    _custom_prefs_write_blocked = transaction.outcome() == mesh::ContactFileTransaction::Outcome::Indeterminate;
    return false;
  }
  CustomNodePrefs verified;
  if (readCustomPrefs(_fs, CUSTOM_PREFS_FILE, verified) && memcmp(&verified, &prefs, sizeof(prefs)) == 0) {
    _fs->remove(CUSTOM_PREFS_FILE ".bak");
    return true;
  }
  // Keep the old verified image until the public candidate has been read back.
  CustomNodePrefs backup;
  bool backup_present = false;
  if (!customPrefsPresence(_fs, CUSTOM_PREFS_FILE ".bak", backup_present)
      || (backup_present && !readCustomPrefs(_fs, CUSTOM_PREFS_FILE ".bak", backup))
      || !_fs->remove(CUSTOM_PREFS_FILE)) {
    _custom_prefs_write_blocked = true;
    return false;
  }
  if (backup_present) {
    _fs->rename(CUSTOM_PREFS_FILE ".bak", CUSTOM_PREFS_FILE);
    _custom_prefs_write_blocked = !readCustomPrefs(_fs, CUSTOM_PREFS_FILE, verified)
        || memcmp(&verified, &backup, sizeof(backup)) != 0;
  } else {
    bool present = false;
    _custom_prefs_write_blocked = !customPrefsPresence(_fs, CUSTOM_PREFS_FILE, present) || present;
  }
  return false;
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
  buf_to_hex_fast(pkey, PUB_KEY_SIZE, pkey_hex);

  // Own messages keep text only: their delivery details are session-only.
  encodeHistoryLineParts(is_sent, is_sent ? nullptr : meta, text, MAX_MESSAGE_LENGTH, line, sizeof(line));

  snprintf(path, sizeof(path), "%s/%s", is_channel ? CHANNEL_HISTORY_ID_DIR : HISTORY_DIR_DIRECT, pkey_hex);

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

  buf_to_hex_fast(pkey, PUB_KEY_SIZE, pkey_hex);

  snprintf(path, sizeof(path), "%s/%s", is_channel ? CHANNEL_HISTORY_ID_DIR : HISTORY_DIR_DIRECT, pkey_hex);

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
