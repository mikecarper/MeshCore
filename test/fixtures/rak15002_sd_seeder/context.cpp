#include <helpers/ota/OtaStore.h>
#include <cassert>
#include <vector>
using namespace mesh::ota;

struct OtaStoreSdNrf52 {};
struct OtaCacheSdNrf52 : OtaStoreRam<128> {
  OtaStoreSdNrf52* owner = nullptr;
  bool enabled = true, init_ok = true, save_ok = true;
  int checkpoints = 0, count = 0, resets = 0;
  std::vector<uint8_t> captured;
  uint8_t selected_mid = 0;
  void attach(OtaStoreSdNrf52& value) { owner = &value; }
  bool initialize() { return init_ok; }
  bool setAutoCaptureEnabled(bool value) { enabled = value; return save_ok; }
  bool autoCaptureEnabled() const { return enabled; }
  uint8_t capturedCount() const { return count; }
  bool contains(const uint8_t* mid) {
    for (auto id : captured) if (id == mid[0]) return true;
    return false;
  }
  void set_mid(const uint8_t* mid) { selected_mid = mid[0]; }
  void checkpoint() override { checkpoints++; }
  void resetMedia() { resets++; }
};
struct OtaManager {
  enum FetchState { IDLE, FETCHING, COMPLETE, FAILED, PAUSED };
  struct CatRow { uint8_t mid[4]; uint32_t target_id; uint16_t have_max; bool ready; };
  FetchState state = IDLE;
  OtaStore* store = nullptr;
  OtaCacheSdNrf52* source = nullptr;
  bool interest = false;
  int pulls = 0, resets = 0, refreshes = 0, announces = 0, queries = 0;
  uint32_t deferred_until = 0;
  std::vector<CatRow> rows;
  FetchState fetchState() const { return state; }
  void reset_session() { state = IDLE; resets++; }
  bool add_source(OtaCacheSdNrf52* value) { source = value; return true; }
  void remove_source(OtaCacheSdNrf52*) { source = nullptr; }
  void set_archive_interest(bool value) { interest = value; }
  void set_fetch_store(OtaStore* value) { store = value; }
  void want(uint32_t) {}
  void want_mid(const uint8_t*) {}
  void queryAll() { queries++; }
  void refresh_sources() { refreshes++; }
  void announce() { announces++; }
  void deferCatalog(const uint8_t*, uint32_t until) { deferred_until = until; }
  uint8_t catalogCount() const { return rows.size(); }
  const CatRow* catalogRow(uint8_t i) { return &rows[i]; }
  bool catalogReady(const CatRow* row, uint32_t) const { return row->ready; }
  void pull_archive(const uint8_t*, uint32_t) { pulls++; state = FETCHING; }
};
struct Context {
@MEMBERS@
  bool apply_pending = false, fetch_to_folder = false;
  bool _sd_cache_source_attached = false, _sd_cache_fetching = false;
  uint8_t _sd_cache_mid[4] = {};
  uint32_t _sd_cache_init_retry_at = 0, _sd_cache_next_at = 0;
@METHODS@
};

static Context fresh() { return Context(); }
static void offer(Context& c) {
  c.manager.rows.push_back({{1, 0, 0, 0}, 44, 1, true});
  c.manager.set_fetch_store(&c.fetch_store);
}
int main() {
  {
    Context c = fresh(); offer(c);
    assert(c.fetch_store.capacity() == 1 && !c.fetch_store.begin(2));
    assert(&c.sdStagingStore() == &c.sd_archive_store);
    c.serviceSdCache(100);
    assert(c.sd_cache.owner == &c.sd_archive_store && c.manager.source == &c.sd_cache);
    assert(c.manager.interest && c.manager.store == &c.sd_cache && c.sdCacheFetching());
    assert(c.sd_cache.begin(4)); uint8_t data[] = {1, 2, 3, 4};
    assert(c.sd_cache.write(0, data, sizeof(data)));
    c.stopSdCacheFetch();
    assert(!c.sdCacheFetching() && c.manager.store == &c.fetch_store);
    assert(c.sd_cache.checkpoints == 1 && c.sd_cache.staged_size() == 4);
    assert(c.manager.source == &c.sd_cache); // install/manual work does not discard published archive
    char msg[96] = {};
    assert(!c.apply_fetched_impl(nullptr, false, msg));
    assert(strstr(msg, "cannot install") && !c.apply_pending);
    assert(!c.apply_fetched_bootloader(nullptr, nullptr, msg));
    assert(strstr(msg, "cannot update its bootloader") && !c.apply_pending);
  }
  for (auto state : {OtaManager::FAILED, OtaManager::PAUSED, OtaManager::IDLE}) {
    Context c; offer(c); c.serviceSdCache(100);
    assert(c.sd_cache.begin(4));
    c.manager.state = state; c.serviceSdCache(200);
    assert(!c.sdCacheFetching() && c.manager.store == &c.fetch_store);
    assert(c.sd_cache.checkpoints == 1 && c.sd_cache.staged_size() == 4);
    assert(c.manager.source == &c.sd_cache);
    if (state != OtaManager::IDLE) assert(c.manager.deferred_until == 300200);
    c.serviceSdCache(300); assert(c.manager.pulls == 1);
    c.serviceSdCache(3300); assert(c.manager.pulls == 2);
  }
  {
    Context c; offer(c); c.serviceSdCache(100);
    c.manager.state = OtaManager::COMPLETE; c.serviceSdCache(200);
    assert(!c.sdCacheFetching() && c.manager.store == &c.fetch_store);
    assert(c.manager.refreshes == 1 && c.manager.announces == 1);
  }
  {
    Context c; offer(c); c.sd_cache.count = OTA_SD_CACHE_MAX;
    c.serviceSdCache(100);
    assert(c.manager.pulls == 0 && c.manager.source == &c.sd_cache);
    assert(c.setSdCacheEnabled(false));
    assert(c.manager.source == &c.sd_cache && !c.manager.interest);
    c.serviceSdCache(200); assert(c.manager.pulls == 0);
  }
  {
    Context c; offer(c); c.sd_cache.init_ok = false;
    c.serviceSdCache(100); assert(c.manager.pulls == 0 && c.manager.source == nullptr);
    c.sd_cache.init_ok = true;
    c.serviceSdCache(200); assert(c.manager.pulls == 0);
    c.serviceSdCache(60100); assert(c.manager.pulls == 1);
    c.prepareSdCardReset();
    assert(c.manager.source == nullptr && !c.sdCacheFetching());
    c.finishSdCardReset(60200);
    assert(c.manager.source == &c.sd_cache && c.sd_cache.resets == 2);
  }
}
