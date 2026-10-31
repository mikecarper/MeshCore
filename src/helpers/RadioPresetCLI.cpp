#include <helpers/RadioPresetConfig.h>

#if MESH_ENABLE_RADIO_PRESETS

#include "RadioPresetCLI.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include <helpers/CLICommandUtils.h>
#include <helpers/RadioProfileCommandUtils.h>

namespace mesh {

namespace {
// One digit slot followed by `end` (NUL for a whole argument).
bool parseSlot(const char* s, uint8_t& slot, char end = 0) {
  if (!s || s[0] < '1' || s[0] > '9' || s[1] != end) return false;
  slot = (uint8_t)(s[0] - '0');
  return slot <= RadioPresetTable::SLOTS;
}

// Matches "<prefix>" alone or "<prefix> <args>"; args points past the spaces.
bool isCmd(const char* cmd, const char* prefix, const char*& args) {
  size_t n = strlen(prefix);
  if (strncmp(cmd, prefix, n) != 0) return false;
  if (cmd[n] == 0) {
    args = cmd + n;
    return true;
  }
  if (cmd[n] != ' ') return false;
  args = cmd + n + 1;
  while (*args == ' ') args++;
  return true;
}

// Out of line so every error path is one short call; returns true (consumed).
bool fail(char* reply, size_t cap, const char* msg) {
  cli::formatError(reply, cap, msg);
  return true;
}

const char BAD_SLOT[] = "bad preset slot";
const char SAVE_FAILED[] = "preset save failed";
const char APPLY_FAILED[] = "apply failed";
#if !MESH_RADIO_PRESETS_RESIDENT
const char UNREADABLE_MSG[] = "presets unreadable";
#endif
}  // namespace

const RadioPresetCLI::Hooks RadioPresetCLI::kNoHooks = {};

// The table a command works on: the resident member, or a stack-local copy
// read from /presets that is dropped when the command returns.
#if MESH_RADIO_PRESETS_RESIDENT
#define PRESET_TABLE(t) RadioPresetTable& t = table_
#else
#define PRESET_TABLE(t) \
  RadioPresetTable t;   \
  if (!loadTable(t)) return fail(reply, cap, UNREADABLE_MSG)
#endif

bool RadioPresetCLI::pending() const {
#if MESH_RADIO_PRESETS_RESIDENT
  return table_.pending.active;
#else
  return pending_slot_ != 0;
#endif
}

bool RadioPresetCLI::save(const RadioPresetTable& t) const {
#if !MESH_RADIO_PRESETS_RESIDENT
  if (flags_ & UNREADABLE) return false;  // t is a placeholder: never overwrite the user's slots
#endif
  uint8_t buf[RadioPresetTable::ENCODED_MAX];
  size_t n = t.encode(buf, sizeof(buf));
  const bool ok = n > 0 && hooks_->save && hooks_->save(ctx_, buf, n);
#if !MESH_RADIO_PRESETS_RESIDENT
  if (ok) flags_ |= STORED;
#endif
  return ok;
}

bool RadioPresetCLI::presetOk(const RadioPreset& p) const {
  if (!isfinite(p.freq) || !isfinite(p.bw)) return false;
  return hooks_->accepts(ctx_, p.freq, p.bw, p.sf, p.cr, p.preamble);
}

void RadioPresetCLI::clearPending(RadioPresetTable& t) {
  memset(&t.pending, 0, sizeof(t.pending));
  remaining_ms_ = 0;
  persisted_min_ = 0;
  timer_flags_ &= ~Reverting;
#if !MESH_RADIO_PRESETS_RESIDENT
  keepPending(t.pending);
#endif
}

// Restores the pre-load radio settings. Pending is cleared only after a
// successful apply; on failure the record stays persisted and a retry is armed
// (loop() retries every RETRY_SECS, begin() retries on the next boot).
bool RadioPresetCLI::revertNow(RadioPresetTable& t) {
  timer_flags_ |= Reverting;
  RadioPreset prev = t.pending.previous;
  char scratch[96];
  scratch[0] = 0;
  if (prev.used && presetOk(prev) && hooks_->apply(ctx_, prev, scratch, sizeof(scratch))
      && currentMatches(prev)) {
    clearPending(t);
    return save(t);
  }
  remaining_ms_ = RETRY_SECS * 1000u;
  persisted_min_ = (RETRY_SECS + 59u) / 60u;
  return false;
}

bool RadioPresetCLI::presetAcceptsDefault() const {
  return hooks_->accepts &&
         hooks_->accepts(ctx_, RADIO_PRESET_DEFAULT_FREQ, RADIO_PRESET_DEFAULT_BW,
                         RADIO_PRESET_DEFAULT_SF, RADIO_PRESET_DEFAULT_CR, 0);
}

// Reads /presets into t; on any failure (missing, unreadable, corrupt) t is
// left clean and false is returned.
bool RadioPresetCLI::readFile(RadioPresetTable& t) const {
  uint8_t buf[RadioPresetTable::ENCODED_MAX + 8];
  size_t len = 0;
  if (hooks_->load && hooks_->load(ctx_, buf, sizeof(buf), len) && t.decode(buf, len)) return true;
  t.decode(nullptr, 0);  // decode clears on failure
  return false;
}

// Drops slots the radio would reject and, with `seed`, puts the US/Canada
// default in slot 1 of an empty table. Nothing is saved; true if t changed.
bool RadioPresetCLI::sanitize(RadioPresetTable& t, bool seed) const {
  bool dirty = false;

  for (uint8_t s = 1; s <= RadioPresetTable::SLOTS; s++) {
    const RadioPreset* p = t.get(s);
    if (p && !presetOk(*p)) {
      t.clear(s);
      dirty = true;
    }
  }
  bool any = !seed;
  for (uint8_t s = 1; s <= RadioPresetTable::SLOTS; s++) any = any || t.get(s) != nullptr;
  if (!any) {
    // Slot 1 is the US/Canada standard, not a copy of the live radio. A board that
    // rejects it (other band) keeps an empty table and nothing is written.
    if (presetAcceptsDefault() &&
        t.set(1, RADIO_PRESET_DEFAULT_NAME, RADIO_PRESET_DEFAULT_FREQ, RADIO_PRESET_DEFAULT_BW,
              RADIO_PRESET_DEFAULT_SF, RADIO_PRESET_DEFAULT_CR, 0)) {
      dirty = true;
    }
  }
  return dirty;
}

#if !MESH_RADIO_PRESETS_RESIDENT
// The table resident mode would hold, with the pending record as this object
// last armed it. Resident seeds only in begin(), so the default is added here
// only while /presets has never been written (the boot-time seed save failed);
// a stored table emptied by "del preset" stays empty, as in RAM. Returns false
// when a stored /presets cannot be read: t then carries only the pending record
// and save() refuses to write until a read succeeds.
bool RadioPresetCLI::loadTable(RadioPresetTable& t) const {
  const bool ok = readFile(t);
  const bool unreadable = !ok && (flags_ & STORED);
  flags_ = unreadable ? (uint8_t)(flags_ | UNREADABLE) : (uint8_t)(flags_ & ~UNREADABLE);
  if (!unreadable) sanitize(t, !ok);
  RadioPresetPending& p = t.pending;
  if (pending_slot_ == 0) {
    memset(&p, 0, sizeof(p));
    return !unreadable;
  }
  // The file normally holds the same record; it only supplies the name and the
  // persisted countdown. Without it the record is rebuilt from RAM.
  if (!p.active) {
    memset(&p, 0, sizeof(p));
    memcpy(p.previous.name, "Previous", 8);
    p.remaining_secs = persisted_min_ * 60u;
  }
  p.active = true;
  p.slot = pending_slot_;
  p.previous.used = (flags_ & PREV_USED) != 0;
  p.previous.freq = prev_freq_;
  p.previous.bw = prev_bw_;
  p.previous.sf = prev_sf_;
  p.previous.cr = prev_cr_;
  p.previous.preamble = prev_preamble_;
  return !unreadable;
}

void RadioPresetCLI::keepPending(const RadioPresetPending& p) {
  pending_slot_ = p.active ? p.slot : 0;
  flags_ = p.previous.used ? (uint8_t)(flags_ | PREV_USED) : (uint8_t)(flags_ & ~PREV_USED);
  prev_freq_ = p.previous.freq;
  prev_bw_ = p.previous.bw;
  prev_sf_ = p.previous.sf;
  prev_cr_ = p.previous.cr;
  prev_preamble_ = p.previous.preamble;
}
#endif

void RadioPresetCLI::begin(void* ctx, const Hooks& hooks) {
  ctx_ = ctx;
  hooks_ = &hooks;
  timer_flags_ = 0;
  remaining_ms_ = 0;
  persisted_min_ = 0;
#if MESH_RADIO_PRESETS_RESIDENT
  RadioPresetTable& t = table_;
#else
  RadioPresetTable t;
#endif
  const bool loaded = readFile(t);
  const bool dirty = sanitize(t, true);  // boot seeds an empty table, stored or not
#if !MESH_RADIO_PRESETS_RESIDENT
  flags_ = loaded ? STORED : 0;
  keepPending(t.pending);
#else
  (void)loaded;
#endif
  if (t.pending.active) {
    revertNow(t);  // reboot during the window reverts, whatever time was left
    return;
  }
  if (dirty) save(t);
}

uint8_t RadioPresetCLI::matchSlot(const RadioPresetTable& t) const {
  RadioPreset cur;
  memset(&cur, 0, sizeof(cur));
  if (!hooks_->current || !hooks_->current(ctx_, cur)) return 0;
  return t.match(cur.freq, cur.bw, cur.sf, cur.cr, cur.preamble);
}

bool RadioPresetCLI::currentMatches(const RadioPreset& target) const {
  RadioPreset current;
  return hooks_->current && hooks_->current(ctx_, current)
      && current.freq == target.freq && current.bw == target.bw
      && current.sf == target.sf && current.cr == target.cr
      && current.preamble == target.preamble;
}

uint8_t RadioPresetCLI::activeSlot() const {
#if MESH_RADIO_PRESETS_RESIDENT
  return matchSlot(table_);
#else
  RadioPresetTable t;
  if (!loadTable(t)) return 0;
  return matchSlot(t);
#endif
}

RadioPresetCLI::TableView RadioPresetCLI::table() const {
#if MESH_RADIO_PRESETS_RESIDENT
  return table_;
#else
  RadioPresetTable t;
  loadTable(t);
  return t;
#endif
}

bool RadioPresetCLI::handleGet(const RadioPresetTable& t, const char* args, char* reply, size_t cap) {
  uint8_t active = matchSlot(t);
  if (*args == 0) {
    size_t off = 0;
    if (t.pending.active) {
      uint32_t mins = (remaining_ms_ + 59999u) / 60000u;
      int w = snprintf(reply, cap, "pending, reverts in %um; ", (unsigned)mins);
      if (w < 0 || (size_t)w >= cap) {
        reply[0] = 0;
        return true;
      }
      off = (size_t)w;
    }
    formatPresetList(t, active, reply + off, cap - off);
    return true;
  }
  uint8_t slot;
  if (!parseSlot(args, slot)) return fail(reply, cap, BAD_SLOT);
  formatPreset(t, slot, active, reply, cap);
  return true;
}

// add preset <name> <slot> <freq,bw,sf,cr[,preamble]>, validated like "set radio".
bool RadioPresetCLI::handleAdd(RadioPresetTable& t, const char* args, char* reply, size_t cap) {
  char name[RadioPresetTable::MAX_NAME_LEN + 2];
  const char* sp = strchr(args, ' ');
  if (!sp || (size_t)(sp - args) >= sizeof(name))
    return fail(reply, cap, "usage: add preset <name> <slot> <freq,bw,sf,cr[,pre]>");
  memcpy(name, args, (size_t)(sp - args));
  name[sp - args] = 0;
  if (!RadioPresetTable::validName(name)) return fail(reply, cap, "bad preset name");
  while (*sp == ' ') sp++;
  uint8_t slot;
  if (!parseSlot(sp, slot, ' ')) return fail(reply, cap, BAD_SLOT);
  const char* sp2 = sp + 1;
  while (*sp2 == ' ') sp2++;
  float f, bw;
  uint8_t sf, cr;
  uint16_t pre = 0;
  char legacy[96];
  if (!cli::parseRadioPreambleSuffix(sp2, 4, legacy, sizeof(legacy), pre) ||
      !cli::parseRadioTupleStrict(legacy, f, bw, sf, cr) || !isfinite(f) || !isfinite(bw) ||
      !hooks_->accepts(ctx_, f, bw, sf, cr, pre))
    return fail(reply, cap, "invalid radio params");
#if MESH_RADIO_PRESETS_RESIDENT
  const RadioPresetTable backup = t;  // the resident table must not keep a failed edit
  const RadioPresetTable* undo = &backup;
#else
  const RadioPresetTable* undo = nullptr;  // the stack copy is dropped anyway
#endif
  t.set(slot, name, f, bw, sf, cr, pre);  // slot and name already validated
  return commit(t, undo, slot, "saved", reply, cap);
}

// Persists a slot edit; on failure t is rolled back to `backup` (when given).
bool RadioPresetCLI::commit(RadioPresetTable& t, const RadioPresetTable* backup, uint8_t slot,
                            const char* what, char* reply, size_t cap) {
  if (!save(t)) {
    if (backup) t = *backup;
    return fail(reply, cap, SAVE_FAILED);
  }
  snprintf(reply, cap, "OK - preset %u %s", (unsigned)slot, what);
  return true;
}

bool RadioPresetCLI::doLoad(RadioPresetTable& t, uint8_t slot, char* reply, size_t cap, bool remote) {
  const RadioPreset* p = t.get(slot);
  if (!p) {
    snprintf(reply, cap, "Error: preset %u empty", (unsigned)slot);
    return true;
  }
  RadioPreset target = *p;
  char tuple[48];
  if (formatPresetTuple(target, tuple, sizeof(tuple)) < 0) tuple[0] = 0;
  if (remote) {
    if (hooks_->tempRadioActive && hooks_->tempRadioActive(ctx_))
      return fail(reply, cap, "tempradio active");
    if (t.pending.active) return fail(reply, cap, "preset load pending");
  }
  if (matchSlot(t) == slot) {
    snprintf(reply, cap, "OK - already on %s", target.name);
    return true;
  }
  if (!remote) {
    reply[0] = 0;
    if (!hooks_->apply(ctx_, target, reply, cap)) {
      if (!reply[0]) fail(reply, cap, APPLY_FAILED);
      return true;
    }
    if (t.pending.active) {  // someone is at the device: the change is trusted
      clearPending(t);
      save(t);
    }
    snprintf(reply, cap, "OK - loaded %s %s", target.name, tuple);
    return true;
  }
  RadioPreset prev;
  memset(&prev, 0, sizeof(prev));
  if (!hooks_->current || !hooks_->current(ctx_, prev))
    return fail(reply, cap, "cannot read current radio");
  prev.used = true;
  if (!presetOk(prev))  // the revert must be possible before we leave this tuple
    return fail(reply, cap, "current radio not revertible");
  if (!RadioPresetTable::validName(prev.name)) {
    memset(prev.name, 0, sizeof(prev.name));
    memcpy(prev.name, "Previous", 8);
  }
  t.pending.active = true;
  t.pending.slot = slot;
  t.pending.previous = prev;
  t.pending.remaining_secs = REVERT_SECS;
  remaining_ms_ = REVERT_SECS * 1000u;
  persisted_min_ = REVERT_SECS / 60u;
#if !MESH_RADIO_PRESETS_RESIDENT
  keepPending(t.pending);
#endif
  if (!save(t)) {  // persist the revert record before touching the radio
    clearPending(t);
    return fail(reply, cap, SAVE_FAILED);
  }
  reply[0] = 0;
  if (!hooks_->apply(ctx_, target, reply, cap)) {
    clearPending(t);
    save(t);
    if (!reply[0]) fail(reply, cap, APPLY_FAILED);
    return true;
  }
  snprintf(reply, cap, "OK - loading %s %s; log in again within 10 min to keep", target.name, tuple);
  return true;
}

bool RadioPresetCLI::loadSlot(uint8_t slot, char* reply, size_t cap) {
  PRESET_TABLE(t);
  return doLoad(t, slot, reply, cap, false);
}

bool RadioPresetCLI::handle(const char* cmd, char* reply, size_t cap, bool remote) {
  if (!cmd || !reply || cap == 0) return false;
  const char* args;
  // Recognise the command before touching the table: every CLI line passes here.
  const bool get = isCmd(cmd, "get preset", args);
  const bool add = !get && isCmd(cmd, "add preset", args);
  const bool del = !get && !add && isCmd(cmd, "del preset", args);
  uint8_t slot = 0;
  if (!get && !add) {
    if (!del && !isCmd(cmd, "load preset", args)) return false;
    if (!parseSlot(args, slot)) return fail(reply, cap, BAD_SLOT);
  }
  PRESET_TABLE(t);
  if (get) return handleGet(t, args, reply, cap);
  if (add) return handleAdd(t, args, reply, cap);
  if (!del) return doLoad(t, slot, reply, cap, remote);
#if MESH_RADIO_PRESETS_RESIDENT
  const RadioPresetTable backup = t;
  const RadioPresetTable* undo = &backup;
#else
  const RadioPresetTable* undo = nullptr;
#endif
  t.clear(slot);
  return commit(t, undo, slot, "deleted", reply, cap);
}

// The lazy PRESET_TABLE returns a reply error, so the void paths below load by
// hand. While /presets is unreadable they still commit or revert from the RAM
// mirror; save() then refuses, leaving the stored bytes untouched.
#if MESH_RADIO_PRESETS_RESIDENT
#define PENDING_TABLE(t) RadioPresetTable& t = table_
#else
#define PENDING_TABLE(t) \
  RadioPresetTable t;    \
  loadTable(t)
#endif

void RadioPresetCLI::onAdminLogin() {
  if (!pending() || (timer_flags_ & Reverting)) return;
  PENDING_TABLE(t);
  const RadioPreset* target = t.get(t.pending.slot);
  // Infrastructure applies after its old-channel reply drains. A login before
  // that retune, after a failed apply, or on a temporary profile cannot confirm
  // the permanent change. Compare tuples rather than slot IDs (aliases exist).
  if (!target || !currentMatches(*target)
      || (hooks_->tempRadioActive && hooks_->tempRadioActive(ctx_))) return;
  clearPending(t);
  save(t);
}

// Call every main-loop pass; the timebase is updated even with nothing pending
// so a load always counts from the most recent call.
void RadioPresetCLI::loop(uint32_t now_ms) {
  uint32_t delta = (timer_flags_ & HaveLast) ? (uint32_t)(now_ms - last_ms_) : 0;  // wrap-safe
  last_ms_ = now_ms;
  timer_flags_ |= HaveLast;
  if (!pending()) return;
  remaining_ms_ = delta >= remaining_ms_ ? 0 : remaining_ms_ - delta;
  if (remaining_ms_ == 0) {
    PENDING_TABLE(t);
    revertNow(t);
    return;
  }
  uint32_t mins = (remaining_ms_ + 59999u) / 60000u;
  if (mins != persisted_min_) {  // flash is only written when the shown minute changes
    persisted_min_ = (uint8_t)mins;
    PENDING_TABLE(t);
    t.pending.remaining_secs = mins * 60u;
    save(t);
  }
}

#undef PENDING_TABLE
#undef PRESET_TABLE

}  // namespace mesh

#endif  // MESH_ENABLE_RADIO_PRESETS
