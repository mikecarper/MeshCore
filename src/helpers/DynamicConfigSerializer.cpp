#include "DynamicConfigSerializer.h"

#include <string.h>

#define PROP_SEP_CHAR   '|'
#define KEY_SEP_CHAR    ':'

namespace {

size_t boundedLength(const char* text, size_t capacity) {
  if (text == nullptr) return capacity;
  size_t length = 0;
  while (length < capacity && text[length] != 0) length++;
  return length;
}

bool isValidKey(const char* key, size_t& key_len) {
  key_len = boundedLength(key, CONFIG_MAX_KEYLEN);
  if (key_len == 0 || key_len >= CONFIG_MAX_KEYLEN) return false;

  for (size_t i = 0; i < key_len; i++) {
    const char c = key[i];
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_')) {
      return false;
    }
  }
  return true;
}

bool isValidValue(const char* value, size_t& value_len) {
  value_len = boundedLength(value, MAX_DYNAMIC_CONFG);
  if (value_len >= MAX_DYNAMIC_CONFG) return false;

  for (size_t i = 0; i < value_len; i++) {
    const uint8_t c = static_cast<uint8_t>(value[i]);
    // ':' and '|' are internal delimiters. Control characters would produce
    // invalid serialized configuration because ConfigSerializer only escapes
    // a subset of them.
    if (c == static_cast<uint8_t>(KEY_SEP_CHAR)
        || c == static_cast<uint8_t>(PROP_SEP_CHAR)
        || c < 0x20 || c == 0x7F) {
      return false;
    }
  }
  return true;
}

bool appendEntry(char* destination, size_t capacity, size_t& used,
                 const char* key, size_t key_len,
                 const char* value, size_t value_len) {
  if (destination == nullptr || key == nullptr || value == nullptr
      || used >= capacity) {
    return false;
  }
  const size_t separator_len = used == 0 ? 0 : 1;
  const size_t available = capacity - used;
  if (separator_len + key_len + 1 + value_len + 1 > available) return false;

  if (separator_len != 0) destination[used++] = PROP_SEP_CHAR;
  memcpy(destination + used, key, key_len);
  used += key_len;
  destination[used++] = KEY_SEP_CHAR;
  memcpy(destination + used, value, value_len);
  used += value_len;
  destination[used] = 0;
  return true;
}

}  // namespace

bool DynamicConfigSerializer::setByKeyPrv(const char* key, const char* value) {
  size_t key_len = 0;
  size_t value_len = 0;
  if (!isValidKey(key, key_len) || !isValidValue(value, value_len)) return false;

  if (_fallback && _fallback->setByKey(key, value)) return true;

  const size_t config_len = boundedLength(_config, sizeof(_config));
  if (config_len >= sizeof(_config)) return false;

  char tmp[MAX_DYNAMIC_CONFG];
  memcpy(tmp, _config, config_len + 1);

  char new_config[MAX_DYNAMIC_CONFG];
  new_config[0] = 0;
  size_t used = 0;

  char* item = tmp;
  while (*item != 0) {
    char* next = strchr(item, PROP_SEP_CHAR);
    if (next != nullptr) *next = 0;

    char* item_separator = strchr(item, KEY_SEP_CHAR);
    if (item_separator == nullptr || item_separator == item) return false;
    *item_separator = 0;

    size_t item_key_len = 0;
    size_t item_value_len = 0;
    const char* item_value = item_separator + 1;
    if (!isValidKey(item, item_key_len)
        || !isValidValue(item_value, item_value_len)) {
      return false;
    }

    const bool replacing = item_key_len == key_len
        && memcmp(item, key, key_len) == 0;
    if (!replacing
        && !appendEntry(new_config, sizeof(new_config), used,
                        item, item_key_len, item_value, item_value_len)) {
      return false;
    }

    if (next == nullptr) break;
    item = next + 1;
    if (*item == 0) return false;  // reject a trailing/consecutive separator
  }

  if (!appendEntry(new_config, sizeof(new_config), used,
                   key, key_len, value, value_len)) {
    return false;
  }

  memcpy(_config, new_config, used + 1);
  return true;
}

bool DynamicConfigSerializer::setByKey(const char* key, const char* value) {
  if (setByKeyPrv(key, value)) {
    markDirty();
    return true;
  }
  return false;
}

bool DynamicConfigSerializer::getByKey(const char* key, char* value, size_t max_len) {
  if (value == nullptr || max_len == 0) return false;

  size_t key_len = 0;
  if (!isValidKey(key, key_len)) {
    value[0] = 0;
    return false;
  }

  if (_fallback && _fallback->getByKey(key, value, max_len)) return true;

  value[0] = 0;
  const size_t config_len = boundedLength(_config, sizeof(_config));
  if (config_len >= sizeof(_config)) return false;

  char tmp[MAX_DYNAMIC_CONFG];
  memcpy(tmp, _config, config_len + 1);

  char* item = tmp;
  while (*item != 0) {
    char* next = strchr(item, PROP_SEP_CHAR);
    if (next != nullptr) *next = 0;

    char* item_separator = strchr(item, KEY_SEP_CHAR);
    if (item_separator == nullptr || item_separator == item) return false;
    *item_separator = 0;

    size_t item_key_len = 0;
    size_t item_value_len = 0;
    const char* item_value = item_separator + 1;
    if (!isValidKey(item, item_key_len)
        || !isValidValue(item_value, item_value_len)) {
      return false;
    }

    if (item_key_len == key_len && memcmp(item, key, key_len) == 0) {
      if (item_value_len >= max_len) return false;
      memcpy(value, item_value, item_value_len + 1);
      return true;
    }

    if (next == nullptr) break;
    item = next + 1;
    if (*item == 0) return false;
  }
  return false;
}

void DynamicConfigSerializer::structure() {
  if (_context->op() == OP::WRITE) {
    const size_t config_len = boundedLength(_config, sizeof(_config));
    if (config_len >= sizeof(_config)) {
      _context->success = false;
      return;
    }

    char tmp[MAX_DYNAMIC_CONFG];
    memcpy(tmp, _config, config_len + 1);

    char* item = tmp;
    while (*item != 0) {
      char* next = strchr(item, PROP_SEP_CHAR);
      if (next != nullptr) *next = 0;

      char* item_separator = strchr(item, KEY_SEP_CHAR);
      if (item_separator == nullptr || item_separator == item) {
        _context->success = false;
        return;
      }
      *item_separator = 0;

      size_t item_key_len = 0;
      size_t item_value_len = 0;
      char* item_value = item_separator + 1;
      if (!isValidKey(item, item_key_len)
          || !isValidValue(item_value, item_value_len)) {
        _context->success = false;
        return;
      }
      def(item, item_value, item_value_len + 1);

      if (next == nullptr) break;
      item = next + 1;
      if (*item == 0) {
        _context->success = false;
        return;
      }
    }
  } else {
    if (!setByKeyPrv(_context->getKey(getDepth()), _context->getToken())) {
      _context->success = false;
    }
  }
}
