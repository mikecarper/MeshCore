"""Exercise explicit nRF52 UART routing without any native USB implementation."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'test/fixtures/nrf52_uart_console'

class Nrf52UartConsoleTest(unittest.TestCase):
    def test_actual_uart_transport_and_native_usb_exclusion(self):
        compiler=shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('a host C++17 compiler is required')
        sanitizer=[] if os.name=='nt' else ['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
        with tempfile.TemporaryDirectory(prefix='nrf52-uart-console-') as directory:
            binary=Path(directory)/'uart-console'
            main=(ROOT/'examples/companion_radio/main.cpp').read_text()
            branch=main.split('#elif MESH_PRIMARY_SERIAL_IS_UART\n  // No native DTR',1)[1].split('#elif defined(NRF52_PLATFORM)',1)[0]
            callback=branch[branch.index('  usb_serial_interface.setConnectedCheck'):]
            source=Path(directory)/'test_console.cpp'
            source.write_text((FIXTURE/'test_console.cpp').read_text().replace('@BINARY_CONNECTION@',callback))
            built=subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror',*sanitizer,
                '-DARDUINO','-DNRF52_PLATFORM','-DUSE_TINYUSB','-DENABLE_USB_INTERFACE',
                '-DMESH_DEBUG=1','-DCOMPANION_RADIO_FULL=1','-DMESH_PRIMARY_UART_PORT=Serial1',
                '-I'+str(FIXTURE/'mocks'),'-I'+str(ROOT/'src'),str(source),
                *[str(ROOT/'src/helpers'/file) for file in ('UsbLogging.cpp','UsbLoggingClientActivity.cpp','UsbLoggingLineStateOverride.cpp')],
                '-o',str(binary)],capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            tested=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(tested.returncode,0,tested.stdout+tested.stderr)
            self.assertIn('UART bridge console transport passed',tested.stdout)

if __name__=='__main__':
    unittest.main()
