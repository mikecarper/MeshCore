"""Exercise reported multi-SF configuration versus effective state."""
from pathlib import Path
import subprocess
import tempfile
from test_lr2021_apply_transaction import method
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text()
fmt=method(source,'void formatW12MultiSf(').replace(' override','')
code=r"""
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "helpers/radiolib/W12MultiSfPlan.h"
#define MESH_W12_PRAM_SEQUENTIAL_LAB 1
namespace mesh {
enum class RadioProfileProtocol { MeshCore, Other };
namespace lr2021 {
struct Config { bool enabled=true; uint8_t first=5,last=8,profile=0; } cfg;
bool valid=true, safe=false;
const Config& multiSfSettings() { return cfg; }
bool multiSfSettingsValid() { return valid; }
bool safeMode() { return safe; }
}}
struct Fake {
 struct { struct { mesh::RadioProfileProtocol protocol=mesh::RadioProfileProtocol::MeshCore; } primary;
   bool dual=false; bool enabled() const { return dual; } } _profiles;
 struct { bool value=false; bool blocked() const { return value; } } _lab_auto;
 bool _multi_sf_enabled=false,_lab_seq_requested=false,_lab_scan_active=false,_multi_failed=false,_params_valid=true;
 bool _lab_user_auto_off=false;
 uint8_t _multi_sf_lower=5,_multi_sf_upper=8,_lab_seq_selected=0,_lab_seq_first=5,_lab_seq_last=8;
 uint8_t _cur_sf=7; float _cur_bw=62.5; int _numSideDet=0;
 bool isInRecvMode() const { return true; }
 @FMT@
};
int main() {
 char out[128]; Fake f;
 f.formatW12MultiSf(out,sizeof(out)); assert(!strcmp(out,"pending"));
 f._cur_sf=9; f.formatW12MultiSf(out,sizeof(out)); assert(strstr(out,"TX SF outside"));
 f._cur_sf=7; f._multi_sf_enabled=true; f.formatW12MultiSf(out,sizeof(out)); assert(strstr(out,"single active"));
 mesh::lr2021::safe=true; f.formatW12MultiSf(out,sizeof(out)); assert(strstr(out,"safe mode stopping"));
 mesh::lr2021::safe=false; mesh::lr2021::cfg.last=9; f.formatW12MultiSf(out,sizeof(out)); assert(strstr(out,"changing"));
 f._multi_sf_enabled=false; mesh::lr2021::cfg.profile=1;
 f.formatW12MultiSf(out,sizeof(out)); assert(strstr(out,"radio2 off"));
 f._profiles.dual=true; f._lab_seq_requested=true; f._lab_scan_active=true;
 f._lab_seq_selected=1; f._lab_seq_last=9;
 f.formatW12MultiSf(out,sizeof(out)); assert(strstr(out,"dual active"));
 f._lab_scan_active=false; f._lab_auto.value=true;
 f.formatW12MultiSf(out,sizeof(out)); assert(strstr(out,"scanner unavailable"));
 mesh::lr2021::valid=false; f.formatW12MultiSf(out,sizeof(out)); assert(strstr(out,"settings unavailable"));
}
""".replace('@FMT@',fmt)
with tempfile.TemporaryDirectory() as directory:
    p=Path(directory); (p/'test.cpp').write_text(code)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-Isrc',str(p/'test.cpp'),'-o',str(p/'test')],check=True,cwd=ROOT)
    subprocess.run([str(p/'test')],check=True)
print('PASS multi-SF active, pending and fallback status')
