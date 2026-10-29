"""Run production nRF BLE lifecycle methods across delayed callbacks and USB."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_t096_full_memory import method

ROOT=Path(__file__).resolve().parents[1]


class NrfBleUsbLifecycleTests(unittest.TestCase):
    def test_actual_lifecycle_rejects_invalid_epochs_and_recovers_without_new_state(self):
        source=(ROOT/'src/helpers/nrf52/SerialBLEInterface.cpp').read_text()
        signatures=('void SerialBLEInterface::clearBuffers()', 'void SerialBLEInterface::resetTxState()',
            'bool SerialBLEInterface::servicePendingTxReset()', 'void SerialBLEInterface::enable()',
            'void SerialBLEInterface::disable()', 'void SerialBLEInterface::disconnect()',
            'void SerialBLEInterface::onConnect(', 'void SerialBLEInterface::onSecured(',
            'void SerialBLEInterface::onDisconnect(', 'bool SerialBLEInterface::isValidConnection(',
            'bool SerialBLEInterface::isConnected()', 'void SerialBLEInterface::recoverStalledTx(',
            'void SerialBLEInterface::serviceTxRecovery(', 'void SerialBLEInterface::loop()',
            'bool SerialBLEInterface::advertisingAllowed()', 'bool SerialBLEInterface::startAdvertising(',
            'bool SerialBLEInterface::isAdvertising()')
        actual='\n'.join(method(source,signature) for signature in signatures)
        harness=(ROOT/'test/fixtures/nrf52_ble_usb_lifecycle.cpp').read_text().replace('@METHODS@',actual)
        flags=[] if os.name=='nt' else ['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
        with tempfile.TemporaryDirectory(prefix='mesh-ble-usb-lifecycle-') as temp:
            cpp=Path(temp)/'model.cpp';binary=Path(temp)/'model';cpp.write_text(harness)
            built=subprocess.run(['c++','-std=c++17',str(cpp),'-I'+str(ROOT/'src'),*flags,'-o',str(binary)],
                                 capture_output=True,text=True,timeout=30)
            self.assertEqual(built.returncode,0,built.stderr)
            ran=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)


if __name__=='__main__':unittest.main()
