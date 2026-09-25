"""Host regression for the RAK internal/QSPI store selection boundary."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RakStoragePolicyTest(unittest.TestCase):
    def test_exact_bootloader_and_nor_pairing(self):
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("C++ compiler unavailable")
        source = r'''
#include "src/helpers/ota/OtaRakStoragePolicy.h"
#include <assert.h>
using mesh::ota::RakStorageChoice;
using mesh::ota::rak_storage_choice;
int main() {
  const auto I = RakStorageChoice::Internal;
  const auto Q = RakStorageChoice::Qspi;
  const auto U = RakStorageChoice::Unsafe;
  for (bool rak3401 : {false, true}) {
    assert(rak_storage_choice(0, false, false, nullptr, rak3401) == I);
    assert(rak_storage_choice(0, true, false, nullptr, rak3401) == U);
    assert(rak_storage_choice(0, true, true,
        rak3401 ? "3401_AUTO_DFU" : "4631_AUTO_DFU", rak3401) == I);
    assert(rak_storage_choice(2, false, false, nullptr, rak3401) == I);
    assert(rak_storage_choice(3, false, false, nullptr, rak3401) == U);
    assert(rak_storage_choice(3, true, true, "3401_W25Q16_DFU", rak3401) == U);
    assert(rak_storage_choice(4, true, true, "3401_W25Q16_DFU", rak3401) == U);
    assert(rak_storage_choice(2, true, false, nullptr, rak3401) == U);
    assert(rak_storage_choice(2, true, true, "4631_15001C_DFU", rak3401) == U);
  }
  assert(rak_storage_choice(1, false, false, nullptr, false) == I);
  assert(rak_storage_choice(1, true, true, "4631_15001C_DFU", false) == Q);
  assert(rak_storage_choice(2, true, true, "4631_W25Q16_DFU", false) == Q);
  assert(rak_storage_choice(1, true, true, "4631_AUTO_DFU", false) == Q);
  assert(rak_storage_choice(2, true, true, "4631_AUTO_DFU", false) == Q);
  assert(rak_storage_choice(2, true, true, "3401_AUTO_DFU", false) == U);
  assert(rak_storage_choice(1, true, true, "4631_W25Q16_DFU", false) == U);
  assert(rak_storage_choice(1, true, true, "4631_15001C_DFU", true) == U);
  assert(rak_storage_choice(2, true, true, "3401_W25Q16_DFU", true) == Q);
  assert(rak_storage_choice(2, true, true, "3401_AUTO_DFU", true) == Q);
  assert(rak_storage_choice(1, true, true, "3401_AUTO_DFU", true) == U);
  assert(rak_storage_choice(2, true, true, "4631_W25Q16_DFU", true) == U);
}
'''
        source = '#include <initializer_list>\n' + source
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "policy.cpp"
            exe = Path(directory) / "policy"
            src.write_text(source)
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT), str(src), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
