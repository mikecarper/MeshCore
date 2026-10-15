"""Execute RAK4631 append-only policy images, CLI rollback and boot ordering."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_common_usb_debug import HARNESS as COMMON_HARNESS
from test_companion_preferences_transaction import HARNESS as COMPANION_HARNESS
from test_radio_receive_contract import method
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def compile_run(code, macros=(), *, serializers=False):
    with tempfile.TemporaryDirectory(prefix="meshcore-peripheral-mode-") as directory:
        work = Path(directory)
        (work / "helpers").mkdir()
        fs = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
        fs = fs.replace("  size_t available() const", "  bool seek(size_t p) { if (p > size()) return false; cursor_ = p; return true; }\n  size_t available() const")
        (work / "helpers/IdentityStore.h").write_text(fs)
        (work / "Utils.h").write_text("""#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
""")
        (work / "platform_shim.h").write_text("""#include <cstdlib>
#include <cstdio>
inline char* utoa(unsigned int v,char* p,int b){if(b!=10)abort();sprintf(p,"%u",v);return p;}
""")
        transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text()
        (work / "ContactFileTransaction.h").write_text(transaction.replace(
            '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
        cpp, binary = work / "test.cpp", work / "test"
        cpp.write_text(code)
        flags = [] if os.name == "nt" else ["-fsanitize=address,undefined",
            "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        command = ["g++", "-std=c++17", *flags, *["-D" + x + "=1" for x in macros],
            "-include", str(work / "platform_shim.h"), "-I" + str(work),
            "-I" + str(ROOT / "test/fixtures/radio_profiles/mocks"),
            "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
            "-I" + str(ROOT / "src/helpers"), "-I" + str(ROOT), str(cpp)]
        if serializers:
            command += [str(ROOT / "src/helpers/" / name) for name in (
                "ConfigSerializer.cpp", "DynamicConfigSerializer.cpp", "CommonRadioPrefs.cpp",
                "TxtDataHelpers.cpp")]
        build = subprocess.run(command + ["-o", str(binary)], capture_output=True, text=True, timeout=90)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)


def common_code():
    source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
    header = (ROOT / "src/helpers/CommonCLI.h").read_text()
    fields = header.split("class NodePrefs :", 1)[1].split("private:", 1)[0]
    fields = "\n".join(re.findall(r"^\s*(?:float|double|char|u?int(?:8|16|32)_t)\s+[^;]+;", fields, re.M))
    constants = "\n".join(re.findall(r"^\s*#define\s+\w+\s+(?:0x[\dA-Fa-f]+|\d+)\s*$", header, re.M))
    constants += "\n" + "\n".join(re.findall(r"^static const size_t LEGACY_\w+\s*=[^;]+;", source, re.M))
    constants += "\n" + re.search(r"^static constexpr uint8_t DEFAULT_ADVERT_LOC_POLICY\s*=[^;]+;", header, re.M).group()
    helpers = "\n".join(extract_braced(source, signature) for signature in (
        "static bool bwMatches(", "static bool isValidLoRaBandwidth(",
        "static float defaultLoRaBandwidth(", "static void markDirectRetryPrefsValid(",
        "static void applyFloodRetryPreset(", "static void applyDirectRetryPreset(",
        "static void setDefaultDirectRetryPrefs(", "static bool directRetryPrefsValid("))
    methods = "\n".join(extract_braced(source, signature) for signature in (
        "static const char* commonPrefsSaveResultName(", "bool CommonCLI::recoverCommonPrefsFiles(",
        "bool CommonCLI::saveCommonPrefsImageAtomically(", "void CommonCLI::savePrefs(FILESYSTEM*",
        "void CommonCLI::savePrefs(PrefsSaveRouting::Scope scope)", "bool CommonCLI::trySavePrefs(",
        "void CommonCLI::loadPrefsInt("))
    code = COMMON_HARNESS.split("int main(")[0]
    for name, value in {
        "FIELDS": fields, "CONSTANTS": constants, "HELPERS": helpers,
        "LEGACY": extract_braced(header, "struct LegacyObserverTail") + ";",
        "SERIALIZER": "template<typename Writer>\n" + extract_braced(source, "static bool writeCommonPrefsImage("),
        "STORE": extract_braced(source, "class CommonPrefsFileStore"), "METHODS": methods,
        "DYNAMIC": extract_braced(header, "class BridgePrefs :"),
        "SET_DEBUG": "", "SET_MASTER": "", "GET_DEBUG": "", "GET_MASTER": "",
    }.items():
        code = code.replace("@" + name + "@", value)
    return "#include <helpers/PeripheralModePrefs.h>\n#include <helpers/bridges/RS232PowerSettings.h>\n" + code


class PeripheralModePrefsTests(unittest.TestCase):
    def test_actual_cli_wrappers_use_saved_intent_without_hot_gpio(self):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        snippets = []
        for source, signature in (
            (common, "void CommonCLI::handleSetCmd("),
            (common, "void CommonCLI::handleGetCmd("),
            (companion, "bool MyMesh::handleLocalControlCommand(")):
            snippet = method(source, signature).split("#if defined(RAK_4631)", 1)[1].split("#else", 1)[0]
            self.assertIn("mesh::peripherals::handleCommand", snippet)
            snippets.append(snippet)
        code = r'''
#include <helpers/PeripheralModePrefs.h>
#include <cassert>
struct Prefs{uint8_t peripherals_shared=0;};
struct Sensors{bool supported=true;unsigned applications=0;
 bool supportsSharedPeripheralMode()const{return supported;}
 void configureSharedPeripheralMode(bool){++applications;}
};
struct Common{
 Prefs prefs,durable;Prefs* _prefs=&prefs;Sensors sensor;Sensors* _sensors=&sensor;
 bool ok=true;unsigned saves=0;
 bool trySavePrefs(){++saves;if(ok)durable=prefs;return ok;}
 void set(const char* command,char* reply){@SET@}
 void get(const char* command,char* reply){@GET@}
};
struct Companion{
 Prefs _prefs,durable;Sensors sensors;bool ok=true;unsigned saves=0;
 bool savePrefs(){++saves;if(ok)durable=_prefs;return ok;}
 bool command(const char* command,char* reply,size_t reply_size){@COMPANION@ return false;}
};
int main(){Common common;Companion companion;char reply[160];
 common.set("set peripherals.mode shared",reply);
 assert(common.prefs.peripherals_shared==1&&common.durable.peripherals_shared==1&&strstr(reply,"reboot"));
 assert(companion.command("set peripherals.mode shared",reply,sizeof(reply)));
 assert(companion._prefs.peripherals_shared==1&&companion.durable.peripherals_shared==1&&strstr(reply,"reboot"));
 assert(common.sensor.applications==0&&companion.sensors.applications==0);
 common.ok=companion.ok=false;
 common.set("set peripherals.mode gps-only",reply);assert(strstr(reply,"could not be saved"));
 assert(companion.command("set peripherals.mode gps-only",reply,sizeof(reply))&&strstr(reply,"could not be saved"));
 assert(common.prefs.peripherals_shared==1&&companion._prefs.peripherals_shared==1);
 common.get("get peripherals.mode",reply);assert(strstr(reply,"shared"));
 assert(companion.command("get peripherals.mode",reply,sizeof(reply))&&strstr(reply,"shared"));
 unsigned cs=common.saves,ps=companion.saves;
 common.sensor.supported=companion.sensors.supported=false;
 common.set("set peripherals.mode gps-only",reply);assert(strstr(reply,"unsupported"));
 assert(companion.command("set peripherals.mode gps-only",reply,sizeof(reply))&&strstr(reply,"unsupported"));
 assert(common.saves==cs&&companion.saves==ps&&common.sensor.applications==0&&companion.sensors.applications==0);
}
'''
        for key, snippet in zip(("SET", "GET", "COMPANION"), snippets):
            code = code.replace("@" + key + "@", snippet)
        compile_run(code)

    def test_codec_and_strict_cli_transactions(self):
        compile_run(r'''
#include <helpers/PeripheralModePrefs.h>
#include <cassert>
#include <initializer_list>
int main(){
 uint8_t mode=0, durable=0;unsigned saves=0;bool ok=true;char reply[160];
 auto save=[&](){++saves;if(ok)durable=mode;return ok;};
 assert(mesh::peripherals::handleCommand(mode,"get peripherals.mode",reply,sizeof(reply),true,save));
 assert(strstr(reply,"gps-only")&&saves==0);
 for(const char* text:{"shared","gps-only"}){
  char cmd[80];snprintf(cmd,sizeof(cmd),"set peripherals.mode %s",text);
  assert(mesh::peripherals::handleCommand(mode,cmd,reply,sizeof(reply),true,save));
  assert(mode==(strcmp(text,"shared")==0)&&durable==mode&&strstr(reply,"reboot"));
 }
 for(const char* text:{"", "on", "1", "SHARED", "shared junk", "shared ", "gps-only\n"}){
  unsigned before=saves;char cmd[80];snprintf(cmd,sizeof(cmd),"set peripherals.mode %s",text);
  assert(mesh::peripherals::handleCommand(mode,cmd,reply,sizeof(reply),true,save));
  assert(strstr(reply,"Error")&&saves==before&&mode==durable);
 }
 ok=false;assert(mesh::peripherals::handleCommand(mode,"set peripherals.mode shared",reply,sizeof(reply),true,save));
 assert(mode==0&&durable==0&&strstr(reply,"could not be saved"));
 unsigned before=saves;
 assert(mesh::peripherals::handleCommand(mode,"set peripherals.mode shared",reply,sizeof(reply),false,save));
 assert(strstr(reply,"unsupported")&&saves==before&&mode==0);
 assert(!mesh::peripherals::handleCommand(mode,"set peripherals.modeX shared",reply,sizeof(reply),true,save));
 for(uint8_t value:{0,1}){
  uint8_t footer[8];mesh::peripherals::encodeFooter(footer,value);uint8_t decoded=9;
  assert(mesh::peripherals::decodeFooter(footer,sizeof(footer),decoded)&&decoded==value);
  for(unsigned i=0;i<8;++i){footer[i]^=1;decoded=9;
   assert(!mesh::peripherals::decodeFooter(footer,sizeof(footer),decoded)&&decoded==9);footer[i]^=1;}
  for(unsigned n=0;n<8;++n)assert(!mesh::peripherals::decodeFooter(footer,n,decoded));
 }
}
''')

    def test_actual_common_images_and_footer_fail_closed(self):
        code = common_code() + r'''
int main(){
 CommonCLI writer;writer.prefs.peripherals_shared=1;strcpy(writer.prefs.password,"keep secret");
 strcpy(writer.prefs.node_name,"keep node");writer.savePrefs(&writer.fs,PrefsSaveRouting::Scope::Common);
 assert(writer._common_save_succeeded);const auto disk=writer.fs.files["/com_prefs"];
 const size_t legacy=mesh::peripherals::CommonLegacySize+
#ifdef WITH_RS232_BRIDGE
 mesh::peripherals::LegacyRs232TailSize;
#else
 0;
#endif
 assert(disk.size()==legacy+8);Capture capture;
 assert(writeCommonPrefsImage(capture,&writer.prefs)&&capture.bytes==disk);
 for(uint8_t mode:{0,1}){
  writer.prefs.peripherals_shared=mode;writer.savePrefs(&writer.fs,PrefsSaveRouting::Scope::Common);
  CommonCLI reader;reader.fs.files=writer.fs.files;reader.prefs.peripherals_shared=9;
  reader.loadPrefsInt(&reader.fs,"/com_prefs");assert(reader.prefs.peripherals_shared==mode);
  assert(!strcmp(reader.prefs.password,"keep secret")&&!strcmp(reader.prefs.node_name,"keep node"));
 }
 for(size_t keep=legacy;keep<disk.size();++keep){
  CommonCLI reader;reader.fs.files["/com_prefs"]={disk.begin(),disk.begin()+keep};
  reader.loadPrefsInt(&reader.fs,"/com_prefs");
  assert(reader.prefs.peripherals_shared==(keep==legacy?0:1));
 }
 for(size_t byte=legacy;byte<disk.size();++byte){
  CommonCLI reader;reader.fs.files["/com_prefs"]=disk;reader.fs.files["/com_prefs"][byte]^=1;
  reader.loadPrefsInt(&reader.fs,"/com_prefs");assert(reader.prefs.peripherals_shared==1);
 }
 // The optional UART tail belongs to the source profile, not the reader.
 // Both no-UART -> UART and UART -> no-UART upgrades retain the checked mode.
 for(bool uart:{false,true}){
  std::vector<uint8_t> foreign(disk.begin(),disk.begin()+mesh::peripherals::CommonLegacySize);
  if(uart){uint8_t tail[mesh::bridge::rs232power::PREFS_SIZE];
   mesh::bridge::rs232power::encodePrefs(tail,1,1,300);foreign.insert(foreign.end(),tail,tail+sizeof(tail));}
  CommonCLI old;old.fs.files["/com_prefs"]=foreign;old.loadPrefsInt(&old.fs,"/com_prefs");
  assert(old.prefs.peripherals_shared==0);
  uint8_t footer[8];mesh::peripherals::encodeFooter(footer,1);foreign.insert(foreign.end(),footer,footer+8);
  CommonCLI migrated;migrated.fs.files["/com_prefs"]=foreign;migrated.loadPrefsInt(&migrated.fs,"/com_prefs");
  assert(migrated.prefs.peripherals_shared==1);
 }
 CommonCLI unreadable;unreadable.fs.files["/com_prefs"]=disk;unreadable.fs.fail_read_open=true;
 unreadable.loadPrefsInt(&unreadable.fs,"/com_prefs");assert(unreadable.prefs.peripherals_shared==1);
 CommonCLI failed;failed.fs.files["/com_prefs"]=disk;failed.fs.fail_write_after=int(legacy+4);
 failed.prefs.peripherals_shared=0;failed.savePrefs(&failed.fs,PrefsSaveRouting::Scope::Common);
 assert(!failed._common_save_succeeded&&failed.fs.files["/com_prefs"]==disk);
}
'''
        for flags in (("NRF52_PLATFORM", "RAK_4631", "ENABLE_OTA"),
                      ("NRF52_PLATFORM", "RAK_4631", "ENABLE_OTA", "WITH_MQTT_BRIDGE"),
                      ("NRF52_PLATFORM", "RAK_4631", "ENABLE_OTA", "WITH_RS232_BRIDGE")):
            with self.subTest(flags=flags):
                # Only the profile's compile-time UART constant matters here.
                compile_run("#define WITH_RS232_BRIDGE_UART 2\n" + code, flags)

    def test_actual_companion_images_and_adapter_snapshot(self):
        source = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        methods = "\n".join(method(source, signature) for signature in (
            "bool DataStore::loadPrefs(", "bool DataStore::loadPrefsInt(", "bool DataStore::savePrefs("))
        code = "#include <helpers/PeripheralModePrefs.h>\n" + COMPANION_HARNESS.split("int main(")[0].replace("@METHODS@", methods)
        code += r'''
int main(){
 DataStore store;CompanionNodePrefs prefs;prefs.peripherals_shared=1;
 strcpy(prefs.node_name,"retained name");prefs.ble_pin=654321;prefs.gps_interval=42;
 assert(prefs.getCustom()->setByKey("snapshot_tag","original"));
 assert(store.savePrefs(prefs,47.2,-122.1));const auto disk=store.fs.files["/new_prefs"];
 assert(disk.size()==252);double lat=0,lon=0;
 CompanionNodePrefs loaded;assert(loaded.getCustom()->setByKey("snapshot_tag","loaded"));
 assert(store.loadPrefsInt("/new_prefs",loaded,lat,lon));
 assert(loaded.peripherals_shared==1&&loaded.ble_pin==654321&&loaded.gps_interval==42);
 assert(!strcmp(loaded.node_name,"retained name")&&lat==47.2&&lon==-122.1);
 char tag[32];assert(loaded.getCustom()->getByKey("snapshot_tag",tag,sizeof(tag))&&!strcmp(tag,"loaded"));
 loaded.getRadioPrefs()->setFreq(919.5f);assert(loaded.freq==919.5f&&prefs.freq!=919.5f);
 for(size_t keep=236;keep<244;++keep){
  DataStore damaged;damaged.fs.files["/new_prefs"]={disk.begin(),disk.begin()+keep};
  CompanionNodePrefs candidate;candidate.peripherals_shared=1;candidate.ble_pin=123456;
  const bool ok=damaged.loadPrefsInt("/new_prefs",candidate,lat,lon);
  assert(ok==(keep==236));if(ok)assert(candidate.peripherals_shared==0);
  else assert(candidate.peripherals_shared==1&&candidate.ble_pin==123456);
 }
 // The previous complete RAK PMO footer remains an accepted legacy image.
 DataStore old;old.fs.files["/new_prefs"]={disk.begin(),disk.begin()+244};
 CompanionNodePrefs old_prefs;old_prefs.tx_media=mesh::RADIO_TX_OFF;
 assert(old.loadPrefsInt("/new_prefs",old_prefs,lat,lon));
 assert(old_prefs.peripherals_shared==1&&old_prefs.tx_media==mesh::RADIO_TX_AUTO);
 for(size_t byte=236;byte<244;++byte){
  DataStore damaged;damaged.fs.files["/new_prefs"]=disk;damaged.fs.files["/new_prefs"][byte]^=1;
  CompanionNodePrefs candidate;candidate.peripherals_shared=1;candidate.ble_pin=123456;
  assert(!damaged.loadPrefsInt("/new_prefs",candidate,lat,lon));
  assert(candidate.peripherals_shared==1&&candidate.ble_pin==123456);
  // A separately chosen, fully validated image remains usable. This does
  // not change nRF52's existing primary-file authority/fallback policy.
  damaged.fs.files["/known_good"]=disk;
  assert(damaged.loadPrefsInt("/known_good",candidate,lat,lon)&&candidate.peripherals_shared==1);
 }
 DataStore interrupted;interrupted.fs.files["/new_prefs"]=disk;
 interrupted.fs.files["/new_prefs.tmp"]={disk.begin(),disk.begin()+240};
 CompanionNodePrefs rebooted;assert(interrupted.loadPrefs(rebooted,lat,lon)&&rebooted.peripherals_shared==1);
 DataStore fail;fail.fs.files["/new_prefs"]=disk;fail.fs.fail_write_after=240;
 prefs.peripherals_shared=0;assert(!fail.savePrefs(prefs,1,2)&&fail.fs.files["/new_prefs"]==disk);
 DataStore fresh;CompanionNodePrefs defaults;
 assert(fresh.loadPrefs(defaults,lat,lon)&&defaults.peripherals_shared==0);
}
'''
        compile_run(code, ("NRF52_PLATFORM", "RAK_4631"), serializers=True)

    def test_production_wiring_is_boot_only_and_before_gps_intent(self):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        load = method(common, "void CommonCLI::loadPrefs(FILESYSTEM*")
        begin = method(companion, "void MyMesh::begin(")
        self.assertLess(load.index("loadPrefsInt("), load.index("configureSharedPeripheralMode("))
        self.assertLess(begin.index("_store->loadPrefs("), begin.index("configureSharedPeripheralMode("))
        self.assertIn("if (!prefs_ready) _prefs.peripherals_shared = 1;", begin)
        self.assertNotIn("configureSharedPeripheralMode", method(companion, "bool MyMesh::handleLocalControlCommand("))
        self.assertNotIn("configureSharedPeripheralMode", method(common, "void CommonCLI::handleSetCmd("))
        for path in ("examples/simple_repeater/MyMesh.cpp", "examples/simple_room_server/MyMesh.cpp",
                     "examples/simple_sensor/SensorMesh.cpp"):
            source = (ROOT / path).read_text()
            self.assertLess(source.index("_cli.loadPrefs(_fs)"), source.index("applyGpsPrefs()"))
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        self.assertLess(main.index("the_mesh.begin("), main.index("the_mesh.applyGpsPrefs()"))


if __name__ == "__main__":
    unittest.main()
