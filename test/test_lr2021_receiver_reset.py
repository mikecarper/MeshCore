"""Exercise the actual LR2021 full-calibration helper at its native API boundary."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r"""
#include <cassert>
#include <cmath>
#include <math.h>
#include <cstdint>
#include <vector>
#include <initializer_list>
#include <helpers/radiolib/LR2021CalibrationPolicy.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_FREQUENCY -12
#define RADIOLIB_ERR_FRONTEND_CALIBRATION_FAILED -711
#define RADIOLIB_LR2021_CALIBRATE_ALL 0x6f
#define RADIOLIB_LR2021_CALIBRATE_FE_HF_PATH 0x8000
#define RADIOLIB_LR2021_LF_RC_CALIB_ERR (1<<3)
#define RADIOLIB_LR2021_HF_RC_CALIB_ERR (1<<4)
#define RADIOLIB_LR2021_PLL_CALIB_ERR (1<<5)
#define RADIOLIB_LR2021_AAF_CALIB_ERR (1<<6)
#define RADIOLIB_LR2021_IMG_CALIB_ERR (1<<7)
#define RADIOLIB_LR2021_RXFREQ_NO_FE_CAL_ERR (1<<9)
#define RADIOLIB_LR2021_MEAS_UNIT_ADC_CALIB_ERR (1<<10)
#define RADIOLIB_LR2021_PA_OFFSET_CALIB_ERR (1<<11)
#define RADIOLIB_LR2021_PPF_CALIB_ERR (1<<12)
#define RADIOLIB_LR2021_SRC_CALIB_ERR (1<<13)
#define RADIOLIB_LR2021_SRC_SATURATION_CALIB_ERR (1<<14)
#define RADIOLIB_LR2021_SRC_TOLERANCE_CALIB_ERR (1<<15)
struct Event { int command; std::vector<uint16_t> value; };
struct LR2021 {
  enum {GetErrors=0x0110,ClearErrors=0x0111,Sleep=0x0127,Standby=0x0128,
        Calibrate=0x0122,FrontEnd=0x0123,Frequency=0x0200,Gain=0x0201,Routing=0x10001};
  std::vector<Event> events;
  std::vector<uint16_t> errors{0,0,0};
  unsigned error_read=0;
  int fail_at=-1;
  uint32_t pram=0x12345678,trim=0x0808,tcxo=0,regulator=0;
  float freqMHz=910.525f,nominal=910.524f,programmed_frequency=0;
  uint8_t gainModeLf=3,gainModeHf=2;
  bool in_standby=false,pram_registered=true;
  int16_t event(int command,std::initializer_list<uint16_t> value={}) {
    events.push_back({command,{value}});
    return int(events.size())-1==fail_at ? -706 : 0;
  }
  int16_t getErrors(uint16_t* value) {
    const int16_t status=event(GetErrors);
    if(!status){assert(error_read<errors.size());*value=errors[error_read++];}
    return status;
  }
  int16_t clearErrors() {return event(ClearErrors);}
  int16_t standby() {
    const int16_t status=event(Standby,{0});if(!status)in_standby=true;return status;
  }
  int16_t sleep(bool retain,uint32_t time) {
    assert(in_standby && retain && time==0);
    const int16_t status=event(Sleep,{uint16_t(retain),uint16_t(time)});
    if(!status){in_standby=false;pram_registered=false;}
    return status;
  }
  int16_t calibrate(uint8_t mask) {
    assert(in_standby);return event(Calibrate,{mask});
  }
  int16_t calibrateFrontEnd(const uint16_t (&frequency)[3]) {
    assert(in_standby);return event(FrontEnd,{frequency[0],frequency[1],frequency[2]});
  }
  int16_t setFrequency(float frequency,bool skipCalibration) {
    assert(in_standby && skipCalibration && frequency==freqMHz);
    const int16_t status=event(Frequency,{uint16_t(skipCalibration)});
    if(!status)programmed_frequency=frequency;
    return status;
  }
  int16_t setRxBoostedGainMode(uint8_t level) {
    assert(in_standby);return event(Gain,{level});
  }
};
struct CustomLR2021:LR2021 {
  uint32_t _activityAt=99;
  bool _headerSeen=true;
  int16_t restoreRfSwitchTable(){assert(in_standby);return event(Routing);}
  @HELPER@
};
static void preserved(const CustomLR2021& chip) {
  assert(chip.pram==0x12345678 && chip.trim==0x0808 && chip.tcxo==0 && chip.regulator==0);
  assert(chip.nominal==910.524f && chip.gainModeLf==3 && chip.gainModeHf==2);
}
static void frequencies() {
  struct Case {float frequency; uint16_t calibration; uint8_t gain;};
  for(Case item:{Case{150.0f,38,3},Case{868.0f,217,3},Case{868.001f,218,3},
      Case{910.525f,228,3},Case{911.150f,228,3},Case{1090.0f,273,3},
      Case{1900.0f,uint16_t(0x8000|475),2},Case{2400.0f,uint16_t(0x8000|600),2},
      Case{2499.99f,uint16_t(0x8000|625),2}}) {
    CustomLR2021 chip;chip.freqMHz=item.frequency;
    assert(chip.resetReceiverCalibration()==0 && chip.in_standby);
    const std::vector<int> expected={LR2021::GetErrors,LR2021::Standby,LR2021::Sleep,
        LR2021::Standby,LR2021::Calibrate,LR2021::GetErrors,LR2021::FrontEnd,
        LR2021::GetErrors,LR2021::Frequency,LR2021::Routing,LR2021::Gain};
    assert(chip.events.size()==expected.size());
    for(unsigned i=0;i<expected.size();++i)assert(chip.events[i].command==expected[i]);
    assert(chip.events[4].value==std::vector<uint16_t>{0x6f});
    assert(chip.events[6].value==std::vector<uint16_t>({item.calibration,0,0}));
    assert(chip.events.back().value==std::vector<uint16_t>{item.gain});
    assert(chip.freqMHz==item.frequency && chip.programmed_frequency==item.frequency);
    assert(!chip._activityAt && !chip._headerSeen);
    preserved(chip);
  }
  for(float invalid:{0.0f,-1.0f,2600.1f,NAN,INFINITY}) {
    CustomLR2021 chip;chip.freqMHz=invalid;
    assert(chip.resetReceiverCalibration()==RADIOLIB_ERR_INVALID_FREQUENCY);
    assert(chip.events.empty() && chip._activityAt==99 && chip._headerSeen);
  }
}
static void failures() {
  for(bool retained_sleep:{false,true})for(int fail=0;fail<(retained_sleep?11:9);++fail) {
    CustomLR2021 chip;chip.fail_at=fail;
    assert(chip.resetReceiverCalibration(nullptr,0,retained_sleep)==-706);
    assert(chip.events.size()==unsigned(fail+1));
    assert(chip._activityAt==99 && chip._headerSeen);preserved(chip);
  }
  const std::vector<uint16_t> bits={8,16,32,64,128,512,1024,2048,4096,8192,16384,32768};
  for(bool retained_sleep:{false,true})for(uint16_t fault:bits) {
    CustomLR2021 chip;chip.errors={fault,0,0};
    assert(chip.resetReceiverCalibration(nullptr,0,retained_sleep)==0);
    assert(chip.events[1].command==LR2021::ClearErrors);preserved(chip);
    CustomLR2021 clear_failed;clear_failed.errors={fault,0,0};clear_failed.fail_at=1;
    assert(clear_failed.resetReceiverCalibration(nullptr,0,retained_sleep)==-706 && clear_failed.events.size()==2);
    for(unsigned stage:{1U,2U}) {
      CustomLR2021 failed;failed.errors[stage]=fault;
      assert(failed.resetReceiverCalibration(nullptr,0,retained_sleep)==RADIOLIB_ERR_FRONTEND_CALIBRATION_FAILED);
      assert(failed.events.size()==(stage==1?(retained_sleep?6U:4U):(retained_sleep?8U:6U)));
      assert(failed._activityAt==99 && failed._headerSeen);preserved(failed);
    }
  }
  // Every unrelated or mixed preexisting fault remains observable; never clear
  // the word merely to make calibration appear successful.
  for(bool retained_sleep:{false,true})for(uint16_t fault:{uint16_t(1),uint16_t(2),uint16_t(4),uint16_t(256),
       uint16_t(1|8),uint16_t(1|512),uint16_t(2|512),uint16_t(4|512),
       uint16_t(256|512),uint16_t(0xffff)}) {
    CustomLR2021 chip;chip.errors[0]=fault;
    assert(chip.resetReceiverCalibration(nullptr,0,retained_sleep)==RADIOLIB_ERR_FRONTEND_CALIBRATION_FAILED);
    assert(chip.events.size()==1 && chip.errors[0]==fault);preserved(chip);
  }
}
static void frontend_coverage() {
  struct Case {float current,profile[3];size_t count;uint16_t expected[3];};
  for(const Case& item:{
      Case{909.5f,{909.5f,919.5f,0},2,{228,230,0}},
      Case{919.5f,{909.5f,919.5f,0},2,{230,228,0}},
      Case{910.525f,{868.0f,2400.0f,0},2,{228,217,uint16_t(0x8000|600)}},
      Case{909.5f,{911.15f,919.5f,0},2,{228,230,0}},
      Case{909.5f,{909.5f,911.15f,919.5f},3,{228,230,0}},
      Case{909.5f,{868.001f,2400.001f,0},2,{228,218,uint16_t(0x8000|601)}}}) {
    CustomLR2021 chip;chip.freqMHz=item.current;chip.errors={512,0,0};
    assert(chip.resetReceiverCalibration(item.profile,item.count)==0);
    assert(chip.events[1].command==LR2021::ClearErrors);
    unsigned calibrations=0;
    for(const Event& event:chip.events)if(event.command==LR2021::FrontEnd) {
      ++calibrations;
      assert(event.value==std::vector<uint16_t>({item.expected[0],item.expected[1],item.expected[2]}));
    }
    assert(calibrations==1 && chip.programmed_frequency==item.current);
    preserved(chip);
  }
  for(float invalid:{0.0f,-1.0f,2600.1f,NAN,INFINITY}) {
    CustomLR2021 chip;
    assert(chip.resetReceiverCalibration(&invalid,1)==RADIOLIB_ERR_INVALID_FREQUENCY);
    assert(chip.events.empty() && chip._activityAt==99 && chip._headerSeen);
  }
  CustomLR2021 missing;
  assert(missing.resetReceiverCalibration(nullptr,1)==RADIOLIB_ERR_INVALID_FREQUENCY);
  assert(missing.events.empty());
  const float profiles[4]={150.0f,868.0f,2400.0f,2496.0f};
  for(size_t count:{3U,4U}) {
    CustomLR2021 overflow;
    assert(overflow.resetReceiverCalibration(profiles,count)==RADIOLIB_ERR_INVALID_FREQUENCY);
    assert(overflow.events.empty());
  }
}
static void calibration_modes() {
  for(bool retained_sleep:{false,true}) {
    CustomLR2021 chip;chip.freqMHz=909.5f;chip.errors={512,0,0};
    const float profiles[]={909.5f,919.5f};
    assert(chip.resetReceiverCalibration(profiles,2,retained_sleep)==0);
    const std::vector<int> standby_expected={LR2021::GetErrors,LR2021::ClearErrors,
        LR2021::Standby,LR2021::Calibrate,LR2021::GetErrors,LR2021::FrontEnd,
        LR2021::GetErrors,LR2021::Frequency,LR2021::Routing,LR2021::Gain};
    const std::vector<int> sleep_expected={LR2021::GetErrors,LR2021::ClearErrors,
        LR2021::Standby,LR2021::Sleep,LR2021::Standby,LR2021::Calibrate,
        LR2021::GetErrors,LR2021::FrontEnd,LR2021::GetErrors,LR2021::Frequency,
        LR2021::Routing,LR2021::Gain};
    const auto& expected=retained_sleep?sleep_expected:standby_expected;
    assert(chip.events.size()==expected.size());
    for(size_t i=0;i<expected.size();++i)assert(chip.events[i].command==expected[i]);
    for(const auto& event:chip.events) {
      if(event.command==LR2021::Calibrate)assert(event.value==std::vector<uint16_t>{0x6f});
      if(event.command==LR2021::FrontEnd)assert(event.value==std::vector<uint16_t>({228,230,0}));
    }
    assert(chip.pram_registered==!retained_sleep && chip.in_standby);
    assert(chip.programmed_frequency==909.5f && !chip._activityAt && !chip._headerSeen);
    preserved(chip);
  }
}
int main(){frequencies();failures();frontend_coverage();calibration_modes();}
"""

class LR2021ReceiverResetTests(unittest.TestCase):
    def test_actual_helper_commands_calibration_points_and_every_failure_boundary(self):
        helper=body((ROOT/'src/helpers/radiolib/CustomLR2021.h').read_text(),
                    'int16_t resetReceiverCalibration(')
        source=HARNESS.replace('@HELPER@',helper)
        with tempfile.TemporaryDirectory(prefix='lr2021-receiver-reset-') as temporary:
            path=Path(temporary);(path/'main.cpp').write_text(source)
            exe=path/'test'
            result=subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
                '-I',str(ROOT/'src'),str(path/'main.cpp'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
