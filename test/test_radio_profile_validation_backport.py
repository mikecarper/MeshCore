"""Exercise production profile validation and per-tuple airtime on each chip."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class ProfileValidationBackportTests(unittest.TestCase):
    def test_supported_tuples_protocol_guards_and_operational_airtime(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        methods = "\n".join(body(source, signature).replace("RadioLibWrapper::", "").replace("uint8_t cr)", "uint8_t cr = 0)")
                            for signature in ("bool RadioLibWrapper::validateProfile(",
                                              "uint32_t RadioLibWrapper::getProfileAirtime(",
                                              "uint32_t RadioLibWrapper::getBeaconAirtime("))
        harness = r'''
#include <cassert>
#include <cmath>
#include <RadioProfiles.h>
#define MAX_TRANS_UNIT 255
#define MESH_ENABLE_ETHERNET_WIRE 1
struct RadioLibWrapper {
 mesh::RadioProfiles _profiles;
 bool foreign=false;
 bool supportsMeshtext()const{return foreign;}
 bool supportsEthernetTunnel()const{return foreign;}
 uint16_t profilePreamble(uint8_t profile)const{
  const auto& p=_profiles.params(profile);
  return p.protocol==mesh::RadioProfileProtocol::Meshtext?16:(p.preamble?p.preamble:32);
 }
 uint32_t getEstAirtimeFor(int){return 777;}
 @METHODS@
};
// Preserve the previous floating-point formula as a regression oracle for the
// integer block-count optimization, including the high-band LDRO exception.
double referenceAirtime(const mesh::RadioProfileParams& p,int length,uint8_t cr,uint16_t preamble){
 const double symbol=double(1U<<p.sf)*1000.0/p.bw;
 const int de=(symbol>=16000
#if MESH_RADIO_PROFILE_CHIP==1121
  ||(p.freq>1000&&p.sf>10)
#endif
 )?1:0;
 const double blocks=ceil((8.0*length-4*p.sf+(p.sf<=6?20:28)+16)/(4*(p.sf-2*de)));
 return ceil((preamble+(p.sf<=6?6.25:4.25)+8+(blocks>0?blocks*cr:0))*symbol/1000.0);
}
uint32_t referenceProfileAirtime(const mesh::RadioProfileParams& p,int length,uint8_t cr,uint16_t preamble){
 const double airtime=referenceAirtime(p,length,cr,preamble);
 const uint32_t rounded=airtime>UINT32_MAX?UINT32_MAX:uint32_t(airtime);
#if MESH_RADIO_PROFILE_CHIP==1110||MESH_RADIO_PROFILE_CHIP==1121
 return rounded<200?200:rounded;
#else
 return rounded;
#endif
}
int main(){
 RadioLibWrapper w;
 mesh::RadioProfileParams p;p.freq=MESH_RADIO_PROFILE_CHIP==1268?433:915;p.bw=125;p.sf=8;p.cr=5;
 assert(w.validateProfile(p));
 p.protocol=mesh::RadioProfileProtocol::Meshtext;assert(!w.validateProfile(p));
 w.foreign=true;assert(w.validateProfile(p));
 p.protocol=mesh::RadioProfileProtocol::Ethernet;assert(w.validateProfile(p));
 w.foreign=false;assert(!w.validateProfile(p));p.protocol=mesh::RadioProfileProtocol::MeshCore;
 p.freq=137;
 assert(w.validateProfile(p)==(MESH_RADIO_PROFILE_CHIP==1276));
 p.freq=149;assert(w.validateProfile(p)==(MESH_RADIO_PROFILE_CHIP==1276));
 p.freq=915;p.bw=406.25f;
 assert(w.validateProfile(p)==(MESH_RADIO_PROFILE_CHIP==2021));
 p.freq=2400;
 assert(w.validateProfile(p)==(MESH_RADIO_PROFILE_CHIP==1121||MESH_RADIO_PROFILE_CHIP==2021));
 p.freq=2300;assert(!w.validateProfile(p));
 p.freq=MESH_RADIO_PROFILE_CHIP==1268?433:915;p.bw=125;p.sf=8;
 w._profiles.primary=p;
 assert(w.getProfileAirtime(0,10)==777); // Preserve the existing live-tuple path.
 w._profiles.secondary.params=p;w._profiles.secondary.mode=mesh::RadioProfileMode::Rx;
 const auto short_air=w.getProfileAirtime(0,1);
#if MESH_RADIO_PROFILE_CHIP==1110||MESH_RADIO_PROFILE_CHIP==1121
 assert(short_air==200);
#else
 assert(short_air<200);
#endif
#if MESH_RADIO_PROFILE_CHIP==1121
 p.freq=2400;p.bw=812.5;p.sf=12;w._profiles.primary=p;
 // Pinned LR11xx forces high-band LDRO: 51 coded blocks * CR5, plus overhead.
 const auto expected=uint32_t(ceil((32+4.25+8+51*5)*4096.0/812.5));
 assert(w.getProfileAirtime(0,255)==expected);
 assert(w.getBeaconAirtime(p,255)==expected);
#endif
 assert(w.getProfileAirtime(2,10)==0&&w.getProfileAirtime(0,-1)==0);
 assert(w.getProfileAirtime(0,256)==0);
 for(float bandwidth:{31.25f,41.67f,41.7f,62.5f,83.34f,101.56f,125.0f,203.125f,250.0f,406.25f,500.0f,812.5f,1000.0f}) {
  p.freq=915;p.bw=bandwidth;
#if MESH_RADIO_PROFILE_CHIP==2021
  assert(w.validateProfile(p));
#endif
 }
 // Exercise every packet length and coding rate, including SF5/6 overhead,
 // zero-length negative numerators, narrow-band LDRO and high-band SF11/12.
 // The tolerance edge and long preamble also cover the largest airtimes.
 for(float frequency:{MESH_RADIO_PROFILE_CHIP==1268?433.0f:915.0f,2400.0f})
 for(float bandwidth:{7.791f,7.8f,31.25f,62.5f,125.0f,500.0f,812.5f})
 for(unsigned sf=5;sf<=12;++sf)
 for(unsigned cr=4;cr<=8;++cr)
 for(unsigned preamble:{0,8,32,65528}) {
  p.freq=frequency;p.bw=bandwidth;p.sf=sf;p.cr=cr;p.preamble=preamble;
  p.protocol=mesh::RadioProfileProtocol::MeshCore;
  if(!w.validateProfile(p))continue;
  w._profiles.primary=p;
  for(int length=0;length<=255;++length) {
   const auto expected=referenceProfileAirtime(p,length,cr,w.profilePreamble(0));
   assert(w.getProfileAirtime(0,length)==expected);
   assert(w.getProfileAirtime(0,length,cr)==expected);
   assert(expected<35000000UL);
   const uint16_t beacon_preamble=p.preamble?p.preamble:32;
   const auto beacon_expected=length&&mesh::RadioProfiles::safePreamble(p,beacon_preamble)
       ?uint32_t(referenceAirtime(p,length,cr,beacon_preamble)):0;
   assert(w.getBeaconAirtime(p,length)==beacon_expected);
  }
  assert(w.getProfileAirtime(0,255,4)==referenceProfileAirtime(p,255,4,w.profilePreamble(0)));
  assert(w.getProfileAirtime(0,255,3)==w.getProfileAirtime(0,255));
  assert(w.getProfileAirtime(0,255,9)==w.getProfileAirtime(0,255));
 }
 // Meshtext fixes the preamble to 16 even when a long explicit value is saved.
 w.foreign=true;p.freq=MESH_RADIO_PROFILE_CHIP==1268?433:915;
 p.bw=125;p.sf=8;p.cr=5;p.preamble=65528;
 p.protocol=mesh::RadioProfileProtocol::Meshtext;w._profiles.primary=p;
 assert(w.getProfileAirtime(0,255)==referenceProfileAirtime(p,255,p.cr,16));
 assert(w.getBeaconAirtime(p,255)==uint32_t(referenceAirtime(p,255,p.cr,16)));
}
'''.replace("#include <cassert>", "#include <cassert>\n#include <initializer_list>").replace("@METHODS@", methods)
        with tempfile.TemporaryDirectory(prefix="profile-validation-") as folder:
            cpp = Path(folder) / "test.cpp"
            binary = Path(folder) / "test.exe"
            cpp.write_text(harness)
            for chip in (1262, 1268, 1276, 1272, 68, 1110, 1121, 2021):
                with self.subTest(chip=chip):
                    flags = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-pie", "-no-pie"]
                    built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11", *flags,
                        "-DMESH_RADIO_PROFILE_CHIP="+str(chip), "-I", str(ROOT / "src"),
                        str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__": unittest.main()
