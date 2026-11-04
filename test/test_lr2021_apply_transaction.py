"""Exercise the production LR2021 calibration transaction and diagnostics."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(source, signature):
    start = source.index(signature)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class LR2021ApplyTransactionTest(unittest.TestCase):
    def test_actual_apply_params_reports_each_native_failure(self):
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        apply = method(source, "bool applyParams(").replace(" override", "")
        receive = method(source, "int startReceiveMode(").replace(" override", "")
        code = r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/LR2021ApplyStatus.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
#define LORA_TX_POWER 5
namespace mesh {int8_t clampLoRaTxPower(int8_t power,float){return power;}}
struct PacketMillis {uint32_t preambleMillis,payloadMillis;};
struct CustomLR2021 {
  int calls=0,fail=0,updated=0;
  int16_t next(){return ++calls==fail?-705:0;}
  int16_t setFrequency(float){return next();}
  int16_t setOutputPower(int8_t){return next();}
  int16_t setSpreadingFactor(uint8_t){return next();}
  int16_t setBandwidth(float){return next();}
  int16_t setCodingRate(uint8_t){return next();}
  int16_t setPreambleLength(uint16_t){return next();}
  void setPreambleMillis(uint32_t v){assert(v==44);++updated;}
  void setMaxPayloadMillis(uint32_t v){assert(v==440);++updated;}
  int16_t standby(){return next();}
  int16_t startReceive(){return next();}
};
struct Board {bool ready=true;bool prepareRadioFrequency(float){return ready;}};
struct Wrapper {
  mesh::lr2021::ApplyStatus _apply_status;
  CustomLR2021 chip;CustomLR2021* _radio=&chip;
  Board board;Board* _board=&board;
  bool _dbm_valid=true;int8_t _cur_dbm=5;
  bool _scan_tuple_owned=false,_scan_retune=false,_scan_tuple_applied=false;
  uint8_t _cur_sf=7,_cur_cr=5;
  bool scanTargetMatches(float,float,uint8_t,uint8_t) const{return false;}
  void invalidateScanTuple(){_scan_tuple_owned=false;}
  float hardwareFrequencyMHz(float frequency) const{return frequency;}
  int16_t recordApply(mesh::lr2021::ApplyStatus::Stage s,int16_t c){return _apply_status.record(s,c);}
  uint16_t preambleLengthForParams(uint8_t,float){return 32;}
  bool applySideDetectorConfig(uint8_t,float){return recordApply(mesh::lr2021::ApplyStatus::Stage::SideDetectors,chip.next())==0;}
  PacketMillis calcMaxPacketMillis(uint8_t,float,uint8_t,uint16_t){return {44,440};}
  @APPLY@
  @RECEIVE@
};
int main(){
  using Stage=mesh::lr2021::ApplyStatus::Stage;
  const Stage stages[]={Stage::Frequency,Stage::Power,Stage::SpreadingFactor,
    Stage::Bandwidth,Stage::CodingRate,Stage::Preamble,Stage::SideDetectors};
  for(int failure=1;failure<=7;++failure){
    Wrapper w;w._apply_status.begin();w.chip.fail=failure;
    assert(!w.applyParams(910.54f,62.5f,7,5));
    assert(w._apply_status.stage==stages[failure-1]&&w._apply_status.code==-705);
    assert(w.chip.calls==failure&&w.chip.updated==0);
    w.chip.fail=0;assert(w.applyParams(910.525f,62.5f,7,5));
    assert(w._apply_status.stage==stages[failure-1]&&w._apply_status.code==-705);
  }
  Wrapper rail;rail.board.ready=false;
  assert(!rail.applyParams(910.54f,62.5f,7,5));
  assert(rail._apply_status.stage==Stage::FrequencyRail&&rail.chip.calls==0);
  Wrapper ready;assert(ready.applyParams(910.54f,62.5f,7,5));assert(ready.chip.updated==2);
  for(int failure=1;failure<=2;++failure){
    Wrapper rx;rx.chip.fail=failure;
    assert(rx.startReceiveMode()==-705);
    assert(rx._apply_status.stage==(failure==1?Stage::Standby:Stage::Receive));
    assert(rx.chip.calls==failure);
  }
}
'''.replace("@APPLY@", apply).replace("@RECEIVE@", receive)
        self.run_cpp(code)

    def test_real_trim_transaction_restores_selection_modem_and_receiver(self):
        source = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        transaction = method(source, "mesh::RadioParamApplyResult trySetXoscTrim(")
        cancel = method(source, "void cancelXoscTrimApply(")
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <helpers/radiolib/LR2021ApplyStatus.h>
#include <helpers/radiolib/LR2021CalibrationPolicy.h>
#include <helpers/radiolib/PrimaryRadioApplyWait.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
#define MESH_ENABLE_CARRIER_TOOLS 1
#define MESH_ENABLE_MORSE_RX 1
#define MESH_ENABLE_MESHTEXT_WIRE 1
#define MAX_TRANS_UNIT 255
static uint32_t now_ms=100;
uint32_t millis(){return now_ms;}
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
struct CustomLR2021 {
  bool supported=true, enabled=false;
  uint8_t a=0,b=0;
  int commands=0, configurations=0;
  int16_t error=0;
  bool getXoscTrim(uint8_t& x,uint8_t& y)const{x=a;y=b;return enabled;}
  bool configureBootXoscTrim(bool e,uint8_t x,uint8_t y){
    ++configurations;enabled=e;a=e?x:0;b=e?y:0;return true;
  }
  void clearXoscTrim(){enabled=false;a=b=0;}
  int16_t applyXoscTrim(uint8_t x,uint8_t y){
    ++commands;if(error)return error;configureBootXoscTrim(true,x,y);return 0;
  }
};
struct Wrapper {
  mesh::lr2021::ApplyStatus _apply_status;
  mesh::PrimaryRadioApplyWait _trim_apply_wait;
  uint16_t _trim_apply_key=UINT16_MAX;
  mesh::RadioProfiles _profiles;
  uint8_t _active_profile=0;
  bool _primary_receive_preempt=false;
  CustomLR2021 chip;
  CustomLR2021* _radio=&chip;
  bool _profile_repair_required=false,_profile_refresh_required=false;
  bool _morse_owned=false,_morse_rx_owned=false,_params_valid=true;
  bool _dbm_valid=true,_dbm_applied=true,_rx_boosted_gain_valid=true;
  bool _rx_boosted_gain_applied=true,_cur_rx_boosted_gain=true;
  float _cur_freq=910.525f,_cur_bw=62.5f;
  uint8_t _cur_sf=7,_cur_cr=5,_physical_protocol=1;
  int8_t _cur_dbm=5;
  uint8_t admission=1;
  int native_fail=0,resets=0,tuple_applies=0,rearms=0;
  int fail_rearm=0;
  bool rx=true,fail_reset=false;
  Wrapper(){_profiles.primary.freq=910.525f;_profiles.primary.bw=62.5f;
            _profiles.primary.sf=7;_profiles.primary.cr=5;}
  uint16_t currentPreambleLength()const{return 32;}
  uint16_t profilePreamble(uint8_t)const{return 32;}
  bool supportsXoscTrim()const{return chip.supported;}
  void noteReconfigureAdmission(const char*,int16_t){}
  uint8_t beginReconfigure(){if(admission<=1)rx=false;return admission;}
  int16_t recordApply(mesh::lr2021::ApplyStatus::Stage s,int16_t c){return _apply_status.record(s,c);}
  bool applyParams(float f,float bw,uint8_t sf,uint8_t cr){
    assert(f==_cur_freq&&bw==_cur_bw&&sf==_cur_sf&&cr==_cur_cr);
    ++tuple_applies;
    if(native_fail==1){recordApply(mesh::lr2021::ApplyStatus::Stage::Frequency,-705);return false;}
    return true;
  }
  bool applyProtocol(uint8_t p){
    assert(p==_physical_protocol);
    if(native_fail==2){recordApply(mesh::lr2021::ApplyStatus::Stage::Protocol,-706);return false;}
    return true;
  }
  int16_t applyCachedTxPower(int8_t p){
    assert(p==_cur_dbm);
    return recordApply(mesh::lr2021::ApplyStatus::Stage::Power,native_fail==3?-707:0);
  }
  bool applyRxBoostedGainMode(bool b){
    assert(b==_cur_rx_boosted_gain);
    if(native_fail==4){recordApply(mesh::lr2021::ApplyStatus::Stage::Gain,-708);return false;}
    return true;
  }
  void recalibrateNoiseFloor(){}
  bool restoreAfterDeepInit(){++resets;_profile_repair_required=false;return !fail_reset;}
  void endReconfigure(bool resume){
    assert(resume);++rearms;
    if(fail_rearm>0){--fail_rearm;rx=false;recordApply(mesh::lr2021::ApplyStatus::Stage::Receive,-709);}
    else rx=true;
  }
  bool isInRecvMode()const{return rx;}
  @CANCEL@
  @TRANSACTION@
};
int main(){
  using Result=mesh::RadioParamApplyResult;
  using Stage=mesh::lr2021::ApplyStatus::Stage;
  for(uint8_t admission:{2,3}){
    Wrapper w;w.admission=admission;
    assert(w.trySetXoscTrim(true,8,8)==(admission==2?Result::BUSY:Result::FAILED));
    assert(!w.chip.enabled&&w.chip.commands==0&&w.chip.configurations==0&&w.resets==0);
    assert(w._trim_apply_key==(admission==2?uint16_t(0x1208):UINT16_MAX));
    assert(!w._primary_receive_preempt);
    w.cancelXoscTrimApply();assert(w._trim_apply_key==UINT16_MAX);
  }
  for(int owner=0;owner<2;++owner){
    Wrapper w;(owner?w._morse_rx_owned:w._morse_owned)=true;
    assert(w.trySetXoscTrim(true,8,8)==Result::BUSY&&w.chip.commands==0&&w.rx);
  }
  for(int invalid:{48,255}){
    Wrapper w;assert(w.trySetXoscTrim(true,invalid,8)==Result::FAILED&&w.chip.commands==0);
  }
  Wrapper unsupported;unsupported.chip.supported=false;
  assert(unsupported.trySetXoscTrim(true,8,8)==Result::FAILED);
  Wrapper success;
  assert(success.trySetXoscTrim(true,8,8)==Result::APPLIED);
  assert(success.chip.enabled&&success.chip.a==8&&success.chip.b==8&&success.rx);
  assert(success.tuple_applies==1&&success.rearms==1&&success.resets==0);
  assert(success._apply_status.stage==Stage::Applied&&success._apply_status.code==0);
  assert(success._trim_apply_key==UINT16_MAX&&!success._primary_receive_preempt);
  int commands=success.chip.commands;
  assert(success.trySetXoscTrim(true,8,8)==Result::APPLIED&&success.chip.commands==commands);
  assert(success.trySetXoscTrim(false,0,0)==Result::APPLIED);
  assert(!success.chip.enabled&&success.resets==1&&success.rx);
  for(bool old:{false,true})for(int failure=0;failure<=5;++failure){
    Wrapper w;w.chip.configureBootXoscTrim(old,3,4);
    if(failure==0)w.chip.error=-704;
    else if(failure==5)w.fail_rearm=1;
    else w.native_fail=failure;
    assert(w.trySetXoscTrim(true,8,8)==Result::FAILED);
    assert(w.chip.enabled==old&&(!old||(w.chip.a==3&&w.chip.b==4)));
    assert(w.resets==1&&w.rx&&!w._profile_repair_required);
    assert(w._trim_apply_key==UINT16_MAX&&!w._primary_receive_preempt);
    assert(w._apply_status.code==-704-failure&&w._apply_status.failed);
  }
  Wrapper failed_restore;failed_restore.chip.error=-704;failed_restore.fail_reset=true;
  assert(failed_restore.trySetXoscTrim(true,8,8)==Result::FAILED);
  assert(failed_restore._profile_repair_required&&failed_restore._profile_refresh_required);
  // First failure survives successful rollback writes; the next transaction
  // resets it, and positive admission guards remain distinguishable.
  mesh::lr2021::ApplyStatus status;status.begin();status.record(Stage::Frequency,-705);
  status.record(Stage::Receive,0);status.applied();
  assert(status.stage==Stage::Frequency&&status.code==-705);
  status.begin();status.applied();assert(status.stage==Stage::Applied&&status.code==0);
}
'''.replace("@TRANSACTION@", transaction).replace("@CANCEL@", cancel)
        code = code.replace("#include <cassert>", "#include <cassert>\n#include <initializer_list>")
        self.run_cpp(code)

    def run_cpp(self, code):
        with tempfile.TemporaryDirectory(prefix="meshcore-lr2021-apply-") as directory:
            cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
            cpp.write_text(code, encoding="ascii")
            built = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                    "-fno-pie", "-no-pie", "-I", str(ROOT / "src"),
                                    str(cpp), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
