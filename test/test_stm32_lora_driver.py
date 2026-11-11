#!/usr/bin/env python3
"""Compile actual compact LoRa driver bodies, preserving RX/TX/CW semantics."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class CompactLoRaDriverTests(unittest.TestCase):
 def test_packet_staging_errors_cw_and_feature_controls(self):
  with tempfile.TemporaryDirectory(prefix='stm32-lora-driver-') as temp:
   work=Path(temp)
   for compact,morse_rx in ((0,0),(1,0),(1,1)):
    with self.subTest(compact=compact,morse_rx=morse_rx):
     binary=work/f'case{compact}{morse_rx}'
     build=subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-pedantic',
       '-fsanitize=address,undefined','-fno-pie','-no-pie','-DMESH_COMPACT_STM32_RADIO='+str(compact),
       '-DMESH_ENABLE_MORSE_RX='+str(morse_rx),'-I',str(ROOT/'src'),str(ROOT/'test/fixtures/stm32_lora_driver/main.cpp'),
       '-o',str(binary)],capture_output=True,text=True,timeout=30)
     self.assertEqual(build.returncode,0,build.stdout+build.stderr)
     for case in ('controls','rx','tx','faults','cw','airtime','blocking','blocking_faults'):
      run=subprocess.run([str(binary),case],capture_output=True,text=True,timeout=10)
      self.assertEqual(run.returncode,0,case+run.stdout+run.stderr)
if __name__=='__main__':unittest.main()
