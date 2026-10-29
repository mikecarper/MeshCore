#pragma once

#include <stddef.h>
#include <stdint.h>

#include <helpers/RadioPresets.h>

// Slot 1 default on first use: the US/Canada standard. Override per build.
#ifndef RADIO_PRESET_DEFAULT_NAME
#define RADIO_PRESET_DEFAULT_NAME "USA-Canada"
#endif
#ifndef RADIO_PRESET_DEFAULT_FREQ
#define RADIO_PRESET_DEFAULT_FREQ 910.525f
#endif
#ifndef RADIO_PRESET_DEFAULT_BW
#define RADIO_PRESET_DEFAULT_BW 62.5f
#endif
#ifndef RADIO_PRESET_DEFAULT_SF
#define RADIO_PRESET_DEFAULT_SF 7
#endif
#ifndef RADIO_PRESET_DEFAULT_CR
#define RADIO_PRESET_DEFAULT_CR 5
#endif

// "get/add/load/del preset" command handler plus the revert state machine for
// remote loads. A preset loaded over the air is provisional: the previous radio
// settings come back after REVERT_SECS (or on reboot) unless a fresh admin login
// commits the change. Role-agnostic; the role supplies storage and radio access
// through Hooks.
namespace mesh {

class RadioPresetCLI {
 public:
  struct Hooks {
    void* ctx;
    bool (*load)(void* ctx, uint8_t* buf, size_t cap, size_t& len);       // read /presets
    bool (*save)(void* ctx, const uint8_t* buf, size_t len);              // atomic write /presets
    bool (*current)(void* ctx, RadioPreset& out);                         // live primary tuple
    bool (*accepts)(void* ctx, float f, float bw, uint8_t sf, uint8_t cr, uint16_t preamble);
    bool (*apply)(void* ctx, const RadioPreset& p, char* reply, size_t cap);  // role's save+apply path
    bool (*tempRadioActive)(void* ctx);
  };
  static constexpr uint32_t REVERT_SECS = 600;
  static constexpr uint32_t RETRY_SECS = 60;  // between failed revert attempts

  void begin(const Hooks& hooks);                                     // load, or seed slot 1 with the US/Canada default; if pending -> revert now
  bool handle(const char* cmd, char* reply, size_t cap, bool remote);  // true if consumed
  bool loadSlot(uint8_t slot, char* reply, size_t cap);               // local load (Fn keys)
  void onAdminLogin();                                                // fresh full-admin login: commit pending
  void loop(uint32_t now_ms);                                         // revert when window expires
  uint8_t activeSlot() const;                                         // match(current), 0 = custom
  const RadioPresetTable& table() const { return table_; }
  bool pending() const { return table_.pending.active; }

 private:
  Hooks hooks_ = {};
  RadioPresetTable table_;
  uint32_t remaining_ms_ = 0;
  uint32_t persisted_min_ = 0;
  uint32_t last_ms_ = 0;
  bool have_last_ = false;

  bool save();
  bool presetAcceptsDefault() const;
  bool presetOk(const RadioPreset& p) const;
  void clearPending();
  bool revertNow();
  bool doLoad(uint8_t slot, char* reply, size_t cap, bool remote);
  bool handleAdd(const char* args, char* reply, size_t cap);
  bool handleGet(const char* args, char* reply, size_t cap);
};

}  // namespace mesh
