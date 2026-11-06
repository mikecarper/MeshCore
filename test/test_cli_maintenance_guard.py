#!/usr/bin/env python3
"""Run the real shared CLI lease gate and cached statistics with a radio spy."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

class MaintenanceGuard(unittest.TestCase):
    def test_actual_gates_cached_diagnostics_prefixes_and_unsafe_commands(self):
        common = (ROOT/'src/helpers/CommonCLI.cpp').read_text()
        start = common.index('    const auto* maintenance_radio=')
        common_gate = common[start:common.index('\n#endif', start)].replace(') return;', ') return true;')
        companion = (ROOT/'examples/companion_radio/MyMesh.cpp').read_text()
        start = companion.index('  if (command == NULL || reply == NULL) return false;', companion.index('bool MyMesh::handleCommand('))
        end = companion.index('#if defined(ESP32_PLATFORM)\n  // Catch remote', start)
        companion_gate = companion[start:end]
        stats = (ROOT/'src/helpers/StatsFormatHelper.h').read_text()
        stat_methods = '\n'.join('template<typename RadioDriverType>\n' + body(stats, signature) for signature in (
            'static void formatRadioStats(', 'static void formatRadioDiag(', 'static void formatPacketStats('))
        code = r'''
#include <cassert>
#include <cstring>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <helpers/RadioMaintenanceCLI.h>
#define MESH_ENABLE_FLEET_CONTROL 1
#define COMPANION_FEATURE_DIRECT_OTA_ADMIN 0
namespace StrHelper { static void ftoaFixed(char* b, size_t n, float v, int p) { snprintf(b,n,"%.*f",p,v); } }
namespace mesh {
struct Radio { bool pending=true; unsigned hardware_reads=0, mutations=0;
 bool isAGCMaintenancePending() const { return pending; }
 int getNoiseFloor() const { return -110; }
 uint8_t getRadioState() const { return 1; }
 unsigned long getLastRecvMillis() const { return 1500; }
};
struct MillisecondClock { unsigned long getMillis() { return 2500; } };
}
struct Driver { float getLastSNR() { return 2.25; } float getLastRSSI() { return -119; }
 unsigned getPacketsRecv() { return 7; } unsigned getPacketsSent() { return 3; }
 unsigned getPacketsRecvErrors() { return 2; }
};
struct Stats { @STATS@ };
struct Callbacks { mesh::Radio* radio; mesh::Radio* getProfileRadio() { return radio; } };
struct CommonCLI {
 Callbacks* _callbacks;
 bool rejected(const char* command, char* reply) {
 @COMMON@
 return false;
 }
};
struct Board { const char* getManufacturerName() { return "heltec_v4"; } } board;
struct MyMesh {
 mesh::Radio* _radio;
 bool rejected(const char* command, uint32_t sender_timestamp, char* reply) {
 (void)sender_timestamp;
 @COMPANION@
 return false;
 }
};
int main() {
 mesh::Radio radio; Callbacks callbacks{&radio}; CommonCLI common{&callbacks}; MyMesh companion{&radio};
 char reply[160];
 for (const char* command : {"ver", "stats-radio", "stats-radio-diag", "stats-packets", "neighbors", "get agc.timing", "get agc.scan", "get agc.old", "get radio", "get radio.fast"}) {
  strcpy(reply,"canary"); assert(!common.rejected(command,reply)); assert(!strcmp(reply,"canary"));
  assert(!companion.rejected(command,0,reply));
 }
 for (const char* command : {"stats-core", "board", "get battery", "get gps", "get radio.register", "get gpio.1", "get future.hardware", "set radio.fast off", "set agc 0", "reboot", "advert.zerohop", "get agc.timing extra"}) {
  assert(common.rejected(command,reply)); assert(strstr(reply,"radio maintenance pending"));
  assert(companion.rejected(command,0,reply)); assert(strstr(reply,"radio maintenance pending"));
  assert(radio.hardware_reads==0 && radio.mutations==0);
 }
 assert(!companion.rejected("ab|get agc.timing",0,reply));
 assert(!companion.rejected("ab|@heltec_v4/c stats-radio-diag",0,reply));
 assert(companion.rejected("ab|set radio.fast off",0,reply));
 assert(!strncmp(reply,"ab|Error: radio maintenance pending",34));
 assert(companion.rejected("@different/c set radio.fast off",0,reply)); // selector consumes skip before mutation
 assert(!strcmp(reply,"OK - skipped (hardware/role)"));
 assert(mesh::cli::maintenanceReadAllowed("ab|@heltec_v4/c get agc.status"));
 assert(mesh::cli::deferForRadioMaintenance(true,"ab|reboot",reply,sizeof(reply)));
 assert(!strncmp(reply,"ab|Error:",9));
 assert(!mesh::cli::maintenanceReadAllowed("get agc.status.fake"));
 assert(!mesh::cli::maintenanceReadAllowed("get agc.scan.fake"));
 Driver driver; mesh::MillisecondClock clock;
 Stats::formatRadioStats(reply,&radio,driver,2000,3000); assert(strstr(reply,"\"last_snr\":2.25"));
 Stats::formatRadioDiag(reply,&radio,driver,clock,8,false); assert(strstr(reply,"recv=7, sent=3, errors=2, err_flags=8"));
 Stats::formatPacketStats(reply,driver,1,2,3,4); assert(strstr(reply,"\"recv_errors\":2"));
 assert(radio.hardware_reads==0 && radio.mutations==0);
 radio.pending=false;
 assert(!common.rejected("set radio.fast off",reply)); assert(!companion.rejected("get battery",0,reply));
 assert(!mesh::cli::maintenanceReadAllowed(nullptr));
}
'''.replace('@COMMON@',common_gate).replace('@COMPANION@',companion_gate).replace('@STATS@',stat_methods)
        with tempfile.TemporaryDirectory(prefix='meshcore-cli-maintenance-') as folder:
            source=Path(folder)/'test.cpp'; binary=Path(folder)/'test'; source.write_text(code,encoding='ascii')
            result=subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I',str(ROOT/'src'),str(source),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_all_companion_text_entry_points_use_shared_gate_before_handlers(self):
        source=(ROOT/'examples/companion_radio/MyMesh.cpp').read_text()
        for signature in ('bool MyMesh::handleDirectCommand(', 'bool MyMesh::handleLocalControlCommand(', 'void MyMesh::handleTerminalCommand('):
            method=body(source,signature)
            self.assertIn('deferForRadioMaintenance(',method)
            self.assertLess(method.index('deferForRadioMaintenance('),method.index('#if'))
        source=(ROOT/'src/helpers/CommonCLI.cpp').read_text()
        method=body(source,'void CommonCLI::handleCommand(')
        self.assertLess(method.index('deferForRadioMaintenance('),method.index('handleRxpsPulseLabCommand('))

if __name__=='__main__': unittest.main()
