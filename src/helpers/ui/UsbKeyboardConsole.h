#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh { namespace ui {

// Bounded editor and boot-keyboard decoder. All methods are called by the UI
// task; the USB task passes reports through a queue and never touches this.
class UsbKeyboardConsole {
public:
  static constexpr size_t CommandCapacity = 542;
  static constexpr size_t LineCapacity = 120;
  static constexpr size_t LineCount = 12;

  enum class Action { None, Changed, Submit };

  void connect() {
    disconnect();
    _connected = true;
    _visible = true;
    append("USB keyboard connected\n");
    ++_revision;
  }

  void disconnect() {
    _connected = false;
    _visible = false;
    memset(_command, 0, sizeof(_command));
    memset(_previous, 0, sizeof(_previous));
    memset(_lines, 0, sizeof(_lines));
    _length = _cursor = _count = _last = _scroll = 0;
    _caps = false;
    ++_revision;
  }

  bool connected() const { return _connected; }
  bool visible() const { return _connected && _visible; }
  const char* command() const { return _command; }
  size_t length() const { return _length; }
  size_t cursor() const { return _cursor; }
  size_t scroll() const { return _scroll; }
  size_t lineCount() const { return _count; }
  uint32_t revision() const { return _revision; }
  void invalidate() { ++_revision; }

  // Keep held-key state while an alert, pairing screen, or sleeping display
  // hides the box. A key held across dismissal must not become a new press.
  void ignoreReport(const uint8_t* bytes, size_t size) {
    if (!_connected || !bytes || size < 8) return;
    for (size_t i = 2; i < 8; ++i) if (bytes[i] >= 1 && bytes[i] <= 3) return;
    memcpy(_previous, bytes + 2, sizeof(_previous));
  }

  const char* lineFromNewest(size_t index) const {
    if (index >= _count) return "";
    return _lines[(_last + LineCount - index) % LineCount];
  }

  void clearCommand() {
    memset(_command, 0, sizeof(_command));
    _length = _cursor = 0;
    ++_revision;
  }

  void append(const char* text) {
    if (text == nullptr) return;
    while (*text) append(static_cast<uint8_t>(*text++));
  }

  void append(uint8_t c) {
    if (c == '\r') return;
    ++_revision;
    if (_count == 0) { _count = 1; _last = 0; _lines[0][0] = 0; }
    if (c == '\n') { nextLine(); return; }
    if (c < 32 || c == 127) return;
    size_t n = strlen(_lines[_last]);
    if (n == LineCapacity - 1) { nextLine(); n = 0; }
    _lines[_last][n] = static_cast<char>(c);
    _lines[_last][n + 1] = 0;
    _scroll = 0;
  }

  // HID boot protocol reports contain six simultaneous key usages. Ignore an
  // entire rollover/error report rather than generating accidental commands.
  Action report(const uint8_t* bytes, size_t size) {
    if (!_connected || !bytes || size < 8) return Action::None;
    for (size_t i = 2; i < 8; ++i) if (bytes[i] >= 1 && bytes[i] <= 3) return Action::None;
    // Escape is handled alone, so an Enter in the same HID report can never
    // submit a draft that was hidden when the report arrived.
    for (size_t i = 2; i < 8; ++i) {
      if (bytes[i] == 0x29 && !contains(_previous, 0x29)) {
        _visible = !_visible;
        memcpy(_previous, bytes + 2, sizeof(_previous));
        ++_revision;
        return Action::Changed;
      }
    }
    if (!_visible) {
      memcpy(_previous, bytes + 2, sizeof(_previous));
      return Action::None;
    }
    Action result = Action::None;
    const uint8_t modifiers = bytes[0];
    for (size_t i = 2; i < 8; ++i) {
      const uint8_t key = bytes[i];
      if (!key || contains(_previous, key)) continue;
      bool duplicate = false;
      for (size_t j = 2; j < i; ++j) if (bytes[j] == key) duplicate = true;
      if (duplicate) continue;
      Action action = press(key, modifiers);
      if (action == Action::Submit) { result = action; break; }
      if (action == Action::Changed) result = action;
    }
    memcpy(_previous, bytes + 2, sizeof(_previous));
    if (result != Action::None) ++_revision;
    return result;
  }

private:
  bool _connected = false, _visible = false, _caps = false;
  char _command[CommandCapacity] = {};
  char _lines[LineCount][LineCapacity] = {};
  uint8_t _previous[6] = {};
  size_t _length = 0, _cursor = 0, _count = 0, _last = 0, _scroll = 0;
  uint32_t _revision = 0;

  static bool contains(const uint8_t* keys, uint8_t key) {
    for (size_t i = 0; i < 6; ++i) if (keys[i] == key) return true;
    return false;
  }

  void nextLine() {
    _last = (_last + 1) % LineCount;
    _lines[_last][0] = 0;
    if (_count < LineCount) ++_count;
    _scroll = 0;
  }

  Action press(uint8_t key, uint8_t modifiers) {
    const bool control = (modifiers & 0x11) != 0;
    if (control && (key == 0x06 || key == 0x18)) { clearCommand(); return Action::Changed; }
    // Do not translate Alt, GUI, or other Ctrl shortcuts into a command.
    if (modifiers & 0xdd) return Action::None;
    const bool shift = (modifiers & 0x22) != 0;
    if (key == 0x39) { _caps = !_caps; return Action::Changed; }
    if (key == 0x28 || key == 0x58) return _length ? Action::Submit : Action::None;
    if (key == 0x2a && _cursor) {
      memmove(_command + _cursor - 1, _command + _cursor, _length - _cursor + 1);
      --_cursor; --_length; return Action::Changed;
    }
    if (key == 0x4c && _cursor < _length) {
      memmove(_command + _cursor, _command + _cursor + 1, _length - _cursor);
      --_length; return Action::Changed;
    }
    if (key == 0x50 && _cursor) { --_cursor; return Action::Changed; }
    if (key == 0x4f && _cursor < _length) { ++_cursor; return Action::Changed; }
    if (key == 0x4a) { _cursor = 0; return Action::Changed; }
    if (key == 0x4d) { _cursor = _length; return Action::Changed; }
    if (key == 0x4b || key == 0x52) {
      if (_scroll + 1 < _count) ++_scroll;
      return Action::Changed;
    }
    if (key == 0x4e || key == 0x51) {
      if (_scroll) --_scroll;
      return Action::Changed;
    }
    char ch = 0;
    if (key >= 0x04 && key <= 0x1d) ch = (shift != _caps ? 'A' : 'a') + key - 0x04;
    else if (key >= 0x1e && key <= 0x27) {
      static const char plain[] = "1234567890", shifted[] = "!@#$%^&*()";
      ch = shift ? shifted[key - 0x1e] : plain[key - 0x1e];
    } else {
      static const char plain[] = " -=[]\\#;'`,./";
      static const char shifted[] = " _+{}|~:\"~<>?";
      if (key == 0x2c) ch = ' ';
      else if (key >= 0x2d && key <= 0x38) ch = shift ? shifted[key - 0x2c] : plain[key - 0x2c];
    }
    if (!ch || _length + 1 >= CommandCapacity) return Action::None;
    memmove(_command + _cursor + 1, _command + _cursor, _length - _cursor + 1);
    _command[_cursor++] = ch; ++_length;
    return Action::Changed;
  }
};

} }  // namespace mesh::ui
