#pragma once

#include <stddef.h>
#include <stdint.h>

#include <helpers/RadioPresetConfig.h>
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
// through Hooks (a static table of functions) and a context pointer.
//
// Storage (MESH_RADIO_PRESETS_RESIDENT, see RadioPresetConfig.h): resident keeps
// the table in this object; lazy loads /presets into a stack-local table for each
// command and keeps only the revert timer here. Replies and behavior match,
// except that lazy refuses preset commands while a stored /presets cannot be
// read (resident keeps serving its RAM copy).
namespace mesh {

class RadioPresetCLI {
 public:
  // Must outlive the RadioPresetCLI (keep it static const): only a pointer is kept.
  struct Hooks {
    bool (*load)(void* ctx, uint8_t* buf, size_t cap, size_t& len);       // read /presets
    bool (*save)(void* ctx, const uint8_t* buf, size_t len);              // atomic write /presets
    bool (*current)(void* ctx, RadioPreset& out);                         // live primary tuple
    bool (*accepts)(void* ctx, float f, float bw, uint8_t sf, uint8_t cr, uint16_t preamble);
    bool (*apply)(void* ctx, const RadioPreset& p, char* reply, size_t cap);  // role's save+apply path
    bool (*tempRadioActive)(void* ctx);
  };
  static constexpr uint32_t REVERT_SECS = 600;
  static constexpr uint32_t RETRY_SECS = 60;  // between failed revert attempts

#if MESH_RADIO_PRESETS_RESIDENT
  using TableView = const RadioPresetTable&;
#else
  using TableView = RadioPresetTable;  // a fresh copy read from /presets
#endif

  void begin(void* ctx, const Hooks& hooks);                          // load, or seed slot 1 with the US/Canada default; if pending -> revert now
  bool handle(const char* cmd, char* reply, size_t cap, bool remote);  // true if consumed
  bool loadSlot(uint8_t slot, char* reply, size_t cap);               // local load (Fn keys)
  void onAdminLogin();                                                // fresh full-admin login: commit pending
  void loop(uint32_t now_ms);                                         // revert when window expires
  uint8_t activeSlot() const;                                         // match(current), 0 = custom
  TableView table() const;
  bool pending() const;

 private:
  static const Hooks kNoHooks;  // until begin(): every hook absent
  void* ctx_ = nullptr;
  const Hooks* hooks_ = &kNoHooks;
#if MESH_RADIO_PRESETS_RESIDENT
  RadioPresetTable table_;
#else
  // Lazy: the pending record of a remote load, mirrored from /presets so a
  // revert never depends on re-reading the file.
  float prev_freq_ = 0, prev_bw_ = 0;
#endif
  uint32_t remaining_ms_ = 0;
  uint32_t last_ms_ = 0;
#if !MESH_RADIO_PRESETS_RESIDENT
  uint16_t prev_preamble_ = 0;
  uint8_t prev_sf_ = 0, prev_cr_ = 0;
  uint8_t pending_slot_ = 0;  // 0 = nothing pending
  // PREV_USED: the mirrored tuple is valid. STORED: /presets holds this node's
  // table (loaded at boot or saved since), so a failed read means "unreadable",
  // never "missing". UNREADABLE: the last loadTable() could not read it; saves
  // are refused until a read succeeds, so the user's slots are never replaced.
  mutable uint8_t flags_ = 0;
  enum : uint8_t { PREV_USED = 1, STORED = 2, UNREADABLE = 4 };
#endif
  uint8_t persisted_min_ = 0;  // revert minutes last written to /presets (<= 10)
  uint8_t timer_flags_ = 0;
  enum : uint8_t { HaveLast = 1, Reverting = 2 };

  bool readFile(RadioPresetTable& t) const;             // false: missing or unreadable (t cleared)
  bool sanitize(RadioPresetTable& t, bool seed) const;  // true if it changed t
#if !MESH_RADIO_PRESETS_RESIDENT
  bool loadTable(RadioPresetTable& t) const;  // false: the stored table cannot be read
  void keepPending(const RadioPresetPending& p);
#endif
  bool save(const RadioPresetTable& t) const;
  bool presetAcceptsDefault() const;
  bool presetOk(const RadioPreset& p) const;
  uint8_t matchSlot(const RadioPresetTable& t) const;
  bool currentMatches(const RadioPreset& target) const;
  void clearPending(RadioPresetTable& t);
  bool revertNow(RadioPresetTable& t);
  bool doLoad(RadioPresetTable& t, uint8_t slot, char* reply, size_t cap, bool remote);
  bool handleAdd(RadioPresetTable& t, const char* args, char* reply, size_t cap);
  bool handleGet(const RadioPresetTable& t, const char* args, char* reply, size_t cap);
  bool commit(RadioPresetTable& t, const RadioPresetTable* backup, uint8_t slot, const char* what,
              char* reply, size_t cap);
};

#if !MESH_RADIO_PRESETS_RESIDENT && UINTPTR_MAX == 0xFFFFFFFFu
static_assert(sizeof(RadioPresetCLI) <= 32, "lazy RadioPresetCLI must stay within 32 B of RAM");
#endif

}  // namespace mesh
