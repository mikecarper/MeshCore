#!/usr/bin/env python3
"""Run actual Companion AGC routing and shared prefs/runtime parsers on host."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_replay_reset_integration import extract_braced
from test_warm_rx_refresh import lab_timer_fields, lab_timer_methods

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'test/fixtures/companion_agc_bridge/main.cpp'

class CompanionAGCBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='companion-agc-bridge-')
        cls.work=Path(cls.temporary.name)
        companion=(ROOT/'examples/companion_radio/MyMesh.cpp').read_text(encoding='ascii')
        dispatcher=(ROOT/'src/Dispatcher.h').read_text(encoding='ascii')
        source=FIXTURE.read_text(encoding='ascii')
        wrapper=(ROOT/'src/helpers/radiolib/RadioLibWrappers.h').read_text(encoding='ascii')
        source=source.replace('@LAB_RADIO_FIELDS@',lab_timer_fields(wrapper))
        source=source.replace('@LAB_RADIO_METHODS@',lab_timer_methods(wrapper))
        source=source.replace('@ALLOWLIST@',extract_braced(companion,'static bool isCompanionRadioPrefsCommand('))
        source=source.replace('@PREFIX@',extract_braced(companion,'if (strlen(command) > 3 && command[2]'))
        start=companion.index('  if (mesh::handleStoredOldAGCCommand(',companion.index('bool MyMesh::handleCommand('))
        end=companion.index('return true;',companion.index('  if (mesh::handleAGCMaintenanceCommand(',start))+len('return true;')
        source=source.replace('@MAINTENANCE@',companion[start:end])
        source=source.replace('@TRANSACTION@',extract_braced(companion,'if (isCompanionRadioPrefsCommand(command))'))
        (cls.work/'main.cpp').write_text(source,encoding='ascii')
        methods=[]
        for signature in ('virtual bool supportsWarmRxRefresh(', 'virtual bool supportsOldAGC(', 'virtual bool setOldAGCInterval(',
                          'virtual uint32_t getOldAGCInterval(', 'virtual bool formatWarmRxRefresh('):
            methods.append(extract_braced(dispatcher,signature))
        methods.append(lab_timer_methods(dispatcher,virtual=True))
        methods.append('#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS\n'+extract_braced(dispatcher,'virtual bool formatAGCTimingDiagnostics(')+'\n#endif')
        (cls.work/'Dispatcher.h').write_text('#pragma once\n#include <stdint.h>\n#include <stddef.h>\nnamespace mesh { struct Radio { virtual ~Radio() {}\n'+'\n'.join(methods)+'\n}; }\n',encoding='ascii')
        (cls.work/'Utils.h').write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''',encoding='ascii')
        cls.binaries={}
        for diagnostics,lab in ((0,0),(1,0),(0,1),(1,1)):
            binary=cls.work/('bridge-'+str(diagnostics)+'-lab'+str(lab))
            sources=['ConfigSerializer.cpp','DynamicConfigSerializer.cpp','CommonRadioPrefs.cpp',
                     'AGCSettings.cpp','radiolib/RXPowerSaving.cpp','radiolib/RXPowerSavingCLI.cpp','TxtDataHelpers.cpp']
            command=['g++','-std=c++17','-DNRF52_PLATFORM=1',
                     '-DMESH_ENABLE_OLD_AGC=1','-DMESH_AGC_TIMING_DIAGNOSTICS='+str(diagnostics),
                     '-DMESH_WARM_RX_LAB_TIMER='+str(lab),
                     '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
                     '-I',str(cls.work),'-I',str(ROOT/'test/fixtures/radio_profiles/mocks'),
                     '-I',str(ROOT/'test/mocks'),'-I',str(ROOT/'src'),'-I',str(ROOT),str(cls.work/'main.cpp'),
                     *(str(ROOT/'src/helpers'/name) for name in sources),'-o',str(binary)]
            built=subprocess.run(command,capture_output=True,text=True)
            if built.returncode:raise AssertionError(built.stdout+built.stderr)
            cls.binaries[diagnostics,lab]=binary

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self,name):
        for (diagnostics,lab),binary in self.binaries.items():
            with self.subTest(diagnostics=diagnostics,lab=lab):
                env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:halt_on_error=1')
                result=subprocess.run([str(binary),name],capture_output=True,text=True,env=env)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_real_saved_aliases_and_loaded_value_units(self):self.run_scenario('aliases')
    def test_real_dirty_tracking_invalid_inputs_and_save_failure_rollback(self):self.run_scenario('transactions')
    def test_old_timer_saved_separately_bounded_and_unsupported_safe(self):self.run_scenario('runtime')
    def test_passive_diagnostic_aliases_flag_and_prefixed_buffer_bounds(self):self.run_scenario('diagnostics')
    def test_lab_override_strict_parser_default_off_zero_and_no_persistence(self):self.run_scenario('lab')

if __name__=='__main__':unittest.main()
