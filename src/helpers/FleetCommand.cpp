#include "FleetCommand.h"
#include <SHA256.h>
#include <string.h>

namespace mesh {
namespace {

constexpr char Domain[] = "MeshCoreFleet1";
constexpr size_t SignedMessageCapacity = sizeof(Domain) - 1
    + FleetCommand::KeySize + FleetCommand::HeaderSize
    + FleetCommand::MaxCommandLength;
constexpr uint8_t PublicKey[FleetCommand::KeySize] = {
  0x8b, 0x33, 0x87, 0xe9, 0xc5, 0xcd, 0xea, 0x6a,
  0xc9, 0xe5, 0xed, 0xba, 0xa1, 0x15, 0xcd, 0x72
};

bool zero(const uint8_t* value, size_t size) {
  uint8_t combined = 0;
  for (size_t i = 0; i < size; ++i) combined |= value[i];
  return combined == 0;
}

void erase(uint8_t* bytes, size_t length) {
  volatile uint8_t* destination = bytes;
  while (length--) *destination++ = 0;
}

void write32(uint8_t* dest, uint32_t value) {
  for (unsigned i = 0; i < 4; ++i) dest[i] = value >> (i * 8);
}

uint32_t read32(const uint8_t* source) {
  uint32_t value = 0;
  for (unsigned i = 0; i < 4; ++i) value |= uint32_t(source[i]) << (i * 8);
  return value;
}

void targetHash(const uint8_t* full_key, uint8_t* target) {
  SHA256 hash;
  hash.update(full_key, PUB_KEY_SIZE);
  hash.finalize(target, FleetCommand::TargetSize);
}

int hexDigit(char digit) {
  if (digit >= '0' && digit <= '9') return digit - '0';
  if (digit >= 'a' && digit <= 'f') return digit - 'a' + 10;
  if (digit >= 'A' && digit <= 'F') return digit - 'A' + 10;
  return -1;
}

bool lifetimeAllowed(uint32_t sequence, uint32_t expires) {
  return sequence >= FleetCommand::MinEpoch && expires >= sequence
      && expires - sequence <= FleetCommand::MaxLifetime;
}

size_t signedMessage(const uint8_t* key, const uint8_t* payload,
                     size_t length, uint8_t* output) {
  const size_t prefix = sizeof(Domain) - 1 + FleetCommand::KeySize;
  memcpy(output, Domain, sizeof(Domain) - 1);
  memcpy(output + sizeof(Domain) - 1, key, FleetCommand::KeySize);
  memcpy(output + prefix, payload, length);
  return prefix + length;
}

bool keyEquals(const char* key, size_t length, const char* candidate) {
  return strlen(candidate) == length && !memcmp(key, candidate, length);
}

// The CLI handler owns board-specific range checks. Authorization admits
// only the exact namespace and a strictly numeric optional row suffix.
bool rowKey(const char* key, size_t length, const char* family) {
  const size_t family_length = strlen(family);
  if (length < family_length || memcmp(key, family, family_length)) return false;
  if (length == family_length) return true;
  if (key[family_length] != '.') return false;
  size_t cursor = family_length + 1;
  if (cursor == length || key[cursor] < '1' || key[cursor] > '9') return false;
  unsigned row = 0;
  for (; cursor < length; ++cursor) {
    if (key[cursor] < '0' || key[cursor] > '9') return false;
    row = row * 10 + unsigned(key[cursor] - '0');
    if (row > 255) return false;
  }
  return true;
}

bool decimalToken(const char* text, size_t length) {
  if (!length) return false;
  bool dot = false, digit = false;
  for (size_t i = 0; i < length; ++i) {
    if (text[i] >= '0' && text[i] <= '9') digit = true;
    else if (text[i] == '.' && !dot) dot = true;
    else return false;
  }
  return digit;
}

bool integerToken(const char* text, size_t length) {
  if (!length) return false;
  for (size_t i = 0; i < length; ++i)
    if (text[i] < '0' || text[i] > '9') return false;
  return true;
}

bool radioTuple(const char* arguments, bool temporary) {
  if (!strcmp(arguments, "off")) return true;
  unsigned fields = 0;
  const unsigned expected = temporary ? 6 : 5;
  const char* cursor = arguments;
  for (;;) {
    const char* end = strchr(cursor, ',');
    const size_t length = end ? size_t(end - cursor) : strlen(cursor);
    bool valid;
    if (fields < 2) valid = decimalToken(cursor, length);
    else if (fields < 4 || (temporary && fields == 5))
      valid = integerToken(cursor, length);
    else if (fields == 4) valid = keyEquals(cursor, length, "rx")
        || keyEquals(cursor, length, "rxtx");
    else valid = fields == expected
        && (integerToken(cursor, length) || keyEquals(cursor, length, "auto"));
    if (!valid || ++fields > expected + 1) return false;
    if (!end) return fields == expected || fields == expected + 1;
    cursor = end + 1;
  }
}

} // namespace

bool FleetCommand::privateKeyAllowed(const uint8_t* key) {
  return key && !zero(key, KeySize) && memcmp(key, PublicKey, KeySize) != 0;
}

bool FleetCommand::parseTarget(const char* text, uint8_t* target) {
  if (!text || !target) return false;
  if (!strcmp(text, "all")) {
    memset(target, 0, TargetSize);
    return true;
  }
  if (strlen(text) != PUB_KEY_SIZE * 2) return false;
  uint8_t full_key[PUB_KEY_SIZE];
  for (size_t i = 0; i < PUB_KEY_SIZE; ++i) {
    const int upper = hexDigit(text[i * 2]);
    const int lower = hexDigit(text[i * 2 + 1]);
    if (upper < 0 || lower < 0) return false;
    full_key[i] = uint8_t((upper << 4) | lower);
  }
  // An invalid zero identity must not masquerade as the reserved all target.
  if (zero(full_key, PUB_KEY_SIZE)) return false;
  targetHash(full_key, target);
  return !zero(target, TargetSize);
}

bool FleetCommand::commandAllowed(const char* command) {
  if (!command) return false;
  size_t length = 0;
  while (command[length]) {
    const unsigned char byte = command[length];
    if (++length > MaxCommandLength || byte < 0x20 || byte > 0x7e
        || byte == ';' || byte == '|' || byte == '&' || byte == '`'
        || byte == '\\') return false;
  }
  if (!length || command[0] == ' ' || command[length - 1] == ' ') return false;
  bool get = false, set = false, del = false;
  if (!strncmp(command, "get ", 4)) get = true;
  else if (!strncmp(command, "set ", 4)) set = true;
  else if (!strncmp(command, "del ", 4)) del = true;
  else return false;
  const char* key = command + 4;
  const char* separator = strchr(key, ' ');
  const size_t key_length = separator ? size_t(separator - key) : strlen(key);
  const char* arguments = separator ? separator + 1 : "";
  if (!key_length || (separator && (!*arguments || *arguments == ' '))) return false;

  if (keyEquals(key, key_length, "radio2")
      || keyEquals(key, key_length, "tempradio2")) {
    return get ? !*arguments : set && radioTuple(arguments, key_length == 10);
  }
  if (keyEquals(key, key_length, "radio2.status")
      || keyEquals(key, key_length, "radio2.timing")) return get && !*arguments;
  if (keyEquals(key, key_length, "radio2.cross")) {
    return get ? !*arguments : set && (!strcmp(arguments, "on")
        || !strcmp(arguments, "off") || !strcmp(arguments, "auto"));
  }
  const bool rows = rowKey(key, key_length, "flood.filter")
      || rowKey(key, key_length, "flood.filter.blacklist")
      || rowKey(key, key_length, "flood.rule")
      || rowKey(key, key_length, "flood.moderation")
      || rowKey(key, key_length, "flood.channel.scope")
      || rowKey(key, key_length, "flood.channel.scope.require");
  if (rows) return get ? !*arguments
      : set ? *arguments != 0 : del && (!*arguments || !strcmp(arguments, "all"));
  const bool settings = keyEquals(key, key_length, "flood.channel.data")
      || keyEquals(key, key_length, "flood.channel.data.hops")
      || keyEquals(key, key_length, "flood.max")
      || keyEquals(key, key_length, "flood.max.unscoped")
      || keyEquals(key, key_length, "flood.max.advert");
  return settings && (get ? !*arguments : set && *arguments);
}

size_t FleetCommand::encode(const LocalIdentity& publisher, const uint8_t* key,
                           uint32_t sequence, uint32_t expires,
                           const uint8_t* target, const char* command,
                           uint8_t* output, size_t capacity) {
  if (!privateKeyAllowed(key) || !target || !output
      || !lifetimeAllowed(sequence, expires) || !commandAllowed(command)
      || zero(publisher.pub_key, PUB_KEY_SIZE)) return 0;
  const size_t command_length = strlen(command);
  const size_t unsigned_length = HeaderSize + command_length;
  const size_t length = unsigned_length + SignatureSize;
  if (capacity < length) return 0;
  memcpy(output, "FMC1", 4);
  write32(output + 4, sequence);
  write32(output + 8, expires);
  memcpy(output + 12, target, TargetSize);
  output[28] = uint8_t(command_length);
  memcpy(output + HeaderSize, command, command_length);
  uint8_t message[SignedMessageCapacity];
  const size_t message_length = signedMessage(key, output, unsigned_length, message);
  publisher.sign(output + unsigned_length, message, int(message_length));
  erase(message, sizeof(message));
  return length;
}

bool FleetCommand::decode(const Identity& publisher, const uint8_t* key,
                         const uint8_t* payload, size_t length, uint32_t now,
                         const uint8_t* self_public_key, Decoded& output) {
  memset(&output, 0, sizeof(output));
  if (!privateKeyAllowed(key) || !payload || !self_public_key
      || zero(publisher.pub_key, PUB_KEY_SIZE) || zero(self_public_key, PUB_KEY_SIZE)
      || now < MinEpoch || length < HeaderSize + SignatureSize
      || length > MaxPayloadLength || memcmp(payload, "FMC1", 4)) return false;
  const size_t command_length = payload[28];
  if (!command_length || command_length > MaxCommandLength
      || length != HeaderSize + command_length + SignatureSize) return false;
  const uint32_t sequence = read32(payload + 4), expires = read32(payload + 8);
  if (!lifetimeAllowed(sequence, expires) || expires < now
      || (sequence > now && sequence - now > MaxClockLead)) return false;
  if (!zero(payload + 12, TargetSize)) {
    uint8_t target[TargetSize];
    targetHash(self_public_key, target);
    if (memcmp(target, payload + 12, TargetSize)) return false;
  }
  // Reject embedded NUL bytes, which would authorize a different text from
  // the signed byte string when passed to the CLI parser.
  if (memchr(payload + HeaderSize, 0, command_length)) return false;
  char command[MaxCommandLength + 1];
  memcpy(command, payload + HeaderSize, command_length);
  command[command_length] = 0;
  if (!commandAllowed(command)) return false;
  const size_t unsigned_length = HeaderSize + command_length;
  uint8_t message[SignedMessageCapacity];
  const size_t message_length = signedMessage(key, payload, unsigned_length, message);
  const bool valid = publisher.verify(payload + unsigned_length, message, int(message_length));
  erase(message, sizeof(message));
  if (!valid) return false;
  output.sequence = sequence;
  output.expires = expires;
  memcpy(output.command, command, command_length + 1);
  return true;
}

} // namespace mesh
