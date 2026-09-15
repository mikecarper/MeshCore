#pragma once

#include <stddef.h>
#include <string.h>

enum class CompanionBluetoothCommandSource { Other, Terminal, Framed };
enum class CompanionBluetoothCommand { None, Get, On, Off, ForceOff, Invalid };

inline const char* skipBluetoothCommandSpace(const char* text) {
  while (*text == ' ' || *text == '\t') ++text;
  return text;
}

inline bool bluetoothCommandWord(const char* text, const char* word) {
  const size_t length = strlen(word);
  return strncmp(text, word, length) == 0
      && *skipBluetoothCommandSpace(text + length) == 0;
}

inline CompanionBluetoothCommand parseCompanionBluetoothCommand(const char* text) {
  if (!text) return CompanionBluetoothCommand::None;
  text = skipBluetoothCommandSpace(text);
  if (bluetoothCommandWord(text, "get bluetooth")
      || bluetoothCommandWord(text, "get ble")) return CompanionBluetoothCommand::Get;
  const char* value = nullptr;
  const char* const prefixes[] = {"set bluetooth", "set ble"};
  for (const char* prefix : prefixes) {
    const size_t length = strlen(prefix);
    if (strncmp(text, prefix, length) == 0
        && (text[length] == 0 || text[length] == ' ' || text[length] == '\t')) {
      value = skipBluetoothCommandSpace(text + length);
      break;
    }
  }
  if (!value) return CompanionBluetoothCommand::None;
  if (bluetoothCommandWord(value, "on")) return CompanionBluetoothCommand::On;
  if (bluetoothCommandWord(value, "off")) return CompanionBluetoothCommand::Off;
  if (strncmp(value, "off", 3) == 0 && (value[3] == ' ' || value[3] == '\t')
      && bluetoothCommandWord(skipBluetoothCommandSpace(value + 3), "force")) {
    return CompanionBluetoothCommand::ForceOff;
  }
  return CompanionBluetoothCommand::Invalid;
}

bool handleCompanionBluetoothCommand(
    const char* command, char* reply, size_t reply_size,
    CompanionBluetoothCommandSource source = CompanionBluetoothCommandSource::Other);
