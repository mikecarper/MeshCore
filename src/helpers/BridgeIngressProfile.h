#pragma once
#ifndef MESH_BRIDGE_INGRESS_PROFILE_H
#define MESH_BRIDGE_INGRESS_PROFILE_H

#include <stdint.h>

// WITH_BRIDGE is also synthesized by CommonCLI.h. This feature must not
// depend on that header having been included first in a translation unit.
#ifndef MESH_ENABLE_BRIDGE_INGRESS
#if defined(WITH_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || \
    defined(WITH_RS232_BRIDGE) || defined(WITH_MQTT_BRIDGE) || defined(WITH_UDP_BRIDGE)
#define MESH_ENABLE_BRIDGE_INGRESS 1
#else
#define MESH_ENABLE_BRIDGE_INGRESS 0
#endif
#endif

namespace mesh { namespace bridge {

enum class IngressProfile : uint8_t { Radio = 0, Radio2 = 1, Blocked = 2, Off = 3 };

struct IngressSource {
  enum class Kind : uint8_t { None = 0, Mac = 1, Udp = 2 };
  Kind kind = Kind::None;
  uint8_t bytes[6] = {};

  static IngressSource mac(const uint8_t* address) {
    IngressSource source;
    if (address) {
      source.kind = Kind::Mac;
      for (unsigned i = 0; i < 6; ++i) source.bytes[i] = address[i];
    }
    return source;
  }
  static IngressSource udp(const uint8_t* address, uint16_t port) {
    IngressSource source;
    if (address && port) {
      source.kind = Kind::Udp;
      for (unsigned i = 0; i < 4; ++i) source.bytes[i] = address[i];
      source.bytes[4] = port >> 8;
      source.bytes[5] = port;
    }
    return source;
  }
};

struct IngressRoute {
  IngressProfile profile;
  bool explicit_profile;
};

#if MESH_ENABLE_BRIDGE_INGRESS
IngressProfile ingressProfile();
IngressRoute ingressRoute(const IngressSource* source = nullptr);
#else
inline IngressProfile ingressProfile() { return IngressProfile::Radio; }
inline IngressRoute ingressRoute(const IngressSource* = nullptr) {
  return {IngressProfile::Radio, false};
}
#endif

}} // namespace mesh::bridge
#endif
