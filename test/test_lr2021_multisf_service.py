"""Run the multi-SF ownership service with a simulated radio."""
from pathlib import Path
import subprocess
import tempfile
from test_lr2021_apply_transaction import method

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
service = method(source, 'void serviceMultiSf()')
code = r"""
#include <cassert>
#include <cstdint>
#include <climits>
static uint32_t clock_ms=10;
uint32_t millis() { return clock_ms; }
namespace mesh {
enum class RadioParamApplyResult { APPLIED, BUSY, FAILED };
namespace lr2021 {
struct Config { bool enabled=true; uint8_t first=5,last=8,profile=0; } cfg;
bool valid=true, safe=false;
const Config& multiSfSettings() { return cfg; }
bool multiSfSettingsValid() { return valid; }
bool safeMode() { return safe; }
}}
struct Profiles { uint32_t generation[2]={1,1}; bool dual=false; bool enabled() const { return dual; } };
struct Fake {
 Profiles _profiles;
 bool _multi_sf_enabled=false, _lab_seq_requested=false, _lab_uncertain=false, _params_valid=true;
 bool blocked=false, fail_apply=false, automatic=true;
 uint8_t _multi_sf_lower=5,_multi_sf_upper=8,_lab_seq_selected=0,_lab_seq_first=5,_lab_seq_last=8;
 uint32_t _multi_retry_at=0,_multi_gen0=UINT32_MAX,_multi_gen1=UINT32_MAX;
 uint8_t _multi_key_first=0,_multi_key_last=0,_multi_key_profile=0,_multi_key_flags=0;
 bool _multi_failed=false;
 mesh::RadioParamApplyResult trySetW12MultiSf(bool on,uint8_t first,uint8_t last) {
   if (blocked) return mesh::RadioParamApplyResult::BUSY;
   if (fail_apply) return mesh::RadioParamApplyResult::FAILED;
   _multi_sf_enabled=on; _multi_sf_lower=first; _multi_sf_upper=last;
   return mesh::RadioParamApplyResult::APPLIED;
 }
 bool labScannerSetAutomatic(bool on) { if (blocked) return false; automatic=on; return true; }
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
 void tick() { clock_ms+=1001; serviceMultiSf(); }
};
int main() {
 Fake f; f.tick(); assert(f._multi_sf_enabled);
 mesh::lr2021::safe=true; f.blocked=true; f.tick(); assert(f._multi_sf_enabled);
 f.blocked=false; f.tick(); assert(!f._multi_sf_enabled);
 mesh::lr2021::safe=false; f.tick(); assert(f._multi_sf_enabled);
 f._profiles.dual=true; ++f._profiles.generation[1]; f.blocked=true; f.tick();
 assert(f._multi_sf_enabled && !f._lab_seq_requested);
 f.blocked=false; f.tick(); assert(!f._multi_sf_enabled && f._lab_seq_requested && f.automatic);
 mesh::lr2021::cfg.profile=1; mesh::lr2021::cfg.first=6; mesh::lr2021::cfg.last=9;
 f.tick(); assert(f._lab_seq_requested && f._lab_seq_selected==1 && f._lab_seq_first==6);
 mesh::lr2021::cfg.enabled=false; f.tick(); assert(!f._lab_seq_requested && f.automatic);
 f._profiles.dual=false; ++f._profiles.generation[1]; mesh::lr2021::cfg.enabled=true;
 mesh::lr2021::cfg.profile=0; f.fail_apply=true; f.tick(); assert(f._multi_failed);
 f.fail_apply=false; f.tick(); assert(!f._multi_sf_enabled); // no reset storm
 ++f._profiles.generation[0]; f.tick(); assert(f._multi_sf_enabled);
 mesh::lr2021::valid=false; f.tick(); assert(!f._multi_sf_enabled);
}
""".replace('@SERVICE@',service)
with tempfile.TemporaryDirectory() as directory:
    p=Path(directory); (p/'test.cpp').write_text(code)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS multi-SF ownership, safe mode, retries and persistence policy')
