#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>
#include <helpers/CLICommandUtils.h>
#include <helpers/LazyPersistence.h>

#define MESH_DEBUG_PRINTLN(...) ((void)0)

static uint32_t now_ms;
static bool morse_active = false, boot_save_ok = true;
struct Clock { uint32_t getMillis() const { return now_ms; } } clock_source;
struct Board { unsigned reboots = 0; void reboot() { ++reboots; } } board;
struct Stream {
  std::string text;
  void print(const char* value) { text += value; }
  void println(const char* value) { text += value; text += '\n'; }
  void printf(const char* format, ...) {
    char value[256];
    va_list args;
    va_start(args, format);
    vsnprintf(value, sizeof(value), format, args);
    va_end(args);
    text += value;
  }
};
namespace mesh {
bool morseTimingActive(const void*) { return morse_active; }
bool saveUsbLoggingBootPreference(bool) { return boot_save_ok; }
bool usbLoggingInterfaceRestartRequired() { return true; }
bool isUsbLoggingPacketStream() { return false; }
}
struct Store {
  bool incomplete = true, repair_ok = true, vol = false;
  bool contacts_saved = true, flush_ok = true, pending = false;
  unsigned repairs = 0, contact_saves = 0, contact_flushes = 0;
  uint32_t capacity = 100;
  bool hasIncompleteContactLoad() const { return incomplete; }
  bool repairInternalExtraFS() { ++repairs; return repair_ok; }
  bool contactsAreVolatile() const { return vol; }
  bool saveContacts(void*, int) { ++contact_saves; return contacts_saved; }
  bool flushContactWrites(void*, int) {
    ++contact_flushes;
    if (flush_ok) pending = false;
    return flush_ok;
  }
  bool hasPendingContactWrites() const { return pending; }
  uint32_t getStorageTotalKb() const { return capacity; }
};
struct MyMesh {
  Clock* _ms = &clock_source;
  void* _radio = nullptr;
  // Match the MCU's 32-bit unsigned-long deadline on native 64-bit hosts.
  uint32_t _scheduled_reboot_at = 0;
  Stream output;
  Store store;
  Store* _store = &store;
  struct { uint8_t usb_logging_enabled = 0; } _prefs;
  unsigned long dirty_contacts_expiry = 0;
  uint8_t dirty_contacts_failures = 0;
  int save_filter = 0;
  bool prefs_ok = true, channels_ok = true;
  unsigned pref_saves = 0, channel_saves = 0, logging_applies = 0;
  Stream& terminalOutput() { return output; }
  void onContactCacheFlushed();
  bool flushContactsBeforeReboot();
  bool prepareForUiShutdown();
  bool savePrefs() { ++pref_saves; return prefs_ok; }
  bool saveChannels() { ++channel_saves; return channels_ok; }
  void scheduleContactWriteRetry();
  void applyUsbLoggingState(bool) { ++logging_applies; }
  bool millisHasNowPassed(unsigned long timestamp) const;
  unsigned long futureMillis(int millis_from_now) const;
  void terminalCommand(const char* command);
  bool loggingCommand(const char* command, char* reply, size_t reply_size);
  void repairInternalExtraFS(Stream& output);
  void service();
};
#include "production.inc"

enum class Arm { Usb, Repair, Terminal };
static void arm(MyMesh& node, Arm command) {
  if (command == Arm::Usb) {
    char reply[160] = {};
    assert(node.loggingCommand("set usb.logging stream reboot", reply, sizeof(reply)));
    assert(strstr(reply, "rebooting to change USB interfaces"));
    assert(node.pref_saves == 1 && node.logging_applies == 1);
  } else if (command == Arm::Repair) {
    node.repairInternalExtraFS(node.output);
    assert(node.output.text.find("rebooting to reload authoritative data") != std::string::npos);
    assert(node.store.repairs == 1 && node.store.contact_saves == 0);
    assert(node.channel_saves == 0 && node.store.incomplete);
  } else {
    node.terminalCommand("reboot");
    assert(node.output.text.find("rebooting in 1 second") != std::string::npos);
  }
}
static void reset(uint32_t now) {
  now_ms = now;
  morse_active = false;
  boot_save_ok = true;
  board.reboots = 0;
}
static void timing_checks(Arm command) {
  for (uint32_t start : {0U, 100U, UINT32_MAX - 1000U, UINT32_MAX - 999U,
                         UINT32_MAX - 998U, UINT32_MAX}) {
    reset(start);
    MyMesh node;
    arm(node, command);
    const uint32_t raw_deadline = start + 1000U;
    const uint32_t deadline = raw_deadline == 0 ? 1 : raw_deadline;
    const uint32_t armed = node._scheduled_reboot_at;
    now_ms = start + 999U;
    node.service();
    assert(board.reboots == 0 && node.store.contact_flushes == 0);
    if (raw_deadline == 0) {
      now_ms = 0;
      node.service();
      assert(board.reboots == 0 && node.store.contact_flushes == 0);
    }
    now_ms = deadline;
    node.service(); // Equality must service the arm despite Dispatcher's strict > helper.
    assert(board.reboots == 1 && node.store.contact_flushes == 0
        && "a wrapped reboot deadline must still reboot exactly once");
    assert(armed == deadline && node._scheduled_reboot_at == 0);
    ++now_ms;
    node.service();
    assert(board.reboots == 1 && node.store.contact_flushes == 0);
    assert(node.store.contact_saves == 0); // Never rebuild an incomplete RAM view.
  }
}
static void service_gate_checks(Arm command) {
  for (uint32_t start : {100U, UINT32_MAX - 999U}) {
    reset(start);
    MyMesh node;
    arm(node, command);
    const uint32_t deadline = node._scheduled_reboot_at;
    morse_active = true;
    now_ms = deadline;
    node.service();
    now_ms += 100;
    node.service();
    assert(board.reboots == 0 && node.store.contact_flushes == 0
        && node._scheduled_reboot_at == deadline);
    morse_active = false;
    node.service();
    assert(board.reboots == 1 && node.store.contact_flushes == 0
        && node._scheduled_reboot_at == 0);
  }
  for (bool flush_ok : {false, true}) {
    reset(100);
    MyMesh node;
    arm(node, command);
    // Model a valid running table with accepted work newer than disk.
    node.store.incomplete = false;
    node.store.pending = true;
    node.store.flush_ok = flush_ok;
    node.dirty_contacts_expiry = 123;
    node.dirty_contacts_failures = flush_ok ? 2 : 0;
    now_ms = node._scheduled_reboot_at;
    node.service();
    assert(board.reboots == unsigned(flush_ok) && node.store.contact_flushes == 1);
    assert(node._scheduled_reboot_at == 0 && node.store.pending == !flush_ok);
    if (flush_ok) {
      assert(node.dirty_contacts_expiry == 0 && node.dirty_contacts_failures == 0);
    } else {
      assert(node.dirty_contacts_failures == 1);
      assert(node.dirty_contacts_expiry == now_ms + LAZY_CONTACTS_WRITE_DELAY);
    }
    ++now_ms;
    node.service();
    assert(board.reboots == unsigned(flush_ok) && node.store.contact_flushes == 1);
    if (!flush_ok) {
      // A recovered medium must not resurrect a cancelled one-shot arm.
      node.store.flush_ok = true;
      now_ms = node.dirty_contacts_expiry;
      node.service();
      assert(board.reboots == 0 && node.store.contact_flushes == 1);
    }
  }
  // Volatile contacts block explicit durability promises, not hardware reboot.
  reset(100);
  MyMesh node;
  arm(node, command);
  node.store.vol = true; node.store.pending = true; node.store.flush_ok = false;
  node.dirty_contacts_expiry = 123; node.dirty_contacts_failures = 2;
  assert(!node.flushContactsBeforeReboot() && node.store.contact_flushes == 0);
  now_ms = node._scheduled_reboot_at;
  node.service();
  assert(board.reboots == 1 && node._scheduled_reboot_at == 0);
  assert(node.store.contact_flushes == 0 && node.store.pending);
  assert(node.dirty_contacts_expiry == 123 && node.dirty_contacts_failures == 2);
  ++now_ms; node.service();
  assert(board.reboots == 1 && node.store.contact_flushes == 0);
}
static void repair_rebuild_checks() {
  for (unsigned fault = 0; fault != 5; ++fault) {
    reset(100);
    MyMesh node;
    node.store.incomplete = false;
    node.dirty_contacts_expiry = 123; node.dirty_contacts_failures = 2;
    if (fault == 1) node.store.contacts_saved = false;
    if (fault == 2) node.store.pending = true;
    if (fault == 3) node.channels_ok = false;
    if (fault == 4) node.store.capacity = 80;
    node.repairInternalExtraFS(node.output);
    assert(node.store.repairs == 1 && node.store.contact_saves == 1
        && node.channel_saves == 1);
    assert(node._scheduled_reboot_at == 0 && node.store.contact_flushes == 0);
    if (fault == 1 || fault == 2) {
      assert(node.dirty_contacts_failures == 3);
      assert(node.dirty_contacts_expiry == now_ms + 4 * LAZY_CONTACTS_WRITE_DELAY);
    } else {
      assert(node.dirty_contacts_expiry == 0 && node.dirty_contacts_failures == 0);
    }
    assert(node.output.text.find(fault == 0 ? "rebuilt" : "failed") != std::string::npos);
  }
}
static void rejected_command_checks(Arm command) {
  reset(0);
  MyMesh node;
  if (command == Arm::Usb) {
    char reply[160] = {};
    node.prefs_ok = false;
    assert(node.loggingCommand("set usb.logging stream reboot", reply, sizeof(reply)));
    assert(strstr(reply, "save failed") && node._scheduled_reboot_at == 0);
    node.prefs_ok = true;
    boot_save_ok = false;
    assert(node.loggingCommand("set usb.logging stream reboot", reply, sizeof(reply)));
    assert(strstr(reply, "could not be saved") && node._scheduled_reboot_at == 0);
  } else if (command == Arm::Repair) {
    node.store.repair_ok = false;
    node.repairInternalExtraFS(node.output);
    assert(node.output.text.find("failed") != std::string::npos);
    assert(node._scheduled_reboot_at == 0 && node.store.contact_saves == 0);
  } else {
    node.terminalCommand("reboot extra");
    assert(node._scheduled_reboot_at == 0 && node.output.text.empty());
  }
  // Unarmed time zero and later polls must never flush or reboot.
  for (uint32_t now : {0U, 1U, 1000U}) {
    now_ms = now;
    node.service();
  }
  assert(board.reboots == 0 && node.store.contact_flushes == 0);
}
int main(int argc, char** argv) {
  assert(argc == 2);
  const Arm command = !strcmp(argv[1], "usb") ? Arm::Usb
      : !strcmp(argv[1], "repair") ? Arm::Repair : Arm::Terminal;
  timing_checks(command);
  service_gate_checks(command);
  rejected_command_checks(command);
  if (command == Arm::Repair) repair_rebuild_checks();
  std::printf("PASS: scheduled Companion reboot (%s)\n", argv[1]);
}
