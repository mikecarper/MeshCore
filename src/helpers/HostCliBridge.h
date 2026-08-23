#pragma once

#include <MeshCore.h>
#include <helpers/UTF8Helpers.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {

// Line-oriented protocol used to hand an authenticated LoRa CLI request to a
// service on the repeater's USB host. The payload is Base64URL encoded so text
// cannot inject a second serial command or debug record.
class HostCliBridge {
public:
  static constexpr uint32_t SERVICE_CLAIM_TIMEOUT_MILLIS = 4000UL;
  static constexpr uint32_t SERVICE_CLAIM_EMIT_DELAY_MILLIS = 250UL;
  static constexpr uint32_t SERVICE_REPLY_TIMEOUT_MILLIS = 6000UL;
  static constexpr uint32_t SERVICE_TIMEOUT_MILLIS =
      SERVICE_CLAIM_TIMEOUT_MILLIS + SERVICE_REPLY_TIMEOUT_MILLIS;
  static constexpr size_t REMOTE_COMMAND_MAX = 10U * CIPHER_BLOCK_SIZE;
  static constexpr size_t REMOTE_REPLY_MAX =
      MAX_PACKET_PAYLOAD - CIPHER_MAC_SIZE - (CIPHER_BLOCK_SIZE - 1U) - 5U;
  static constexpr size_t REQUEST_VERB_BYTES = 5U;  // "host "
  static constexpr size_t CORRELATION_BYTES = 3U;   // legacy "XX|"
  static constexpr size_t REQUEST_TEXT_MAX =
      REMOTE_COMMAND_MAX - REQUEST_VERB_BYTES;
  static constexpr size_t REQUEST_TEXT_WITH_CORRELATION_MAX =
      REQUEST_TEXT_MAX - CORRELATION_BYTES;
  static constexpr size_t REQUEST_ENCODED_MAX =
      (REQUEST_TEXT_MAX * 4U + 2U) / 3U;
  static constexpr size_t USB_SIGNED_CONTENT_MAX =
      sizeof("HOSTCLI/1 REQUEST FFFFFFFF FFFFFFFFFFFFFFFF ") - 1U
      + REQUEST_ENCODED_MAX;
  static constexpr size_t USB_RECORD_MAX =
      sizeof("DEBUG ") - 1U + USB_SIGNED_CONTENT_MAX + 1U
      + SIGNATURE_SIZE * 2U;
  static constexpr size_t SERIAL_REPLY_COMMAND_MAX =
      sizeof("host.reply FFFFFFFF FFFFFFFFFFFFFFFF ") - 1U
      + REMOTE_REPLY_MAX;
  static constexpr size_t SERVICE_CLAIM_PREFIX_SIZE =
      sizeof("@claim=") - 1U;
  static constexpr size_t SERVICE_CLAIM_TEXT_SIZE =
      SERVICE_CLAIM_PREFIX_SIZE + 16U;
  static constexpr size_t CLAIMED_SIGNED_CONTENT_MAX =
      sizeof("HOSTCLI/1 CLAIMED FFFFFFFF FFFFFFFFFFFFFFFF "
             "FFFFFFFFFFFFFFFF") - 1U;
  static constexpr size_t CLAIMED_USB_RECORD_MAX =
      sizeof("DEBUG ") - 1U + CLAIMED_SIGNED_CONTENT_MAX + 1U
      + SIGNATURE_SIZE * 2U;

  enum ParseResult : uint8_t {
    NOT_HOST_COMMAND = 0,
    INVALID_HOST_COMMAND,
    VALID_HOST_COMMAND,
  };

  struct RequestView {
    const char* text;
    size_t text_len;
    const char* correlation;
    size_t correlation_len;

    RequestView()
        : text(NULL), text_len(0), correlation(NULL), correlation_len(0) {}
  };

  struct ReplyView {
    uint32_t request_id;
    uint64_t request_nonce;
    const char* text;
    size_t text_len;

    ReplyView() : request_id(0), request_nonce(0), text(NULL), text_len(0) {}
  };

  static ParseResult parseRequest(const char* command, RequestView& view) {
    view = RequestView();
    if (command == NULL) return NOT_HOST_COMMAND;

    const size_t command_len = strlen(command);

    const char* cursor = command;
    if (command_len >= CORRELATION_BYTES && command[2] == '|') {
      view.correlation = command;
      view.correlation_len = CORRELATION_BYTES;
      cursor += CORRELATION_BYTES;
    }

    if (strncmp(cursor, "host", 4) != 0) return NOT_HOST_COMMAND;
    if (cursor[4] != 0 && cursor[4] != ' ' && cursor[4] != '\t') {
      return NOT_HOST_COMMAND;
    }
    if (command_len > REMOTE_COMMAND_MAX) return INVALID_HOST_COMMAND;

    cursor += 4;
    while (*cursor == ' ' || *cursor == '\t') cursor++;
    const char* text_end = command + command_len;
    while (text_end > cursor
           && (text_end[-1] == ' ' || text_end[-1] == '\t'
               || text_end[-1] == '\r' || text_end[-1] == '\n')) {
      text_end--;
    }
    if (text_end == cursor) return INVALID_HOST_COMMAND;

    const size_t text_len = (size_t)(text_end - cursor);
    const size_t max_text = view.correlation_len == 0
        ? REQUEST_TEXT_MAX : REQUEST_TEXT_WITH_CORRELATION_MAX;
    if (text_len > max_text) return INVALID_HOST_COMMAND;

    view.text = cursor;
    view.text_len = text_len;
    return VALID_HOST_COMMAND;
  }

  static ParseResult parseReply(const char* command, ReplyView& view) {
    view = ReplyView();
    if (command == NULL) return NOT_HOST_COMMAND;
    const char* end = command + strlen(command);
    if (strncmp(command, "host.reply", 10) != 0) return NOT_HOST_COMMAND;
    if (command[10] != 0 && command[10] != ' ' && command[10] != '\t') {
      return NOT_HOST_COMMAND;
    }

    const char* cursor = command + 10;
    while (*cursor == ' ' || *cursor == '\t') cursor++;

    uint32_t request_id = 0;
    if ((size_t)(end - cursor) < 8U) return INVALID_HOST_COMMAND;
    for (size_t i = 0; i < 8; i++) {
      const int value = hexValue(cursor[i]);
      if (value < 0) return INVALID_HOST_COMMAND;
      request_id = (request_id << 4) | (uint32_t)value;
    }
    cursor += 8;
    if (*cursor != ' ' && *cursor != '\t') return INVALID_HOST_COMMAND;
    while (*cursor == ' ' || *cursor == '\t') cursor++;

    uint64_t request_nonce = 0;
    if ((size_t)(end - cursor) < 16U) return INVALID_HOST_COMMAND;
    for (size_t i = 0; i < 16; i++) {
      const int value = hexValue(cursor[i]);
      if (value < 0) return INVALID_HOST_COMMAND;
      request_nonce = (request_nonce << 4) | (uint64_t)value;
    }
    cursor += 16;
    if (*cursor != ' ' && *cursor != '\t') return INVALID_HOST_COMMAND;
    while (*cursor == ' ' || *cursor == '\t') cursor++;
    if (*cursor == 0) return INVALID_HOST_COMMAND;

    const size_t text_len = strlen(cursor);
    if (text_len > REMOTE_REPLY_MAX
        || validUtf8PrefixLength(cursor, text_len) != text_len) {
      return INVALID_HOST_COMMAND;
    }
    for (size_t i = 0; i < text_len; i++) {
      const uint8_t byte = (uint8_t)cursor[i];
      if (byte < 0x20 || byte == 0x7F) return INVALID_HOST_COMMAND;
    }

    view.request_id = request_id;
    view.request_nonce = request_nonce;
    view.text = cursor;
    view.text_len = text_len;
    return VALID_HOST_COMMAND;
  }

  static bool formatUsbRequest(char* output, size_t output_size,
                               uint32_t request_id, uint64_t request_nonce,
                               const char* text, size_t text_len) {
    if (output == NULL || output_size == 0 || text == NULL
        || text_len == 0 || text_len > REQUEST_TEXT_MAX) {
      return false;
    }

    const int prefix_len = snprintf(output, output_size,
                                    "HOSTCLI/1 REQUEST %08lX %08lX%08lX ",
                                    (unsigned long)request_id,
                                    (unsigned long)(uint32_t)(request_nonce >> 32),
                                    (unsigned long)(uint32_t)request_nonce);
    if (prefix_len < 0 || (size_t)prefix_len >= output_size) return false;
    return base64UrlEncode((const uint8_t*)text, text_len,
                           output + prefix_len, output_size - prefix_len);
  }

  static bool parseServiceClaim(const char* text, size_t text_len,
                                uint64_t& challenge) {
    challenge = 0;
    if (text == NULL || text_len != SERVICE_CLAIM_TEXT_SIZE
        || memcmp(text, "@claim=", SERVICE_CLAIM_PREFIX_SIZE) != 0) {
      return false;
    }
    for (size_t i = 0; i < 16U; i++) {
      const int value = hexValue(text[SERVICE_CLAIM_PREFIX_SIZE + i]);
      if (value < 0) {
        challenge = 0;
        return false;
      }
      challenge = (challenge << 4) | (uint64_t)value;
    }
    return challenge != 0;
  }

  static bool formatUsbClaim(char* output, size_t output_size,
                             uint32_t request_id, uint64_t request_nonce,
                             uint64_t challenge) {
    if (output == NULL || output_size == 0 || challenge == 0) return false;
    const int length = snprintf(
        output, output_size,
        "HOSTCLI/1 CLAIMED %08lX %08lX%08lX %08lX%08lX",
        (unsigned long)request_id,
        (unsigned long)(uint32_t)(request_nonce >> 32),
        (unsigned long)(uint32_t)request_nonce,
        (unsigned long)(uint32_t)(challenge >> 32),
        (unsigned long)(uint32_t)challenge);
    return length > 0 && (size_t)length < output_size;
  }

  static size_t formatRemoteReply(char* output, size_t output_size,
                                  const char* original_command,
                                  const char* service_reply) {
    if (output == NULL || output_size == 0 || original_command == NULL
        || service_reply == NULL) {
      return 0;
    }

    size_t output_len = 0;
    const size_t hard_limit = output_size - 1U < REMOTE_REPLY_MAX
        ? output_size - 1U : REMOTE_REPLY_MAX;
    if (strlen(original_command) >= CORRELATION_BYTES
        && original_command[2] == '|'
        && hard_limit >= CORRELATION_BYTES) {
      memcpy(output, original_command, CORRELATION_BYTES);
      output_len = CORRELATION_BYTES;
    }

    const size_t body_len = validUtf8PrefixLength(
        service_reply, hard_limit - output_len);
    memcpy(output + output_len, service_reply, body_len);
    output_len += body_len;
    output[output_len] = 0;
    return output_len;
  }

private:
  static int hexValue(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    return -1;
  }

  static bool base64UrlEncode(const uint8_t* input, size_t input_len,
                              char* output, size_t output_size) {
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    const size_t encoded_len = (input_len * 4U + 2U) / 3U;
    if (input == NULL || output == NULL || output_size <= encoded_len) {
      return false;
    }

    size_t input_pos = 0;
    size_t output_pos = 0;
    while (input_pos + 3U <= input_len) {
      const uint32_t value = ((uint32_t)input[input_pos] << 16)
          | ((uint32_t)input[input_pos + 1U] << 8)
          | (uint32_t)input[input_pos + 2U];
      output[output_pos++] = alphabet[(value >> 18) & 0x3F];
      output[output_pos++] = alphabet[(value >> 12) & 0x3F];
      output[output_pos++] = alphabet[(value >> 6) & 0x3F];
      output[output_pos++] = alphabet[value & 0x3F];
      input_pos += 3U;
    }

    const size_t remaining = input_len - input_pos;
    if (remaining == 1U) {
      const uint32_t value = (uint32_t)input[input_pos] << 16;
      output[output_pos++] = alphabet[(value >> 18) & 0x3F];
      output[output_pos++] = alphabet[(value >> 12) & 0x3F];
    } else if (remaining == 2U) {
      const uint32_t value = ((uint32_t)input[input_pos] << 16)
          | ((uint32_t)input[input_pos + 1U] << 8);
      output[output_pos++] = alphabet[(value >> 18) & 0x3F];
      output[output_pos++] = alphabet[(value >> 12) & 0x3F];
      output[output_pos++] = alphabet[(value >> 6) & 0x3F];
    }

    output[output_pos] = 0;
    return output_pos == encoded_len;
  }
};

}  // namespace mesh
