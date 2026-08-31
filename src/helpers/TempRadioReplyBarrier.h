#pragma once

namespace mesh {

// A parameterized TempRadio command changes the modulation used to carry its
// own remote-CLI reply.  A wall-clock delay cannot prove that the reply has
// left a busy outbound queue, so keep the scheduled handoff blocked by the
// exact packet object until Dispatcher reports TX completion.  This class is
// deliberately pointer-only: packet ownership stays with Dispatcher.
class TempRadioReplyBarrier {
  const void* _packet;

public:
  TempRadioReplyBarrier() : _packet(nullptr) {}

  void arm(const void* packet) { _packet = packet; }
  void clear() { _packet = nullptr; }

  bool waiting() const { return _packet != nullptr; }

  bool complete(const void* packet) {
    if (_packet == nullptr || packet != _packet) return false;
    _packet = nullptr;
    return true;
  }

  bool fail(const void* packet) { return complete(packet); }
};

}  // namespace mesh
