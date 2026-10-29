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
bool parseSlot(const char* s, uint8_t& slot) {
  if (!s || s[0] < '1' || s[0] > '9' || s[1] != 0) return false;
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

// "<freq>,<bw>,<sf>,<cr>[,<pre>]" part of a formatted preset line.
void formatTuple(const RadioPreset& p, char* out, size_t cap) {
  RadioPresetTable t;
  t.set(1, p.name, p.freq, p.bw, p.sf, p.cr, p.preamble);
  char line[64];
  formatPreset(t, 1, 0, line, sizeof(line));
  const char* sp = strchr(line + 2, ' ');  // skip "1 <name> "
  snprintf(out, cap, "%s", sp ? sp + 1 : "");
}
}  // namespace

bool RadioPresetCLI::save() {
  uint8_t buf[RadioPresetTable::ENCODED_MAX];
  size_t n = table_.encode(buf, sizeof(buf));
  return n > 0 && hooks_.save && hooks_.save(hooks_.ctx, buf, n);
}

bool RadioPresetCLI::presetOk(const RadioPreset& p) const {
  if (!isfinite(p.freq) || !isfinite(p.bw)) return false;
  return hooks_.accepts(hooks_.ctx, p.freq, p.bw, p.sf, p.cr, p.preamble);
}

void RadioPresetCLI::clearPending() {
  memset(&table_.pending, 0, sizeof(table_.pending));
  remaining_ms_ = 0;
  persisted_min_ = 0;
}

// Restores the pre-load radio settings. Pending is cleared only after a
// successful apply; on failure the record stays persisted and a retry is armed
// (loop() retries every RETRY_SECS, begin() retries on the next boot).
bool RadioPresetCLI::revertNow() {
  RadioPreset prev = table_.pending.previous;
  char scratch[96];
  scratch[0] = 0;
  if (prev.used && presetOk(prev) && hooks_.apply(hooks_.ctx, prev, scratch, sizeof(scratch))) {
    clearPending();
    return save();
  }
  remaining_ms_ = RETRY_SECS * 1000u;
  persisted_min_ = (RETRY_SECS + 59u) / 60u;
  return false;
}

bool RadioPresetCLI::presetAcceptsDefault() const {
  return hooks_.accepts &&
         hooks_.accepts(hooks_.ctx, RADIO_PRESET_DEFAULT_FREQ, RADIO_PRESET_DEFAULT_BW,
                        RADIO_PRESET_DEFAULT_SF, RADIO_PRESET_DEFAULT_CR, 0);
}

void RadioPresetCLI::begin(const Hooks& hooks) {
  hooks_ = hooks;
  have_last_ = false;
  remaining_ms_ = 0;
  persisted_min_ = 0;
  uint8_t buf[RadioPresetTable::ENCODED_MAX + 8];
  size_t len = 0;
  bool dirty = false;
  bool ok = hooks_.load && hooks_.load(hooks_.ctx, buf, sizeof(buf), len) && table_.decode(buf, len);
  if (!ok) table_ = RadioPresetTable();  // missing or corrupt: start clean

  for (uint8_t s = 1; s <= RadioPresetTable::SLOTS; s++) {
    const RadioPreset* p = table_.get(s);
    if (p && !presetOk(*p)) {
      table_.clear(s);
      dirty = true;
    }
  }
  bool any = false;
  for (uint8_t s = 1; s <= RadioPresetTable::SLOTS; s++) any = any || table_.get(s) != nullptr;
  if (!any) {
    // Slot 1 is the US/Canada standard, not a copy of the live radio. A board that
    // rejects it (other band) keeps an empty table and nothing is written.
    if (presetAcceptsDefault() &&
        table_.set(1, RADIO_PRESET_DEFAULT_NAME, RADIO_PRESET_DEFAULT_FREQ, RADIO_PRESET_DEFAULT_BW,
                   RADIO_PRESET_DEFAULT_SF, RADIO_PRESET_DEFAULT_CR, 0)) {
      dirty = true;
    }
  }
  if (table_.pending.active) {
    revertNow();  // reboot during the window reverts, whatever time was left
    return;
  }
  if (dirty) save();
}

uint8_t RadioPresetCLI::activeSlot() const {
  RadioPreset cur;
  memset(&cur, 0, sizeof(cur));
  if (!hooks_.current || !hooks_.current(hooks_.ctx, cur)) return 0;
  return table_.match(cur.freq, cur.bw, cur.sf, cur.cr, cur.preamble);
}

bool RadioPresetCLI::handleGet(const char* args, char* reply, size_t cap) {
  uint8_t active = activeSlot();
  if (*args == 0) {
    size_t off = 0;
    if (table_.pending.active) {
      uint32_t mins = (remaining_ms_ + 59999u) / 60000u;
      int w = snprintf(reply, cap, "pending, reverts in %um; ", (unsigned)mins);
      if (w < 0 || (size_t)w >= cap) {
        reply[0] = 0;
        return true;
      }
      off = (size_t)w;
    }
    formatPresetList(table_, active, reply + off, cap - off);
    return true;
  }
  uint8_t slot;
  if (!parseSlot(args, slot)) {
    cli::formatError(reply, cap, "bad preset slot");
    return true;
  }
  formatPreset(table_, slot, active, reply, cap);
  return true;
}

// add preset <name> <slot> <freq,bw,sf,cr[,preamble]>, validated like "set radio".
bool RadioPresetCLI::handleAdd(const char* args, char* reply, size_t cap) {
  char name[RadioPresetTable::MAX_NAME_LEN + 2];
  const char* sp = strchr(args, ' ');
  if (!sp || (size_t)(sp - args) >= sizeof(name)) {
    cli::formatError(reply, cap, "usage: add preset <name> <slot> <freq,bw,sf,cr[,pre]>");
    return true;
  }
  memcpy(name, args, (size_t)(sp - args));
  name[sp - args] = 0;
  if (!RadioPresetTable::validName(name)) {
    cli::formatError(reply, cap, "bad preset name");
    return true;
  }
  while (*sp == ' ') sp++;
  char slotTok[4];
  const char* sp2 = strchr(sp, ' ');
  uint8_t slot;
  if (!sp2 || (size_t)(sp2 - sp) >= sizeof(slotTok)) {
    cli::formatError(reply, cap, "bad preset slot");
    return true;
  }
  memcpy(slotTok, sp, (size_t)(sp2 - sp));
  slotTok[sp2 - sp] = 0;
  if (!parseSlot(slotTok, slot)) {
    cli::formatError(reply, cap, "bad preset slot");
    return true;
  }
  while (*sp2 == ' ') sp2++;
  float f, bw;
  uint8_t sf, cr;
  uint16_t pre = 0;
  char legacy[96];
  if (!cli::parseRadioPreambleSuffix(sp2, 4, legacy, sizeof(legacy), pre) ||
      !cli::parseRadioTupleStrict(legacy, f, bw, sf, cr) || !isfinite(f) || !isfinite(bw) ||
      !hooks_.accepts(hooks_.ctx, f, bw, sf, cr, pre)) {
    cli::formatError(reply, cap, "invalid radio params");
    return true;
  }
  RadioPresetTable backup = table_;
  if (!table_.set(slot, name, f, bw, sf, cr, pre)) {
    cli::formatError(reply, cap, "bad preset");
    return true;
  }
  if (!save()) {
    table_ = backup;
    cli::formatError(reply, cap, "preset save failed");
    return true;
  }
  snprintf(reply, cap, "OK - preset %u saved", (unsigned)slot);
  return true;
}

bool RadioPresetCLI::doLoad(uint8_t slot, char* reply, size_t cap, bool remote) {
  const RadioPreset* p = table_.get(slot);
  if (!p) {
    char msg[32];
    snprintf(msg, sizeof(msg), "preset %u empty", (unsigned)slot);
    cli::formatError(reply, cap, msg);
    return true;
  }
  RadioPreset target = *p;
  char tuple[64];
  formatTuple(target, tuple, sizeof(tuple));
  if (remote) {
    if (hooks_.tempRadioActive && hooks_.tempRadioActive(hooks_.ctx)) {
      cli::formatError(reply, cap, "tempradio active");
      return true;
    }
    if (table_.pending.active) {
      cli::formatError(reply, cap, "preset load pending");
      return true;
    }
  }
  if (activeSlot() == slot) {
    snprintf(reply, cap, "OK - already on %s", target.name);
    return true;
  }
  if (!remote) {
    reply[0] = 0;
    if (!hooks_.apply(hooks_.ctx, target, reply, cap)) {
      if (!reply[0]) cli::formatError(reply, cap, "apply failed");
      return true;
    }
    if (table_.pending.active) {  // someone is at the device: the change is trusted
      clearPending();
      save();
    }
    snprintf(reply, cap, "OK - loaded %s %s", target.name, tuple);
    return true;
  }
  RadioPreset prev;
  memset(&prev, 0, sizeof(prev));
  if (!hooks_.current || !hooks_.current(hooks_.ctx, prev)) {
    cli::formatError(reply, cap, "cannot read current radio");
    return true;
  }
  prev.used = true;
  if (!presetOk(prev)) {  // the revert must be possible before we leave this tuple
    cli::formatError(reply, cap, "current radio not revertible");
    return true;
  }
  if (!RadioPresetTable::validName(prev.name)) {
    memset(prev.name, 0, sizeof(prev.name));
    memcpy(prev.name, "Previous", 8);
  }
  table_.pending.active = true;
  table_.pending.slot = slot;
  table_.pending.previous = prev;
  table_.pending.remaining_secs = REVERT_SECS;
  remaining_ms_ = REVERT_SECS * 1000u;
  persisted_min_ = REVERT_SECS / 60u;
  if (!save()) {  // persist the revert record before touching the radio
    clearPending();
    cli::formatError(reply, cap, "preset save failed");
    return true;
  }
  reply[0] = 0;
  if (!hooks_.apply(hooks_.ctx, target, reply, cap)) {
    clearPending();
    save();
    if (!reply[0]) cli::formatError(reply, cap, "apply failed");
    return true;
  }
  snprintf(reply, cap, "OK - loading %s %s; log in again within 10 min to keep", target.name, tuple);
  return true;
}

bool RadioPresetCLI::loadSlot(uint8_t slot, char* reply, size_t cap) {
  return doLoad(slot, reply, cap, false);
}

bool RadioPresetCLI::handle(const char* cmd, char* reply, size_t cap, bool remote) {
  if (!cmd || !reply || cap == 0) return false;
  const char* args;
  if (isCmd(cmd, "get preset", args)) return handleGet(args, reply, cap);
  if (isCmd(cmd, "add preset", args)) return handleAdd(args, reply, cap);
  if (isCmd(cmd, "del preset", args)) {
    uint8_t slot;
    if (!parseSlot(args, slot)) {
      cli::formatError(reply, cap, "bad preset slot");
      return true;
    }
    RadioPresetTable backup = table_;
    table_.clear(slot);
    if (!save()) {
      table_ = backup;
      cli::formatError(reply, cap, "preset save failed");
      return true;
    }
    snprintf(reply, cap, "OK - preset %u deleted", (unsigned)slot);
    return true;
  }
  if (isCmd(cmd, "load preset", args)) {
    uint8_t slot;
    if (!parseSlot(args, slot)) {
      cli::formatError(reply, cap, "bad preset slot");
      return true;
    }
    return doLoad(slot, reply, cap, remote);
  }
  return false;
}

void RadioPresetCLI::onAdminLogin() {
  if (!table_.pending.active) return;
  clearPending();
  save();
}

// Call every main-loop pass; the timebase is updated even with nothing pending
// so a load always counts from the most recent call.
void RadioPresetCLI::loop(uint32_t now_ms) {
  uint32_t delta = have_last_ ? (uint32_t)(now_ms - last_ms_) : 0;  // wrap-safe
  last_ms_ = now_ms;
  have_last_ = true;
  if (!table_.pending.active) return;
  remaining_ms_ = delta >= remaining_ms_ ? 0 : remaining_ms_ - delta;
  if (remaining_ms_ == 0) {
    revertNow();
    return;
  }
  uint32_t mins = (remaining_ms_ + 59999u) / 60000u;
  if (mins != persisted_min_) {  // flash is only written when the shown minute changes
    persisted_min_ = mins;
    table_.pending.remaining_secs = mins * 60u;
    save();
  }
}

}  // namespace mesh

#endif  // MESH_ENABLE_RADIO_PRESETS
