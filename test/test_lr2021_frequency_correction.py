"""Host tests for the W12 nominal/programmed-frequency boundary."""
import unittest
import test_lr2021_calibration as calibration
from test_lr2021_calibration import RADIO, method


class FrequencyCorrectionTests(unittest.TestCase):
    compile_run = calibration.LR2021CalibrationTests.compile_run

    def test_virtual_retunes_keep_the_profile_nominal_and_bound_correction(self):
        source = (RADIO / 'CustomLR2021.h').read_text()
        signatures = ('float getNominalFreqMHz() const', 'int32_t frequencyCorrectionPpb() const',
                      'bool configureFrequencyCorrection(int32_t ppb)', 'int16_t setFrequency(float nominal_mhz) override')
        methods = '\n'.join(method(source, s).replace(' override', '') for s in signatures)
        harness = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <helpers/radiolib/LR2021FrequencyCorrection.h>
#define RADIOLIB_ERR_NONE 0
class LR2021 {
public:
  float physical=0;
  bool fail=false;
  virtual int16_t setFrequency(float value) {if(fail)return -1; physical=value;return 0;}
  int16_t boot(float nominal) {return setFrequency(nominal);}
};
class CustomLR2021:public LR2021 {
  int32_t _frequency_correction_ppb=0;
  float _nominal_frequency_mhz=0;
public:
@METHODS@
};
int main() {
  CustomLR2021 chip; LR2021& physical_layer=chip;
  assert(chip.configureFrequencyCorrection(1180));
  assert(physical_layer.boot(910.525f)==0);
  assert(chip.getNominalFreqMHz()==910.525f);
  assert(chip.physical>910.525f && fabs((chip.physical-910.525f)*1e6f-1074)<80);
  assert(physical_layer.setFrequency(911.150f)==0);
  assert(chip.getNominalFreqMHz()==911.150f);
  assert(fabs((chip.physical-911.150f)*1e6f-1075)<80);
  assert(physical_layer.setFrequency(2400.0f)==0);
  assert(fabs((chip.physical-2400.0f)*1e6f-2832)<200);
  assert(!chip.configureFrequencyCorrection(5501) && chip.frequencyCorrectionPpb()==1180);
  assert(!chip.configureFrequencyCorrection(-5501) && chip.frequencyCorrectionPpb()==1180);
  assert(chip.configureFrequencyCorrection(-5500));
  chip.fail=true; assert(physical_layer.setFrequency(904.525f)!=0);
  assert(chip.getNominalFreqMHz()==2400.0f);
  chip.fail=false; assert(physical_layer.setFrequency(904.525f)==0);
  assert(chip.physical<904.525f);
  assert(chip.configureFrequencyCorrection(0));
  assert(physical_layer.boot(904.525f)==0 && chip.physical==904.525f);
}
'''
        self.compile_run(harness.replace('@METHODS@', methods))

    def test_live_apply_busy_failure_and_verified_rollback(self):
        source = (RADIO / 'CustomLR2021Wrapper.h').read_text()
        candidate = method(source, 'mesh::RadioParamApplyResult trySetFrequencyCorrection(int32_t ppb)')
        harness = r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/LR2021FrequencyCorrection.h>
#define RADIOLIB_ERR_NONE 0
#define MAX_TRANS_UNIT 255
namespace mesh {
enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};
namespace lr2021 { struct ApplyStatus { enum class Stage {Other}; void begin() {} void applied() {} }; }
}
unsigned millis(){return 100;}
struct Wait { void clear() {} template<class... T> bool mayAbandonReceive(T...) {return false;} };
struct Profiles {int primary=0; int value=0; int& params(unsigned){return value;} };
struct CustomLR2021 {
  int32_t correction=0;
  int32_t frequencyCorrectionPpb() const{return correction;}
  bool configureFrequencyCorrection(int32_t value) {correction=value;return true;}
};
struct Wrapper {
  CustomLR2021 chip; void* _radio=&chip;
  Wait _frequency_apply_wait;
  int32_t _frequency_apply_key=INT32_MAX;
  mesh::lr2021::ApplyStatus _apply_status;
  Profiles _profiles;
  unsigned _active_profile=0;
  bool _primary_receive_preempt=false, _params_valid=true, _profile_repair_required=false;
  bool _profile_refresh_required=false, _dbm_valid=false, _rx_boosted_gain_valid=false;
  bool _dbm_applied=false, _rx_boosted_gain_applied=false;
  float _cur_freq=910.525f,_cur_bw=62.5f;
  unsigned _cur_sf=7,_cur_cr=5;
  int _cur_dbm=0; bool _cur_rx_boosted_gain=false;
  unsigned admission=0; bool apply_ok=true,rollback_ok=true,rx_after_end=true,in_rx=true;
  unsigned applies=0,rollbacks=0;
  bool supportsXoscTrim() const {return true;}
  void cancelFrequencyCorrectionApply(){_frequency_apply_wait.clear();_frequency_apply_key=INT32_MAX;}
  unsigned currentPreambleLength(){return 16;} unsigned profilePreamble(unsigned){return 16;}
  uint8_t beginReconfigure(){return admission;}
  bool applyParams(float,float,unsigned,unsigned){++applies;return apply_ok;}
  int applyCachedTxPower(int){return 0;} bool applyRxBoostedGainMode(bool){return true;}
  void recalibrateNoiseFloor(){} void endReconfigure(bool){in_rx=rx_after_end;}
  bool isInRecvMode()const{return in_rx;}
  bool restoreAfterDeepInit(){++rollbacks;return rollback_ok;}
  void noteReconfigureAdmission(const char*,int){}
@METHOD@
};
int main() {
  using R=mesh::RadioParamApplyResult; Wrapper w;
  assert(w.trySetFrequencyCorrection(5501)==R::FAILED && w.chip.correction==0);
  w.admission=2;
  assert(w.trySetFrequencyCorrection(1180)==R::BUSY && w.chip.correction==0);
  w.admission=0;
  assert(w.trySetFrequencyCorrection(1180)==R::APPLIED && w.chip.correction==1180);
  assert(w.applies==1 && w.rollbacks==0);
  assert(w.trySetFrequencyCorrection(1180)==R::APPLIED && w.applies==1);
  w.apply_ok=false;
  assert(w.trySetFrequencyCorrection(-400)==R::FAILED);
  assert(w.chip.correction==1180 && w.rollbacks==1 && !w._profile_repair_required);
  w.rollback_ok=false;
  assert(w.trySetFrequencyCorrection(-400)==R::FAILED);
  assert(w.chip.correction==1180 && w._profile_repair_required && w._profile_refresh_required);
}
'''
        self.compile_run(harness.replace('@METHOD@', candidate))


if __name__ == '__main__':
    unittest.main()
