"""Execute Bluetooth CLI parsing, real transport routing, and shutdown guards."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / 'examples/companion_radio/main.cpp').read_text()

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <helpers/MultiSerialInterface.h>
#include "examples/companion_radio/CompanionBluetooth.h"
uint32_t now_ms=100;
uint32_t millis(){return now_ms;}
bool usb_open=false, usb_power=true, network_terminal=false;
bool companion_bluetooth_initialized=true, usb_mota_mode=false;
const char* companion_bluetooth_start_failure="Bluetooth has not started";
uint32_t companion_bluetooth_off_at=0;
bool companion_bluetooth_force_off=false;
struct Transport : BaseSerialInterface {
 bool enabled=false,connected=false,busy=false,frame=false,enable_fails=false;
 unsigned disabled=0,enables=0;
 void enable() override {++enables;enabled=!enable_fails;}
 void disable() override {enabled=false;connected=false;++disabled;}
 bool isEnabled() const override{return enabled;}
 bool isConnected() const override{return connected;}
 bool isReadBusy() const override{return false;}
 bool isWriteBusy() const override{return busy;}
 bool hasPendingIO() const override{return busy;}
 size_t writeFrame(const uint8_t*,size_t n) override{return n;}
 size_t checkRecvFrame(uint8_t* out) override{
   if(!frame)return 0; frame=false;out[0]=0x42;return 1;
 }
} bluetooth_interface,usb_serial_interface,wifi_interface,ethernet_interface;
MultiSerialInterface interface_manager;
bool isUsbTerminalDataConnected(){return usb_open;}
unsigned cancelled=0,logs=0;
struct {
 bool bluetooth_preference=true,durable_bluetooth_preference=true,storage_accepts=true;
 unsigned saves=0,fail_save_at=0;
 bool isBluetoothEnabledPreference() const {return bluetooth_preference;}
 bool setBluetoothEnabledPreference(bool enabled){
   ++saves;if(!storage_accepts||saves==fail_save_at)return false;
   bluetooth_preference=durable_bluetooth_preference=enabled;return true;
 }
 bool isAnyNetworkTerminalMode(){return network_terminal;}
 void cancelSerialResponseStream(BaseSerialInterface*) {}
 void cancelSerialOperationsForRoute(BaseSerialInterface* route){
   assert(route==&bluetooth_interface);++cancelled;
 }
} the_mesh;
namespace mesh {
struct Logger {void println(const char*){++logs;}} logger;
Logger& usbLoggingPort(){return logger;}
}
@METHODS@
char reply[160];
bool command(const char* text,CompanionBluetoothCommandSource source=CompanionBluetoothCommandSource::Framed){
 memset(reply,0,sizeof(reply));
 return handleCompanionBluetoothCommand(text,reply,sizeof(reply),source);
}
void route(Transport& target){
 target.connected=true;target.frame=true;
 uint8_t frame[176];assert(interface_manager.checkRecvFrame(frame)==1);
 assert(interface_manager.captureReplyRoute()==&target);
}
int main(){
#if defined(BLE_PIN_CODE)
 interface_manager.addInterface(InterfaceType::Bluetooth,&bluetooth_interface);
 interface_manager.addInterface(InterfaceType::USB,&usb_serial_interface);
 interface_manager.addInterface(InterfaceType::WiFi,&wifi_interface);
 interface_manager.addInterface(InterfaceType::Ethernet,&ethernet_interface);
 interface_manager.enable();route(bluetooth_interface);
 assert(command("get bluetooth")&&strstr(reply,"bluetooth on"));
 for(const char* invalid : {"set ble","set bluetooth on force","set ble off force extra",
                            "set ble off forced","set bluetooth off junk","set ble yes"}){
   assert(command(invalid)&&strstr(reply,"Error:"));
   assert(bluetooth_interface.disabled==0&&companion_bluetooth_off_at==0);
 }
 assert(!command("get bluetooth.mac"));
 assert(!command("set ble.stealth on"));
 assert(command("set bluetooth off")&&strstr(reply,"no other active"));
 assert(usb_power&&bluetooth_interface.enabled&&!companion_bluetooth_off_at);
 assert(the_mesh.saves==0&&the_mesh.durable_bluetooth_preference);
 // A disabled TCP interface and a socket that is merely listening don't count.
 wifi_interface.connected=true;wifi_interface.enabled=false;
 assert(command("set ble off")&&strstr(reply,"Error:"));
 wifi_interface.enabled=true;wifi_interface.connected=false;
 assert(command("set ble off")&&strstr(reply,"Error:"));
 // A real TCP client allows a delayed BLE response. Losing it cancels shutdown.
 wifi_interface.connected=true;
 assert(command("set ble off")&&strstr(reply,"requested"));
 assert(command("get ble")&&strstr(reply,"off pending"));
 assert(the_mesh.durable_bluetooth_preference); // Requested is not yet committed.
 now_ms+=249;serviceCompanionBluetoothControl();assert(bluetooth_interface.enabled);
 wifi_interface.connected=false;++now_ms;serviceCompanionBluetoothControl();
 assert(bluetooth_interface.enabled&&!companion_bluetooth_off_at&&logs==1);
 assert(the_mesh.saves==0&&the_mesh.durable_bluetooth_preference);
 // Force may disconnect the sole client, but waits for queued notifications.
 assert(command(" set ble\t off\tforce \t")&&strstr(reply,"requested"));
 bluetooth_interface.busy=true;now_ms+=250;serviceCompanionBluetoothControl();
 assert(bluetooth_interface.enabled);
 now_ms+=1749;serviceCompanionBluetoothControl();assert(bluetooth_interface.enabled);
 ++now_ms;serviceCompanionBluetoothControl();
 assert(!bluetooth_interface.enabled&&cancelled==1);
 assert(!the_mesh.durable_bluetooth_preference);
 assert(interface_manager.captureReplyRoute()==nullptr);
 assert(command("set ble off")&&strstr(reply,"already off"));
 assert(command("get ble")&&strcmp(reply,"bluetooth off")==0);
 bluetooth_interface.busy=false;
 assert(command("set bluetooth on")&&strstr(reply,"OK"));route(bluetooth_interface);
 assert(the_mesh.durable_bluetooth_preference);
 // Another native CDC session counts; HWCDC/bridge presence alone doesn't.
 usb_open=true;usb_serial_interface.connected=true;
 assert(command("set ble off"));
#if defined(NRF52_PLATFORM) || (defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 0)
 assert(strstr(reply,"requested"));
 usb_open=false;usb_serial_interface.connected=false;
 now_ms+=250;serviceCompanionBluetoothControl();assert(bluetooth_interface.enabled);
#else
 assert(strstr(reply,"Error:"));usb_open=false;usb_serial_interface.connected=false;
#endif
 // USB MOTA ownership cannot count as a separate management terminal.
 usb_mota_mode=true;usb_open=true;
 assert(command("set ble off")&&strstr(reply,"Error:"));
 usb_mota_mode=false;usb_open=false;
 // An actual USB command proves that requester is present on every backend.
 route(usb_serial_interface);
 assert(command("set ble off")&&strstr(reply,"Bluetooth off"));
 assert(!bluetooth_interface.enabled&&interface_manager.captureReplyRoute()==&usb_serial_interface);
 assert(!the_mesh.durable_bluetooth_preference);
 assert(usb_serial_interface.enabled); // Persisting BLE off never disables USB.
 const unsigned saved_off_enables=bluetooth_interface.enables;
 interface_manager.disable();interface_manager.enable();
 assert(!bluetooth_interface.enabled&&bluetooth_interface.enables==saved_off_enables);
 assert(usb_serial_interface.enabled);route(usb_serial_interface);
 assert(command("set ble on")&&bluetooth_interface.enabled);
 assert(the_mesh.durable_bluetooth_preference);
 interface_manager.disable();interface_manager.enable();
 assert(bluetooth_interface.enabled); // Successful on also restores the aggregate boot policy.
 // ASCII terminal commands cannot inherit the previous framed BLE route.
 route(bluetooth_interface);
 assert(command("set ble off",CompanionBluetoothCommandSource::Terminal));
 assert(!bluetooth_interface.enabled);
 assert(command("set ble on"));route(bluetooth_interface);
 // Active browser/TCP terminal and Ethernet sessions each qualify.
 network_terminal=true;
 assert(command("set ble off")&&strstr(reply,"requested"));
 assert(command("set ble on")&&!companion_bluetooth_off_at);
 now_ms+=300;serviceCompanionBluetoothControl();assert(bluetooth_interface.enabled);
 network_terminal=false;ethernet_interface.connected=true;
 assert(command("set ble off")&&strstr(reply,"requested"));
 now_ms+=250;serviceCompanionBluetoothControl();assert(!bluetooth_interface.enabled);
 assert(command("set ble on"));route(bluetooth_interface);ethernet_interface.connected=false;
 // Failed delayed saves leave the requesting BLE session alive and cancellable.
 assert(command("set ble off force"));
 const unsigned delayed_disabled=bluetooth_interface.disabled;
 const uint32_t pending_off=companion_bluetooth_off_at;
 the_mesh.storage_accepts=false;
 assert(command("set ble on")&&strstr(reply,"Error:"));
 assert(companion_bluetooth_off_at==pending_off); // Failed reversal does not cancel a prior request.
 now_ms+=250;serviceCompanionBluetoothControl();
 assert(bluetooth_interface.enabled&&the_mesh.durable_bluetooth_preference);
 assert(!companion_bluetooth_off_at&&bluetooth_interface.disabled==delayed_disabled);
 the_mesh.storage_accepts=true;
 // Failed immediate off saves must not disconnect the USB recovery command.
 route(usb_serial_interface);the_mesh.storage_accepts=false;
 assert(command("set ble off")&&strstr(reply,"Error:"));
 assert(bluetooth_interface.enabled&&the_mesh.durable_bluetooth_preference);
 assert(interface_manager.captureReplyRoute()==&usb_serial_interface);
 the_mesh.storage_accepts=true;
 assert(command("set ble off")&&!bluetooth_interface.enabled);
 assert(!the_mesh.durable_bluetooth_preference);
 // Failed on saves restore the off runtime state as well as durable preference.
 the_mesh.storage_accepts=false;
 const unsigned failed_on_enables=bluetooth_interface.enables;
 assert(command("set ble on")&&strstr(reply,"Error:"));
 assert(!bluetooth_interface.enabled&&!the_mesh.durable_bluetooth_preference);
 assert(bluetooth_interface.enables==failed_on_enables); // No transient advertising.
 the_mesh.storage_accepts=true;
 assert(command("set ble on")&&bluetooth_interface.enabled);
 assert(the_mesh.durable_bluetooth_preference);route(bluetooth_interface);
 // Pending deadlines work across millis() wrap, including the zero sentinel.
 for(uint32_t start : {0xffffff00u,0xffffff06u}){
   now_ms=start;assert(command("set ble off force"));
   now_ms=start+249;serviceCompanionBluetoothControl();assert(bluetooth_interface.enabled);
   now_ms=start+251;serviceCompanionBluetoothControl();assert(!bluetooth_interface.enabled);
   assert(command("set ble on"));route(bluetooth_interface);
 }
 companion_bluetooth_initialized=false;
 bluetooth_interface.enabled=false;
 assert(command("get ble")&&strstr(reply,"bluetooth off"));
 assert(command("set ble on")&&strstr(reply,"unavailable"));
 assert(command("set ble off force")&&strstr(reply,"already off"));
 assert(!the_mesh.durable_bluetooth_preference);
 companion_bluetooth_initialized=true;
 assert(command("set ble off",CompanionBluetoothCommandSource::Terminal));
 bluetooth_interface.enable_fails=true;
 assert(command("set ble on")&&strstr(reply,"enable failed"));
 assert(!the_mesh.durable_bluetooth_preference&&!bluetooth_interface.enabled);
 assert(command("get ble")&&strcmp(reply,"bluetooth off")==0);
 bluetooth_interface.enable_fails=false;
 interface_manager.disable();interface_manager.enable();
 assert(!bluetooth_interface.enabled&&usb_serial_interface.enabled); // Policy rollback matches saved off.
 // If both enable and its rollback save fail, report the durable on choice honestly.
 bluetooth_interface.enable_fails=true;the_mesh.fail_save_at=the_mesh.saves+2;
 assert(command("set ble on")&&strstr(reply,"rollback save failed"));
 assert(the_mesh.durable_bluetooth_preference&&!bluetooth_interface.enabled);
 assert(command("get ble")&&strcmp(reply,"bluetooth off")==0); // Status remains the live state.
 the_mesh.fail_save_at=0;bluetooth_interface.enable_fails=false;
 interface_manager.disable();interface_manager.enable();
 assert(bluetooth_interface.enabled); // Aggregate policy remains consistent with the durable on value.
#else
 assert(command("get ble")&&strstr(reply,"not supported"));
 assert(command("set ble off force")&&strstr(reply,"not supported"));
#endif
 assert(!handleCompanionBluetoothCommand(nullptr,reply,sizeof(reply)));
 assert(!handleCompanionBluetoothCommand("get ble",nullptr,sizeof(reply)));
 assert(!handleCompanionBluetoothCommand("set ble off force",reply,0));
}
'''


class BluetoothControlTests(unittest.TestCase):
    def test_actual_commands_and_connection_guards(self):
        guards = '\n'.join(method(MAIN, signature) for signature in (
            'static bool hasCompanionNonBluetoothClient(',
            'static void disableCompanionBluetoothForCli(',
            'static void serviceCompanionBluetoothControl('))
        methods = '#if defined(BLE_PIN_CODE)\n' + guards + '\n#endif\n' + method(
            MAIN, 'bool handleCompanionBluetoothCommand(')
        configurations = {
            'no_ble': [],
            'nrf52': ['BLE_PIN_CODE=123456', 'NRF52_PLATFORM=1'],
            'esp32_tinyusb': ['BLE_PIN_CODE=123456', 'ESP32=1',
                             'ARDUINO_USB_CDC_ON_BOOT=1', 'ARDUINO_USB_MODE=0'],
            'esp32_hwcdc': ['BLE_PIN_CODE=123456', 'ESP32=1',
                           'ARDUINO_USB_CDC_ON_BOOT=1', 'ARDUINO_USB_MODE=1'],
            'esp32_uart': ['BLE_PIN_CODE=123456', 'ESP32=1'],
        }
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'Arduino.h').write_text(
                '#pragma once\n#include <cstdint>\n#include <cstddef>\n#include <initializer_list>\n')
            source = folder/'bluetooth.cpp'
            source.write_text(HARNESS.replace('@METHODS@', methods))
            for name, defines in configurations.items():
                with self.subTest(platform=name):
                    binary = folder/name
                    built = subprocess.run([
                        os.environ.get('CXX','g++'), '-std=c++17', '-Wall', '-Wextra',
                        '-I',str(folder),'-I',str(ROOT),'-I',str(ROOT/'src'),
                        '-DENABLE_USB_INTERFACE=1', '-DCOMPANION_FEATURE_TEXT_TERMINAL=1',
                        '-DCOMPANION_FEATURE_NETWORK_TERMINAL=1',
                        '-DCOMPANION_FEATURE_USB_MOTA_SOURCE=1',
                        *['-D'+flag for flag in defines],str(source),'-o',str(binary)],
                        capture_output=True,text=True)
                    self.assertEqual(built.returncode,0,built.stderr)
                    ran = subprocess.run([str(binary)],capture_output=True,text=True)
                    self.assertEqual(ran.returncode,0,ran.stderr)

    def test_saved_off_policy_keeps_usb_recovery_and_explicit_runtime_on(self):
        # Execute the actual multi-transport manager. A saved-off boot must
        # not briefly advertise when the aggregate interface is enabled.
        source_text = HARNESS.split('@METHODS@')[0] + r'''
int main(){
 interface_manager.addInterface(InterfaceType::Bluetooth,&bluetooth_interface);
 interface_manager.addInterface(InterfaceType::USB,&usb_serial_interface);
 interface_manager.addInterface(InterfaceType::WiFi,&wifi_interface);
 interface_manager.addInterface(InterfaceType::Ethernet,&ethernet_interface);
 interface_manager.setBluetoothAutoEnable(false);
 interface_manager.enable();
 assert(!bluetooth_interface.enabled&&bluetooth_interface.enables==0);
 assert(usb_serial_interface.enabled&&wifi_interface.enabled&&ethernet_interface.enabled);
 interface_manager.disable();interface_manager.enable();
 assert(!bluetooth_interface.enabled&&bluetooth_interface.enables==0);
 // USB can explicitly enable an already-initialized stack while saved off.
 interface_manager.enableBluetooth();assert(bluetooth_interface.enabled);
 assert(bluetooth_interface.enables==1);
 interface_manager.disable();interface_manager.enable();
 assert(!bluetooth_interface.enabled&&bluetooth_interface.enables==1);
 interface_manager.setBluetoothAutoEnable(true);
 interface_manager.disable();interface_manager.enable();
 assert(bluetooth_interface.enabled&&bluetooth_interface.enables==2);
 // The legacy default remains automatic on.
 MultiSerialInterface legacy;Transport ble;
 legacy.addInterface(InterfaceType::Bluetooth,&ble);legacy.enable();
 assert(ble.enabled&&ble.enables==1);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cstddef>\n')
            source = folder/'startup_policy.cpp'
            source.write_text(source_text)
            binary = folder/'startup_policy'
            built = subprocess.run([os.environ.get('CXX','g++'), '-std=c++17',
                '-I',str(folder),'-I',str(ROOT),'-I',str(ROOT/'src'),
                str(source),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            ran = subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(ran.returncode,0,ran.stderr)

    def test_wireless_restore_does_not_change_the_saved_bluetooth_choice(self):
        adapter = method(MAIN, 'class CompanionWirelessBackend')+';\n'
        source_text = HARNESS.split('@METHODS@')[0] + '\n' + method(
            MAIN, 'static void disableCompanionBluetoothForCli(') + '\n' + adapter + r'''
int main(){
 using namespace mesh::wireless;
 interface_manager.addInterface(InterfaceType::Bluetooth,&bluetooth_interface);
 interface_manager.addInterface(InterfaceType::USB,&usb_serial_interface);
 the_mesh.bluetooth_preference=the_mesh.durable_bluetooth_preference=false;
 interface_manager.setBluetoothAutoEnable(false);interface_manager.enable();
 CompanionWirelessBackend backend;control().begin(backend);
 assert(backend.available()==Bluetooth&&!bluetooth_interface.enabled);
 char reply[160];
 control().handle("set 2.4ghz off",reply,sizeof(reply),now_ms,Independent);
 now_ms+=250;control().service(now_ms);
 control().handle("set 2.4ghz on",reply,sizeof(reply),now_ms,Independent);
 now_ms+=250;control().service(now_ms);
 assert(!bluetooth_interface.enabled&&bluetooth_interface.enables==0);
 assert(!the_mesh.durable_bluetooth_preference&&the_mesh.saves==0);
 // Explicit all is a runtime-only override; the saved-off boot policy remains.
 control().handle("set 2.4ghz on all",reply,sizeof(reply),now_ms,Independent);
 now_ms+=250;control().service(now_ms);
 assert(bluetooth_interface.enabled&&!the_mesh.durable_bluetooth_preference);
 assert(the_mesh.saves==0);
 interface_manager.disable();interface_manager.enable();
 assert(!bluetooth_interface.enabled&&usb_serial_interface.enabled);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cstddef>\n')
            source = folder/'wireless_policy.cpp'
            source.write_text(source_text)
            binary = folder/'wireless_policy'
            built = subprocess.run([os.environ.get('CXX','g++'), '-std=c++17',
                '-DBLE_PIN_CODE=123456','-I',str(folder),'-I',str(ROOT),'-I',str(ROOT/'src'),
                str(source),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            ran = subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(ran.returncode,0,ran.stderr)

    def test_cli_entry_points_pass_the_current_source(self):
        mesh = (ROOT/'examples/companion_radio/MyMesh.cpp').read_text()
        terminal = method(mesh,'void MyMesh::handleTerminalCommand(')
        framed = method(mesh,'void MyMesh::handleCmdFrame(')
        local = method(mesh,'bool MyMesh::handleLocalControlCommand(')
        self.assertIn('CompanionBluetoothCommandSource::Terminal',terminal)
        self.assertIn('CompanionBluetoothCommandSource::Framed',framed)
        self.assertIn('handleCompanionBluetoothCommand(command, reply, reply_size)',local)
        loop = method(MAIN,'\nvoid loop()')
        self.assertIn('serviceCompanionBluetoothControl();',loop)


if __name__ == '__main__':
    unittest.main()
