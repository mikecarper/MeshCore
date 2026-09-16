"""Execute Pico W's production queue predicates used by companion sleep control."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class PicoBleQueueTests(unittest.TestCase):
    def test_pending_frames_keep_the_transport_awake_below_high_water(self):
        source = (ROOT / 'src/helpers/rp2040/SerialBLEInterface.cpp').read_text()
        methods = '\n'.join(method(source, 'bool SerialBLEInterface::' + name + '() const')
                            for name in ('isReadBusy', 'isWriteBusy', 'hasPendingIO'))
        code = r'''
#include <cassert>
#define FRAME_QUEUE_SIZE 8
static int locks = 0;
struct BluetoothLock {
  BluetoothLock() { ++locks; }
  ~BluetoothLock() { --locks; }
};
struct SerialBLEInterface {
  unsigned recv_queue_len = 0, send_queue_len = 0;
  bool _tx_pending = false;
  bool isReadBusy() const;
  bool isWriteBusy() const;
  bool hasPendingIO() const;
};
@METHODS@
int main() {
  SerialBLEInterface transport;
  for (unsigned rx = 0; rx <= FRAME_QUEUE_SIZE; ++rx) {
    for (unsigned tx = 0; tx <= FRAME_QUEUE_SIZE; ++tx) {
      for (unsigned pending = 0; pending <= 1; ++pending) {
        transport.recv_queue_len = rx;
        transport.send_queue_len = tx;
        transport._tx_pending = pending;
        assert(transport.isReadBusy() == (rx != 0));
        assert(transport.isWriteBusy() == (tx >= 5));
        assert(transport.hasPendingIO() == (rx || tx || pending));
        assert(locks == 0);
      }
    }
  }
}
'''.replace('@METHODS@', methods)
        for name in ('isReadBusy', 'isWriteBusy', 'hasPendingIO'):
            self.assertIn('BluetoothLock lock;', method(source, 'bool SerialBLEInterface::' + name))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.cpp').write_text(code)
            subprocess.run(['g++', '-std=c++17', '-fsanitize=address,undefined',
                            '-fno-pie', '-no-pie', str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
