"""Execute the Full RAK SD/Ethernet context handoff without hardware."""
from pathlib import Path
import unittest

from test_rak15002_sd_seeder import build
from test_rak15002_shared_spi import run_native
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cassert>
#include <cstdlib>
#include <cstdint>
#include <initializer_list>
struct OtaManager { enum { IDLE, FETCHING, COMPLETE, PAUSED }; };
struct Store {
  uint32_t staged = 0;
  unsigned releases = 0, selections = 0;
  uint32_t staged_size() const { return staged; }
  bool releaseBus() { ++releases; return staged == 0; }
  bool resetSelection() { ++selections; return staged == 0; }
};
struct Cache { unsigned resets = 0; void resetMedia() { ++resets; } };
struct Manager {
  int state = OtaManager::IDLE;
  bool own = true, interest = true, accept_full = true;
  unsigned serve_jobs = 0, manifest_jobs = 0, removals = 0, clears = 0;
  bool servingPrimaryManifest(const uint8_t*) const { return own; }
  int fetchState() const { return state; }
  unsigned pendingServeJobs() const { return serve_jobs; }
  unsigned pendingManifestJobs() const { return manifest_jobs; }
  void remove_source(Cache*) { ++removals; }
  void set_accept_full(bool value) { accept_full = value; }
  void set_archive_interest(bool value) { interest = value; }
  void clear_primary() { ++clears; }
};
struct Context {
  Manager manager;
  Store fetch_store, card;
  Cache sd_cache;
  uint8_t serve_self_manifest[1] = {};
  bool apply_pending = false, bootloader_apply_pending = false, _sd_cache_fetching = false;
  bool serving = true, folder_active = false, fetch_to_folder = false;
  bool _sd_cache_source_attached = true;
  void* _folder_source = nullptr;
  void* folder_dest = nullptr;
  void* disconnected_folder_dest = nullptr;
  uint32_t serve_expected = 0, _sd_cache_init_retry_at = 999;
  uint8_t* serve_self_leaves = nullptr;
  uint8_t* serve_self_proof = nullptr;
  unsigned buffer_releases = 0;
  Store& sdStagingStore() { return card; }
  void releaseServeBuffer() { ++buffer_releases; }
@METHODS@
};
int main() {
  for (unsigned busy = 0; busy < 14; ++busy) {
    Context c;
    switch (busy) {
      case 0: c.manager.state = OtaManager::FETCHING; break;
      case 1: c.manager.state = OtaManager::COMPLETE; break;
      case 2: c.manager.state = OtaManager::PAUSED; break;
      case 3: c.fetch_store.staged = 1; break;
      case 4: c.apply_pending = true; break;
      case 5: c.bootloader_apply_pending = true; break;
      case 6: c._sd_cache_fetching = true; break;
      case 7: c.manager.serve_jobs = 1; break;
      case 8: c.manager.manifest_jobs = 1; break;
      case 9: c.manager.own = false; break;
      case 10: c.serve_expected = 1; break;
      case 11: c.folder_active = true; break;
      case 12: c._folder_source = &c; break;
      case 13: c.folder_dest = &c; break;
    }
    assert(!c.canReleaseRakSdForEthernet());
    assert(!c.prepareRakSdForEthernet() && !c.releasedRakSdEthernet());
    assert(c.sd_cache.resets == 0 && c.card.releases == 0);
    assert(c.manager.removals == 0 && c.manager.clears == 0 && c.manager.interest);
  }
  for (bool disconnected : {false, true}) {
    Context c;
    if (disconnected) c.disconnected_folder_dest = &c;
    else c.fetch_to_folder = true;
    assert(!c.prepareRakSdForEthernet() && c.card.releases == 0);
  }
  Context c;
  c.serve_self_leaves = static_cast<uint8_t*>(malloc(8));
  c.serve_self_proof = static_cast<uint8_t*>(malloc(8));
  assert(c.canReleaseRakSdForEthernet() && c.prepareRakSdForEthernet());
  assert(c.card.releases == 1 && c.sd_cache.resets == 1);
  assert(!c._sd_cache_source_attached && c.manager.removals == 1);
  assert(!c.serving && !c.serve_self_leaves && !c.serve_self_proof && c.buffer_releases == 1);
  assert(!c.manager.interest && c._sd_cache_init_retry_at == 0);
#ifdef OTA_SEEDER_ONLY
  assert(c.manager.accept_full && c.fetch_store.selections == 0);
#else
  assert(!c.manager.accept_full && c.fetch_store.selections == 1);
#endif
  assert(c.releasedRakSdEthernet() && c.manager.interest);
  assert(c.card.releases == 1); // release callback never probes or remounts
}
'''


class RakSdContextHandoffTests(unittest.TestCase):
    def test_card_maintenance_preserves_non_sd_install_sessions(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'static void formatSdCardElapsed(', 'bool CommonCLI::handleSdCardSetCmd('))
        fixture = (ROOT / 'test/fixtures/rak15002_sd_seeder/maintenance.cpp').read_text()
        for profile in (
                ['OTA_RAK15002_SD_STORE', 'OTA_RAK_AUTO_STORE'],
                ['OTA_RAK15002_SD_STORE', 'OTA_SEEDER_ONLY'],
                ['OTA_TOWER_AUTO_STORE'],
                []):
            with self.subTest(profile=profile):
                run_native(self, fixture.replace('@METHODS@', methods),
                           defines=['NRF52_PLATFORM', 'OTA_SD_STORE', *profile])
        # A passing native compile alone is not a regression check. Removing
        # only the new ownership guard must reproduce the destructive reset.
        guard = extract_braced(methods, 'if (!context.fetch_store.usesSd() &&')
        regressed = methods.replace(guard, '', 1)
        self.assertNotEqual(regressed, methods)
        run_native(self, fixture.replace('@METHODS@', regressed),
                   defines=['NRF52_PLATFORM', 'OTA_SD_STORE',
                            'OTA_RAK15002_SD_STORE', 'OTA_RAK_AUTO_STORE'], negative=True)

    def test_actual_idle_handoff_and_every_pinned_work_guard(self):
        source = (ROOT / 'src/helpers/ota/OtaContext.h').read_text()
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'bool canReleaseRakSdForEthernet() const', 'bool prepareRakSdForEthernet()',
            'bool releasedRakSdEthernet()'))
        harness = HARNESS.replace('@METHODS@', methods)
        for seeder in (False, True):
            with self.subTest(seeder=seeder):
                build(self, harness, ['OTA_RAK15002_SD_STORE',
                                     *(['OTA_SEEDER_ONLY'] if seeder else [])])

    def test_companion_physical_ethernet_and_sd_commands_keep_failed_admission_off(self):
        source = (ROOT / 'examples/companion_radio/main.cpp').read_text()
        method = extract_braced(source, 'bool handleRak4631PeripheralCommand(')
        fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
struct Board {} board;
struct Ethernet {
  bool active = false, allowed = true;
  const char* state = "off";
  unsigned enables = 0, disables = 0;
  bool tryEnable() { ++enables; if (!allowed) return false; active = true; state = "starting"; return true; }
  bool tryDisable() { ++disables; if (!allowed) return false; state = "stopping"; return true; }
  bool hardwareActive() const { return active; }
  const char* status() const { return state; }
  const char* last_error() const { return "storage busy"; }
} ethernet_interface;
namespace mesh { namespace ota {
bool admission = true;
unsigned acquires = 0;
struct Card {
  unsigned queries = 0;
  bool okay = true;
  bool getSpace(Board&, uint64_t& used, uint64_t& free) {
    ++queries; used = 42; free = 4096; return okay;
  }
  const char* last_error() const { return "no SD card"; }
};
struct Context { Card card; Card& sdStagingStore() { return card; } } context;
Context& ota_ctx() { assert(admission); return context; }
bool ota_acquire_context(char* reply, size_t cap) {
  ++acquires; if (!admission) snprintf(reply, cap, "Error: queue busy"); return admission;
}
} }
@METHOD@
int main() {
  char reply[160];
  assert(handleRak4631PeripheralCommand("get eth", reply, sizeof reply));
  assert(strstr(reply, "eth=off") && !ethernet_interface.enables);
  assert(!handleRak4631PeripheralCommand("get unrelated", reply, sizeof reply));
  assert(handleRak4631PeripheralCommand("set eth both", reply, sizeof reply));
  assert(strstr(reply, "Error") && !ethernet_interface.enables);
  ethernet_interface.allowed = false;
  assert(handleRak4631PeripheralCommand("set eth on", reply, sizeof reply));
  assert(strstr(reply, "storage busy") && !ethernet_interface.active);
  ethernet_interface.allowed = true;
  assert(handleRak4631PeripheralCommand("set eth on", reply, sizeof reply));
  assert(strstr(reply, "eth=starting") && ethernet_interface.active);
  ethernet_interface.state = "online";
  assert(handleRak4631PeripheralCommand("get eth", reply, sizeof reply));
  assert(strstr(reply, "eth=online"));
  assert(handleRak4631PeripheralCommand("get sdcard free", reply, sizeof reply));
  assert(strstr(reply, "Ethernet off") && !mesh::ota::acquires);
  assert(handleRak4631PeripheralCommand("set eth off", reply, sizeof reply));
  assert(strstr(reply, "eth=stopping") && ethernet_interface.active);
  assert(handleRak4631PeripheralCommand("get sdcard", reply, sizeof reply));
  assert(strstr(reply, "Ethernet off") && !mesh::ota::acquires);
  ethernet_interface.active = false; ethernet_interface.state = "off";
  assert(handleRak4631PeripheralCommand("get eth", reply, sizeof reply));
  assert(strstr(reply, "eth=off"));
  assert(!ethernet_interface.active);
  mesh::ota::admission = false;
  assert(handleRak4631PeripheralCommand("get sdcard", reply, sizeof reply));
  assert(strstr(reply, "queue busy") && !mesh::ota::context.card.queries);
  mesh::ota::admission = true;
  assert(handleRak4631PeripheralCommand("get sdcard free", reply, sizeof reply));
  assert(strstr(reply, "used=42 free=4096 bytes") && mesh::ota::context.card.queries == 1);
  mesh::ota::context.card.okay = false;
  assert(handleRak4631PeripheralCommand("get sdcard", reply, sizeof reply));
  assert(strstr(reply, "no SD card"));
}
'''.replace('@METHOD@', method)
        build(self, fixture)


if __name__ == '__main__':
    unittest.main()
