"""Exercise production flood-profile settings, persistence and forwarding gates."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
CODE=r'''
#define main original_main
#include "test/fixtures/radio_profiles/cli_test.cpp"
#undef main
#include <helpers/FloodProfilePolicy.h>
struct Packet { bool flood, forwarded; uint8_t radio_profile; bool isRouteFlood() const {return flood;} };
int main() {
  Fixture f(true);
  f.cli.beginRepeaterFloodPolicy();
  assert(!strcmp(f.cmd("get radio.flood"), "> on"));
  assert(!strcmp(f.cmd("get radio2.flood"), "> on"));
  f.cmd("set radio.flood off"); f.cmd("set radio2.flood on");
  Packet primary{true,false,0}, secondary{true,false,1}, direct{false,true,0}, local{true,false,0};
  assert(!mesh::flood_profiles::allowIngress(&primary,f.cli));
  assert(mesh::flood_profiles::allowIngress(&secondary,f.cli));
  assert(mesh::flood_profiles::allowIngress(&direct,f.cli));
  assert(mesh::flood_profiles::allowEgress(&direct,f.cli));
  assert(mesh::flood_profiles::allowEgress(&local,f.cli));
  Packet crossToPrimary{true,true,0}, crossToSecondary{true,true,1};
  assert(!mesh::flood_profiles::allowEgress(&crossToPrimary,f.cli));
  assert(mesh::flood_profiles::allowEgress(&crossToSecondary,f.cli));
  Radio reboot; mesh::RadioProfileCLI restored;
  restored.begin(&f.fs,&reboot,&f.clock,true); restored.beginRepeaterFloodPolicy();
  assert(!restored.floodProfileAllowed(0) && restored.floodProfileAllowed(1));
  f.fs.fail_write=true; f.cmd("set radio2.flood off",false);
  assert(f.cli.floodProfileAllowed(1));
  f.fs.fail_write=false;
  f.fs.files["/flood_profiles"][0]='X';
  Radio broken; mesh::RadioProfileCLI safe;
  safe.begin(&f.fs,&broken,&f.clock,true); safe.beginRepeaterFloodPolicy();
  assert(!safe.floodProfileAllowed(0) && !safe.floodProfileAllowed(1));
  char reply[160]={};assert(safe.handle("set radio.flood on",reply,sizeof(reply)));
  assert(strncmp(reply,"Error",5)==0);
}
'''
class FloodProfileTest(unittest.TestCase):
    def test_production_policy(self):
        cc=shutil.which('g++') or shutil.which('clang++')
        if not cc:self.skipTest('C++ compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'flood.cpp';src.write_text(CODE);exe=Path(d)/'flood.exe'
            p=subprocess.run([cc,'-std=c++17','-DMESH_ENABLE_FLEET_CONTROL=0','-DMESH_ENABLE_CARRIER_TOOLS=0',
                '-I',str(ROOT),'-I',str(ROOT/'test/fixtures/radio_profiles/mocks'),'-I',str(ROOT/'test/mocks'),'-I',str(ROOT/'src'),
                str(ROOT/'src/helpers/RadioProfileCLI.cpp'),str(src),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            r=subprocess.run([str(exe)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_production_repeater_wires_ingress_and_egress(self):
        s=(ROOT/'examples/simple_repeater/MyMesh.cpp').read_text(encoding='utf8')
        self.assertIn('mesh::flood_profiles::allowIngress(packet, _cli.radioProfiles())',s)
        self.assertIn('mesh::flood_profiles::allowEgress(packet, _cli.radioProfiles())',s)
        self.assertIn('_cli.radioProfiles().beginRepeaterFloodPolicy()',s)
if __name__=='__main__':unittest.main()
