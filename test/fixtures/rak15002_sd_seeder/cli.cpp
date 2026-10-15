#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
static uint32_t clock_ms = 500000;
static uint32_t millis() { return clock_ms; }
static const char* skipSpacesConst(const char* in) { while (*in == ' ' || *in == '\t') ++in; return in; }
static constexpr uint32_t SDCARD_ACTION_COOLDOWN_MILLIS = 300000;
struct Board {};
namespace mesh { namespace ota {
struct OtaStoreSdNrf52 {
  int formats = 0, erases = 0, spaces = 0, lists = 0;
  uint16_t page = 0;
  bool formatCard(Board&) { formats++; return true; }
  bool eraseCard(Board&) { erases++; return true; }
  bool getSpace(Board&, uint64_t& used, uint64_t& free) { spaces++; used = 12; free = 34; return true; }
  bool listFiles(Board&, uint16_t value, char* reply, size_t cap) {
    lists++; page = value; snprintf(reply, cap, "> files"); return true;
  }
  const char* last_error() const { return "error"; }
};
struct OtaManager {
  enum { IDLE };
  int resets = 0;
  void reset_session() { resets++; }
  void want(int) {}
  void want_mid(const uint8_t*) {}
};
struct OtaContext {
  OtaManager manager;
  OtaStoreSdNrf52 card;
  bool apply_pending = false;
  uint32_t session_started_ms = 0;
  int prev_fstate = 0, prepares = 0, finishes = 0;
  OtaStoreSdNrf52& sdStagingStore() { return card; }
  void prepareSdCardReset() { prepares++; }
  void finishSdCardReset(uint32_t) { finishes++; }
};
static OtaContext context;
static bool acquired = false, out_of_memory = false;
static int acquisitions = 0, accesses = 0;
static bool ota_acquire_context(char* reply, size_t cap) {
  acquisitions++;
  if (out_of_memory) { snprintf(reply, cap, "ERR mOTA is out of memory"); return false; }
  acquired = true; return true;
}
static OtaContext& ota_ctx() { assert(acquired); accesses++; return context; }
} namespace cli {
static void formatStorageBytes(char* reply, size_t cap, uint64_t value) { snprintf(reply, cap, "%uB", unsigned(value)); }
} }
struct CommonCLI {
  Board board;
  Board* _board = &board;
  bool _sdcard_format_recorded = false, _sdcard_erase_recorded = false;
  uint32_t _sdcard_format_at = 0, _sdcard_erase_at = 0;
  bool handleSdCardSetCmd(const char*, char*);
  bool handleSdCardGetCmd(const char*, char*);
};
@METHODS@

int main() {
  using namespace mesh::ota;
  CommonCLI cli;
  struct GuardedReply { char reply[160]; char sentinel[8]; } output{};
  strcpy(output.sentinel, "guarded");
  for (const char* cmd : {"sdcard format", "sdcard erase", "sdcard format --force", "sdcard erase --force"}) {
    acquired = false; out_of_memory = true;
    assert(cli.handleSdCardSetCmd(cmd, output.reply));
    assert(strstr(output.reply, "out of memory") && !acquired && accesses == 0);
    assert(context.card.formats == 0 && context.card.erases == 0 && context.prepares == 0);
    assert(strcmp(output.sentinel, "guarded") == 0);
  }
  for (const char* cmd : {"sdcard free", "sdcard ls", "sdcard dir 2"}) {
    assert(cli.handleSdCardGetCmd(cmd, output.reply));
    assert(strstr(output.reply, "out of memory") && context.card.spaces == 0 && context.card.lists == 0);
    assert(accesses == 0 && strcmp(output.sentinel, "guarded") == 0);
  }
  int previous_acquisitions = acquisitions;
  for (const char* cmd : {"sdcard", "sdcard *", "sdcard format", "sdcard erase", "sdcard invalid", "sdcard ls 0", "sdcard dir -1", "sdcard lsoops"}) {
    assert(cli.handleSdCardGetCmd(cmd, output.reply));
    assert(acquisitions == previous_acquisitions); // status and invalid commands never allocate
  }
  assert(cli.handleSdCardSetCmd("sdcard format unknown", output.reply));
  assert(acquisitions == previous_acquisitions);
  assert(!cli.handleSdCardGetCmd("sdcardoops", output.reply));
  assert(!cli.handleSdCardSetCmd("other", output.reply));
  out_of_memory = false;
  assert(cli.handleSdCardGetCmd("sdcard free", output.reply));
  assert(context.card.spaces == 1 && strstr(output.reply, "12B"));
  acquired = false;
  assert(cli.handleSdCardGetCmd("sdcard dir 65535", output.reply));
  assert(context.card.lists == 1 && context.card.page == 65535);
  acquired = false; context.apply_pending = true;
  assert(cli.handleSdCardSetCmd("sdcard format", output.reply));
  assert(strstr(output.reply, "apply is pending") && context.card.formats == 0);
  context.apply_pending = false;
  assert(cli.handleSdCardSetCmd("sdcard format", output.reply));
  assert(context.card.formats == 1 && context.prepares == 1 && context.finishes == 1);
  acquired = false; previous_acquisitions = acquisitions;
  assert(cli.handleSdCardSetCmd("sdcard format", output.reply));
  assert(strstr(output.reply, "--force") && acquisitions == previous_acquisitions);
  assert(cli.handleSdCardSetCmd("sdcard erase", output.reply));
  assert(context.card.erases == 1 && context.card.formats == 2 && context.finishes == 2);
  assert(strcmp(output.sentinel, "guarded") == 0);
}
