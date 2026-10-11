#pragma once

#include "RoomMailStore.h"
#include "RoomWebJson.h"

namespace mesh {

inline void roomMailWebHex(char* output, const uint8_t* key, size_t length) {
  static const char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < length; ++i) {
    output[2 * i] = digits[key[i] >> 4]; output[2 * i + 1] = digits[key[i] & 15];
  }
  output[length * 2] = 0;
}

inline bool roomMailWebKey(uint8_t* output, size_t length, const char* text) {
  if (!text || strlen(text) != 2 * length) return false;
  for (size_t i = 0; i < length; ++i) {
    unsigned byte = 0;
    for (size_t n = 0; n < 2; ++n) {
      const char c = text[2 * i + n];
      unsigned digit;
      if (c >= '0' && c <= '9') digit = c - '0';
      else if (c >= 'a' && c <= 'f') digit = c - 'a' + 10;
      else if (c >= 'A' && c <= 'F') digit = c - 'A' + 10;
      else return false;
      byte = 16 * byte + digit;
    }
    output[i] = uint8_t(byte);
  }
  return true;
}

inline const char* roomMailModeName(RoomMailMode mode) {
  return mode == RoomMailMode::Public ? "public" : mode == RoomMailMode::Private ? "private" : "closed";
}

inline const char* roomMailError(RoomMailResult result) {
  switch (result) {
    case RoomMailResult::Invalid: return "invalid mailbox request";
    case RoomMailResult::Unavailable: return "mailbox storage unavailable; previous data retained";
    case RoomMailResult::WriteFailure: return "mailbox save failed; previous data retained";
    case RoomMailResult::NotFound: return "mailbox or message not found";
    case RoomMailResult::StaleVersion: return "mailbox changed; refresh before editing";
    case RoomMailResult::Forbidden: return "recipient is not accepting messages from this identity";
    case RoomMailResult::Full: return "mailbox or server queue full; message not accepted";
    case RoomMailResult::Mismatch: return "request identifier already used for different mail";
    default: return "mailbox request failed";
  }
}

inline void writeRoomMailSettingsJson(RoomJsonWriter& json, const uint8_t* owner,
                                      const RoomMailStatus& status, bool blocked = false) {
  char key[65]; roomMailWebHex(key, owner, 32);
  json.raw("{\"owner\":"); json.string(key);
  json.raw(",\"revision\":"); json.number(status.revision);
  json.raw(",\"mode\":"); json.string(roomMailModeName(status.settings.mode));
  json.raw(",\"mailbox_only\":"); json.raw(status.settings.mailbox_only ? "true" : "false");
  json.raw(",\"allowlist\":[");
  for (uint8_t i = 0; i < status.settings.allowed_count; ++i) {
    if (i) json.raw(",");
    roomMailWebHex(key, status.settings.allowed[i], 32); json.string(key);
  }
  json.raw("],\"total\":"); json.number(status.count);
  json.raw(",\"unread\":"); json.number(status.count);
  json.raw(",\"blocked\":"); json.raw(blocked ? "true" : "false"); json.raw("}");
}

// The caller has already authenticated the browser's own token identity and
// charged room quotas. Only explicit administrator operations may select a
// different owner; a shared room password never proves a radio's private key.
template <typename Filesystem, typename AllowsIdentity>
bool handleRoomMailWeb(Filesystem* fs, const uint8_t* caller, bool can_send,
                       bool administrator, uint64_t request_id, uint32_t created,
                       JsonObjectConst input, RoomJsonWriter& json,
                       AllowsIdentity allows) {
  const char* operation = input["op"].as<const char*>();
  if (!operation) return false;
  const bool admin = strncmp(operation, "admin.mail.", 11) == 0;
  if (!admin && strncmp(operation, "mail.", 5) != 0) return false;
  auto error = [&json](const char* message) {
    json.reset();
    json.raw("{\"error\":"); json.string(message); json.raw("}"); json.finish();
  };
  if (json.capacity() < 1024) { error("mailbox reply capacity too small"); return true; }
  auto checked = [&error](RoomMailResult result) {
    if (result == RoomMailResult::Success || result == RoomMailResult::Duplicate) return true;
    error(roomMailError(result)); return false;
  };
  auto keyValue = [](JsonVariantConst value, uint8_t* out) {
    if (!value.is<const char*>()) return false;
    const JsonString text = value.as<JsonString>();
    if (text.size() != 64 || strlen(text.c_str()) != 64
        || !roomMailWebKey(out, 32, text.c_str())) return false;
    uint8_t any = 0; for (size_t i = 0; i < 32; ++i) any |= out[i];
    return any != 0;
  };
  if (admin && !administrator) { error("admin permission required"); return true; }
  uint8_t owner[32]; memcpy(owner, caller, sizeof(owner));
  if (admin && strcmp(operation, "admin.mail.list") != 0
      && !keyValue(input["owner"], owner)) {
    error("complete mailbox owner public key required"); return true;
  }
  if (strcmp(operation, "admin.mail.list") == 0) {
    if (!input["cursor"].is<uint8_t>() || !input["revision"].is<uint32_t>()
        || (input["cursor"].as<uint8_t>() && !input["revision"].as<uint32_t>())) {
      error("mailbox cursor and snapshot revision required"); return true;
    }
    uint8_t keys[2][32]; size_t copied = 0; RoomMailStats stats;
    const uint8_t cursor = input["cursor"].as<uint8_t>();
    if (!checked(listRoomMailOwners(fs, input["revision"].as<uint32_t>(), cursor,
                                   keys, 2, copied, stats))) return true;
    json.raw("{\"mailboxes\":[");
    for (size_t i = 0; i < copied; ++i) {
      RoomMailStatus status;
      const auto result = getRoomMailStatus(fs, keys[i], status);
      if (result != RoomMailResult::Success) { error(roomMailError(result)); return true; }
      if (i) json.raw(",");
      char key[65]; roomMailWebHex(key, keys[i], 32);
      json.raw("{\"owner\":"); json.string(key);
      json.raw(",\"mode\":"); json.string(roomMailModeName(status.settings.mode));
      json.raw(",\"revision\":"); json.number(status.revision);
      json.raw(",\"mailbox_only\":"); json.raw(status.settings.mailbox_only ? "true" : "false");
      json.raw(",\"unread\":"); json.number(status.count);
      json.raw(",\"total\":"); json.number(status.count);
      json.raw(",\"blocked\":"); json.raw(allows(keys[i]) ? "false" : "true"); json.raw("}");
    }
    json.raw("],\"next\":");
    if (cursor + copied < stats.owners) json.number(cursor + copied); else json.raw("null");
    json.raw(",\"total\":"); json.number(stats.owners);
    json.raw(",\"queued\":"); json.number(stats.messages);
    json.raw(",\"revision\":"); json.number(stats.revision); json.raw("}"); json.finish(); return true;
  }
  if (strcmp(operation, "mail.send") == 0) {
    if (!can_send) { error("read-only room access cannot send mail"); return true; }
    uint8_t recipient[32];
    if (!keyValue(input["recipient"], recipient)) { error("complete recipient public key required"); return true; }
    if (!allows(recipient)) { error("recipient is blocked"); return true; }
    if (!input["text"].is<const char*>()) { error("message required"); return true; }
    const JsonString text = input["text"].as<JsonString>();
    if (!text.size() || text.size() > 512 || strlen(text.c_str()) != text.size()) {
      error("mail must contain 1-512 UTF-8 bytes"); return true;
    }
    uint32_t id = 0;
    if (!checked(sendRoomMail(fs, caller, recipient, request_id, text.c_str(), text.size(), id, created))) return true;
    json.raw("{\"ok\":true,\"id\":"); json.number(id); json.raw("}"); json.finish(); return true;
  }
  if (strcmp(operation, "mail.policy") == 0 || strcmp(operation, "admin.mail.policy") == 0) {
    if (!input["expected_revision"].is<uint32_t>() || !input["mailbox_only"].is<bool>()
        || !input["mode"].is<const char*>() || !input["allowlist"].is<JsonArrayConst>()) {
      error("mailbox mode, delivery, sender list and expected revision required"); return true;
    }
    RoomMailSettings settings;
    const char* mode = input["mode"].as<const char*>();
    if (strcmp(mode, "public") == 0) settings.mode = RoomMailMode::Public;
    else if (strcmp(mode, "private") == 0) settings.mode = RoomMailMode::Private;
    else if (strcmp(mode, "closed") != 0) { error("use public, private or closed mailbox access"); return true; }
    settings.mailbox_only = input["mailbox_only"].as<bool>();
    JsonArrayConst allowed = input["allowlist"].as<JsonArrayConst>();
    if (allowed.size() > 8) { error("at most eight selected senders"); return true; }
    for (JsonVariantConst value : allowed) {
      if (!keyValue(value, settings.allowed[settings.allowed_count])) {
        error("selected senders need complete public keys"); return true;
      }
      ++settings.allowed_count;
    }
    if (!checked(saveRoomMailSettings(fs, owner, settings, input["expected_revision"].as<uint32_t>()))) return true;
  }
  if (strcmp(operation, "mail.ack") == 0 || strcmp(operation, "mail.delete") == 0
      || strcmp(operation, "admin.mail.delete") == 0) {
    if (!input["id"].is<uint32_t>() || (!admin && input["id"].as<uint32_t>() == 0)) {
      error("message identifier required"); return true;
    }
    const auto result = strcmp(operation, "mail.ack") == 0
        ? acknowledgeRoomMail(fs, owner, input["id"].as<uint32_t>())
        : deleteRoomMail(fs, owner, input["id"].as<uint32_t>());
    if (!checked(result)) return true;
    json.raw("{\"ok\":true}"); json.finish(); return true;
  }
  RoomMailStatus status;
  const auto loaded = getRoomMailStatus(fs, owner, status);
  if (loaded != RoomMailResult::Success && loaded != RoomMailResult::NotFound) {
    error(roomMailError(loaded)); return true;
  }
  if (strcmp(operation, "mail.settings") == 0 || strcmp(operation, "mail.policy") == 0
      || strcmp(operation, "admin.mail.settings") == 0 || strcmp(operation, "admin.mail.policy") == 0) {
    writeRoomMailSettingsJson(json, owner, status, !allows(owner)); json.finish(); return true;
  }
  if (strcmp(operation, "mail.check") == 0) {
    char key[65]; roomMailWebHex(key, owner, 32);
    json.raw("{\"owner\":"); json.string(key);
    json.raw(",\"unread\":"); json.number(status.count);
    json.raw(",\"total\":"); json.number(status.count);
    json.raw(",\"revision\":"); json.number(status.revision);
    json.raw(",\"mailbox_only\":"); json.raw(status.settings.mailbox_only ? "true" : "false");
    json.raw("}"); json.finish(); return true;
  }
  if (strcmp(operation, "mail.list") == 0) {
    if (!input["cursor"].is<uint8_t>() || !input["revision"].is<uint32_t>()
        || (input["cursor"].as<uint8_t>() && !input["revision"].as<uint32_t>())) {
      error("mail cursor and snapshot revision required"); return true;
    }
    const uint8_t cursor = input["cursor"].as<uint8_t>();
    const uint32_t expected_revision = input["revision"].as<uint32_t>();
    if (expected_revision && expected_revision != status.revision) {
      error(roomMailError(RoomMailResult::StaleVersion)); return true;
    }
    if (loaded == RoomMailResult::NotFound) {
      if (cursor) { error("invalid mail cursor"); return true; }
      json.raw("{\"messages\":[],\"next\":null,\"revision\":0}"); json.finish(); return true;
    }
    RoomMailMessage messages[2]; size_t copied = 0;
    if (!checked(listRoomMail(fs, owner, status.revision, cursor, messages, 2, copied))) return true;
    json.raw("{\"messages\":[");
    for (size_t i = 0; i < copied; ++i) {
      if (i) json.raw(","); char key[65]; roomMailWebHex(key, messages[i].sender, 32);
      json.raw("{\"id\":"); json.number(messages[i].id);
      json.raw(",\"sender\":"); json.string(key);
      json.raw(",\"created\":"); json.number(messages[i].created);
      json.raw(",\"length\":"); json.number(messages[i].length); json.raw(",\"acked\":false}");
    }
    json.raw("],\"next\":");
    if (cursor + copied < status.count) json.number(cursor + copied); else json.raw("null");
    json.raw(",\"revision\":"); json.number(status.revision); json.raw("}"); json.finish(); return true;
  }
  if (strcmp(operation, "mail.read") == 0) {
    if (!input["id"].is<uint32_t>() || !input["offset"].is<uint16_t>()) {
      error("message identifier and offset required"); return true;
    }
    uint8_t bytes[128]; char encoded[173]; size_t copied = 0; RoomMailMessage message;
    const uint16_t offset = input["offset"].as<uint16_t>();
    if (!checked(readRoomMail(fs, owner, input["id"].as<uint32_t>(), offset,
                             bytes, sizeof(bytes), copied, message))) return true;
    roomEncodeBase64(bytes, copied, encoded); char key[65]; roomMailWebHex(key, message.sender, 32);
    json.raw("{\"id\":"); json.number(message.id);
    json.raw(",\"sender\":"); json.string(key);
    json.raw(",\"created\":"); json.number(message.created);
    json.raw(",\"length\":"); json.number(message.length);
    json.raw(",\"offset\":"); json.number(offset);
    json.raw(",\"count\":"); json.number(copied);
    json.raw(",\"next\":"); json.number(offset + copied);
    json.raw(",\"data64\":"); json.string(encoded); json.raw(",\"acked\":false}"); json.finish(); return true;
  }
  error("unknown mailbox operation"); return true;
}

} // namespace mesh
