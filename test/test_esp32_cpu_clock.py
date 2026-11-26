"""Exercise CPU selection transactions and the actual Companion preference codec."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS, esp_recovery_helpers
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

POLICY = r'''
#include <cassert>
#include <deque>
#include <string>
#include <vector>
#include <helpers/esp32/CpuClockPolicy.h>
using namespace mesh::esp32::cpuclock;
struct Board {
  uint16_t actual=80;
  bool supports240=true, companion=true;
  unsigned applies=0;
  std::deque<bool> outcomes;
  bool supportsCpuFrequencyMHz(uint16_t value) const {
    return value==80 || value==160 || (value==240 && supports240);
  }
  uint16_t getCpuFrequencyMHz() const { return actual; }
  bool applyCpuFrequencyMHz(uint16_t selected,bool ps) {
    ++applies;
    actual=resolveMHz(selected,ps,companion); // partial mutation can precede failure
    if(outcomes.empty()) return true;
    bool ok=outcomes.front();outcomes.pop_front();return ok;
  }
};
int main() {
  assert(nominalMHz()==160);
  assert(resolveMHz(0,true,true)==80 && resolveMHz(0,false,true)==160);
  assert(resolveMHz(0,true,false)==160 && resolveMHz(0,false,false)==160);
  for(uint16_t selected : {80,160,240})
    for(bool ps : {false,true}) for(bool companion : {false,true})
      assert(resolveMHz(selected,ps,companion)==selected);
  uint16_t selected=160,durable=160;Board board;board.actual=160;
  bool storage=true;unsigned saves=0;char reply[160]={};
  auto command=[&](const char* text,bool ps=true) {
    return handleCommand(board,selected,ps,text,reply,sizeof(reply),[&] {
      ++saves;if(!storage)return false;durable=selected;return true;
    });
  };
  assert(command("get cpu.freq"));
  assert(std::string(reply)=="> selected=160 actual=160MHz supported=auto,80,160,240");
  assert(!command("get cpu.frequency"));
  for(const char* invalid : {"", "0", "40", "200", "080", "160MHz", "160.0",
      "-80", "+80", "AUTO", "160 240", "99999999999999999999999"}) {
    const std::string text=std::string("set cpu.freq ")+invalid;
    unsigned old=board.applies,old_saves=saves;
    assert(command(text.c_str()));assert(std::string(reply).find("Error:")==0);
    assert(selected==160&&durable==160&&board.actual==160);
    assert(board.applies==old&&saves==old_saves);
  }
  board.supports240=false;assert(command("set cpu.freq 240"));
  assert(selected==160&&durable==160&&board.actual==160&&board.applies==0&&saves==0);
  assert(command("get cpu.freq"));
  assert(std::string(reply)=="> selected=160 actual=160MHz supported=auto,80,160");
  board.supports240=true;
  assert(command("set cpu.freq 240"));assert(selected==240&&durable==240&&board.actual==240);
  for(bool ps : {false,true}) {
    assert(board.applyCpuFrequencyMHz(selected,ps));assert(board.actual==240);
  }
  storage=false;assert(command("set cpu.freq 80"));
  assert(selected==240&&durable==240&&board.actual==240);
  assert(std::string(reply)=="Error: CPU clock save failed; restored");
  storage=true;board.outcomes={false,true};unsigned old_saves=saves;
  assert(command("set cpu.freq 160"));assert(saves==old_saves);
  assert(selected==240&&durable==240&&board.actual==240);
  board.outcomes={true,false};storage=false;
  assert(command("set cpu.freq 80"));
  assert(std::string(reply).find("save and restore failed")!=std::string::npos);
  assert(selected==240&&durable==240);
  storage=true;board.outcomes.clear();
  assert(command("set cpu.freq auto"));assert(selected==0&&durable==0&&board.actual==80);
  assert(board.applyCpuFrequencyMHz(selected,false));assert(board.actual==160);
  assert(command("set cpu.freq \t160\t "));
  assert(selected==160&&durable==160&&board.actual==160);
  for(uint16_t value : {0,80,160,240}) {
    uint8_t footer[FooterSize];encodeFooter(footer,value);
    uint16_t decoded=999;assert(decodeFooter(footer,sizeof(footer),decoded)&&decoded==value);
    for(size_t n=0;n<FooterSize;++n) {
      decoded=240;assert(!decodeFooter(footer,n,decoded)&&decoded==0);
      for(unsigned bit=0;bit<8;++bit) {
        footer[n]^=uint8_t(1u<<bit);decoded=240;
        assert(!decodeFooter(footer,sizeof(footer),decoded)&&decoded==0);
        footer[n]^=uint8_t(1u<<bit);
      }
    }
    footer[4]=0;footer[5]=200;uint16_t crc=footerChecksum(footer,6);
    footer[6]=uint8_t(crc>>8);footer[7]=uint8_t(crc);
    decoded=240;assert(!decodeFooter(footer,sizeof(footer),decoded)&&decoded==0);
  }
}
'''

PERSISTENCE = r'''
int main() {
  using namespace mesh::esp32::cpuclock;
  for(float timezone : {-7.0f,-7.5f}) {
    DataStore store;CompanionNodePrefs prefs;
    prefs.freq=910.525f;strcpy(prefs.node_name,"preserved");prefs.ble_pin=123456;
    prefs.tz_offset=timezone;prefs.tx_media=mesh::RADIO_TX_SECONDARY;
    assert(store.savePrefs(prefs,47.125,-122.25));
    const auto legacy=store.fs.files["/new_prefs"];
    assert(legacy.size()==236+mesh::companion::traffic::FooterSize
        +(timezone==-7.5f?mesh::companion::timezone::FooterSize:0));
    for(uint16_t value : {80,160,240}) {
      prefs.cpu_freq_mhz=value;assert(store.savePrefs(prefs,47.125,-122.25));
      const auto image=store.fs.files["/new_prefs"];
      assert(image.size()==legacy.size()+FooterSize);
      assert(std::equal(legacy.begin(),legacy.end(),image.begin()));
      CompanionNodePrefs loaded;double lat=0,lon=0;
      assert(store.loadPrefsInt("/new_prefs",loaded,lat,lon));
      assert(loaded.cpu_freq_mhz==value&&loaded.freq==prefs.freq&&loaded.ble_pin==123456);
      assert(loaded.tz_offset==timezone&&loaded.tx_media==mesh::RADIO_TX_SECONDARY);
      assert(lat==47.125&&lon==-122.25);
      CompanionNodePrefs snapshot;
      assert(snapshot.copyPersistedValuesFrom(loaded)&&snapshot.cpu_freq_mhz==value);
      // Every CPU-footer bit fault falls back to Auto without discarding the
      // independently checked radio, timezone, traffic, identity and location.
      for(size_t n=0;n<FooterSize;++n) for(unsigned bit=0;bit<8;++bit) {
        DataStore bad;auto corrupt=image;corrupt[legacy.size()+n]^=uint8_t(1u<<bit);
        bad.fs.files["/new_prefs"]=corrupt;loaded.cpu_freq_mhz=240;
        assert(bad.loadPrefsInt("/new_prefs",loaded,lat,lon));
        assert(loaded.cpu_freq_mhz==0&&loaded.freq==prefs.freq&&loaded.tz_offset==timezone);
        assert(loaded.ble_pin==123456&&loaded.tx_media==mesh::RADIO_TX_SECONDARY);
      }
      // Unsupported selections still preserve the independently checked
      // timezone/radio data when their CPU record has a valid header and CRC.
      {
        DataStore bad;auto unsupported=image;
        uint8_t* footer=unsupported.data()+legacy.size();
        footer[4]=0;footer[5]=200;
        const uint16_t crc=footerChecksum(footer,6);
        footer[6]=uint8_t(crc>>8);footer[7]=uint8_t(crc);
        bad.fs.files["/new_prefs"]=unsupported;loaded.cpu_freq_mhz=240;
        assert(bad.loadPrefsInt("/new_prefs",loaded,lat,lon));
        assert(loaded.cpu_freq_mhz==0&&loaded.freq==prefs.freq&&loaded.tz_offset==timezone);
        assert(loaded.ble_pin==123456&&loaded.tx_media==mesh::RADIO_TX_SECONDARY);
      }
      for(size_t quota=0;quota<image.size();++quota) {
        DataStore failed;failed.fs.files["/new_prefs"]=legacy;
        failed.fs.fail_write_after=int(quota);
        assert(!failed.savePrefs(prefs,47.125,-122.25));
        assert(failed.fs.files["/new_prefs"]==legacy);
      }
      // Old images explicitly clear a prior in-memory fixed selection.
      DataStore old;old.fs.files["/new_prefs"]=legacy;loaded.cpu_freq_mhz=value;
      assert(old.loadPrefsInt("/new_prefs",loaded,lat,lon));assert(loaded.cpu_freq_mhz==0);
      // Returning to Auto removes only the appended override.
      prefs.cpu_freq_mhz=0;assert(store.savePrefs(prefs,47.125,-122.25));
      assert(store.fs.files["/new_prefs"]==legacy);
    }
  }
}
'''


class CpuClockTests(unittest.TestCase):
    def compile_run(self, code, *, persistence=False):
        with tempfile.TemporaryDirectory(prefix="meshcore-cpu-clock-") as directory:
            work = Path(directory)
            (work / "Utils.h").write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''')
            (work / "shim.h").write_text('''#include <cstdlib>
#include <cstdio>
inline char* utoa(unsigned value,char* out,int base){
 if(base!=10)abort();sprintf(out,"%u",value);return out;
}
''')
            transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text()
            (work / "ContactFileTransaction.h").write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            cpp, binary = work / "test.cpp", work / "test"
            cpp.write_text(code)
            command = [os.environ.get("CXX", "g++"), "-std=c++17", "-DESP32_PLATFORM=1",
                       "-DESP32_CPU_FREQ=160", "-fsanitize=address,undefined",
                       "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                       "-include", str(work / "shim.h"), "-I", str(work),
                       "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                       "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                       "-I", str(ROOT / "src/helpers"), "-I", str(ROOT), str(cpp)]
            if persistence:
                command += [str(ROOT / "src/helpers" / name) for name in (
                    "ConfigSerializer.cpp", "DynamicConfigSerializer.cpp",
                    "CommonRadioPrefs.cpp", "radiolib/RXPowerSaving.cpp",
                    "radiolib/RXPowerSavingCLI.cpp", "TxtDataHelpers.cpp")]
            built = subprocess.run(command + ["-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_policy_cli_transactions_and_checked_footer(self):
        self.compile_run(POLICY)

    def test_actual_companion_codec_roundtrip_legacy_and_write_faults(self):
        source = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        methods = esp_recovery_helpers(source) + "\n".join(
            method(source, signature) for signature in (
                "bool DataStore::loadPrefs(", "bool DataStore::loadPrefsInt(",
                "bool DataStore::savePrefs("))
        prefix = HARNESS.split("int main(")[0].replace("@METHODS@", methods)
        self.compile_run(prefix + PERSISTENCE, persistence=True)

    def test_actual_common_writers_match_and_footer_is_append_only(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        atomic = "template <typename Writer>\n" + method(source, "static bool writeCommonPrefsImage(")
        retry = "template <typename Writer>\n" + method(source, "static void writeCommonRetryPrefs(")
        save = method(source, "void CommonCLI::savePrefs(FILESYSTEM*")
        scalar = save[save.index("    uint8_t pad[8];"):
                      save.index("    _common_save_succeeded = file.commit();")]
        fields_used = set(re.findall(r"(?:_prefs|prefs)->(\w+)", atomic + retry + scalar))
        header = (ROOT / "src/helpers/CommonCLI.h").read_text()
        prefix = header.split("class NodePrefs :", 1)[1].split("private:", 1)[0]
        fields = []
        for kind, declaration in re.findall(
                r"^\s*(float|double|char|u?int(?:8|16|32)_t)\s+([^;\n]+);", prefix, re.M):
            declaration = re.sub(r"\s*=\s*[^,;]+", "", declaration)
            names = re.findall(r"\b[A-Za-z_]\w*\b", declaration)
            if fields_used.intersection(names):
                fields.append(kind + " " + declaration + "{};")
        # Field-array dimensions are the existing shipping limits. Both real
        # emitters must retain every historical byte and append only CPUv1.
        constants = "\n".join(re.findall(
            r"^\s*#define FLOOD_RETRY_(?:PREFIX_SLOTS|PREFIX_LEN|BRIDGE_BUCKETS|BUCKET_PREFIXES|IGNORE_PREFIXES)\s+\d+",
            header, re.M))
        constants += "\n" + "\n".join(re.findall(
            r"^#define DIRECT_RETRY_PREFS_MAGIC_[01]\s+0x[0-9A-Fa-f]+", header, re.M))
        marker = method(source, "static void markDirectRetryPrefsValid(")
        harness = r'''
#include <cassert>
#include <algorithm>
#include <cstring>
#include <vector>
#include <helpers/esp32/CpuClockPolicy.h>
#include <helpers/bridges/RS232PowerSettings.h>
#include <helpers/UnscopedFloodLimits.h>
@CONSTANTS@
struct NodePrefs { @FIELDS@ };
@MARKER@
struct File {
 std::vector<uint8_t> bytes;size_t cursor=0;bool short_write=false;
 size_t write(const uint8_t* data,size_t size){
   if(short_write&&size)--size;
   bytes.insert(bytes.end(),data,data+size);return size;
 }
 size_t size() const{return bytes.size();}
 bool seek(size_t value){if(value>size())return false;cursor=value;return true;}
 size_t read(uint8_t* out,size_t count){
   count=std::min(count,size()-cursor);memcpy(out,bytes.data()+cursor,count);cursor+=count;return count;
 }
};
@ATOMIC@
@RETRY@
void non_mqtt(File& file,NodePrefs* _prefs){ @SCALAR@ }
int main(){
 using namespace mesh::esp32::cpuclock;
 NodePrefs prefs{};prefs.freq=910.525f;prefs.bw=62.5f;prefs.sf=7;prefs.cr=5;
 prefs.discovery_responses=3;
 prefs.powersaving_enabled=1;strcpy(prefs.node_name,"unchanged prefix");
 File legacy;assert(writeCommonPrefsImage(legacy,&prefs));
 File nonlegacy;non_mqtt(nonlegacy,&prefs);assert(nonlegacy.bytes==legacy.bytes);
 assert(readFooter(legacy)==0);
 for(uint16_t mhz : {80,160,240}){
   prefs.cpu_freq_mhz=mhz;File a,b;
   assert(writeCommonPrefsImage(a,&prefs));non_mqtt(b,&prefs);
   assert(a.bytes==b.bytes&&a.size()==legacy.size()+FooterSize);
   assert(std::equal(legacy.bytes.begin(),legacy.bytes.end(),a.bytes.begin()));
   assert(readFooter(a)==mhz);
   for(size_t n=0;n<FooterSize;++n){
     a.bytes[legacy.size()+n]^=0x80;assert(readFooter(a)==0);
     a.bytes[legacy.size()+n]^=0x80;
   }
   File short_write;short_write.short_write=true;
   assert(!writeCommonPrefsImage(short_write,&prefs));
 }
 prefs.cpu_freq_mhz=0;File restored;non_mqtt(restored,&prefs);
 assert(restored.bytes==legacy.bytes&&readFooter(restored)==0);
}
'''
        for marker, value in {"CONSTANTS": constants, "FIELDS": "\n".join(fields),
                              "ATOMIC": atomic, "RETRY": retry, "SCALAR": scalar,
                              "MARKER": marker}.items():
            harness = harness.replace("@" + marker + "@", value)
        self.compile_run(harness)
        self.compile_run("#define WITH_RS232_BRIDGE 1\n" + harness)


if __name__ == "__main__":
    unittest.main()
