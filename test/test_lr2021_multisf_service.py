"""Run the multi-SF ownership service with a simulated radio."""
from pathlib import Path
import subprocess
import tempfile
from test_lr2021_apply_transaction import method

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
service = method(source, 'void serviceMultiSf(')
prepare = method(source, 'mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t p, bool reply)').replace(' override', '').replace('RadioLibWrapper::prepareTransmitProfile', 'Base::prepareTransmitProfile')
release = method(source, 'mesh::RadioParamApplyResult labReleaseForExclusive()')
pending = method(source, 'bool labExclusivePending() const')
manual = method(source, 'bool labScannerSetAutomaticManual(')
beacon = method(source, 'mesh::RadioParamApplyResult prepareBeaconTransmit(').replace(' override', '').replace('RadioLibWrapper::prepareBeaconTransmit', 'Base::prepareBeaconTransmit')
code = r"""
#include <cassert>
#include <cstdint>
#include <climits>
#define MESH_W12_PRAM_SEQUENTIAL_LAB 1
#define MESH_W12_PRAM_SEQ_TX_LAB 1
#define MESH_W12_MULTISF_EXPERIMENT 1
static uint32_t clock_ms=10;
uint32_t millis() { return clock_ms; }
namespace mesh {
enum class RadioParamApplyResult { APPLIED, BUSY, FAILED };
struct RadioProfileParams {};
namespace radioDirection { enum class Mode { Rx, Both, Tx }; Mode current=Mode::Both; Mode mode() { return current; } }
namespace w12pram { struct AutoPolicy { enum class Reason { None, Off, Failed }; bool failed=false,on=true;
  bool blocked() const { return failed; } bool enabled() const { return on; }
  Reason reason() const { return !on ? Reason::Off : failed ? Reason::Failed : Reason::None; }
  void setEnabled(bool value) { on=value; failed=false; } void block(Reason) { failed=true; } }; }
namespace lr2021 {
struct Config { bool enabled=true; uint8_t first=5,last=8,profile=0; } cfg;
bool valid=true, safe=false;
const Config& multiSfSettings() { return cfg; }
bool multiSfSettingsValid() { return valid; }
bool safeMode() { return safe; }
}}
struct Base { mesh::RadioParamApplyResult beacon_result=mesh::RadioParamApplyResult::APPLIED; bool beacon_owned=false;
 mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t,bool) { return mesh::RadioParamApplyResult::APPLIED; }
 mesh::RadioParamApplyResult prepareBeaconTransmit(const mesh::RadioProfileParams&,int8_t) {
  if (beacon_result==mesh::RadioParamApplyResult::APPLIED) beacon_owned=true; return beacon_result;
 } };
struct Profiles { uint32_t generation[2]={1,1}; bool dual=false; bool enabled() const { return dual; }
 bool canTransmit(uint8_t,bool) const { return true; } };
struct Fake : Base {
 Profiles _profiles;
 bool _multi_sf_enabled=false, _lab_seq_requested=false, _lab_uncertain=false, _params_valid=true;
 bool blocked=false, fail_apply=false, automatic=true,_profile_repair_required=false;
 bool _multi_key_extra=false,_multi_key_enabled=false; unsigned _numSideDet=0;
 mesh::w12pram::AutoPolicy _lab_auto;
 struct { int cancellations=0; bool queued=false; bool pending() const { return queued; } void cancel() { ++cancellations; queued=false; } } _lab_handoff_retry;
 bool _lab_seq_tx_prepared=false,_lab_seq_tx_lease=false,_lab_scan_active=false,_lab_resume_pending=false;
 bool _lab_session_ldo=false,_lab_exclusive_restore_auto=false,_lab_user_auto_off=false;
 uint32_t _lab_exclusive_until=0;
 uint8_t _lab_seq_tx_profile=0; uint32_t _lab_tx_pauses=0,_lab_resume_at=0;
 uint8_t _multi_sf_lower=5,_multi_sf_upper=8,_lab_seq_selected=0,_lab_seq_first=5,_lab_seq_last=8;
 uint32_t _multi_retry_at=0,_multi_gen0=UINT32_MAX,_multi_gen1=UINT32_MAX;
 uint8_t _multi_key_first=0,_multi_key_last=0,_multi_key_profile=0,_multi_key_flags=0;
 bool _multi_failed=false;
 mesh::RadioParamApplyResult trySetW12MultiSf(bool on,uint8_t first,uint8_t last) {
   if (blocked) return mesh::RadioParamApplyResult::BUSY;
   if (fail_apply || (on && _numSideDet)) return mesh::RadioParamApplyResult::FAILED;
   _multi_sf_enabled=on; _multi_sf_lower=first; _multi_sf_upper=last;
   return mesh::RadioParamApplyResult::APPLIED;
 }
 bool labScannerSetAutomatic(bool on) { if (blocked) return false; automatic=on; _lab_auto.setEnabled(on);
  if (!on) { _lab_scan_active=false; _lab_session_ldo=false; } return true; }
 mesh::RadioParamApplyResult labPauseScanner(bool,uint8_t) { return mesh::RadioParamApplyResult::APPLIED; }
 bool labSequentialConfigure(int selected) {
   if (blocked || automatic) return false;
   _lab_seq_requested=selected>=0;
   if (selected>=0) _lab_seq_selected=selected;
   return true;
 }
 bool labSequentialSetRange(uint8_t first,uint8_t last) {
   if (blocked || automatic) return false;
   _lab_seq_first=first; _lab_seq_last=last; return true;
 }
 @SERVICE@
 @PREPARE@
 @RELEASE@
 @PENDING@
 @MANUAL@
 @BEACON@
 void tick() { clock_ms+=1001; serviceMultiSf(mesh::radioDirection::current!=mesh::radioDirection::Mode::Tx,
     labExclusivePending()||beacon_owned); }
};
int main() {
 Fake f; f.tick(); assert(f._multi_sf_enabled);
 mesh::lr2021::safe=true; f.blocked=true; f.tick(); assert(f._multi_sf_enabled);
 f.blocked=false; f.tick(); assert(!f._multi_sf_enabled);
 mesh::lr2021::safe=false; f.tick(); assert(f._multi_sf_enabled);
 f._profiles.dual=true; ++f._profiles.generation[1]; f.blocked=true; f.tick();
 assert(f._multi_sf_enabled && !f._lab_seq_requested);
 f.blocked=false; f.tick(); assert(!f._multi_sf_enabled && f._lab_seq_requested && f.automatic);
 // The real TX handoff is BUSY while the requested scanner has not started.
 assert(f.prepareTransmitProfile(0,false)==mesh::RadioParamApplyResult::BUSY);
 // Once scanner startup is blocked, the service relinquishes sequential
 // ownership; the real prepare method can transmit using the host fallback.
 f._lab_auto.failed=true; f.tick(); assert(f._multi_failed && !f._lab_seq_requested);
 assert(f.prepareTransmitProfile(0,false)==mesh::RadioParamApplyResult::APPLIED);
 ++f._profiles.generation[1]; f.tick(); assert(f._lab_seq_requested);
 f._lab_handoff_retry.queued=true;
 mesh::lr2021::cfg.profile=1; mesh::lr2021::cfg.first=6; mesh::lr2021::cfg.last=9;
 f.tick(); assert(!f._lab_handoff_retry.pending() && f._lab_seq_requested && f._lab_seq_selected==1 && f._lab_seq_first==6);
 mesh::lr2021::cfg.enabled=false; f.tick(); assert(!f._lab_seq_requested && f.automatic);
 f._profiles.dual=false; ++f._profiles.generation[1]; mesh::lr2021::cfg.enabled=true;
 mesh::lr2021::cfg.profile=0; f.fail_apply=true; f.tick(); assert(f._multi_failed);
 f.fail_apply=false; f.tick(); assert(!f._multi_sf_enabled); // no reset storm
 ++f._profiles.generation[0]; f.tick(); assert(f._multi_sf_enabled);
 mesh::lr2021::valid=false; f.tick(); assert(!f._multi_sf_enabled);
 mesh::lr2021::valid=true; f.tick(); assert(f._multi_sf_enabled);
 mesh::RadioProfileParams beacon; f.beacon_result=mesh::RadioParamApplyResult::BUSY;
 assert(f.prepareBeaconTransmit(beacon,9)==mesh::RadioParamApplyResult::BUSY);
 assert(!f._multi_sf_enabled && f.labExclusivePending());
 f.tick(); assert(!f._multi_sf_enabled); // BUSY retry cannot be starved by reacquisition
 f.beacon_result=mesh::RadioParamApplyResult::APPLIED;
 assert(f.prepareBeaconTransmit(beacon,9)==mesh::RadioParamApplyResult::APPLIED && f.beacon_owned);
 for (int i=0;i<8;++i) f.tick(); assert(!f._multi_sf_enabled);
 f.beacon_owned=false; f.tick(); assert(f._multi_sf_enabled);
 mesh::lr2021::cfg.enabled=false; f.tick(); assert(!f._multi_sf_enabled);
 f._numSideDet=1; mesh::lr2021::cfg.enabled=true; f.tick(); assert(f._multi_failed);
 f._numSideDet=0; f.tick(); assert(!f._multi_failed && f._multi_sf_enabled);
 // A beacon can also temporarily release an active dual scanner.
 f._profiles.dual=true; ++f._profiles.generation[1]; f.tick();
 assert(f._lab_seq_requested && !f._multi_sf_enabled);
 f._lab_scan_active=true; f._lab_session_ldo=true;
 f.beacon_result=mesh::RadioParamApplyResult::BUSY;
 assert(f.prepareBeaconTransmit(beacon,9)==mesh::RadioParamApplyResult::BUSY);
 assert(!f._lab_seq_requested && !f._lab_scan_active && f.labExclusivePending());
 f.tick(); assert(!f._lab_seq_requested);
 f.beacon_result=mesh::RadioParamApplyResult::APPLIED;
 assert(f.prepareBeaconTransmit(beacon,9)==mesh::RadioParamApplyResult::APPLIED);
 for (int i=0;i<8;++i) f.tick(); assert(!f._lab_seq_requested);
 f.beacon_owned=false; f.tick(); assert(f._lab_seq_requested);
 // Active dual mode -> TX-only releases scanner ownership and restores
 // normal host TX; switching back makes the scanner eligible again.
 f._lab_scan_active=true; f._lab_session_ldo=true;
 mesh::radioDirection::current=mesh::radioDirection::Mode::Tx; f.tick();
 assert(!f._lab_seq_requested && !f._lab_scan_active);
 assert(f.prepareTransmitProfile(0,false)==mesh::RadioParamApplyResult::APPLIED);
 mesh::radioDirection::current=mesh::radioDirection::Mode::Both; f.tick();
 assert(f._lab_seq_requested);
 // Manual scanner off during an exclusive owner wins over deferred restore.
 f._lab_scan_active=true; f._lab_session_ldo=true;
 assert(f.prepareBeaconTransmit(beacon,9)==mesh::RadioParamApplyResult::APPLIED);
 assert(f._lab_exclusive_restore_auto);
 const int cancellations_before_manual=f._lab_handoff_retry.cancellations;
 assert(f.labScannerSetAutomaticManual(false));
 assert(f._lab_handoff_retry.cancellations==cancellations_before_manual+1);
 assert(!f._lab_exclusive_restore_auto && f._lab_user_auto_off);
 for (int i=0;i<8;++i) f.tick(); f.beacon_owned=false; f.tick();
 assert(!f._lab_seq_requested && f._lab_user_auto_off);
 assert(f.labScannerSetAutomaticManual(true)); assert(f._lab_handoff_retry.cancellations==cancellations_before_manual+2); f.tick(); assert(f._lab_seq_requested);
 // A recovered handoff is blocked while awaiting its retry. Disabling
 // multi-SF supersedes that retry and must release the old temporary block
 // so the ordinary dual-profile scanner can run.
 mesh::lr2021::cfg.enabled=true; mesh::lr2021::safe=false;
 Fake g; g._profiles.dual=true; g.tick(); assert(g._lab_seq_requested);
 g._lab_auto.failed=true; g._lab_handoff_retry.queued=true; g.tick();
 assert(!g._lab_seq_requested && g._lab_auto.failed && g._lab_handoff_retry.pending());
 mesh::lr2021::cfg.enabled=false; g.tick();
 assert(!g._lab_handoff_retry.pending() && !g._lab_auto.failed && g._lab_auto.enabled() && !g._lab_seq_requested);
 mesh::lr2021::cfg.enabled=true; Fake range; range._profiles.dual=true; range.tick();
 range._lab_auto.failed=true; range._lab_handoff_retry.queued=true; range.tick();
 ++mesh::lr2021::cfg.first; range.tick();
 assert(!range._lab_handoff_retry.pending() && !range._lab_auto.failed && range._lab_seq_requested
     && range._lab_seq_first==mesh::lr2021::cfg.first);
 // A real fault without a pending successful handoff recovery stays latched.
 mesh::lr2021::cfg.enabled=true; Fake h; h._profiles.dual=true; h.tick();
 h._lab_auto.failed=true; h.tick(); assert(!h._lab_seq_requested && h._lab_auto.failed);
 mesh::lr2021::cfg.enabled=false; h.tick(); assert(h._lab_auto.failed);
 // A still-broken radio is never released by cancelling an obsolete retry.
 mesh::lr2021::cfg.enabled=true; Fake j; j._profiles.dual=true; j.tick();
 j._lab_auto.failed=true; j._lab_handoff_retry.queued=true; j.tick();
 j._profile_repair_required=true; mesh::lr2021::cfg.enabled=false; j.tick();
 assert(j._lab_auto.failed && !j._lab_handoff_retry.pending());
 // Disabling the scanner manually remains authoritative.
 mesh::lr2021::cfg.enabled=true; Fake k; k._profiles.dual=true; k.tick();
 k._lab_auto.failed=true; k._lab_handoff_retry.queued=true; k.tick();
 k._lab_user_auto_off=true; k._lab_auto.setEnabled(false);
 mesh::lr2021::cfg.enabled=false; k.tick(); assert(!k._lab_auto.enabled() && !k._lab_handoff_retry.pending());
 // Safe mode still prevents scanner activation after a plan change.
 mesh::lr2021::cfg.enabled=true; Fake s; s._profiles.dual=true; s.tick();
 s._lab_auto.failed=true; s._lab_handoff_retry.queued=true; s.tick();
 mesh::lr2021::safe=true; mesh::lr2021::cfg.enabled=false; s.tick();
 assert(!s._lab_seq_requested && !s._lab_scan_active && !s._lab_handoff_retry.pending());
 mesh::lr2021::safe=false;
}
""".replace('@SERVICE@',service).replace('@PREPARE@',prepare).replace('@RELEASE@',release).replace('@PENDING@',pending).replace('@MANUAL@',manual).replace('@BEACON@',beacon)
with tempfile.TemporaryDirectory() as directory:
    p=Path(directory); (p/'test.cpp').write_text(code)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS multi-SF ownership, safe mode, retries and persistence policy')
