"""Exercise the production conditional rearm with conflicting scheduler state."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#define ENABLE_OTA 1
#define MESH_ENABLE_FLEET_CONTROL 1
namespace mesh { namespace ota {
struct OtaManager { enum FetchState {IDLE, ACTIVE}; FetchState state=IDLE; FetchState fetchState() const {return state;} };
struct Context {OtaManager manager;}; Context context; bool present=false;
const Context* ota_context_if_active() {return present ? &context : nullptr;}
}}
struct Barrier {bool value=false; bool waiting() const {return value;}};
struct Profiles {bool available=true; bool secondaryRuntimeAvailable() const {return available;}};
struct Cli {bool pending=false; Profiles profiles; bool hasPendingRadioSettings() const {return pending;} Profiles& radioProfiles() {return profiles;}};
struct RadioProfiles {bool value=false; bool enabled() const {return value;}};
struct Radio {RadioProfiles p; bool present=true; RadioProfiles* profiles() {return present?&p:nullptr;}};
struct MyMesh {
 bool temp_radio_applied=false, temp_radio_handoff_pending=false, saved_radio_apply_pending=false;
 bool fleet_schedule_pending_=false, otaApply=false; unsigned _ota_update_at=0, calls=0;
 Barrier temp_radio_reply_barrier; Cli _cli; Radio radio; Radio* _radio=&radio;
 struct Setting {bool active=false;} scheduled_radio_settings[2];
 struct {float freq=910.525f,bw=62.5f;uint8_t sf=7,cr=5;} _prefs;
 bool hasPendingOtaApply() {return otaApply;}
 int findFreeScheduledRadioSlot() {for(int i=0;i<2;i++) if(!scheduled_radio_settings[i].active)return i;return -1;}
 void applyTempRadioParams(float,float,uint8_t,uint8_t,int,uint16_t) {++calls;scheduled_radio_settings[0].active=true;}
 bool tryApplyIdleTempRadioParams(float,float,uint8_t,uint8_t,int,uint16_t);
};
'''

MAIN = r'''
bool attempt(MyMesh& m) {return m.tryApplyIdleTempRadioParams(910.525f,62.5f,7,5,30,32);}
int main() {
 {MyMesh m; assert(attempt(m)); assert(m.calls==1); assert(!attempt(m)); assert(m.calls==1);}
 #define REJECT(expr) {MyMesh m; expr; assert(!attempt(m)); assert(m.calls==0);}
 REJECT(m.temp_radio_applied=true)
 REJECT(m.temp_radio_handoff_pending=true)
 REJECT(m.saved_radio_apply_pending=true)
 REJECT(m.temp_radio_reply_barrier.value=true)
 REJECT(m._cli.pending=true)
 REJECT(m._cli.profiles.available=false)
 REJECT(m.otaApply=true)
 REJECT(m._ota_update_at=1)
 REJECT(m.fleet_schedule_pending_=true)
 REJECT(m.scheduled_radio_settings[0].active=true)
 REJECT(m.radio.p.value=true)
 REJECT(m.radio.present=false)
 REJECT(m._radio=nullptr)
 REJECT(m._prefs.freq=910.250f)
 REJECT(m._prefs.bw=125.0f)
 REJECT(m._prefs.sf=8)
 REJECT(m._prefs.cr=6)
 mesh::ota::present=true; mesh::ota::context.manager.state=mesh::ota::OtaManager::ACTIVE;
 {MyMesh m; assert(!attempt(m)); assert(m.calls==0);}
 mesh::ota::context.manager.state=mesh::ota::OtaManager::IDLE;
 {MyMesh m; assert(attempt(m));}
}
'''


class IdleTempRadioTest(unittest.TestCase):
    def test_admission_and_conflicts(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        source = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text(encoding='utf-8')
        signature = 'bool MyMesh::tryApplyIdleTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins, uint16_t preamble)'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.cpp').write_text(HARNESS + '\n' + body(source, signature) + MAIN, encoding='utf-8')
            exe = path / 'test.exe'
            subprocess.run([compiler, '-std=c++17', str(path / 'test.cpp'), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
