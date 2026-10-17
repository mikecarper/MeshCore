"""Execute the actual role ownership vetoes for USB watchdog recovery."""
from itertools import product
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

COMMON_HARNESS = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <helpers/CompanionDelayedReplies.h>
struct Board {
 bool updating=false, testing=false;
 bool isOTAUpdateRunning() const{return updating;}
 bool isRadioTestActive() const{return testing;}
} board;
struct RadioDriver {
 bool observing=false, calibrating=false;
 bool isWatchdogObserving() const{return observing;}
 bool isCalibratingNoiseFloor() const{return calibrating;}
} radio_driver;
namespace mesh {
 struct Status {bool reader_connected=true, stalled=false;};
 static Status status;
 Status usbLoggingStatus(){return status;}
}
struct Store {
 bool dirty=false;
 bool hasPendingContactWrites() const{return dirty;}
};
struct SerialInterface {
 bool pending=false, connected=false, passthrough=true;
 bool hasPendingIO() const{return pending;}
 bool isConnected() const{return connected;}
 bool isReplyRouteAvailable(const SerialInterface* route) const {
   return route==this&&connected;
 }
 bool isPassthroughMode() const{return passthrough;}
};
struct RoleMesh {
 uint32_t dirty_contacts_expiry=0, _ota_update_at=0, set_radio_at=0,
     _scheduled_reboot_at=0;
 bool outbound=false, temporary=false, ota_apply=false,
     saved_radio_apply_pending=false, temp_radio_handoff_pending=false,
     command_radio_apply_pending=false,
     _iter_started=false, _terminal_trace_pending=false, terminal=true;
 mesh::CompanionDelayedReplies _delayed_replies;
 void* sign_data=nullptr;
 Store store;
 Store* _store=&store;
 SerialInterface serial;
 SerialInterface* _serial=&serial;
 SerialInterface* _iter_reply_route=&serial;
 bool hasOutbound() const{return outbound;}
 bool isAnyTempRadioActive() const{return temporary;}
 bool hasPendingOtaApply() const{return ota_apply;}
 bool hasPendingReqs() const{return _delayed_replies.hasRequest();}
 bool isTerminalMode() const{return terminal;}
#if MESH_USB_CONSOLE_COOPERATIVE
 bool functional_output=false;
 bool hasPendingSerialOutput() const{return functional_output;}
#endif
 @ACCESSOR_DECLARATION@
} the_mesh;
@ACCESSOR_DEFINITION@
static char command[32]={};
static bool command_overflow=false;
#if defined(ENABLE_USB_INTERFACE)
static SerialInterface usb_serial_interface;
static bool usb_connected=false;
static bool usb_mota_mode=false;
static unsigned usb_terminal_line_len=0;
static bool usb_terminal_discard_line=false;
bool isUsbTerminalDataConnected(){return usb_connected;}
#endif
@CALLBACK@

void reset(){
 board=Board{};radio_driver=RadioDriver{};mesh::status=mesh::Status{};
 the_mesh=RoleMesh{};
 // Assignment copies the fixture's dependency pointers; point them back at
 // the live fixture objects, not the temporary used to reset scalar state.
 the_mesh._store=&the_mesh.store;the_mesh._serial=&the_mesh.serial;
 the_mesh._iter_reply_route=&the_mesh.serial;
 command[0]=0;command_overflow=false;
#if defined(ENABLE_USB_INTERFACE)
 usb_serial_interface=SerialInterface{};usb_connected=false;
 usb_mota_mode=false;usb_terminal_line_len=0;usb_terminal_discard_line=false;
#endif
}

int main(){
 reset();assert(the_mesh.canRecoverUsbLogging());
 assert(usbLoggingRecoverySafe(nullptr));
 // Each protection must survive a physically absent/stalled reader. That
 // exception is only for stale ASCII work, never radio, flash or protocol.
 for(unsigned state=0;state<3;++state){
   reset();mesh::status.reader_connected=state!=1;
   mesh::status.stalled=state==2;
   board.updating=true;assert(!usbLoggingRecoverySafe(nullptr));board.updating=false;
   board.testing=true;assert(!usbLoggingRecoverySafe(nullptr));board.testing=false;
   radio_driver.observing=true;assert(!usbLoggingRecoverySafe(nullptr));
   radio_driver.observing=false;
   radio_driver.calibrating=true;assert(!usbLoggingRecoverySafe(nullptr));
   radio_driver.calibrating=false;
   for(auto member : {&RoleMesh::outbound,&RoleMesh::temporary,&RoleMesh::ota_apply,
                      &RoleMesh::saved_radio_apply_pending}){
     the_mesh.*member=true;
     assert(!the_mesh.canRecoverUsbLogging());
     assert(!usbLoggingRecoverySafe(nullptr));
     the_mesh.*member=false;
   }
   the_mesh.dirty_contacts_expiry=1;
   assert(!the_mesh.canRecoverUsbLogging());assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.dirty_contacts_expiry=0;
   @ROLE_VETOES@
 }
 @COMMAND_VETOES@
 @COMPANION_VETOES@
 reset();assert(usbLoggingRecoverySafe(nullptr));
}
'''


class UsbLoggingWatchdogRoleTests(unittest.TestCase):
    def test_actual_ordinary_role_callbacks_with_and_without_native_console(self):
        for role, header, specific in (
            ('simple_repeater', 'MyMesh.h', '''
   the_mesh.temp_radio_handoff_pending=true;
   assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.temp_radio_handoff_pending=false;
   the_mesh._ota_update_at=1;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh._ota_update_at=0;
'''),
            ('simple_room_server', 'MyMesh.h', '''
   the_mesh.set_radio_at=1;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.set_radio_at=0;
   the_mesh._ota_update_at=1;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh._ota_update_at=0;
'''),
            ('simple_sensor', 'SensorMesh.h', '''
   the_mesh.set_radio_at=1;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.set_radio_at=0;
''')):
            directory = ROOT / 'examples' / role
            accessor = method((directory / header).read_text(),
                              'bool canRecoverUsbLogging() const')
            callback = method((directory / 'main.cpp').read_text(),
                              'static bool usbLoggingRecoverySafe(void*)')
            command_checks = '''
 reset();command[0]='x';assert(!usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=false;assert(usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=true;mesh::status.stalled=true;
 assert(usbLoggingRecoverySafe(nullptr));
 reset();
'''
            if role == 'simple_repeater':
                command_checks += '''
 command_overflow=true;assert(!usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=false;assert(usbLoggingRecoverySafe(nullptr));
 reset();
'''
            if role != 'simple_sensor':
                command_checks += '''
#if MESH_USB_CONSOLE_COOPERATIVE
 the_mesh.functional_output=true;assert(!usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=false;assert(usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=true;mesh::status.stalled=true;
 assert(usbLoggingRecoverySafe(nullptr));
 reset();
#endif
'''
            harness = self._harness(accessor, '', callback, specific,
                                    command_checks, '')
            for cooperative in (0, 1):
                with self.subTest(role=role, cooperative=cooperative):
                    self._compile_and_run(harness, cooperative=cooperative)

    def test_actual_companion_protocol_flash_radio_and_command_ownership(self):
        directory = ROOT / 'examples/companion_radio'
        accessor = method((directory / 'MyMesh.cpp').read_text(),
                          'bool MyMesh::canRecoverUsbLogging() const')
        accessor = accessor.replace('MyMesh::', 'RoleMesh::', 1)
        callback = method((directory / 'main.cpp').read_text(),
                          'static bool usbLoggingRecoverySafe(void*)')
        role_checks = r'''
   the_mesh.command_radio_apply_pending=true;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.command_radio_apply_pending=false;
   for(auto slot : {&the_mesh._delayed_replies.request, &the_mesh._delayed_replies.trace}){
     slot->phase=mesh::CompanionDelayedReplies::AwaitRadio;
     assert(!usbLoggingRecoverySafe(nullptr));
     slot->phase=mesh::CompanionDelayedReplies::AwaitAdmission;
     assert(!usbLoggingRecoverySafe(nullptr));
     slot->phase=mesh::CompanionDelayedReplies::Empty;
   }
   // Terminal TRACE owns only a history reservation, not a binary reply slot.
   // Its deadline still must be serviced before USB recovery can reset it.
   the_mesh._terminal_trace_pending=true;
#if COMPANION_FEATURE_TEXT_TERMINAL
   assert(!the_mesh.canRecoverUsbLogging());
   assert(!usbLoggingRecoverySafe(nullptr));
#else
   assert(the_mesh.canRecoverUsbLogging());
   assert(usbLoggingRecoverySafe(nullptr));
#endif
   the_mesh._terminal_trace_pending=false;
   the_mesh.store.dirty=true;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.store.dirty=false;
   the_mesh._scheduled_reboot_at=1;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh._scheduled_reboot_at=0;
   int route=1;
   the_mesh.sign_data=&route;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.sign_data=nullptr;
   the_mesh.serial.pending=true;assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.serial.pending=false;
   the_mesh._iter_started=true;the_mesh.serial.connected=true;
   assert(!usbLoggingRecoverySafe(nullptr));
   the_mesh.serial.connected=false;assert(usbLoggingRecoverySafe(nullptr));
   the_mesh._iter_started=false;
   the_mesh._serial=nullptr;assert(usbLoggingRecoverySafe(nullptr));
   the_mesh._serial=&the_mesh.serial;
'''
        companion_checks = r'''
#if defined(ENABLE_USB_INTERFACE)
 // A live binary client or buffered protocol transaction is always protected,
 // even if the dedicated logging CDC is disconnected or its output is stuck.
 for(unsigned state=0;state<3;++state){
   reset();mesh::status.reader_connected=state!=1;mesh::status.stalled=state==2;
   the_mesh.terminal=false;usb_serial_interface.passthrough=false;usb_connected=true;
   assert(!usbLoggingRecoverySafe(nullptr));
   usb_connected=false;assert(usbLoggingRecoverySafe(nullptr));
   usb_serial_interface.pending=true;assert(!usbLoggingRecoverySafe(nullptr));
   usb_serial_interface.pending=false;
#if COMPANION_FEATURE_USB_MOTA_SOURCE
   usb_mota_mode=true;assert(!usbLoggingRecoverySafe(nullptr));
   usb_mota_mode=false;
#endif
 }
 // A network CLI owns the role without owning a log-only USB transport.
 // The new network parking state must not make a stalled logger look like a
 // live Binary Companion, including a host which keeps DTR asserted.
 for(unsigned state=0;state<3;++state){
   reset();the_mesh.terminal=false;usb_connected=true;
   mesh::status.reader_connected=state!=1;mesh::status.stalled=state==2;
   assert(usb_serial_interface.isPassthroughMode());
   assert(usbLoggingRecoverySafe(nullptr));
 }
 reset();usb_terminal_line_len=1;assert(!usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=false;assert(usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=true;mesh::status.stalled=true;
 assert(usbLoggingRecoverySafe(nullptr));
 reset();usb_terminal_discard_line=true;assert(!usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=false;assert(usbLoggingRecoverySafe(nullptr));
 mesh::status.reader_connected=true;mesh::status.stalled=true;
 assert(usbLoggingRecoverySafe(nullptr));
#endif
'''
        harness = self._harness('bool canRecoverUsbLogging() const;', accessor,
                                callback, role_checks, '', companion_checks)
        for usb, mota, terminal in product((0, 1), repeat=3):
            with self.subTest(usb=usb, mota=mota, terminal=terminal):
                self._compile_and_run(harness, cooperative=usb, usb=usb, mota=mota,
                                      terminal=terminal)

    def test_every_role_loads_durable_state_and_services_recovery_in_normal_loop(self):
        for role in ('companion_radio', 'simple_repeater', 'simple_room_server',
                     'simple_sensor'):
            with self.subTest(role=role):
                source = (ROOT / 'examples' / role / 'main.cpp').read_text()
                setup = method(source, 'void setup()')
                loop = method(source[source.rindex('\nvoid loop()'):], 'void loop()')
                self.assertIn('loadUsbLoggingWatchdog(', setup)
                self.assertTrue('!volatile_primary_fs' in setup or
                                '!store.isVolatilePrimaryFS()' in setup)
                self.assertEqual(setup.count('[]() -> uint32_t { return rtc_clock.getCurrentTime(); }'), 2)
                self.assertNotIn('getCurrentTimeUnique()', setup)
                self.assertIn('mesh::serviceUsbLoggingPort();', loop)
                self.assertIn('if (mesh::serviceUsbLoggingWatchdog(usbLoggingRecoverySafe)) '
                              'board.reboot();', loop)
                if role == 'companion_radio':
                    guard = method(loop, 'mesh::powerdiag::Mask sleep_reason = [&]()')
                    self.assertIn('if (mesh::isUsbLoggingWatchdogArmed()) '
                                  'return mask(Reason::UsbRecovery);', guard)
                    self.assertIn('const bool can_sleep = sleep_reason == 0;', loop)
                    self.assertLess(loop.index(guard), loop.index(
                        'if (can_sleep && the_mesh.canIdleDualRadio())'))
                else:
                    self.assertIn('!mesh::isUsbLoggingWatchdogArmed()', loop)

    @staticmethod
    def _harness(declaration, definition, callback, role, commands, companion):
        return (COMMON_HARNESS.replace('@ACCESSOR_DECLARATION@', declaration)
                .replace('@ACCESSOR_DEFINITION@', definition)
                .replace('@CALLBACK@', callback).replace('@ROLE_VETOES@', role)
                .replace('@COMMAND_VETOES@', commands)
                .replace('@COMPANION_VETOES@', companion))

    def _compile_and_run(self, harness, cooperative, usb=0, mota=0, terminal=0):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            cpp = work / 'roles.cpp'
            cpp.write_text(harness)
            binary = work / 'roles'
            flags = ['-DMESH_USB_CONSOLE_COOPERATIVE=' + str(cooperative),
                     '-DCOMPANION_FEATURE_USB_MOTA_SOURCE=' + str(mota),
                     '-DCOMPANION_FEATURE_TEXT_TERMINAL=' + str(terminal)]
            if usb:
                flags.append('-DENABLE_USB_INTERFACE=1')
            built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                '-fno-pie', '-no-pie', *flags,
                '-isystem', str(ROOT / "test/mocks"), f'-I{ROOT / "src"}',
                str(cpp), str(ROOT / 'src/helpers/CompanionDelayedReplies.cpp'),
                '-o', str(binary)],
                capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
