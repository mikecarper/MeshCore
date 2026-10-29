# Radio Presets and Cardputer Chat Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Named radio presets (`get/add/load/del preset`) on the M5 repeater, T114 and Cardputer companions, with a remote-load auto-revert on the repeater (confirmed by a fresh admin login over LoRa or from the CYD head over ESP-NOW); plus Cardputer Fn keys, status-bar preset name, and a focus-based chat screen with a per-message Reply/Info/Resend/Copy menu.

**Architecture:** One role-agnostic preset core in `src/helpers/` (pure table + codec, then a command handler with a pending-revert state machine driven through a small hooks struct). The repeater wires it into `CommonCLI`, the companion wires it into `examples/companion_radio/MyMesh.cpp`; the Cardputer UI calls the companion's public preset API. Cardputer chat work is UI plus a backward-compatible history line codec.

**Tech Stack:** C++17 (Arduino/ESP32-S3, nRF52), PlatformIO, Python `unittest` host harnesses that compile real headers with `g++` (pattern: `test/test_head_helpers.py`).

**Spec:** `docs/superpowers/specs/2026-10-29-radio-presets-and-chat-focus-design.md`

## Global Constraints

- Work in a new worktree `C:\Users\Micha\mc_presets`, branch `feature/radio-presets` off `keymindCascade` (`e5d5981`). Never touch `C:\Users\Micha\mc_pr` (uncommitted CYD head work lives there).
- Commits and pushes only with the owner's explicit OK. If local commits are authorized, commit at each task's commit step; never push.
- One PlatformIO process at a time (AGENTS.md). On Windows set `PLATFORMIO_CORE_DIR=C:\pio`; Cardputer envs need `variants/m5stack_cardputer_adv/win_long_cmdline.py` (already wired).
- M5 build: `bash build.sh build-firmware ThinkNode_M5_Repeater --profile cascade --radio-preset usa-cascadia --full` with the flock shim on PATH and `PLATFORMIO_EXTRA_SCRIPTS=pre:variants/m5stack_cardputer_adv/win_long_cmdline.py`. App-only flash: `esptool write_flash 0xe000 boot_app0.bin 0x10000 firmware.bin` after checking partitions.bin matches the backup at 0x8000.
- M5 is a live repeater: announce before opening its port (COM15; opening resets it and clears its RAM node table). Admin password via env `M5PW`, never in files.
- Secrets never in source. Preset commands are admin-only remotely: do not add them to `isRegionMgrAllowed`/`isFilterMgrAllowed`.
- Preset tuple = exactly what `set radio` accepts, parsed by `mesh::cli::parseRadioPreambleSuffix(input, 4, legacy, cap, preamble)` then `mesh::cli::parseRadioTupleStrict(legacy, f, bw, sf, cr)` (both inline in `src/helpers/CLICommandUtils.h`), then the role's `acceptsPrimary` check.
- 5 slots, 1-based. Name: 1–12 printable ASCII chars, no spaces or commas. Slot 1 seeds as `Primary` from the live radio when no preset file exists.
- Remote revert window: 10 minutes. Remote CLI reply capacity is 160 bytes.
- Tests: `python -m unittest test.test_radio_presets test.test_cardputer_chat -v` from the worktree root (WinLibs `g++` 16.2 on PATH).

## Review Focus

1. Reboot or power loss during the revert window must come back on the **previous** radio (pending record persisted before the switch) — test `pending_survives_reload` in Task 2.
2. `get preset` over remote admin must fit 160 bytes with five 12-char names and wide tuples — test `list_fits_remote_capacity` in Task 1.
3. A remote `load preset` while a `tempradio` is active, or of an empty slot, or the slot already active, must change nothing — tests `remote_load_refused_during_tempradio`, `load_empty_slot_errors`, `load_active_slot_is_noop` in Task 2.
4. A corrupt/truncated `/presets` file must not crash and must re-seed slot 1 — test `decode_rejects_corrupt` in Task 1.
5. Old plain `>> text` / `<< text` history lines must still load after the format change — test `legacy_lines_load` in Task 6.

---

### Task 0: Worktree

- [ ] **Step 1:** `git -C C:\Users\Micha\mc_pr worktree add -b feature/radio-presets C:\Users\Micha\mc_presets keymindCascade`
Expected: `Preparing worktree (new branch 'feature/radio-presets')`, HEAD `e5d5981`.
- [ ] **Step 2:** Copy the spec and this plan from `mc_pr/docs/superpowers/{specs,plans}/2026-10-29-radio-presets-*` into the same paths in `mc_presets`. Run `python -m unittest test.test_head_helpers -v` there only if that file exists on this branch; otherwise run `python -m unittest test.test_atomic_file_writer` as a smoke check that `g++` harnesses work.
Expected: tests pass (or the smoke harness compiles).

---

### Task 1: Preset table and codec

**Files:**
- Create: `src/helpers/RadioPresets.h`
- Test: `test/test_radio_presets.py`

**Interfaces:**
- Produces:
```cpp
namespace mesh {
struct RadioPreset {
  char name[13]; float freq; float bw; uint8_t sf; uint8_t cr; uint16_t preamble; bool used;
};
struct RadioPresetPending {
  bool active; uint8_t slot; RadioPreset previous; uint32_t remaining_secs;
};
class RadioPresetTable {
 public:
  static constexpr uint8_t SLOTS = 5, NAME_MAX = 12;
  static bool validName(const char* name);
  bool set(uint8_t slot, const char* name, float freq, float bw, uint8_t sf, uint8_t cr, uint16_t preamble);
  bool clear(uint8_t slot);
  const RadioPreset* get(uint8_t slot) const;          // nullptr if out of range or unused
  uint8_t match(float freq, float bw, uint8_t sf, uint8_t cr, uint16_t preamble) const;  // slot or 0
  RadioPresetPending pending;                          // persisted with the table
  size_t encode(uint8_t* out, size_t cap) const;       // returns bytes or 0
  bool decode(const uint8_t* in, size_t len);          // false leaves table cleared
  static constexpr size_t ENCODED_MAX = 160;
};
// "1*Primary 910.525,62.5,7,5\n2 Private 915,62.5,7,5,16\n3 -\n..." ; '*' = active.
// If the full form exceeds cap: "1*Primary 2 Private 3 - 4 - 5 - (get preset N)".
size_t formatPresetList(const RadioPresetTable& t, uint8_t active, char* out, size_t cap);
size_t formatPreset(const RadioPresetTable& t, uint8_t slot, uint8_t active, char* out, size_t cap);
}
```
Floats compare with `fabsf(a-b) < 0.0005f` (freq) / `< 0.05f` (bw). Encoding: magic `'R','P'`, version byte `1`, then fixed records, then pending, then CRC32 over the preceding bytes (reuse any CRC helper already in `src/helpers`, else a local table-free CRC32). Number formatting must match `get radio` (`StrHelper::ftoa` for freq, `ftoa3` for bw); preamble appended as `,<n>` only when non-zero.

- [ ] **Step 1: Write the failing tests** in `test/test_radio_presets.py` using the `build_and_run` pattern from `test/test_head_helpers.py` (copy the helper; include `-I src -I test`). One C++ harness `TABLE_HARNESS`, cases:
  - `set_and_get`: `set(2,"Private",915.0,62.5,7,5,0)` → `get(2)->sf==7`, `get(3)==nullptr`, `set(6,...)==false`, `set(0,...)==false`.
  - `name_rules`: `validName("Private")`, `!validName("")`, `!validName("has space")`, `!validName("a,b")`, `!validName("ThirteenChars")`, `validName("TwelveChars1")`.
  - `match_active`: slot 2 set as above → `match(915.0,62.5,7,5,0)==2`, `match(915.0,62.5,7,5,16)==0`.
  - `roundtrip`: encode → decode into a fresh table → identical slots and `pending` (`active=true, slot=2, previous=Primary 910.525,62.5,7,5`).
  - `decode_rejects_corrupt`: flip one byte / truncate to 10 bytes / wrong version → `decode` false and all slots unused.
  - `list_fits_remote_capacity`: five slots named `TwelveChars1`…`TwelveChars5` with `2400.000,812.5,12,8,65535` → `formatPresetList(...,160)` ≤ 159 chars and starts with `1` and contains `(get preset N)`; with short names `Primary`/`Private` the full form contains `910.525,62.5,7,5` and `1*Primary`.
- [ ] **Step 2: Run** `python -m unittest test.test_radio_presets -v` — Expected: FAIL (header missing).
- [ ] **Step 3: Implement** `src/helpers/RadioPresets.h` (header-only, `inline`) per the interface.
- [ ] **Step 4: Run** the same command — Expected: all table cases PASS, compiled with `-Wall -Wextra -Werror` and sanitizers as in the copied helper.
- [ ] **Step 5: Commit** (if authorized) `feat(presets): preset table, codec and list formatting`.

---

### Task 2: Preset command handler and revert state machine

**Files:**
- Create: `src/helpers/RadioPresetCLI.h`, `src/helpers/RadioPresetCLI.cpp`
- Test: `test/test_radio_presets.py` (second harness `CLI_HARNESS`, compiles the `.cpp`)

**Interfaces:**
- Consumes: Task 1 types.
- Produces:
```cpp
namespace mesh {
class RadioPresetCLI {
 public:
  struct Hooks {
    void* ctx;
    bool (*load)(void* ctx, uint8_t* buf, size_t cap, size_t& len);      // read /presets
    bool (*save)(void* ctx, const uint8_t* buf, size_t len);             // atomic write /presets
    bool (*current)(void* ctx, RadioPreset& out);                        // live primary tuple
    bool (*accepts)(void* ctx, float f, float bw, uint8_t sf, uint8_t cr, uint16_t preamble);
    bool (*apply)(void* ctx, const RadioPreset& p, char* reply, size_t cap); // role's save+apply path
    bool (*tempRadioActive)(void* ctx);
  };
  static constexpr uint32_t REVERT_SECS = 600;
  void begin(const Hooks& hooks);                 // load or seed slot 1; if pending → revert now
  bool handle(const char* cmd, char* reply, size_t cap, bool remote);  // true if consumed
  bool loadSlot(uint8_t slot, char* reply, size_t cap);               // local load (Fn keys)
  void onAdminLogin();                            // fresh full-admin login: commit pending
  void loop(uint32_t now_ms);                     // revert when window expires
  uint8_t activeSlot() const;                     // match(current), 0 = custom
  const RadioPresetTable& table() const;
  bool pending() const;
};
}
```
Commands consumed: `get preset`, `get preset <n>`, `add preset <name> <slot> <tuple>`, `load preset <slot>`, `del preset <slot>`. Anything else → return false. Replies use the repo's `mesh::cli::formatError` for errors.

Remote load algorithm (the part tests pin):
1. Reject if slot empty (`Error: preset N empty`), if `tempRadioActive` (`Error: tempradio active`), if a load is already pending (`Error: preset load pending`); if slot already active reply `OK - already on <name>` and change nothing.
2. `current()` → `pending = {true, slot, previous, REVERT_SECS}`; `save()` the table **before** calling `apply()`. If save fails → error, no apply.
3. `apply(preset)`; on failure clear pending, save, return the apply error.
4. Reply: `OK - loading <name> <tuple>; log in again within 10 min to keep`.
- `onAdminLogin()` with pending → clear pending, save. Without pending → no-op.
- `loop()` counts down with wrap-safe `millis` deltas; at 0 → `apply(previous)`, clear pending, save.
- `begin()` with a pending record → `apply(previous)`, clear pending, save (reboot = revert).
- Local load (`remote=false` or `loadSlot`) → `apply` directly, no pending.

- [ ] **Step 1: Write failing tests** (fake hooks record calls in a struct; `load/save` use an in-memory buffer):
  - `seed_primary`: empty storage, `current`=910.525/62.5/7/5 → after `begin`, `get preset` reply contains `1*Primary 910.525,62.5,7,5`; storage was saved.
  - `add_validates_like_set_radio`: `add preset Private 2 915.0,62.5,7,5` → `OK`; `add preset Private 2 915.0,62.5,13,5` (accepts returns false for sf 13) → error, slot 2 unchanged; `add preset bad name 2 915,62.5,7,5` → error.
  - `local_load_applies_immediately`: `handle("load preset 2",…,false)` → one `apply` call with 915.0, `pending()==false`.
  - `remote_load_then_commit`: remote load → saved storage decodes with `pending.active`, then `apply`; `onAdminLogin()` → pending cleared and saved; `loop` past 600 s → no further `apply`.
  - `get_preset_shows_pending`: after a remote load, `get preset` reply starts with `pending, reverts in 10m; ` and after `loop(now+300000)` with `reverts in 5m`.
  - `remote_load_reverts_on_timeout`: remote load; `loop(now+599000)` → no revert; `loop(now+600001)` → `apply` called with 910.525; pending cleared.
  - `pending_survives_reload`: remote load; construct a new `RadioPresetCLI` on the same storage; `begin` → `apply(910.525…)` called, pending cleared.
  - `remote_load_refused_during_tempradio`, `load_empty_slot_errors`, `load_active_slot_is_noop`, `second_remote_load_while_pending_errors`: no `apply`, no storage change.
  - `del_preset`: `del preset 2` → slot 2 shows `2 -`.
  - `unrelated_commands_pass_through`: `get radio`, `set radio 1,2,3,4`, `get retry.preset` → `handle` returns false.
- [ ] **Step 2: Run** `python -m unittest test.test_radio_presets -v` — Expected: CLI cases FAIL (missing files), table cases still PASS.
- [ ] **Step 3: Implement** `RadioPresetCLI.h/.cpp`.
- [ ] **Step 4: Run** — Expected: all PASS.
- [ ] **Step 5: Commit** `feat(presets): preset CLI with remote-load revert`.

---

### Task 3: Repeater integration (M5)

**Files:**
- Create: `src/helpers/RadioPresetFile.h` — `bool loadRadioPresetFile(FILESYSTEM* fs, uint8_t* buf, size_t cap, size_t& len)` and `bool saveRadioPresetFile(FILESYSTEM* fs, const uint8_t* buf, size_t len)` at path `/presets`, save via `mesh::AtomicFileWriter`.
- Modify: `src/helpers/CommonCLI.h/.cpp`, `examples/simple_repeater/MyMesh.h/.cpp`
- Test: `test/test_radio_presets.py` (source-contract cases)

**Interfaces:**
- Consumes: `RadioPresetCLI` (Task 2).
- Produces: `mesh::RadioPresetCLI& CommonCLI::radioPresets()`.

Wiring:
- `CommonCLI` owns `mesh::RadioPresetCLI _radio_presets;`. Static trampolines inside `CommonCLI` implement the hooks: `current` from `_prefs->freq/bw/sf/cr` + `_prefs->primary_radio_preamble`; `accepts` → `acceptsPrimaryRadioParams`; `apply` → `applyAndSavePrimaryRadioParams(…, reply)` (it already defers the radio switch 2 s so the reply goes out first) and then appends nothing; `tempRadioActive` → `_callbacks->isTempRadioActive()`; load/save → Task 3 file helpers on the FS passed to `loadPrefs`.
- `_radio_presets.begin(hooks)` at the end of `CommonCLI::loadPrefs(FILESYSTEM*)` (after prefs are read). `_radio_presets.loop(millis())` next to `_radio_profiles.loop()` (CommonCLI.cpp ~238).
- In command dispatch, call `if (_radio_presets.handle(command, reply, 160, sender_timestamp != 0)) return;` immediately before `_radio_profiles.handle(...)` (CommonCLI.cpp ~3613).
- `examples/simple_repeater/MyMesh.cpp` login site (~2843): after `handleLoginReq` returns, if the reply is `RESP_SERVER_LOGIN_OK`, and the client is `isAdmin()` (strict admin, not region/filter manager), call `_cli.radioPresets().onAdminLogin()`. Logins over LoRa and over the ESP-NOW bridge (CYD head) both count (owner decision). Expose the admin result from `handleLoginReq` through a member flag set just before its `return 13`.
- Docs: add the four commands to `docs/cli_commands.md` next to `set radio` and to `docs/cli_command_availability.md` (repeater + companion, admin only).

- [ ] **Step 1: Write failing source-contract tests** (read files with `Path.read_text`, assert with regex): `CommonCLI.cpp` calls `_radio_presets.handle(` before `_radio_profiles.handle(`; simple_repeater `MyMesh.cpp` calls `onAdminLogin()` guarded by `isAdmin()` and the login-OK result; `isRegionMgrAllowed`/`isFilterMgrAllowed` bodies do not contain `preset`.
- [ ] **Step 2: Run** — Expected: new contract cases FAIL.
- [ ] **Step 3: Implement** the wiring above.
- [ ] **Step 4: Run** tests — Expected: PASS. Build the M5 (Global Constraints command) — Expected: SUCCESS, RAM/flash within budget.
- [ ] **Step 5: Hardware check (announce first, owner present).** Flash app-only to the M5. Over USB with `m5_cmds.py`: `get preset` → `1*Primary 910.525,62.5,7,5`; `add preset Test 2 910.525,62.5,7,5,16`; `get preset 2`; `del preset 2`. Do **not** remote-load a different frequency on the live repeater without the owner choosing the time.
- [ ] **Step 6: Commit** `feat(presets): repeater preset commands with login-confirmed switch`.

---

### Task 4: Companion integration (T114, Cardputer)

**Files:**
- Modify: `examples/companion_radio/MyMesh.h/.cpp`
- Test: `test/test_radio_presets.py` (contract cases)

**Interfaces:**
- Consumes: Tasks 2–3 (`RadioPresetCLI`, `RadioPresetFile.h`).
- Produces (public on companion `MyMesh`, used by Task 5):
```cpp
bool loadRadioPreset(uint8_t slot, char* reply, size_t cap);   // local load
uint8_t activeRadioPreset() const;                              // 0 = custom
const mesh::RadioPresetTable& radioPresetTable() const;
```

Wiring: member `mesh::RadioPresetCLI _radio_presets;`; hooks — `current` from `_prefs.freq/bw/sf/cr` and the primary preamble the companion stores; `accepts` → `_radio_profiles.acceptsPrimary`; `apply` → `applyAndSaveRadioParams(f, bw, sf, cr, _prefs.client_repeat, preamble, false)` mapping `RadioSettingResult::Saved` to `OK`; `tempRadioActive` → false; load/save on `_store->getPrimaryFS()`. `begin` right after `_radio_profiles.begin(...)` (~3092); `loop` next to `_radio_profiles.loop()` (~11875); `handle(command, reply, reply_capacity, false)` immediately before `_radio_profiles.handle(...)` (~11266) — companions never take the remote path.

- [ ] **Step 1: Failing contract tests:** companion `MyMesh.cpp` calls `_radio_presets.handle(` with literal `false` before `_radio_profiles.handle(`; `loadRadioPreset` declared public in `MyMesh.h`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** tests — PASS. Build `Heltec_t114_companion_radio_ble` **and** `Heltec_t114_companion_radio_usb`, then `M5Stack_Cardputer_Adv_companion_radio_ble` — Expected: all SUCCESS. Before flashing the T114, read its `ver` and flash the env that matches what it runs today (the owner uses its USB CLI; confirm the USB CLI exists in that env — memory says it needs `ENABLE_USB_INTERFACE`).
- [ ] **Step 5: Hardware check:** on the Cardputer USB CLI (COM11): `get preset`, `add preset Private 2 <owner's tuple>`, `load preset 2`, `get radio` shows the new tuple, `load preset 1` restores. Same on the T114 (COM18) when connected.
- [ ] **Step 6: Commit** `feat(presets): companion preset commands`.

---

### Task 5: Cardputer Fn keys and status-bar preset

**Files:**
- Modify: `variants/m5stack_cardputer_adv/companion_radio/ui-cardputer/CardputerUITask.h/.cpp` (`handleShortcut`, ~line 374), `.../screen/MainScreen.cpp` (`renderStatusIcons`)
- Modify: `variants/m5stack_cardputer_adv/companion_radio/CardputerMesh.h` (expose last-pinged contact)
- Create: `variants/m5stack_cardputer_adv/companion_radio/ui-cardputer/KeyLock.h` (tiny state: `bool locked; bool filter(const Keyboard::Event&)` → true when the event must be dropped; Fn+Q always passes)
- Test: `test/test_cardputer_chat.py` (`KeyLock` harness)

**Interfaces:**
- Consumes: Task 4 public API, `UnreadCounter` (`variants/m5stack_cardputer_adv/UnreadCounter.h`), `CardputerMesh::sendPing(ContactInfo&)`.
- Produces: `bool CardputerMesh::lastPingContact(ContactInfo& out) const` (set whenever `sendPing` is called).

Behavior (exact alert strings):
- Fn+1…Fn+5 → `loadRadioPreset(n)`; alert `"<name> <tuple>"` or `"Preset N empty"`.
- Fn+0 → `"<name> <tuple>"` for the active slot, else `"custom <tuple>"`.
- Fn+Z → `sendPing(lastPingContact)`; reply arrives through existing `pingRecv`; no contact → `"Ping a repeater from Contacts first"`; add a 10 s timeout alert `"No answer"`. (Ruling recorded here: "selected repeater" = the last contact pinged from Contacts.)
- Fn+U → newest entry in `UnreadCounter` with `count > 0` → open that contact/channel chat; none → `"No unread"`.
- Fn+Q → toggle `KeyLock`; alerts `"Keys locked"` / `"Keys unlocked"`; filter applied at the top of the key dispatch before `handleShortcut`; not persisted.
- Status bar: active preset name (or `custom`) drawn in small text left of the battery icon.
- Digits: check the key enum for the number row (`Keyboard::KEY_1`… or equivalent in `hw/CardputerKeyboard.h`) and handle them in `handleShortcut` only when `modifiers.fn` is set.

- [ ] **Step 1: Failing test** `keylock_filters`: unlocked passes everything; after Fn+Q event, letters/Enter dropped, Fn+Q passes and unlocks.
- [ ] **Step 2: Run** `python -m unittest test.test_cardputer_chat -v` — FAIL.
- [ ] **Step 3: Implement** `KeyLock.h` and the UI wiring.
- [ ] **Step 4: Run** — PASS. Build `M5Stack_Cardputer_Adv_companion_radio_ble` — SUCCESS.
- [ ] **Step 5: Commit** `feat(cardputer): preset Fn keys, ping, unread, key lock, status preset`.

---

### Task 6: Message metadata and history line codec

**Files:**
- Create: `variants/m5stack_cardputer_adv/HistoryLine.h` (pure codec)
- Modify: `variants/m5stack_cardputer_adv/CardputerDataStore.h/.cpp` (`HistoryMessage`, `storeMessage`, `push_history_line`)
- Modify: `variants/m5stack_cardputer_adv/companion_radio/CardputerMesh.h/.cpp` (capture on receive/send; repeat tracking)
- Create: `variants/m5stack_cardputer_adv/OwnMessageTracker.h` (RAM-only, last 8 own messages)
- Test: `test/test_cardputer_chat.py`

**Interfaces:**
- Produces:
```cpp
struct MessageMeta {            // zeroed = "no details"
  bool has = false;
  uint32_t sent_ts = 0, recv_ts = 0;
  uint8_t hops = 0xFF;          // 0xFF unknown, 0 direct
  int8_t snr_q4 = 0;            // SNR*4 as on the packet
  int16_t rssi = 0;
  uint8_t path_len = 0, hash_size = 1;
  uint8_t path[16];             // repeater hash bytes, path_len*hash_size used
};
struct HistoryMessage { char text[MAX_MESSAGE_LENGTH]; bool out; MessageMeta meta; uint32_t own_id; };
// Line format v2: "<< {m1;<sent>;<recv>;<hops>;<snr_q4>;<rssi>;<hs>;<pathhex>} text"
// Legacy: "<< text" / ">> text". A text starting with '{' from legacy files stays text unless it matches the full v2 prefix.
size_t encodeHistoryLine(const HistoryMessage& m, char* out, size_t cap);
bool decodeHistoryLine(const char* line, HistoryMessage& out);
struct OwnMessageInfo { uint32_t own_id; uint8_t packet_hash[MAX_HASH_SIZE]; bool is_channel;
  uint16_t repeats; uint8_t repeaters[8]; uint8_t repeater_count;
  enum State : uint8_t { Pending, Delivered, Failed } state; uint32_t sent_ms, rtt_ms; };
class OwnMessageTracker { public:
  uint32_t add(const uint8_t hash[], bool is_channel, uint32_t now_ms);   // returns own_id
  void onEcho(const uint8_t hash[], uint8_t repeater_hash);                // channel repeats
  void onAck(uint32_t own_id, uint32_t now_ms); void onFailed(uint32_t own_id);
  const OwnMessageInfo* find(uint32_t own_id) const; };
// Channel sender: "Name: text" → name. Returns false if no ": ".
bool splitChannelSender(const char* text, char* name, size_t cap);
```
Capture: received channel/DM messages fill `meta` from the packet in `CardputerMesh::onChannelMessageRecv`/`onMessageRecv` (`pkt->_snr`, `pkt->_rssi`, `pkt->path_len` with hash size from the packed length, `pkt->path`). Own channel echo: in `onRecvPacket` where `last_message_hash` matches today, call `tracker.onEcho(hash, last path hash byte of the echoed packet)`. Own DM: hook the existing ACK/failed paths (`last_msg.state` transitions). `storeMessage` gains a `const MessageMeta*` parameter and writes v2 lines; own messages persist text only (session-only metadata, per spec).

- [ ] **Step 1: Failing tests:** `v2_roundtrip` (all fields incl. 3-hop path `C5 D8 14`); `legacy_lines_load` (`">> hi"`, `"<< hi there"` → out flags right, `meta.has==false`); `brace_text_stays_text` (`"<< {not meta} hi"` → text `{not meta} hi`); `tracker_counts_echoes` (two echoes with hashes `0xC5`,`0xD8` → repeats 2, repeaters `{C5,D8}`; duplicate repeater hash still counts the repeat but not a second ID); `tracker_evicts_oldest` (9th add evicts the 1st); `split_sender` (`"Bob: hi"` → `Bob`; `"no colon"` → false).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** codec, tracker, and capture wiring.
- [ ] **Step 4: Run** — PASS. Build the Cardputer env — SUCCESS.
- [ ] **Step 5: Commit** `feat(cardputer): message metadata, v2 history lines, own-message tracker`.

---

### Task 7: Chat focus model and message menu

**Files:**
- Create: `variants/m5stack_cardputer_adv/companion_radio/ui-cardputer/screen/main/ChatFocus.h` (pure focus/menu state machine)
- Modify: `.../screen/main/ChatPage.h/.cpp`
- Test: `test/test_cardputer_chat.py`

**Interfaces:**
- Consumes: Task 6 `HistoryMessage`/`MessageMeta`/`OwnMessageTracker`/`splitChannelSender`.
- Produces:
```cpp
struct ChatFocus {
  enum class Target : uint8_t { TextBox, Message, Menu };
  enum class Action : uint8_t { None, Send, OpenMenu, Reply, Info, Resend, Copy, LeaveChat, TypeChar };
  enum class Key : uint8_t { Up, Down, Enter, Esc, Char, FnUp };
  Target target = Target::TextBox; int msg = -1;  // index from newest (0 = newest)
  int menu_item = 0;                              // 0 Reply,1 Info,2 Resend,3 Copy (Resend skipped for received)
  Action press(Key k, bool text_empty, int message_count, bool focused_is_own);
};
```
Rules pinned by tests (spec "Focus model"): TextBox + Up with empty text → Message 0; TextBox + Up with text → `TypeChar` (`;`); TextBox + FnUp → Message 0; TextBox + Down → `TypeChar` (`.`); TextBox + Enter → `Send`; TextBox + Esc → `LeaveChat`; Message + Up → older (clamped); Message + Down at 0 → TextBox; Message + Enter → Menu (`OpenMenu`); Message + Char → `None`; Message + Esc → TextBox; Menu Up/Down cycle items (Resend hidden for received); Menu + Enter → the item's action and target per item (Reply/Copy → TextBox; Info → stays Menu-owned info view; Resend → Message); Menu + Esc → Message.

ChatPage maps keyboard events to `ChatFocus::Key` (Up = `Keyboard::ARROW_UP`, Down = `ARROW_DOWN`, `FnUp` = Up with `modifiers.fn`), keeps Ctrl+Up recall and Ctrl+T cancel when the text box has focus, draws a highlight bar on the focused message and a bright border on the focused text box, renders the menu as a small overlay list, and renders Info as an overlay:
- received: `From <name>`, `Sent <hh:mm> Rcvd <hh:mm>`, `Direct` or `<n> hops`, `SNR <x.x> RSSI <n>`, `Path C5 > D8 > 14` (omit when empty); `No details` when `!meta.has`.
- own channel: `Heard <n> repeats`, `By C5 D8 …`; own DM: `Delivered (<rtt> ms)` / `Failed` / `Pending`, route `Direct`/`<n> hops`/`Flood`.
Reply prefix on channels: `@[<name>] ` via `splitChannelSender`; DMs just focus the text box. Resend reuses `sendChatMessage` with the message text.

- [ ] **Step 1: Failing tests** — one per rule line above (`up_from_empty_box_focuses_newest`, `up_with_text_types_semicolon`, `fn_up_leaves_box_with_text`, `down_types_period`, `enter_in_box_sends`, `enter_on_message_opens_menu_never_sends`, `char_on_message_ignored`, `down_from_newest_returns_to_box`, `esc_on_message_returns_to_box`, `esc_in_box_leaves_chat`, `menu_hides_resend_for_received`, `menu_reply_targets_box`, `menu_esc_returns_to_message`).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `ChatFocus.h` and the ChatPage integration.
- [ ] **Step 4: Run** — PASS. Build the Cardputer env — SUCCESS.
- [ ] **Step 5: Hardware check on the Cardputer** (package per Launcher rules: data partition inside the `.bin`; or app-only `esptool write_flash 0x10000` per the current partition table — read the table first): scroll a channel, Enter on a message opens the menu, Enter never sends from a message, `.` types in the box, Info shows hops/SNR/path for a received message, Reply prefills `@[Name] `, Fn+1/Fn+0/Fn+Q/Fn+U/Fn+Z behave as in Task 5, old history still shows.
- [ ] **Step 6: Commit** `feat(cardputer): focus-based chat with message menu`.

---

### Task 8: End-to-end preset test and docs

- [ ] **Step 1:** With the owner choosing the time: from the head or Cardputer, log in to the M5, `load preset 2` (remote), confirm the reply; Fn+2 on the Cardputer; log in again → `get preset` on the M5 shows slot 2 active and no pending. Third run from the CYD head: `load preset 2` in the Repeater console, log out, log in on the head → committed. Second run: remote load without logging in → after 10 minutes the M5 is back on preset 1 (`get radio` over USB, announced).
- [ ] **Step 2:** Update `variants/m5stack_cardputer_adv` README (or the Cardputer key list in its docs) with Fn+0…5, Fn+Z, Fn+U, Fn+Q and the chat focus keys.
- [ ] **Step 3: Run** the full new suites `python -m unittest test.test_radio_presets test.test_cardputer_chat -v` — PASS.
- [ ] **Step 4: Commit** `docs: presets and Cardputer chat keys`.
