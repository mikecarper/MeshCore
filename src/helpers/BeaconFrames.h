#pragma once

#include "BeaconConfig.h"
#include "MeshtextChat.h"
#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace beacon {

static constexpr size_t MaxNameBytes = 31;
static constexpr size_t MaxTextBytes = BeaconSlot::TextCapacity - 1;
static constexpr size_t MaxFormattedBytes = MaxNameBytes + 2 + MaxTextBytes;
static constexpr size_t MaxPublicPayloadBytes = 5 + MaxFormattedBytes;
extern const uint8_t PublicKey[16];

// Inputs must be NUL terminated within their respective bounded capacities.
// Reject malformed/overlong text instead of cutting a UTF-8 scalar in half.
bool validText(const char* text);
size_t formatText(const char* node_name, const char* text, char* out, size_t capacity);
size_t publicPayload(uint32_t timestamp, const char* node_name, const char* text,
                     uint8_t* out, size_t capacity);
size_t meshtextFrame(mt::Preset preset, uint32_t from, uint32_t id, uint8_t hops,
                     const char* node_name, const char* text,
                     uint8_t out[mt::MaxFrameSize]);

// Separate the automatic beacon sender from normal Companion channel chat.
// One persistent monotonic packet-ID allocator must serve this sender.
uint32_t nodeId(const uint8_t public_key[32]);

} } // namespace mesh::beacon
