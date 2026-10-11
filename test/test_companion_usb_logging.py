"""Execute Companion debug persistence/CLI and bounded packet logging paths."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS as STORE_HARNESS
from test_companion_preferences_transaction import esp_recovery_helpers
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

PREFERENCE_HARNESS = r'''
struct ConfigText : Stream {
 std::string text;size_t offset=0;
 int available() override{return int(text.size()-offset);}
 int read() override{return offset<text.size()?uint8_t(text[offset++]):-1;}
 int peek() override{return offset<text.size()?uint8_t(text[offset]):-1;}
 size_t write(uint8_t value) override{text.push_back(char(value));return 1;}
 size_t write(const uint8_t* value,size_t size) override{
   text.append(reinterpret_cast<const char*>(value),size);return size;
 }
 size_t print(unsigned int value,int base) override{
   assert(base==10);const auto number=std::to_string(value);
   return write(reinterpret_cast<const uint8_t*>(number.data()),number.size());
 }
};
namespace mesh {
bool logging_enabled=true, debug_enabled=false;
unsigned debug_applies=0;
bool isUsbLoggingEnabled(){return logging_enabled;}
bool isUsbDebugEnabled(){return debug_enabled;}
void setUsbDebugEnabled(bool enabled){debug_enabled=enabled;++debug_applies;}
}
struct {double node_lat=47.1,node_lon=-122.2;} sensors;
struct MyMesh {
 CompanionNodePrefs _prefs;
 DataStore store;
 DataStore* _store=&store;
 @SAVE_PREFS@
 @SAVE_PREFERENCE@
 bool command(const char* command,char* reply,size_t reply_size){
   @DEBUG_COMMANDS@
   return false;
 }
};
int main(){
 MyMesh node;
 assert(node._prefs.usb_debug_enabled==0);
 strcpy(node._prefs.node_name,"durable node");node._prefs.freq=910.525f;
 node._prefs.ble_pin=876543;node._prefs.bluetooth_enabled=0;
 node._prefs.one_key_dm_enabled=1;node._prefs.usb_logging_enabled=1;
 node._prefs.gps_sync_interval_hours=257;
 assert(node.savePrefs());
 const auto original=node.store.fs.files["/new_prefs"];
#if defined(TBEAM_1W)
 assert(original.size()==243);
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
 assert(original.size()==333);
#else
 assert(original.size()==236);
#endif
 const size_t debug_offset=original.size()-2;
 assert(original[debug_offset]==0&&original[debug_offset-3]==0);
 assert(original[debug_offset-2]==1&&original[debug_offset-1]==1);
 assert(original[debug_offset-4]==1&&original[158]==1);
 assert(original.back()==0);
 char reply[160]={};
 for(const char* malformed : {"set usb.debug","set usb.debug ",
     "set usb.debug 1","set usb.debug ON","set usb.debug on ",
     "set usb.debug on reboot","set usb.debug off extra", "set usb.debug off\n"}){
   const auto before=node.store.fs.files["/new_prefs"];
   const unsigned applied=mesh::debug_applies;
   assert(node.command(malformed,reply,sizeof(reply)));
   assert(!strcmp(reply,"Error: use set usb.debug <on|off>"));
   assert(node._prefs.usb_debug_enabled==0&&!mesh::debug_enabled);
   assert(mesh::debug_applies==applied&&node.store.fs.files["/new_prefs"]==before);
 }
 for(const char* unrelated : {"set usb.debugger on","get usb.debugx","get usb.debug on"})
   assert(!node.command(unrelated,reply,sizeof(reply)));
 assert(node.command("set usb.debug\t on",reply,sizeof(reply)));
 assert(!strncmp(reply,"OK - USB debug on (saved)",24));
 assert(node._prefs.usb_debug_enabled==1&&mesh::debug_enabled);
 const auto enabled_disk=node.store.fs.files["/new_prefs"];
 assert(enabled_disk[debug_offset]==1);
 for(size_t i=0;i<original.size();++i)
   if(i!=debug_offset)assert(enabled_disk[i]==original[i]);
 assert(node.command("get usb.debug",reply,sizeof(reply)));
#if MESH_DEBUG
 assert(!strcmp(reply,"usb.debug on"));
#else
 assert(!strcmp(reply,"usb.debug on (core debug not compiled)"));
#endif
 mesh::logging_enabled=false;
 assert(node.command("get usb.debug",reply,sizeof(reply)));
#if MESH_DEBUG
 assert(!strcmp(reply,"usb.debug on (USB logging off)"));
#endif
 // Faults at early and newly appended bytes both preserve the committed image
 // and requested runtime state. A subsequent successful save applies once.
 for(int fail_after : {17,int(enabled_disk.size()-1)}){
   node.store.fs.fail_write_after=fail_after;
   const unsigned applied=mesh::debug_applies;
   assert(node.command("set usb.debug off",reply,sizeof(reply)));
   assert(!strcmp(reply,"Error: USB debug setting could not be saved"));
   assert(node._prefs.usb_debug_enabled==1&&mesh::debug_enabled);
   assert(mesh::debug_applies==applied);
   assert(node.store.fs.files["/new_prefs"]==enabled_disk);
 }
 node.store.fs.fail_write_after=-1;
 assert(node.command("set usb.debug off",reply,sizeof(reply)));
 assert(node._prefs.usb_debug_enabled==0&&!mesh::debug_enabled);
 for(uint8_t enabled : {0,1}){
   node._prefs.usb_debug_enabled=enabled;assert(node.savePrefs());
   assert(mesh::debug_enabled==(enabled!=0));
   DataStore reboot;reboot.fs=node.store.fs;
   CompanionNodePrefs loaded;loaded.usb_debug_enabled=1-enabled;
   double lat=0,lon=0;assert(reboot.loadPrefs(loaded,lat,lon));
   assert(loaded.usb_debug_enabled==enabled&&loaded.bluetooth_enabled==0);
   assert(loaded.one_key_dm_enabled==1&&loaded.usb_logging_enabled==1);
   assert(loaded.gps_sync_interval_hours==257);
   assert(loaded.ble_pin==876543&&loaded.freq==910.525f);
 }
 // Existing complete tails retain their established offsets and always start
 // quiet, even if the object was already using verbose runtime diagnostics.
 for(unsigned size : {84u,156u,215u,226u,unsigned(debug_offset)}){
   DataStore legacy;legacy.fs.files["/new_prefs"]=enabled_disk;
   legacy.fs.files["/new_prefs"].resize(size);
   CompanionNodePrefs loaded;loaded.usb_debug_enabled=1;
   double lat=0,lon=0;assert(legacy.loadPrefs(loaded,lat,lon));
   assert(loaded.usb_debug_enabled==0&&loaded.ble_pin==876543);
   assert(legacy.fs.files["/new_prefs"].size()==size);
 }
 // Normalize the new boolean only after the entire persisted image validates.
 for(uint8_t byte : {2,255}){
   DataStore corrupt;corrupt.fs.files["/new_prefs"]=enabled_disk;
   corrupt.fs.files["/new_prefs"][debug_offset]=byte;
   CompanionNodePrefs loaded;double lat=0,lon=0;
   assert(corrupt.loadPrefsInt("/new_prefs",loaded,lat,lon));
   assert(loaded.usb_debug_enabled==0);
 }
 DataStore extra;extra.fs.files["/new_prefs"]=enabled_disk;
 extra.fs.files["/new_prefs"].push_back(0);
 CompanionNodePrefs unchanged;unchanged.usb_debug_enabled=0;
 double lat=1,lon=2;assert(!extra.loadPrefsInt("/new_prefs",unchanged,lat,lon));
 assert(unchanged.usb_debug_enabled==0&&lat==1&&lon==2);
 // Numeric dynamic configuration uses the actual preference, not a shadow
 // string in the custom dictionary, and applies through the same save path.
 char dynamic[8]={};
 assert(node._prefs.getCustom()->setByKey("usb_debug","1"));
 assert(node._prefs.usb_debug_enabled==1&&node._prefs.isDirty());
 assert(node._prefs.getCustom()->getByKey("usb_debug",dynamic,sizeof(dynamic)));
 assert(!strcmp(dynamic,"1"));
 assert(node.savePrefs()&&mesh::debug_enabled&&!node._prefs.isDirty());
 assert(node._prefs.getCustom()->setByKey("usb_debug","0"));
 assert(node.savePrefs()&&!mesh::debug_enabled);
 // The radio serializer exports the preference; custom JSON imports delegate
 // to the same field rather than creating an unrelated string preference.
 ConfigText imported;imported.text="{usb_debug:1}";
 assert(node._prefs.getCustom()->getByKey("usb_debug",dynamic,sizeof(dynamic)));
 auto* serializer=static_cast<DynamicConfigSerializer*>(node._prefs.getCustom());
 assert(serializer->loadSerial(imported)&&node._prefs.usb_debug_enabled==1);
 assert(node.savePrefs()&&mesh::debug_enabled);
 ConfigText exported;assert(node._prefs.getRadioPrefs()->saveSerial(exported));
 assert(exported.text.find("usb_debug:1")!=std::string::npos);
 // Only an explicit 1 enables verbosity, including when a dynamic schema has
 // assigned an out-of-range byte. Failed commits still leave runtime alone.
 node._prefs.usb_debug_enabled=255;assert(node.savePrefs());
 assert(node._prefs.usb_debug_enabled==0&&!mesh::debug_enabled);
 node._prefs.usb_debug_enabled=255;node.store.fs.fail_write=true;
 assert(!node.savePrefs()&&node._prefs.usb_debug_enabled==255&&!mesh::debug_enabled);
 node.store.fs.fail_write=false;node._prefs.usb_debug_enabled=0;
 // Bounded replies do not write beyond their supplied capacity.
 char small[9];memset(small,'X',sizeof(small));
 assert(node.command("get usb.debug",small,8));assert(small[7]==0&&small[8]=='X');
}
'''

PACKET_HARNESS = r'''
#include <cassert>
#include <cstring>
#include <string>
#include <vector>
#include <ctime>
#include <helpers/SerialPacketLog.h>
#include <helpers/BaseSerialInterface.h>
std::vector<std::string> events;
struct Port : Stream {
 std::string output;
 bool stalled=false;
 int availableForWrite() override{return stalled?0:4096;}
 size_t write(const uint8_t* bytes,size_t size) override{
   events.push_back("ascii");output.append(reinterpret_cast<const char*>(bytes),size);return size;
 }
} log_port;
namespace mesh {
bool enabled=true,debug_enabled=false;
bool isUsbLoggingEnabled(){return enabled;}
Port& usbLoggingPort(){return log_port;}
}
struct Transport {
 bool connected=true;
 std::vector<std::vector<uint8_t>> frames;
 bool isConnected() const{return connected;}
 size_t writeFrame(const uint8_t* bytes,size_t size){
   events.push_back("binary");frames.emplace_back(bytes,bytes+size);return size;
 }
};
struct Bridge {
 bool running=true;
 std::vector<std::vector<uint8_t>> raw;
 float snr=0,rssi=0;
 bool isRunning() const{return running;}
 void storeRawRadioData(const uint8_t* bytes,int len,float s,float r){
   events.push_back("mqtt");raw.emplace_back(bytes,bytes+len);snr=s;rssi=r;
 }
};
@PUSH_CODE@
struct Clock {
 uint32_t timestamp=1791030896;
 uint32_t getCurrentTime() const{return timestamp;}
};
class DateTime {
 std::tm value;
public:
 explicit DateTime(uint32_t timestamp){const std::time_t time=timestamp;value=*std::gmtime(&time);}
 unsigned hour() const{return value.tm_hour;}
 unsigned minute() const{return value.tm_min;}
 unsigned second() const{return value.tm_sec;}
 unsigned day() const{return value.tm_mday;}
 unsigned month() const{return value.tm_mon+1;}
 unsigned year() const{return value.tm_year+1900;}
};
struct MyMesh {
 Transport transport;Transport* _serial=&transport;
 Bridge bridge;Bridge* _mqtt_bridge=&bridge;
 uint8_t out_frame[MAX_FRAME_SIZE]={};
 Clock clock;Clock* getRTCClock(){return &clock;}
#if MESH_PACKET_LOGGING && !MESH_PACKET_LOGGING_COMPACT
 const char* getLogDateTime();
#else
 const char* getLogDateTime(){return "";}
#endif
 void logRxRaw(float,float,const uint8_t*,int);
};
@LOG_TIMESTAMP@
@LOG_RX_RAW@
void reset(){
 events.clear();log_port.output.clear();log_port.stalled=false;
 mesh::serialLogDroppedCount()=0;mesh::serialLogPortSeen()=false;
 mesh::enabled=true;mesh::debug_enabled=false;g_mock_millis=0;
}
int main(){
 const uint8_t raw[]={0x00,0xab,0xff};
 MyMesh node;reset();node.logRxRaw(2.5f,-92.0f,raw,sizeof(raw));
#if MESH_PACKET_LOGGING
#if MESH_PACKET_LOGGING_COMPACT
 assert(log_port.output=="R00ABFF\n");
#else
 assert(log_port.output=="12:34:56 - 3/10/2026 U RAW: 00ABFF\r\n");
 // A second invocation uses the current RTC and is the formatter dispatched
 // by the shared RX/TX summary path as well as this RAW path.
 assert(!strcmp(node.getLogDateTime(),"12:34:56 - 3/10/2026 U"));
 node.clock.timestamp+=1;
 assert(!strcmp(node.getLogDateTime(),"12:34:57 - 3/10/2026 U"));
 node.clock.timestamp-=1;
#endif
 assert(events.front()=="ascii"); // RAW precedes the unchanged downstream sinks.
#else
 assert(log_port.output.empty());
#endif
 assert(node.transport.frames.size()==1);
 const std::vector<uint8_t> frame={PUSH_CODE_LOG_RX_DATA,10,uint8_t(-92),0x00,0xab,0xff};
 assert(node.transport.frames[0]==frame);
#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
 assert(node.bridge.raw.size()==1&&node.bridge.raw[0]==std::vector<uint8_t>(raw,raw+sizeof(raw)));
 assert(node.bridge.snr==2.5f&&node.bridge.rssi==-92.0f);
 assert(events[events.size()-2]=="mqtt");
#endif
 reset();mesh::enabled=false;node.logRxRaw(2.5f,-92.0f,raw,sizeof(raw));
 assert(log_port.output.empty()&&node.transport.frames.size()==2);
 reset();node.transport.connected=false;node.logRxRaw(2.5f,-92.0f,raw,sizeof(raw));
 assert(node.transport.frames.size()==2); // Disconnected binary session receives no push.
#if MESH_PACKET_LOGGING
 assert(!log_port.output.empty()); // Independent diagnostics port still receives RAW.
#endif
 reset();uint8_t maximum[255];memset(maximum,0xa5,sizeof(maximum));
 node.logRxRaw(0,-100,maximum,sizeof(maximum));
 assert(node.transport.frames.size()==2); // Never overflow the 176-byte frame buffer.
#if MESH_PACKET_LOGGING
 assert(log_port.output.size()<=SERIAL_LOG_LINE_MAX);
 std::string expected_hex;for(unsigned i=0;i<255;++i)expected_hex+="A5";
#if MESH_PACKET_LOGGING_COMPACT
 assert(log_port.output=="R"+expected_hex+"\n");
#else
 assert(log_port.output=="12:34:56 - 3/10/2026 U RAW: "+expected_hex+"\r\n");
#endif
 reset();log_port.stalled=true;node.transport.connected=true;
 const uint32_t before=millis();node.logRxRaw(2.5f,-92.0f,raw,sizeof(raw));
 assert(log_port.output.empty()&&millis()==before);
 assert(node.transport.frames.size()==3); // A stalled host cannot block other sinks.
#endif
}
'''


class CompanionUsbLoggingTests(unittest.TestCase):
    def compile_and_run(self, work, text, flags, extra=()):
        source = work / 'test.cpp'
        source.write_text(text)
        binary = work / 'test'
        built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie', *['-D' + flag for flag in flags],
            '-include', str(work / 'platform_shim.h'),
            '-I', str(work), '-I', str(ROOT / 'test/fixtures/radio_profiles/mocks'),
            '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
            '-I', str(ROOT / 'src/helpers'), '-I', str(ROOT), str(source),
            *[str(ROOT / 'src/helpers' / name) for name in extra],
            '-o', str(binary)], capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def prepare(self, work):
        (work / 'Utils.h').write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''')
        (work / 'platform_shim.h').write_text('''#include <cstdlib>
#include <cstdio>
inline char* utoa(unsigned int value,char* output,int base){
 if(base!=10)abort();sprintf(output,"%u",value);return output;
}
''')
        transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
        (work / 'ContactFileTransaction.h').write_text(transaction.replace(
            '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))

    def test_real_preference_io_cli_and_saved_runtime_gate(self):
        store = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        header = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        store_methods = esp_recovery_helpers(store) + '\n'.join(method(store, signature)
            for signature in ('bool DataStore::loadPrefs(',
                              'bool DataStore::loadPrefsInt(', 'bool DataStore::savePrefs('))
        prefix = STORE_HARNESS.split('int main(')[0].replace('@METHODS@', store_methods)
        start = source.index('  if (strcmp(command, "get usb.debug") == 0)')
        end = source.index('  if (strcmp(command, "get usb.logging") == 0)', start)
        harness = PREFERENCE_HARNESS.replace('@DEBUG_COMMANDS@', source[start:end])
        harness = harness.replace('@SAVE_PREFS@', method(header, '  bool savePrefs()'))
        harness = harness.replace('@SAVE_PREFERENCE@', method(
            header, 'template <typename T> bool savePreference('))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.prepare(work)
            for platform in ('NRF52_PLATFORM', 'STM32_PLATFORM', 'ESP32_PLATFORM',
                             'RP2040_PLATFORM', 'ESP32_PLATFORM,TBEAM_1W',
                             'RP2040_PLATFORM,ENABLE_WIFI_INTERFACE'):
                for debug in (0, 1):
                    with self.subTest(platform=platform, debug=debug):
                        self.compile_and_run(work, prefix + harness,
                            [flag + '=1' for flag in platform.split(',')] +
                            ['MESH_USB_LOGGING_AVAILABLE=1', 'MESH_DEBUG=' + str(debug)],
                            ('ConfigSerializer.cpp', 'DynamicConfigSerializer.cpp',
                             'CommonRadioPrefs.cpp', 'TxtDataHelpers.cpp'))

    def test_real_raw_emitter_master_gate_compact_and_other_transports(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        code = next(line for line in source.splitlines()
                    if line.startswith('#define PUSH_CODE_LOG_RX_DATA'))
        harness = PACKET_HARNESS.replace('@PUSH_CODE@', code)
        harness = harness.replace('@LOG_TIMESTAMP@',
            '#if MESH_PACKET_LOGGING && !MESH_PACKET_LOGGING_COMPACT\n' +
            method(source, 'const char* MyMesh::getLogDateTime()') + '\n#endif\n')
        harness = harness.replace('@LOG_RX_RAW@', method(source, 'void MyMesh::logRxRaw('))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.prepare(work)
            for packets, compact, mqtt in ((0, 0, 0), (0, 0, 1),
                                           (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)):
                with self.subTest(packets=packets, compact=compact, mqtt=mqtt):
                    flags = ['MESH_PACKET_LOGGING=' + str(packets),
                             'MESH_PACKET_LOGGING_COMPACT=' + str(compact)]
                    if mqtt:
                        flags += ['WITH_MQTT_BRIDGE=1', 'ESP32_PLATFORM=1', 'WIFI_SSID="test"']
                    self.compile_and_run(work, harness, flags)

    def test_begin_defaults_clamps_and_applies_debug_before_packet_master(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        self.assertIn('_prefs.usb_debug_enabled = 0;', source)
        self.assertIn('_prefs.usb_debug_enabled = _prefs.usb_debug_enabled == 1 ? 1 : 0;', source)
        apply = source.index('mesh::setUsbDebugEnabled(_prefs.usb_debug_enabled != 0);')
        self.assertLess(apply, source.index('applyUsbLoggingState(usb_logging_enabled);'))
        header = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        self.assertIn('const char* getLogDateTime() override;', header)

    def test_actual_help_chunks_keep_controls_and_bounded_native_writes(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        start = source.index('  } else if (strcmp(command, "help") == 0) {')
        start = source.index('{', start) + 1
        end = source.index('  } else if (handleCommand(command, 0, local_reply))', start)
        harness = r'''
#include <cassert>
#include <cstring>
#include <string>
struct Port {
 std::string output;
 void print(const char* text){assert(strlen(text)<=256);output+=text;}
 void println(const char* text){print(text);output+="\r\n";}
};
struct MyMesh {
 Port port;bool _terminal_mode=true;
 Port& terminalOutput(){return port;}
 void help(){ @HELP@ }
};
int main(){
 MyMesh node;
 for(bool terminal : {false,true}){
   node._terminal_mode=terminal;node.port.output.clear();node.help();
   const auto& text=node.port.output;
   assert(text.find("Commands:\r\n  stats-core / stats-radio / stats-radio-diag / stats-packets\r\n")==0);
   assert(text.find("  get public.key\r\n")!=std::string::npos);
#if MESH_ENABLE_LOST_REPLY
   assert(text.find("  get lost.reply\r\n  set lost.reply <off|no|yes>\r\n")!=std::string::npos);
#else
   assert(text.find("lost.reply")==std::string::npos);
#endif
   assert(text.find("  get usb.debug\r\n  set usb.debug <on|off>\r\n")!=std::string::npos);
#if MESH_USB_CONSOLE_COOPERATIVE
   assert(text.find("  get usb.watchdog\r\n  get usb.watchdog.last\r\n  set usb.watchdog <off|on|auto> (saved; default auto)\r\n")!=std::string::npos);
#else
   assert(text.find("usb.watchdog")==std::string::npos);
#endif
   assert(text.find("  reboot\r\n  ver\r\n")!=std::string::npos);
   assert(text.find("  +++MESHCORE-TERM-STOP\r\n")!=std::string::npos ? terminal : !terminal);
   assert(text.find("  disconnect (closes the network terminal)\r\n")!=std::string::npos ? !terminal : terminal);
 }
}
'''.replace('@HELP@', source[start:end])
        features = ['MESH_USB_LOGGING_AVAILABLE', 'COMPANION_FEATURE_READER',
                    'COMPANION_FEATURE_MEMORY_DIAGNOSTICS', 'COMPANION_FEATURE_NOTIFICATIONS',
                    'COMPANION_RADIO_FULL', 'COMPANION_EXCLUSIVE_WIFI_BLE',
                    'WITH_WEBCONFIG', 'MESH_PRIMARY_ESPNOW', 'COMPANION_FEATURE_TEMP_RADIO',
                    'COMPANION_FEATURE_OTA_CLI', 'BLE_PIN_CODE', 'ESP32', 'NRF52_PLATFORM',
                    'EXTRAFS']
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.prepare(work)
            for native in (0, 1):
                for lost in (0, 1):
                    with self.subTest(native=native, lost=lost):
                        self.compile_and_run(work, harness,
                            [flag + '=1' for flag in features] + ['WIFI_SSID="test"',
                             'MESH_USB_CONSOLE_COOPERATIVE=' + str(native),
                             'MESH_ENABLE_LOST_REPLY=' + str(lost)])


if __name__ == '__main__':
    unittest.main()
