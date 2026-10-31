#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <initializer_list>
#include <vector>
#include <helpers/CLICommandUtils.h>
#include <helpers/PrefsSaveRouting.h>
#define MESH_HIL_TX_FREQ_MHZ 909.5f
#define MIN_LORA_TX_POWER -9
#define MAX_LORA_TX_POWER 22
#define MIN_LOCAL_ADVERT_INTERVAL 60
#include <helpers/radiolib/RadioPowerLimits.h>

static uint32_t now_ms = 1000;
static bool completion_seen = false;
uint32_t millis() { return now_ms; }
namespace mesh {
@ENUM@
struct Radio {
  bool carrier = false, morse = false;
  bool isCarrierWaveActive() const { return carrier; }
};
bool morseTimingActive(const Radio* radio) { return radio && radio->morse; }
}
using Result = mesh::RadioParamApplyResult;
struct CallbackBase {
  virtual bool setTxPower(int8_t) = 0;
  @DEFAULT_CALLBACK@
  @DEFAULT_APPLIED@
};
struct LegacyCallbacks : CallbackBase {
  bool accept = true;
  bool setTxPower(int8_t) override { return accept; }
};
struct Prefs {
  int8_t tx_power_dbm = 10;
  float freq = 915;
  uint8_t advert_interval = 0;
  void clearDirty() {}
};
class CommonCLI;
struct Callbacks : CallbackBase {
  CommonCLI* owner = nullptr;
  mesh::Radio radio;
  std::deque<Result> results;
  std::vector<int8_t> attempts;
  unsigned saves = 0;
  bool save_ok = true, acknowledge = true;
  bool power_verified = false, transmitting = false, require_completion = false;
  int8_t hardware = 10, persisted = 10;
  bool setTxPower(int8_t power) override {
    return trySetTxPower(power) == Result::APPLIED;
  }
  Result trySetTxPower(int8_t power) override {
    if (require_completion) assert(completion_seen);
    attempts.push_back(power);
    const Result result = transmitting ? Result::BUSY
        : results.empty() ? Result::APPLIED : results.front();
    if (!transmitting && !results.empty()) results.pop_front();
    // FAILED deliberately models a partial hardware write.
    if (result != Result::BUSY) hardware = power;
    if (result != Result::BUSY) power_verified = result == Result::APPLIED;
    return result;
  }
  bool isTxPowerApplied(int8_t power) const override {
    return power_verified && hardware == power;
  }
  mesh::Radio* getProfileRadio() { return &radio; }
  void updateAdvertTimer() {}
  void savePrefs(PrefsSaveRouting::Scope);
};
@KEY_EQUALS@
class CommonCLI {
public:
  Prefs prefs;
  Callbacks callbacks;
  Prefs* _prefs = &prefs;
  Callbacks* _callbacks = &callbacks;
  bool _tx_power_pending = false;
  uint32_t _tx_power_retry_at = 0;
  bool _common_save_result_known = false;
  bool _common_save_succeeded = false;
  struct Profiles { void loop() {} void servicePendingTuning() {} } _radio_profiles;
  Profiles& radioProfiles() { return _radio_profiles; }
  struct Presets { void loop(unsigned long) {} } _radio_presets;
  struct MorseReply { void complete(const void*) {} } _morse_reply;
  MorseReply& morseReply() { return _morse_reply; }
  CommonCLI() { callbacks.owner = this; }
  @PENDING_QUERY@
  void loopManagement() {}
  void recoverMediaMeshRegions() {}
  void loop();
  void applyAndSaveTxPower(int8_t, char*);
  void servicePendingTxPower();
  void servicePendingRxSettings() {}
  bool trySavePrefs(PrefsSaveRouting::Scope scope = PrefsSaveRouting::Scope::Common);
  void command(const char* text, char* reply) {
    if (!strncmp(text, "set ", 4)) {
      const char* config = text + 4;
      @SET@
      else strcpy(reply, "unknown config");
    } else if (!strncmp(text, "get ", 4)) {
      const char* config = text + 4;
      @GET@
      else strcpy(reply, "unknown config");
    } else strcpy(reply, "unknown command");
  }
};
void Callbacks::savePrefs(PrefsSaveRouting::Scope scope) {
  assert(scope == PrefsSaveRouting::Scope::Common);
  ++saves;
  if (acknowledge) {
    owner->_common_save_result_known = true;
    owner->_common_save_succeeded = save_ok;
  }
  if (save_ok) persisted = owner->prefs.tx_power_dbm;
}
@METHODS@

int main() {
  LegacyCallbacks legacy;
  assert(!legacy.isTxPowerApplied(10));
  assert(legacy.trySetTxPower(14) == Result::APPLIED);
  assert(!legacy.isTxPowerApplied(14)); // The legacy bool setter cannot prove cache validity.
  legacy.accept = false;
  assert(legacy.trySetTxPower(14) == Result::FAILED);
  char reply[160];
  {
    CommonCLI cli;
    // Boot and full-radio recovery can own the outstanding hardware apply,
    // with no dedicated TX queue flag. Never infer success from that flag.
    assert(!cli.hasPendingTxPower() && cli.callbacks.hardware == cli.prefs.tx_power_dbm);
    cli.command("get tx.status", reply);
    assert(!strcmp(reply, "> desired=10,apply=pending"));
    cli.callbacks.power_verified = true;
    cli.command("get tx.status", reply);
    assert(!strcmp(reply, "> desired=10,apply=applied"));
    cli.callbacks.hardware = 12;
    cli.command("get tx.status", reply);
    assert(!strcmp(reply, "> desired=10,apply=pending"));
    assert(cli.callbacks.saves == 0 && cli.callbacks.attempts.empty());
  }
  {
    CommonCLI cli;
    cli.callbacks.results = {Result::BUSY, Result::BUSY, Result::APPLIED};
    cli.command("set tx 14", reply);
    assert(!strcmp(reply, "OK - saved; pending") && cli.hasPendingTxPower());
    assert(cli.prefs.tx_power_dbm == 14 && cli.callbacks.persisted == 14);
    assert(cli.callbacks.hardware == 10 && cli.callbacks.saves == 1);
    cli.command("get tx", reply); assert(!strcmp(reply, "> 14"));
    cli.command("get tx.status", reply);
    assert(!strcmp(reply, "> desired=14,apply=pending"));
    cli.loop(); assert(cli.hasPendingTxPower() && cli.callbacks.saves == 1);
    cli.loop(); assert(!cli.hasPendingTxPower() && cli.callbacks.hardware == 14);
    cli.loop(); assert(cli.callbacks.attempts.size() == 3 && cli.callbacks.saves == 1);
    cli.command("get tx.status", reply);
    assert(!strcmp(reply, "> desired=14,apply=applied"));
  }
  {
    CommonCLI cli;
    cli.callbacks.results = {Result::BUSY, Result::BUSY, Result::APPLIED};
    cli.command("set tx 14", reply); cli.command("set tx 16", reply);
    cli.loop();
    assert(!cli.hasPendingTxPower() && cli.callbacks.hardware == 16);
    assert(cli.prefs.tx_power_dbm == 16 && cli.callbacks.persisted == 16);
    assert(cli.callbacks.attempts == std::vector<int8_t>({14, 16, 16}));
    assert(cli.callbacks.saves == 2);
  }
  {
    CommonCLI cli;
    cli.callbacks.results = {Result::BUSY, Result::BUSY};
    cli.command("set tx 14", reply);
    cli._tx_power_retry_at = now_ms + 500;
    cli.callbacks.save_ok = false;
    cli.command("set tx 16", reply);
    assert(!strncmp(reply, "Error", 5) && cli.prefs.tx_power_dbm == 14);
    assert(cli.hasPendingTxPower() && cli._tx_power_retry_at == now_ms + 500);
    cli.loop(); assert(cli.callbacks.attempts.size() == 2);
    now_ms += 500; cli.loop();
    assert(cli.callbacks.hardware == 14 && !cli.hasPendingTxPower());
  }
  for (Result restore : {Result::APPLIED, Result::BUSY, Result::FAILED}) {
    CommonCLI cli;
    cli.callbacks.save_ok = false;
    cli.callbacks.results = {Result::APPLIED, restore};
    cli.command("set tx 16", reply);
    assert(!strncmp(reply, "Error", 5) && cli.prefs.tx_power_dbm == 10);
    assert(cli.callbacks.saves == 1 && cli.callbacks.persisted == 10);
    assert(cli.callbacks.attempts == std::vector<int8_t>({16, 10}));
    assert(cli.hasPendingTxPower() == (restore != Result::APPLIED));
    cli.command("get tx.status", reply);
    assert(!strcmp(reply, restore == Result::APPLIED
        ? "> desired=10,apply=applied" : "> desired=10,apply=pending"));
    if (restore == Result::FAILED) {
      // A partial restore must remain unverified even if another recovery
      // owner retires the dedicated TX queue before a successful write.
      cli._tx_power_pending = false;
      cli.command("get tx.status", reply);
      assert(!strcmp(reply, "> desired=10,apply=pending"));
      cli._tx_power_pending = true;
      const auto count = cli.callbacks.attempts.size();
      now_ms += 999; cli.loop(); assert(cli.callbacks.attempts.size() == count);
      ++now_ms;
    }
    cli.loop(); assert(!cli.hasPendingTxPower() && cli.callbacks.hardware == 10);
    assert(cli.callbacks.saves == 1);
  }
  {
    CommonCLI cli;
    cli.callbacks.results = {Result::FAILED};
    cli.command("set tx 16", reply);
    assert(!strncmp(reply, "Error", 5) && cli.prefs.tx_power_dbm == 10);
    assert(cli.callbacks.saves == 0 && cli.hasPendingTxPower());
    cli.loop(); assert(cli.callbacks.hardware == 10 && !cli.hasPendingTxPower());
  }
  for (uint32_t start : {1000U, UINT32_MAX - 999U, UINT32_MAX - 500U}) {
    now_ms = start;
    CommonCLI cli;
    cli.callbacks.results = {Result::BUSY, Result::FAILED, Result::APPLIED};
    cli.command("set tx 14", reply); cli.loop();
    assert(cli.hasPendingTxPower() && cli._tx_power_retry_at != 0);
    const auto count = cli.callbacks.attempts.size();
    now_ms = start + 999; cli.loop(); assert(cli.callbacks.attempts.size() == count);
    now_ms = cli._tx_power_retry_at; cli.loop();
    assert(!cli.hasPendingTxPower() && cli.callbacks.hardware == 14);
    assert(cli.callbacks.saves == 1);
  }
  {
    CommonCLI cli;
    cli.callbacks.results = {Result::BUSY, Result::APPLIED};
    cli.command("set tx 14", reply);
    cli.callbacks.radio.carrier = true; cli.loop();
    assert(cli.callbacks.attempts.size() == 1 && cli.hasPendingTxPower());
    cli.callbacks.radio.carrier = false; cli.callbacks.radio.morse = true; cli.loop();
    assert(cli.callbacks.attempts.size() == 1 && cli.hasPendingTxPower());
    cli.callbacks.radio.morse = false; cli.loop();
    assert(cli.callbacks.hardware == 14 && !cli.hasPendingTxPower());
  }
  {
    CommonCLI cli;
    cli.command("set tx 14", reply); assert(!strcmp(reply, "OK"));
    cli.callbacks.acknowledge = false;
    cli.command("set tx 16", reply);
    assert(!strncmp(reply, "Error", 5) && cli.prefs.tx_power_dbm == 14);
    assert(cli.callbacks.hardware == 14 && !cli.hasPendingTxPower());
  }
  for (const char* value : {"garbage", "14tail", "-10", "23", "256", "2147483648"}) {
    CommonCLI cli;
    char command[80]; snprintf(command, sizeof(command), "set tx %s", value);
    cli.command(command, reply);
    assert(!strncmp(reply, "Error", 5) && cli.prefs.tx_power_dbm == 10);
    assert(cli.callbacks.saves == 0 && cli.callbacks.attempts.empty());
  }
}
