#pragma once

namespace mesh {

// A parameterized TempRadio command changes the modulation used to carry its
// own remote-CLI reply.  A wall-clock delay cannot prove that the reply has
// left a busy outbound queue, so keep the scheduled handoff blocked by the
// exact packet objects on both profiles until Dispatcher reports TX completion.
// At least one successful TX permits the handoff, after both copies drain. This class is
// deliberately pointer-only: packet ownership stays with Dispatcher.
class TempRadioReplyBarrier {
  const void* _packet = nullptr;
  const void* _copy = nullptr;
  bool _armed = false;
  bool _succeeded = false;

  bool remove(const void* packet) {
    if (!_armed || packet == nullptr) return false;
    if (packet == _packet) _packet = nullptr;
    else if (packet == _copy) _copy = nullptr;
    else return false;
    return true;
  }

public:
  // Collect the Dispatcher-created second copy during queue admission. Retry
  // bookkeeping can call onSendFail before admission; it must not resolve this.
  void prepare(const void* packet) { clear(); _packet = packet; }
  void trackCopy(const void* original, const void* copy) {
    if (original && original == _packet && copy != original) _copy = copy;
  }
  void arm(const void* packet) {
    if (packet != _packet) prepare(packet);
    _armed = packet != nullptr;
  }
  void clear() { _packet = _copy = nullptr; _armed = _succeeded = false; }

  bool waiting() const { return _armed && (_packet || _copy); }

  bool complete(const void* packet) {
    if (!remove(packet)) return false;
    _succeeded = true;
    return true;
  }

  // Cancel the transition only when every admitted copy has failed.
  bool fail(const void* packet) {
    return remove(packet) && !waiting() && !_succeeded;
  }
};

}  // namespace mesh
