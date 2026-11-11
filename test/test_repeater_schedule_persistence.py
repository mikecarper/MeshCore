"""Run permanent schedules/GPS capacity through production checked storage."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_repeater_radio_timing_integration import HARNESS as TIMING, method

ROOT = Path(__file__).resolve().parents[1]

STORE_TEST = r'''
struct Row {
 uint64_t hard_end_uptime_millis=0;float freq=910.5,bw=250;
 uint32_t start_time=1800000060,end_time=0;uint16_t preamble=32;
 uint8_t sf=7,cr=5;bool active=false,temporary=false,started=false;
};
using Store=mesh::RepeaterScheduleStore<Row,3>;
int main(){
 MemoryFS fs;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
 fs.rename_replaces=true;
#endif
 Row rows[6];Store store;assert(store.begin(&fs,rows)&&store.days()==7&&fs.files.empty());
 rows[4].active=true;rows[1].active=rows[1].temporary=true;
 rows[1].start_time=1800000010;
 assert(store.save(rows,6,21));const auto good=fs.files.at("/radioat");
 assert(good.size()==58&&good[4]==21&&good[5]==1);
 Row reboot[6];Store restored;assert(restored.begin(&fs,reboot)&&restored.days()==21);
 assert(reboot[0].active&&!reboot[0].temporary&&reboot[0].start_time==1800000060);
 assert(reboot[0].freq==910.5f&&reboot[0].preamble==32&&!reboot[1].active);
 // A failed write cannot change the old capacity/queue or claim success.
 fs.fail_write=true;assert(!store.save(rows,6,30)&&store.days()==21);
 assert(fs.files.at("/radioat")==good);fs.fail_write=false;
 // Durable fleet addition/deletion can be staged without changing live rows.
 Row candidate;candidate.active=true;candidate.start_time=1800000090;
 assert(store.save(rows,6,21,3,&candidate));assert(!rows[3].active);
 Row staged[6];Store accepted;assert(accepted.begin(&fs,staged)&&staged[1].active);
 uint8_t deleted[]={uint8_t(1U<<4)};
 assert(store.save(rows,6,21,-1,nullptr,deleted));
 Row erased[6];Store cleared;assert(cleared.begin(&fs,erased)&&!erased[0].active);
 // CRC, length, version and semantic corruption never arm an unknown tuple.
 for(size_t bit=0;bit<good.size()*8;++bit){
   fs.files["/radioat"]=good;fs.files["/radioat"][bit/8]^=uint8_t(1U<<(bit%8));
   Row invalid[6];Store bad;assert(!bad.begin(&fs,invalid)&&!invalid[0].active);
   assert(bad.ready()&&bad.days()==7);
 }
 for(size_t size:{size_t(0),good.size()-1,good.size()+1}){
   fs.files["/radioat"]=good;fs.files["/radioat"].resize(size);
   Row invalid[6];Store bad;assert(!bad.begin(&fs,invalid)&&!invalid[0].active);
 }
 for(unsigned offset:{4U,5U,20U,21U}){
   fs.files["/radioat"]=good;auto& image=fs.files["/radioat"];
   image[offset]=255;
   mesh::storage::writeLE32(image.data()+54,~mesh::storage::updateCRC32(0xffffffff,image.data(),54));
   Row invalid[6];Store bad;assert(!bad.begin(&fs,invalid)&&!invalid[0].active);
 }
 fs.files["/radioat"]=good;fs.fail_read_open=true;
 Row unreadable[6];Store bad;assert(!bad.begin(&fs,unreadable));fs.fail_read_open=false;
 assert(!bad.ready()&&!bad.save(rows,6,7));
 // Complete corruption permits explicit repair, without an automatic rewrite.
 fs.files["/radioat"]={1,2,3};const auto corrupt=fs.files.at("/radioat");
 Row repair_rows[6];Store repair;assert(!repair.begin(&fs,repair_rows)&&repair.ready());
 assert(fs.files.at("/radioat")==corrupt&&!repair_rows[0].active);
 assert(repair.save(repair_rows,6,9));
 Store repaired;Row repaired_rows[6];assert(repaired.begin(&fs,repaired_rows)&&repaired.days()==9);
 Store unavailable;assert(!unavailable.begin(nullptr,repair_rows)&&!unavailable.save(rows,6,7));
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
 fs.files["/radioat.bak"]=good;fs.files.erase("/radioat");
 Store recovered;Row recovered_rows[6];assert(recovered.begin(&fs,recovered_rows));
 assert(recovered_rows[0].active&&recovered.days()==21);
#endif
}
'''

SCHEDULER_TEST = r'''
int main(){
 MemoryFS disk;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
 disk.rename_replaces=true;
#endif
 MyMesh node;node.rtc.now=1800000000;char reply[160];
 assert(node.scheduled_radio_store.begin(&disk,node.scheduled_radio_settings));
 node.gps("set telemetry.gps 21",reply);
 assert(!strncmp(reply,"OK",2)&&node.gps_resizes==1&&node.scheduled_radio_store.days()==21);
 disk.fail_write=true;node.gps("set telemetry.gps 12",reply);disk.fail_write=false;
 assert(strstr(reply,"unconfirmed")&&node.gps_resizes==1&&node.scheduled_radio_store.days()==21);
 node.addScheduledRadioParams(false,910.5,250,7,5,1800000060,0,reply,32);
 assert(!strncmp(reply,"OK",2));const auto saved=disk.files.at("/radioat");
 // A cold reset retains the pending absolute date, never a temporary window.
 node.addScheduledRadioParams(true,911,250,7,5,1800000090,1800000120,reply,32);
 assert(!strncmp(reply,"OK",2)&&disk.files.at("/radioat")==saved);
 MyMesh boot;assert(boot.scheduled_radio_store.begin(&disk,boot.scheduled_radio_settings));
 assert(boot.scheduled_radio_store.days()==21); // Requested value survives even if allocation was smaller.
 boot.rtc.now=1800000100;boot.schedule_clock_ready=false;boot.refreshScheduledRadioState();
 boot.processScheduledRadioSettings();assert(boot.saves==0&&boot.scheduled_radio_settings[0].active);
 boot.schedule_clock_ready=true;boot.advance(61);boot.processScheduledRadioSettings();
 assert(boot.saves==1&&!boot.scheduled_radio_settings[0].active);
 MyMesh completed;assert(completed.scheduled_radio_store.begin(&disk,completed.scheduled_radio_settings));
 assert(!completed.scheduled_radio_settings[0].active);
 // Failed add and delete both leave the acknowledged queue unchanged.
 node.rtc.now=1800000000;disk.fail_write=true;
 node.addScheduledRadioParams(false,912,250,7,5,1800000100,0,reply,32);
 assert(!strncmp(reply,"Error",5)&&node.countScheduledRadioSettings(false)==1);
 node.deleteScheduledRadioParams(false,"all",reply);
 assert(!strncmp(reply,"Error",5)&&node.countScheduledRadioSettings(false)==1);
 disk.fail_write=false;node.deleteScheduledRadioParams(false,"1",reply);
 assert(!strcmp(reply,"OK")&&node.countScheduledRadioSettings(false)==0);
 MyMesh deleted;assert(deleted.scheduled_radio_store.begin(&disk,deleted.scheduled_radio_settings));
 assert(!deleted.scheduled_radio_settings[0].active);
 // Admission rejects an untrusted software-clock seed rather than persisting
 // an epoch which will fire prematurely after the next clock correction.
 node.schedule_clock_ready=false;
 node.addScheduledRadioParams(false,912,250,7,5,1800000200,0,reply,32);
 assert(strstr(reply,"clock")&&!node.countScheduledRadioSettings(false));
}
'''

FLEET_TEST = r'''
int main(){
 MemoryFS disk;MyMesh node;char reply[160];node.rtc.now=1800000000;
 assert(node.scheduled_radio_store.begin(&disk,node.scheduled_radio_settings));
 node.fleet_command_=true;
 node.addScheduledRadioParams(false,910.5,250,7,5,1800000060,0,reply,32);
 node.fleet_command_=false;
 assert(!strncmp(reply,"OK",2)&&node.fleet_schedule_pending_);
 assert(node.countScheduledRadioSettings(false)==0); // Live apply still waits for ACK.
 MyMesh power_failure;
 assert(power_failure.scheduled_radio_store.begin(&disk,power_failure.scheduled_radio_settings));
 assert(power_failure.countScheduledRadioSettings(false)==1); // Durable before acknowledgement.
 node.advance(61);node.processScheduledRadioSettings();assert(node.saves==0);
 node.finishFleetReplyMutation(true);node.processScheduledRadioSettings();
 assert(node.saves==1&&!node.countScheduledRadioSettings(false));
 // A failed save cannot acknowledge or publish a new candidate.
 node.fleet_command_=true;disk.fail_write=true;
 node.addScheduledRadioParams(false,911.5,250,7,5,1800000200,0,reply,32);
 assert(strstr(reply,"unconfirmed")&&!node.fleet_schedule_pending_);
 disk.fail_write=false;
 node.addScheduledRadioParams(false,911.5,250,7,5,1800000200,0,reply,32);
 node.fleet_command_=false;assert(!strncmp(reply,"OK",2));
 // Failed physical ACK leaves the runtime queue intact and durably cancels
 // the candidate from the main loop; failed rollback is retained for retry.
 node.finishFleetReplyMutation(false);disk.fail_write=true;
 node.processScheduledRadioSettings();assert(node.fleet_schedule_pending_&&node.saves==1);
 disk.fail_write=false;node.advance(61);node.processScheduledRadioSettings();
 assert(!node.fleet_schedule_pending_&&!node.countScheduledRadioSettings(false));
 MyMesh cancelled;assert(cancelled.scheduled_radio_store.begin(&disk,cancelled.scheduled_radio_settings));
 assert(!cancelled.countScheduledRadioSettings(false));
 // Deletion is also durable before ACK, then rolls back when ACK fails.
 node.addScheduledRadioParams(false,912.5,250,7,5,1800000300,0,reply,32);
 node.fleet_command_=true;node.deleteScheduledRadioParams(false,"all",reply);node.fleet_command_=false;
 assert(!strncmp(reply,"OK",2)&&node.countScheduledRadioSettings(false)==1);
 MyMesh pending_delete;assert(pending_delete.scheduled_radio_store.begin(&disk,pending_delete.scheduled_radio_settings));
 assert(!pending_delete.countScheduledRadioSettings(false));
 node.finishFleetReplyMutation(false);node.processScheduledRadioSettings();
 MyMesh rollback;assert(rollback.scheduled_radio_store.begin(&disk,rollback.scheduled_radio_settings));
 assert(rollback.countScheduledRadioSettings(false)==1);
}
'''


class RepeaterSchedulePersistenceTests(unittest.TestCase):
    def prepare(self, work):
        (work / "helpers").mkdir()
        mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
        (work / "helpers/IdentityStore.h").write_text(mock)
        for name in ("RepeaterScheduleStore.h", "ContactFileTransaction.h", "CheckedSettingsFile.h"):
            text = (ROOT / "src/helpers" / name).read_text()
            for header in ("IdentityStore.h", "PersistentStoreFormat.h", "AtomicFileWriter.h", "ContactFileTransaction.h", "CheckedSettingsFile.h", "FilePresence.h"):
                text = text.replace(f'#include "{header}"', f'#include <helpers/{header}>')
            (work / "helpers" / name).write_text(text)
        (work / "helpers/FilePresence.h").write_text('''#pragma once
#include <helpers/IdentityStore.h>
namespace mesh {template<typename FS> bool filePresence(FS* fs,const char* path,bool& present){
 if(!fs)return false;present=fs->exists(path);return true;}}
''')

    def build_run(self, work, source, platform):
        path, binary = work / "test.cpp", work / platform
        path.write_text('#include <cassert>\n#include <helpers/IdentityStore.h>\n'
            '#define ATOMIC_FILE_WRITER_IMPLEMENTATION\n#include <helpers/RepeaterScheduleStore.h>\n' + source)
        result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-O1",
            "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare", "-Wno-unused-parameter",
            "-Wno-misleading-indentation", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-pie", "-no-pie", "-D" + platform, "-I" + str(work), "-I" + str(ROOT / "src"),
            str(path), "-o", str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_saved_record_reboot_corruption_and_io_failures(self):
        with tempfile.TemporaryDirectory(prefix="repeater-schedule-store-") as tmp:
            work = Path(tmp);self.prepare(work)
            for platform in ("ESP32_PLATFORM", "RP2040_PLATFORM", "NRF52_PLATFORM", "STM32_PLATFORM"):
                with self.subTest(platform=platform):
                    self.build_run(work, STORE_TEST, platform)

    def runtime_source(self):
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        prefix = TIMING[:TIMING.index('static constexpr uint32_t HOUR')]
        stub = method(prefix, '  bool saveScheduledRadioSettings(')
        prefix = prefix.replace(stub, '  bool saveScheduledRadioSettings(int = -1, const ScheduledRadioSetting* = nullptr, const uint8_t* = nullptr);')
        prefix = prefix.replace('  bool schedule_save_ok = true, schedule_clock_ready = true;',
            '  mesh::RepeaterScheduleStore<ScheduledRadioSetting,MAX_SCHEDULED_RADIO_SETTINGS_PER_TYPE> scheduled_radio_store;\n'
            '  bool schedule_save_ok = true, schedule_clock_ready = true;')
        prefix = prefix.replace('  void servicePostMeshLoop();', '''
  int findScheduledRadioSettingByIndex(bool,int) const;
  void deleteScheduledRadioParams(bool,const char*,char*);
  void servicePostMeshLoop();''')
        prefix = prefix.replace('  MyMesh() {', '''
  ScheduledRadioSetting fleet_schedule_setting_{};
  uint8_t fleet_schedule_delete_mask_[(MAX_SCHEDULED_RADIO_SETTINGS+7)/8]{};
  int fleet_schedule_slot_=-1;
  bool fleet_command_=false,fleet_schedule_pending_=false,fleet_schedule_delivered_=false;
  bool fleet_schedule_delete_all_temp_=false,fleet_schedule_persisted_=false,fleet_schedule_cancelled_=false;
  void finishFleetReplyMutation(bool);
  void serviceFleetScheduleReply();
  MyMesh() {''')
        gps_branch = method(source, '  if (strncmp(command, telemetry_gps_set_command,')
        gps_members = '''
  unsigned gps_resizes=0;
  struct {unsigned gpsPageCount()const{return 2;}} telemetry_history;
  uint8_t resizeTelemetryGpsDays(uint8_t days){++gps_resizes;return days>14?14:days;}
  void gps(const char* command,char* reply){
    const char telemetry_gps_set_command[]="set telemetry.gps";
    struct Client {bool isAdmin()const{return true;}};Client* sender=nullptr;
''' + gps_branch + '\n  }\n'
        prefix = prefix.replace('  MyMesh() {', gps_members + '  MyMesh() {')
        parser = method(source, 'static bool parseTelemetryGpsDays(')
        parser = parser.replace('mesh::TelemetryHistory::GPS_MAX_RETENTION_DAYS', '30')
        prefix = parser + '\n' + prefix
        signatures = [
            'static const char* skipLocalSpaces(', 'static bool selectorIsEmpty(',
            'static bool selectorIsAll(', 'static bool parsePositiveSelector(',
            'void MyMesh::checkRxInactivityWatchdog()', 'void MyMesh::setTempRadioTiming(',
            'void MyMesh::updateAdvertTimer()', 'void MyMesh::updateFloodAdvertTimer()',
            'bool MyMesh::radio2FloodAdvertEligible() const', 'void MyMesh::queueSavedRadioApply()',
            'bool MyMesh::applySavedRadioParams()', 'void MyMesh::refreshScheduledRadioState()',
            'void MyMesh::processScheduledRadioSettings(bool tx_gap)', 'void MyMesh::applyTempRadioParams(',
            'bool MyMesh::scheduleNormalRadio()', 'bool MyMesh::isTempRadioPending() const',
            'void MyMesh::clearScheduledRadioSetting(', 'int MyMesh::findFreeScheduledRadioSlot() const',
            'int MyMesh::countScheduledRadioSettings(', 'bool MyMesh::scheduledRadioConflicts(',
            'void MyMesh::addScheduledRadioParams(', 'bool MyMesh::saveScheduledRadioSettings(',
            'int MyMesh::findScheduledRadioSettingByIndex(', 'void MyMesh::deleteScheduledRadioParams(',
            'void MyMesh::finishFleetReplyMutation(', 'void MyMesh::serviceFleetScheduleReply(']
        methods = '\n'.join(method(source, signature) for signature in signatures)
        # Match production's feature boundary around the two extracted methods.
        start = methods.index('void MyMesh::finishFleetReplyMutation(')
        methods = methods[:start] + '#if MESH_ENABLE_FLEET_CONTROL\n' + methods[start:] + '\n#endif\n'
        callback = method((ROOT / "examples/simple_repeater/MyMesh.h").read_text(),
                          'void onRadio2FloodAdvertChanged(').replace(' override', '')
        prefix = prefix.replace('@ADVERT_CALLBACK@', callback).replace('@METHODS@', methods)
        return prefix

    def test_production_scheduler_persists_mutations_and_waits_for_clock(self):
        with tempfile.TemporaryDirectory(prefix="repeater-schedule-runtime-") as tmp:
            work = Path(tmp);self.prepare(work)
            self.build_run(work, self.runtime_source() + SCHEDULER_TEST, "RP2040_PLATFORM")

    def test_fleet_acknowledgements_have_durable_queue_and_failed_ack_rolls_back(self):
        with tempfile.TemporaryDirectory(prefix="repeater-schedule-fleet-") as tmp:
            work = Path(tmp);self.prepare(work)
            self.build_run(work, '#define MESH_ENABLE_FLEET_CONTROL 1\n'
                           + self.runtime_source() + FLEET_TEST, "RP2040_PLATFORM")


if __name__ == "__main__":
    unittest.main()
