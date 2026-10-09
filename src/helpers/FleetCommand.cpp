#include "FleetCommand.h"
#include <SHA256.h>
#include <string.h>

namespace mesh {
namespace {

constexpr char Domain[] = "MeshCoreFleet1";
constexpr size_t SignedMessageCapacity = sizeof(Domain) - 1
    + FleetCommand::KeySize + FleetCommand::MaxPayloadLength
    - FleetCommand::SignatureSize;
static_assert(FleetCommand::HeaderSize + FleetCommand::MaxCommandLength
              + FleetCommand::SignatureSize <= FleetCommand::MaxPayloadLength,
              "Legacy fleet envelope must fit the shared packet budget");
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

// Validate every record, including records after one that matches this node.
// An absent self key is used by encode to validate externally supplied lists.
bool targetRecords(const uint8_t* data, size_t available, uint8_t count,
                   const uint8_t* self, size_t& consumed, bool& matched,
                   bool& broadcast) {
  consumed = 0;
  matched = count == 0;
  broadcast = count == 0 || count > 1;
  if (count > FleetCommand::MaxTargetCount) return false;
  uint8_t hash[FleetCommand::TargetSize];
  bool hashed = false;
  for (unsigned i = 0; i < count; ++i) {
    if (consumed >= available) return false;
    const uint8_t type = data[consumed++];
    if (type != 4 && type != 6 && type != FleetCommand::TargetSize) return false;
    if (type > available - consumed
        || consumed + type > FleetCommand::MaxTargetBytes) return false;
    if (type != FleetCommand::TargetSize) broadcast = true;
    if (self) {
      if (type == FleetCommand::TargetSize && !hashed) {
        targetHash(self, hash);
        hashed = true;
      }
      const uint8_t* expected = type == FleetCommand::TargetSize ? hash : self;
      if (!memcmp(data + consumed, expected, type)) matched = true;
    }
    consumed += type;
  }
  return true;
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

bool boundedIntegerToken(const char* text, size_t length, uint32_t maximum,
                         uint32_t& value) {
  if (!length) return false;
  value = 0;
  for (size_t i = 0; i < length; ++i) {
    if (text[i] < '0' || text[i] > '9') return false;
    const uint32_t digit = uint32_t(text[i] - '0');
    if (digit > maximum || value > (maximum - digit) / 10U) return false;
    value = value * 10U + digit;
  }
  return true;
}

bool scheduleTimeToken(const char* text, size_t length) {
  if (!length) return false;
  const bool relative = text[0] == '+';
  uint32_t value;
  // The scheduler resolves +N whole minutes against its current RTC and
  // performs future/overflow/endpoint-order checks using that one snapshot.
  return boundedIntegerToken(text + relative, length - relative,
                             relative ? UINT32_MAX / 60U : UINT32_MAX, value)
      && value != 0;
}

bool preambleToken(const char* text, size_t length) {
  if (keyEquals(text, length, "auto")) return true;
  uint32_t value;
  return boundedIntegerToken(text, length, 65528U, value)
      && (value == 0 || value >= 8);
}

bool scheduleSelector(const char* arguments, bool secondary) {
  if (!*arguments || !strcmp(arguments, "all")) return true;
  if (*arguments < '1' || *arguments > '9') return false;
  uint32_t value;
  // Primary capacity is role/build configurable. Match the bounded row
  // authorization policy and let the role reject nonexistent slots.
  return boundedIntegerToken(arguments, strlen(arguments), secondary ? 4U : 255U, value);
}

bool scheduledRadioTuple(const char* arguments, bool temporary, bool secondary) {
  unsigned fields = 0;
  const unsigned time_index = secondary ? 5 : 4;
  const unsigned expected = time_index + (temporary ? 2 : 1);
  const char* cursor = arguments;
  for (;;) {
    const char* end = strchr(cursor, ',');
    const size_t length = end ? size_t(end - cursor) : strlen(cursor);
    bool valid = false;
    if (fields < 2) valid = decimalToken(cursor, length);
    else if (fields < 4) {
      uint32_t value;
      valid = boundedIntegerToken(cursor, length, 255U, value);
    } else if (secondary && fields == 4) {
      valid = keyEquals(cursor, length, "rx") || keyEquals(cursor, length, "rxtx");
    } else if (fields < expected) valid = scheduleTimeToken(cursor, length);
    else if (fields == expected) valid = preambleToken(cursor, length);
    if (!valid || ++fields > expected + 1) return false;
    if (!end) return fields == expected || fields == expected + 1;
    cursor = end + 1;
  }
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

bool FleetCommand::parseTargets(const char* text, size_t length, Targets& targets) {
  memset(&targets, 0, sizeof(targets));
  if (!text || !length || length > MaxTargetCount * (PUB_KEY_SIZE * 2 + 1)) return false;
  if (length == 3 && !memcmp(text, "all", 3)) return true;
  // Clear partial output on failure without retaining another 88-byte list
  // on the command parser's stack.
  const auto fail = [&targets]() {
    memset(&targets, 0, sizeof(targets));
    return false;
  };
  size_t cursor = 0;
  while (cursor < length) {
    const size_t start = cursor;
    while (cursor < length && text[cursor] != ',') ++cursor;
    const size_t token_length = cursor - start;
    if (token_length != 8 && token_length != 12 && token_length != PUB_KEY_SIZE * 2) return fail();
    const size_t type = token_length == PUB_KEY_SIZE * 2 ? TargetSize : token_length / 2;
    if (targets.count == MaxTargetCount || targets.length + 1 + type > MaxTargetBytes) return fail();
    uint8_t full_key[PUB_KEY_SIZE];
    const size_t byte_length = token_length / 2;
    for (size_t i = 0; i < byte_length; ++i) {
      const int upper = hexDigit(text[start + i * 2]);
      const int lower = hexDigit(text[start + i * 2 + 1]);
      if (upper < 0 || lower < 0) return fail();
      full_key[i] = uint8_t((upper << 4) | lower);
    }
    if (type == TargetSize && zero(full_key, PUB_KEY_SIZE)) return fail();
    targets.data[targets.length++] = uint8_t(type);
    if (type == TargetSize) targetHash(full_key, targets.data + targets.length);
    else memcpy(targets.data + targets.length, full_key, type);
    targets.length += uint8_t(type);
    ++targets.count;
    if (cursor < length && ++cursor == length) return fail();
  }
  return true;
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
  if (!strcmp(command, "clock") || !strcmp(command, "clock sync")) return true;
  if (!strncmp(command, "time ", 5)) {
    uint32_t value;
    return boundedIntegerToken(command + 5, length - 5, UINT32_MAX, value)
        && value >= MinEpoch;
  }
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

  const bool scheduled_primary = keyEquals(key, key_length, "radioat")
      || keyEquals(key, key_length, "tempradioat");
  const bool scheduled_secondary = keyEquals(key, key_length, "radioat2")
      || keyEquals(key, key_length, "tempradioat2");
  if (scheduled_primary || scheduled_secondary) {
    return set ? scheduledRadioTuple(arguments, key[0] == 't', scheduled_secondary)
        : (get || del) && scheduleSelector(arguments, scheduled_secondary);
  }
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

size_t FleetCommand::encode(const LocalIdentity& publisher, const uint8_t* key,
                           uint32_t sequence, uint32_t expires,
                           const Targets& targets, const char* command,
                           uint8_t* output, size_t capacity) {
  if (!privateKeyAllowed(key) || !output || targets.length > MaxTargetBytes
      || !lifetimeAllowed(sequence, expires) || !commandAllowed(command)
      || zero(publisher.pub_key, PUB_KEY_SIZE)) return 0;
  size_t consumed;
  bool matched, broadcast;
  if (!targetRecords(targets.data, targets.length, targets.count, nullptr,
                     consumed, matched, broadcast) || consumed != targets.length) return 0;
  // Broadcast has no target record in FMC2. A single complete key retains
  // its original encoding, which is smaller than a one-record FMC2 envelope.
  if (targets.count == 1 && targets.data[0] == TargetSize
      && !zero(targets.data + 1, TargetSize)) {
    return encode(publisher, key, sequence, expires, targets.data + 1, command, output, capacity);
  }
  const size_t command_length = strlen(command);
  const size_t unsigned_length = MinHeaderSize + targets.length + command_length;
  const size_t length = unsigned_length + SignatureSize;
  if (length > MaxPayloadLength || capacity < length) return 0;
  memcpy(output, "FMC2", 4);
  write32(output + 4, sequence);
  write32(output + 8, expires);
  output[12] = targets.count;
  memcpy(output + 13, targets.data, targets.length);
  output[13 + targets.length] = uint8_t(command_length);
  memcpy(output + MinHeaderSize + targets.length, command, command_length);
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
      || now < MinEpoch || length < MinHeaderSize + SignatureSize
      || length > MaxPayloadLength) return false;
  const size_t unsigned_length = length - SignatureSize;
  size_t command_offset;
  bool broadcast;
  if (!memcmp(payload, "FMC1", 4)) {
    if (length < HeaderSize + SignatureSize) return false;
    broadcast = zero(payload + 12, TargetSize);
    if (!broadcast) {
      uint8_t target[TargetSize];
      targetHash(self_public_key, target);
      if (memcmp(target, payload + 12, TargetSize)) return false;
    }
    command_offset = HeaderSize;
  } else if (!memcmp(payload, "FMC2", 4)) {
    size_t consumed;
    bool matched;
    if (!targetRecords(payload + 13, unsigned_length - 13, payload[12],
                       self_public_key, consumed, matched, broadcast)
        || !matched) return false;
    command_offset = MinHeaderSize + consumed;
    if (command_offset > unsigned_length) return false;
  } else return false;
  const size_t command_length = payload[command_offset - 1];
  if (!command_length || command_length > MaxCommandLength
      || unsigned_length != command_offset + command_length) return false;
  const uint32_t sequence = read32(payload + 4), expires = read32(payload + 8);
  if (!lifetimeAllowed(sequence, expires) || expires < now
      || (sequence > now && sequence - now > MaxClockLead)) return false;
  // Reject embedded NUL bytes, which would authorize a different text from
  // the signed byte string when passed to the CLI parser.
  if (memchr(payload + command_offset, 0, command_length)) return false;
  char command[MaxCommandLength + 1];
  memcpy(command, payload + command_offset, command_length);
  command[command_length] = 0;
  if (!commandAllowed(command)) return false;
  uint8_t message[SignedMessageCapacity];
  const size_t message_length = signedMessage(key, payload, unsigned_length, message);
  const bool valid = publisher.verify(payload + unsigned_length, message, int(message_length));
  erase(message, sizeof(message));
  if (!valid) return false;
  output.sequence = sequence;
  output.expires = expires;
  output.broadcast = broadcast;
  memcpy(output.command, command, command_length + 1);
  return true;
}

} // namespace mesh
