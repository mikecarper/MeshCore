"""Execute saved Companion stream-mode selection and its production USB routes.

Framework endpoints are native boundaries. Preference I/O, the CLI branches,
boot selection, and USB routing/descriptor functions are actual source bodies.
The complete primary-CDC transport fixture separately exercises real queues.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS as STORE_HARNESS
import test_companion_usb_logging as logging_tests
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

MODE_HARNESS = r'''
#include <atomic>
#include <helpers/CLICommandUtils.h>
struct Endpoint : Stream {
 unsigned begins=0; std::string output;
 int available() override{return 0;} int read() override{return -1;}
 int peek() override{return -1;}
 size_t write(uint8_t b) override{output+=char(b);return 1;}
 size_t write(const uint8_t* p,size_t n) override{output.append((const char*)p,n);return n;}
 void begin(unsigned){++begins;}
};
struct UsbDevice {unsigned detaches=0,attaches=0;
 void detach(){++detaches;} void attach(){++attaches;}} TinyUSBDevice;
template<class T> T constrain(T value,int low,int high){return T(value<low?low:value>high?high:value);}
uint32_t futureMillis(uint32_t value){return value+10;}
namespace mesh {
static std::atomic<bool> usb_logging_preference_known{false};
bool logging=false;
bool dedicated_usb_logging_port_started=false,dedicated_usb_logging_port_configured=false;
std::atomic<bool> dedicated_usb_logging_port_connected{false};
Endpoint dedicated_usb_logging_port,nonblocking_dedicated_usb_logging_port;
Endpoint nonblocking_primary_usb_logging_port,null_usb_logging_stream;
bool isUsbLoggingEnabled(){return logging;}
bool usbDataLocked(){return false;}
bool isUsbDebugLoggingEnabled(){return false;}
void setPlatformDebugOutputEnabled(bool){}
void setUsbDebugEnabled(bool){}
void setUsbPacketLoggingAlways(bool){}
void setUsbLoggingEnabled(bool enabled){logging=enabled;usb_logging_preference_known=true;}
bool saveUsbLoggingBootPreference(bool){return true;}
unsigned packet_logging_resumes=0;
void resumeUsbPacketLogging(){++packet_logging_resumes;}
@USB_FUNCTIONS@
void freshBoot(){
 dedicated_usb_logging_port_started=false;dedicated_usb_logging_port_configured=false;
 dedicated_usb_logging_port_connected=false;dedicated_usb_logging_port.begins=0;
 TinyUSBDevice.detaches=0;TinyUSBDevice.attaches=0;
 usb_logging_preference_known=false;logging=false;configureUsbLoggingPacketStream(false);
}
}
struct {double node_lat=47.1,node_lon=-122.2;} sensors;
bool usb_logging_reply_hold=false,usb_logging_reply_staged=false,usb_logging_reply_state=false;
bool usb_logging_reply_pending=false,usb_protocol_initialized=true;
unsigned ownership_checks=0;
void serviceUsbLoggingOwnership(bool){++ownership_checks;}
struct MyMesh {
 CompanionNodePrefs _prefs;DataStore store;DataStore* _store=&store;
 uint32_t _scheduled_reboot_at=0;
 @SAVE_PREFS@
 @APPLY@
 bool command(const char* command,char* reply,size_t reply_size){
   @COMMANDS@
   return false;
 }
 void boot(){
   @NORMALIZE@
   @BOOT@
   mesh::beginUsbLoggingPort();
 }
};
static void bootMode(MyMesh& node,uint8_t expected){
 DataStore reboot;reboot.fs=node.store.fs;
 CompanionNodePrefs saved;double lat=0,lon=0;
 assert(reboot.loadPrefs(saved,lat,lon));assert(saved.usb_logging_enabled==expected);
 node._prefs=saved;mesh::freshBoot();node.boot();
 assert(node._prefs.usb_logging_enabled==expected);
 assert(mesh::isUsbLoggingPacketStream()==(expected==2));
 assert(mesh::isUsbLoggingEnabled()==(expected!=0));
 assert(!mesh::usbLoggingInterfaceRestartRequired());
#if defined(MESH_DUAL_CDC_LOGGING)
 assert(mesh::dedicated_usb_logging_port_configured==(expected==1));
 assert(mesh::dedicated_usb_logging_port.begins==(expected==1));
 assert(TinyUSBDevice.detaches==(expected==1)&&TinyUSBDevice.attaches==(expected==1));
#else
 assert(!mesh::dedicated_usb_logging_port_configured);
#endif
 if(expected==2){
   assert(!mesh::hasDedicatedUsbLoggingPort());
   assert(&mesh::usbLoggingPort()==&mesh::nonblocking_primary_usb_logging_port);
   assert(strstr(mesh::usbLoggingPortDescription(),"primary"));
 }else if(expected==0){assert(&mesh::usbLoggingPort()==&mesh::null_usb_logging_stream);}
 else {
#if defined(MESH_DUAL_CDC_LOGGING)
   assert(mesh::hasDedicatedUsbLoggingPort());
   assert(&mesh::usbLoggingPort()==&mesh::null_usb_logging_stream);
   mesh::dedicated_usb_logging_port_connected=true;
   assert(&mesh::usbLoggingPort()==&mesh::nonblocking_dedicated_usb_logging_port);
#else
   assert(&mesh::usbLoggingPort()==&mesh::nonblocking_primary_usb_logging_port);
#endif
 }
}
int main(){
 MyMesh node;strcpy(node._prefs.node_name,"stable owned identity");
 node._prefs.freq=915.125f;node._prefs.ble_pin=246810;node._prefs.bluetooth_enabled=1;
 char reply[160]={};
 for(uint8_t mode : {0,1,2}){
   node._prefs.usb_logging_enabled=mode;assert(node.savePrefs());
   assert(node.store.fs.files["/new_prefs"][158]==mode);
   bootMode(node,mode);
   assert(!strcmp(node._prefs.node_name,"stable owned identity"));
   assert(node._prefs.freq==915.125f&&node._prefs.ble_pin==246810);
 }
 node._prefs.usb_logging_enabled=1;assert(node.savePrefs());bootMode(node,1);
 const auto original=node.store.fs.files["/new_prefs"];
 const unsigned attached=TinyUSBDevice.attaches,detached=TinyUSBDevice.detaches;
 for(const char* invalid : {"set usb.logging stream extra","set usb.logging STREAM",
      "set usb.logging stream ","set usb.logging stream reboot ","set usb.loggingstream"}){
   const auto before=node.store.fs.files["/new_prefs"];
   const unsigned resumes=mesh::packet_logging_resumes;
   const bool handled=node.command(invalid,reply,sizeof(reply));
   assert(mesh::packet_logging_resumes==resumes);
   if(handled)assert(strstr(reply,"Error:"));
   assert(node.store.fs.files["/new_prefs"]==before&&node._prefs.usb_logging_enabled==1);
 }
 const unsigned resumes=mesh::packet_logging_resumes;
 assert(node.command("set usb.logging stream",reply,sizeof(reply)));
 assert(mesh::packet_logging_resumes==resumes+1);
 assert(node._prefs.usb_logging_enabled==2&&node.store.fs.files["/new_prefs"][158]==2);
 assert(strstr(reply,"reboot required"));assert(node._scheduled_reboot_at==0);
 assert(!mesh::isUsbLoggingPacketStream()); // Saved choice cannot flip a live descriptor/route.
 assert(TinyUSBDevice.attaches==attached&&TinyUSBDevice.detaches==detached);
 const auto stream=node.store.fs.files["/new_prefs"];
 for(size_t i=0;i<stream.size();++i)if(i!=158)assert(stream[i]==original[i]);
 assert(node.command("get usb.logging",reply,sizeof(reply))&&strstr(reply,"reboot required"));
 assert(node.command("set usb.logging stream reboot",reply,sizeof(reply)));
 assert(node._scheduled_reboot_at==futureMillis(1000));
 assert(!mesh::isUsbLoggingPacketStream());
 bootMode(node,2);node._scheduled_reboot_at=0;
 assert(node.command("get usb.logging",reply,sizeof(reply)));
 assert(strstr(reply,"usb.logging stream")&&strstr(reply,"primary")&&!strstr(reply,"reboot required"));
 assert(node.command("set usb.logging stream reboot",reply,sizeof(reply)));
 assert(node._scheduled_reboot_at==0); // Identical boot selection does not reboot.
 assert(node.command("set usb.logging on",reply,sizeof(reply)));
#if defined(MESH_DUAL_CDC_LOGGING)
 assert(strstr(reply,"reboot required"));
#endif
 assert(mesh::isUsbLoggingPacketStream()); // Mode remains latched until boot.
 bootMode(node,1);node._scheduled_reboot_at=0;
 assert(node.command("set usb.logging stream",reply,sizeof(reply)));
 node.store.fs.fail_write=true;
 const unsigned before_failed_save=mesh::packet_logging_resumes;
 assert(node.command("set usb.logging off",reply,sizeof(reply)));
 for(const char* failed : {"set usb.logging on","set usb.logging stream"}) {
   assert(node.command(failed,reply,sizeof(reply)) && strstr(reply,"save failed"));
   assert(mesh::packet_logging_resumes==before_failed_save);
 }
 assert(strstr(reply,"save failed")&&node.store.fs.files["/new_prefs"][158]==2);
 assert(!mesh::isUsbLoggingPacketStream());
 char guarded[17];memset(guarded,'X',sizeof(guarded));
 assert(node.command("get usb.logging",guarded,16));assert(guarded[15]==0&&guarded[16]=='X');
}
'''


class CompanionPacketStreamTests(unittest.TestCase):
    def test_actual_preferences_cli_boot_descriptor_and_port(self):
        store = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        mesh = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        header = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        usb = (ROOT / 'src/helpers/UsbLogging.cpp').read_text()
        main = (ROOT / 'examples/companion_radio/main.cpp').read_text()
        methods = '\n'.join(method(store, sig) for sig in (
            'bool DataStore::loadPrefs(', 'bool DataStore::loadPrefsInt(',
            'bool DataStore::savePrefs('))
        prefix = STORE_HARNESS.split('int main(')[0].replace('@METHODS@', methods)
        start = usb.index('static std::atomic<bool> usb_logging_packet_stream')
        end = usb.index('\nvoid setUsbCompanionTxBufferCapacity', start)
        functions = usb[start:end] + '\n' + '\n'.join(method(usb, sig) for sig in (
            'bool hasDedicatedUsbLoggingPort()', 'bool isDedicatedUsbLoggingPortConfigured()',
            'bool usbLoggingInterfaceRestartRequired()', 'const char* usbLoggingPortDescription()',
            'void beginUsbLoggingPort()', 'static Stream& rawUsbLoggingPort()',
            'Stream& usbLoggingPort()'))
        start = mesh.index('  if (strcmp(command, "get usb.logging") == 0)')
        end = mesh.index('\n#endif', start)
        boot_start = mesh.index('  const bool usb_logging_enabled = _prefs.usb_logging_enabled != 0;')
        boot_end = mesh.index('  if (!mesh::saveUsbLoggingBootPreference(', boot_start)
        normalize = next(line for line in mesh.splitlines()
                         if '_prefs.usb_logging_enabled = constrain(' in line)
        code = MODE_HARNESS.replace('@USB_FUNCTIONS@', functions)
        code = code.replace('@COMMANDS@', mesh[start:end])
        code = code.replace('@SAVE_PREFS@', method(header, '  bool savePrefs()'))
        code = code.replace('@APPLY@', method(main, 'void MyMesh::applyUsbLoggingState(')
                            .replace('void MyMesh::applyUsbLoggingState(', 'void applyUsbLoggingState('))
        code = code.replace('@BOOT@', mesh[boot_start:boot_end]).replace('@NORMALIZE@', normalize)
        helper = logging_tests.CompanionUsbLoggingTests()
        with tempfile.TemporaryDirectory(prefix='companion-packet-stream-') as directory:
            work = Path(directory)
            helper.prepare(work)
            for dual in (False, True):
                with self.subTest(dual=dual):
                    helper.compile_and_run(work, prefix + code,
                        ['NRF52_PLATFORM=1', 'MESH_USB_LOGGING_AVAILABLE=1']
                        + (['MESH_DUAL_CDC_LOGGING=1'] if dual else []),
                        ('ConfigSerializer.cpp', 'DynamicConfigSerializer.cpp',
                         'CommonRadioPrefs.cpp', 'radiolib/RXPowerSaving.cpp',
                         'radiolib/RXPowerSavingCLI.cpp', 'TxtDataHelpers.cpp'))

    def test_actual_full_dual_compiled_primary_transport(self):
        fixture = ROOT / 'test/fixtures/nrf52_usb_console'
        with tempfile.TemporaryDirectory(prefix='companion-stream-transport-') as directory:
            work = Path(directory)
            # The full implementation uses existing native endpoint boundaries;
            # only add the optional second-CDC class needed by this profile.
            arduino = (fixture / 'mocks/Arduino.h').read_text().replace(
                '"../../../mocks/Arduino.h"', '"' + str(ROOT / 'test/mocks/Arduino.h') + '"')
            (work / 'Arduino.h').write_text(arduino)
            tiny = (fixture / 'mocks/Adafruit_TinyUSB.h').read_text().replace(
                '#define CFG_TUD_CDC 1', '#define CFG_TUD_CDC 2').replace(
                '#define CFG_TUD_CDC_TX_BUFSIZE 64', '#define CFG_TUD_CDC_TX_BUFSIZE 128').replace(
                'uint32_t tud_cdc_n_write_flush(uint8_t);',
                'extern "C" uint32_t tud_cdc_n_write_flush(uint8_t);')
            tiny += '''
void tud_sof_cb_enable(bool);
uint32_t tud_cdc_n_read(uint8_t,void*,uint32_t);
class Adafruit_USBD_CDC : public Stream {
 public:
 virtual uint16_t getInterfaceDescriptor(uint8_t,uint8_t*,uint16_t){return 0;}
 void setStringDescriptor(const char*){} void begin(unsigned){} bool dtr(){return false;}
 int available() override{return 0;} int read() override{return -1;} int peek() override{return -1;}
 int availableForWrite() override{return 128;}
 size_t write(uint8_t) override{assert(false);return 0;}
 size_t write(const uint8_t*,size_t) override{assert(false);return 0;}
};
'''
            (work / 'Adafruit_TinyUSB.h').write_text(tiny)
            source = (fixture / 'test_console.cpp').read_text()
            source = source.replace('int main() {',
                'void tud_sof_cb_enable(bool){}\n'
                'uint32_t tud_cdc_n_read(uint8_t n,void*,uint32_t){assert(n==1);return 0;}\n'
                'int main() {', 1)
            # Run the unchanged comprehensive queue/session fixture on the
            # dual-capable build, selecting primary stream before enable.
            source = source.replace('  mesh::setUsbLoggingEnabled(true);',
                '  mesh::configureUsbLoggingPacketStream(true);\n'
                '  assert(!mesh::hasDedicatedUsbLoggingPort());\n'
                '  mesh::setUsbLoggingEnabled(true);', 1)
            (work / 'test.cpp').write_text(source)
            binary = work / 'test'
            built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
                '-DARDUINO', '-DNRF52_PLATFORM', '-DUSE_TINYUSB', '-DENABLE_USB_INTERFACE',
                '-DMESH_DEBUG=1', '-DMESH_DUAL_CDC_LOGGING=1',
                '-DCOMPANION_FEATURE_DEDICATED_USB_LOGGING=1',
                '-I' + str(work), '-I' + str(fixture / 'mocks'), '-I' + str(ROOT / 'src'),
                str(work / 'test.cpp'), *[str(ROOT / 'src/helpers' / name) for name in (
                    'UsbLogging.cpp', 'UsbLoggingClientActivity.cpp', 'UsbLoggingLineStateOverride.cpp')],
                '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn('native nRF52 console transport passed', ran.stdout)


if __name__ == '__main__':
    unittest.main()
