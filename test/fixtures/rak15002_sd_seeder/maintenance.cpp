// Compile the real card-maintenance CLI around its side-effect boundaries.
// No filesystem, flash, bootloader, or radio hardware is accessed here.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>

static uint32_t clock_ms = 500000;
static uint32_t millis() { return clock_ms; }
static const char* skipSpacesConst(const char* in) {
  while (*in == ' ' || *in == '\t') ++in;
  return in;
}
static constexpr uint32_t SDCARD_ACTION_COOLDOWN_MILLIS = 300000;
struct Board {};

namespace mesh { namespace ota {
struct OtaManager {
  enum State { IDLE, FETCHING, COMPLETE, PAUSED, FAILED };
  State state = IDLE;
  unsigned resets = 0, wants = 0, mids = 0;
  State fetchState() const { return state; }
  void reset_session() { ++resets; state = IDLE; }
  void want(unsigned) { ++wants; }
  void want_mid(const uint8_t*) { ++mids; }
};

enum Medium { Internal, Qspi, Sd, Unselected };
struct FetchStore {
  Medium medium = Unselected;
  uint32_t staged = 0;
  uint32_t staged_size() const { return staged; }
#if !defined(OTA_SEEDER_ONLY)
  // The real source-only Companion's OtaStoreRam has neither selector. Its
  // maintenance path must compile without granting an install backend.
  bool usesInternal() const { return medium == Internal; }
  bool usesSd() const { return medium == Sd; }
#endif
};
struct OtaStoreSdNrf52 {
  unsigned formats = 0, erases = 0;
  bool format_ok = true, erase_ok = true;
  bool formatCard(Board&) { ++formats; return format_ok; }
  bool eraseCard(Board&) { ++erases; return erase_ok; }
  const char* last_error() const { return "card unavailable"; }
};
struct OtaContext {
  OtaManager manager;
  FetchStore fetch_store;
  OtaStoreSdNrf52 card;
  bool apply_pending = false;
  uint32_t session_started_ms = 1234;
  int prev_fstate = OtaManager::FETCHING;
  unsigned prepares = 0, finishes = 0;
  OtaStoreSdNrf52& sdStagingStore() { return card; }
  void prepareSdCardReset() { ++prepares; }
  void finishSdCardReset(uint32_t) { ++finishes; }
};
static OtaContext context;
static bool ota_acquire_context(char*, size_t) { return true; }
static OtaContext& ota_ctx() { return context; }
} }

struct CommonCLI {
  Board board;
  Board* _board = &board;
  bool _sdcard_format_recorded = false, _sdcard_erase_recorded = false;
  uint32_t _sdcard_format_at = 0, _sdcard_erase_at = 0;
  bool handleSdCardSetCmd(const char*, char*);
};
@METHODS@

int main() {
  using namespace mesh::ota;
  unsigned checked = 0;
  for (const char* command : {"sdcard format", "sdcard erase",
                              "sdcard format --force", "sdcard erase --force"}) {
    for (Medium medium : {Internal, Qspi, Sd, Unselected}) {
      for (auto state : {OtaManager::IDLE, OtaManager::FETCHING,
                         OtaManager::COMPLETE, OtaManager::PAUSED, OtaManager::FAILED}) {
        for (uint32_t staged : {0u, 4096u}) {
          context = OtaContext{};
          context.fetch_store.medium = medium;
          context.fetch_store.staged = staged;
          context.manager.state = state;
          CommonCLI cli;
          // --force only bypasses the action cooldown, never store ownership.
          if (strstr(command, "--force")) {
            cli._sdcard_format_recorded = cli._sdcard_erase_recorded = true;
            cli._sdcard_format_at = cli._sdcard_erase_at = clock_ms;
          }
          struct Reply { char text[160]; char guard[8] = "guarded"; } reply{};
          assert(cli.handleSdCardSetCmd(command, reply.text));
          assert(strcmp(reply.guard, "guarded") == 0);
          const bool occupied = state != OtaManager::IDLE || staged != 0;
#if defined(OTA_TOWER_AUTO_STORE)
          const bool refused = medium == Internal && occupied;
#elif defined(OTA_RAK15002_SD_STORE) && !defined(OTA_SEEDER_ONLY)
          const bool refused = medium != Sd && occupied;
#else
          (void)occupied;
          const bool refused = false;
#endif
          if (refused) {
            assert(strstr(reply.text, "OTA slot is occupied; use ota cancel first"));
            assert(context.manager.state == state && context.fetch_store.staged == staged);
            assert(!context.prepares && !context.finishes);
            assert(!context.manager.resets && !context.manager.wants && !context.manager.mids);
            assert(!context.card.formats && !context.card.erases);
            assert(context.session_started_ms == 1234 && context.prev_fstate == OtaManager::FETCHING);
          } else {
            assert(strncmp(reply.text, "OK - SD card ", 13) == 0);
            assert(context.manager.state == OtaManager::IDLE);
            assert(context.prepares == 1 && context.finishes == 1);
            assert(context.manager.resets == 1 && context.manager.wants == 1 && context.manager.mids == 1);
            assert(context.card.formats == 1 && context.card.erases == unsigned(strstr(command, "erase") != nullptr));
            assert(context.session_started_ms == 0 && context.prev_fstate == OtaManager::IDLE);
          }
          ++checked;
        }
      }
    }
  }
  assert(checked == 160);
  puts("PASS: SD maintenance preserves unrelated install storage (160 cases)");
}
