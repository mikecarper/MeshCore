"""Exercise Indicator stale-edge retirement with the real HAL and pulse code."""
import ast
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from cpp_source import body
from test_rxps_pulse_integration import harness

ROOT=Path(__file__).resolve().parents[1]


def fixture():
    source=harness()
    source=source[:source.index('static void naturalWake(')]
    source=source.replace('uint32_t micros() { return now_us; }',r'''
uint32_t micros() { return now_us; }
#include <algorithm>
#include <helpers/radiolib/RadioInterruptEvents.h>
#define private public
#include "@ROOT@/variants/sensecap_indicator-espnow/IndicatorRadioHal.cpp"
#undef private
int nativeLevel=1,nativeWrites=0,nativeModes=0;
uint8_t inputs[8]={0,0,255,255,0,0,255,255};
unsigned i2c_reads=0;
bool fail_read=false,skip_poll=false;
uint32_t extra_read_us=0,irq_delay_us=0;
RadioInterruptEvents radio_interrupt_events[1];
constexpr uint8_t RADIO_INTERRUPT_SLOT_COUNT=1;
void actual_irq() { radio_interrupt_events[0].record(); }
IndicatorRadioHal* actual_hal=nullptr;
void (*standby_hook)()=nullptr;
namespace lgfx { namespace i2c {
Result readRegister(uint8_t port,uint8_t address,uint8_t reg,uint8_t* data,size_t n,uint32_t hz) {
 assert(port==0 && address==0x20 && hz==400000 && reg+n<=8);++i2c_reads;now_us+=7+extra_read_us;
 if(fail_read)return {false};
 std::copy(inputs+reg,inputs+reg+n,data);return {true};
}
Result transactionWrite(uint8_t port,uint8_t address,const uint8_t* data,size_t n,uint32_t hz) {
 assert(port==0 && address==0x20 && hz==400000 && data[0]+n-1<=8);
 std::copy(data+1,data+n,inputs+data[0]);return {true};
}
Result init(uint8_t,uint8_t,uint8_t) {return {true};}
} }
'''.replace('@ROOT@',str(ROOT)))
    source=source.replace('  Module module;', '  SPIClass spi;IndicatorRadioHal _hal;\n  Module module;')
    source=source.replace('Wrapper():driver(&module),_radio(&driver) { _rxps_pulse.cadence(60000); }',
        'Wrapper():_hal(spi),driver(&module),_radio(&driver) { _rxps_pulse.cadence(60000);assert(_hal.beginExpander());_hal.attachInterrupt(67,actual_irq,ArduinoHal::GpioInterruptRising);radio_interrupt_events[0].claim();actual_hal=&_hal; }')
    base=(ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
    pending=body(base,'bool RadioLibWrapper::hasPendingRadioInterrupt(').replace('RadioLibWrapper::','')
    claim=body(base,'bool RadioLibWrapper::claimAGCSoftwareInterrupt(').replace('RadioLibWrapper::','')
    source=source.replace('  bool hasPendingRadioInterrupt() const { return module.host_irq; }',
        '  bool _loop_event_pending=false;uint8_t _interrupt_slot=0;\n'
        '  bool hasAssertedRadioInterrupt() const {return false;}\n'+pending+'\n'+claim)
    source=source.replace('bool isChipBusy() const { return module.busy; }',
                          'bool isChipBusy() {return _hal.digitalRead(66);}')
    # The command spy preserves the pinned Module's existing BUSY reads; each
    # one runs the actual I2C HAL, including synchronous DIO edge publication.
    source=source.replace('    assert(!verify);',
                          '    if(wait && actual_hal && !skip_poll)assert(!actual_hal->digitalRead(66));\n    assert(!verify);')
    source=source.replace('    assert(wait && !verify && !busy);',
                          '    if(wait && actual_hal && !skip_poll)assert(!actual_hal->digitalRead(66));\n    assert(wait && !verify && !busy);')
    source=source.replace('++standbys;', '++standbys;if(standby_hook)standby_hook();')
    source=source.replace('data[1]=uint8_t(irq); }', 'data[1]=uint8_t(irq);now_us+=irq_delay_us; }')
    source=source.replace('bool startAGCPulse() {','bool startBaseAGCPulse() {')
    indicator=(ROOT/'variants/sensecap_indicator-espnow/IndicatorSX1262Wrapper.h').read_text()
    methods='\n'.join(body(indicator,sig).replace(' override','') for sig in (
        'bool startAGCPulse(', 'bool pulseContextMatches(', 'uint32_t agcInputReadGeneration(',
        'bool prepareStaleAGCIrqWake(', 'bool retireStaleAGCIrqWake('))
    methods=methods.replace('CustomSX1262Wrapper::startAGCPulse()', 'startBaseAGCPulse()')
    storage=re.search(r'  struct \{.*?\} _pulseContext;',indicator,re.S).group()
    source=source.replace('  unsigned legacy=0,scan_spy=0;',
        '  float _cur_freq=906.525f;uint8_t _cur_cr=5;\n'+methods+'\n'+storage+'\n  unsigned legacy=0,scan_spy=0;')
    source+=r'''
Wrapper* active=nullptr;
std::string hook_case;
void inject() {
 if(hook_case=="generation")++active->_profiles.generation[1];
 else if(hook_case=="physical_generation")++active->_profile_generation;
 else if(hook_case=="preamble")++active->_physical_preamble;
 else if(hook_case=="tuple")active->_cur_freq+=1;
 else if(hook_case=="cancel")active->_rxps_pulse.cancelRequest();
 else if(hook_case=="post_dio")inputs[0]|=8;
 else if(hook_case=="post_invalid")active->_hal._inputSnapshot.valid=false;
}
void signal(Wrapper& w,bool busy,bool dio) {
 w.module.busy=busy;inputs[0]=(busy?4:0)|(dio?8:0);
}
void sleep(Wrapper& w,bool wrap=false) {
 begin(w);
 if(wrap) {w._rxps_pulse.armed_at=UINT32_MAX-w._rxps_pulse.rx_us-200;w._rxps_pulse.started=w._rxps_pulse.armed_at;}
 const uint32_t armed=w._rxps_pulse.armed_at;
 now_us=armed+1;signal(w,false,false);w.serviceAGCPulse();
 now_us=armed+w._rxps_pulse.rx_us;signal(w,true,false);w.serviceAGCPulse();
 const size_t calls=w.module.calls.size();
 now_us+=30;signal(w,true,true);w.serviceAGCPulse();
 assert(w.module.calls.size()==calls && w._rxps_pulse.high==1 && w._rxps_pulse.cycles==0);
 assert(radio_interrupt_events[0].pending());
}
int main(int argc,char** argv) {
 assert(argc==2);const std::string which=argv[1];Wrapper w;active=&w;
 if(which=="dual_generation") {w._profiles.active=true;w._profile_rxps_suspended=true;}
 sleep(w,which=="clock_wrap");
 const size_t before=w.module.calls.size();const auto fifo=w.module.fifo;
 const uint32_t high_at=w._rxps_pulse.high_at;
 uint32_t duration=500;
 if(which=="min")duration=250;
 if(which=="short")duration=249;
 if(which=="short_rtc_error")duration=249;
 if(which=="max")duration=w._rxps_pulse.sleep_us+2000;
 if(which=="long")duration=w._rxps_pulse.sleep_us+2001;
 now_us=high_at+duration;signal(w,false,false);
 if(which=="deadline")now_us=w._rxps_pulse.armed_at+w._rxps_pulse.deadline_us;
 if(which=="abort_wrap")w._rxps_pulse.aborts=UINT32_MAX;
 if(which=="read_generation_wrap")w._hal._inputSnapshot.generation=UINT32_MAX;
 if(which=="rx_done") {w.module.irq=2;inputs[0]=8;}
 if(which=="crc")w.module.irq=0x42;
 if(which=="header")w.module.irq=0x20;
 if(which=="timeout")w.module.irq=0x200;
 if(which=="unknown")w.module.irq=0x8000;
 if(which=="read_error")w.module.fail_at=before+1;
 if(which=="standby_error")w.module.fail_at=before;
 if(which=="short_rtc_error")w.module.fail_at=before+2;
 if(which=="stale_post")skip_poll=true;
 if(which=="slow_observation")extra_read_us=2001;
 if(which=="stale_post_time")irq_delay_us=2001;
 if(which=="early")w._rxps_pulse.noteError(RxpsPulse::WitnessError,RxpsPulse::Step::Witness);
 if(which=="already_cycle")w._rxps_pulse.had_cycle=true;
 if(which=="pre_cancel")w._rxps_pulse.cancelRequest();
 if(which=="dual_generation")++w._profiles.generation[0];
 if(which=="generation" || which=="physical_generation" || which=="preamble"
    || which=="tuple" || which=="cancel" || which=="post_dio") {
   hook_case=which;standby_hook=inject;
 }
 const bool good=which=="normal" || which=="min" || which=="max" || which=="clock_wrap"
     || which=="abort_wrap" || which=="read_generation_wrap";
 w.serviceAGCPulse();standby_hook=nullptr;
 assert(w.module.fifo==fifo);
 if(good) {
   assert(w._rxps_pulse.cycles==1 && w._rxps_pulse.ok==1 && w._rxps_pulse.fail==0);
   assert(w._rxps_pulse.aborts==(which=="abort_wrap"?UINT32_MAX:0));
   assert(w._rxps_pulse.wake==1 && !radio_interrupt_events[0].pending());
   assert(w._rxps_pulse.verified==1 && w._rxps_pulse.rtc==1);
   assert(w.module.calls.size()==before+14); // exact original natural-wake command sequence
 } else {
   assert(w._rxps_pulse.cycles==0 && w._rxps_pulse.ok==0);
   if(which=="short" || which=="long") {
     assert(w._rxps_pulse.fail==0 && w._rxps_pulse.error==0 && w._rxps_pulse.defer==1
       && w._rxps_pulse.missed==1 && w._rxps_pulse.aborts==0 && w._rxps_pulse.wake==1);
     assert(!w._rxps_pulse.pending && w._rxps_pulse.verified==1 && w._rxps_pulse.rtc==1);
     assert(w._rxps_pulse.takeCompletion()==RxpsPulse::Result::Deferred);
   } else if(which=="short_rtc_error") {
     // An unconfirmed GPIO span cannot mask a real command error while
     // restoring RX. The original hard error must survive checked recovery.
     assert(w._rxps_pulse.pending && w._rxps_pulse.failed && w._rxps_pulse.fail==0);
     assert(w._rxps_pulse.missed==1 && w._rxps_pulse.aborts==0 && w._rxps_pulse.defer==0);
     assert(w._rxps_pulse.error==-705 && w._rxps_pulse.failed_step==RxpsPulse::Step::RtcStop);
     w.module.fail_at=SIZE_MAX;now_us+=250001;w.serviceAGCPulse();
     assert(!w._rxps_pulse.pending && w._rxps_pulse.fail==1 && w._rxps_pulse.ok==0);
     assert(w._rxps_pulse.error==-705 && w._rxps_pulse.failed_step==RxpsPulse::Step::RtcStop);
     assert(w._rxps_pulse.takeCompletion()==RxpsPulse::Result::Failed);
   } else {
     assert(w._rxps_pulse.aborts==1);
     if(which=="deadline")assert(w._rxps_pulse.error==0 && w._rxps_pulse.fail==0);
     if(which=="early")assert(w._rxps_pulse.error==RxpsPulse::WitnessError);
     if(which=="read_error" || which=="standby_error")assert(w._rxps_pulse.error==-705);
     for(size_t i=before;i<w.module.calls.size();++i)assert(w.module.calls[i].command[0]!=0x02);
     assert(radio_interrupt_events[0].pending());
   }
 }
 // A failed/new stale cache cannot be retired directly and must not consume
 // another published software event or issue a bus transaction.
 const unsigned reads=i2c_reads;const size_t calls=w.module.calls.size();
 w._hal._inputSnapshot.valid=false;assert(!w.retireStaleAGCIrqWake(0));
 assert(i2c_reads==reads && w.module.calls.size()==calls);
 uint32_t invalid_generation=w._hal._inputSnapshot.generation-1;
 assert(!w.prepareStaleAGCIrqWake(now_us,invalid_generation));
 assert(i2c_reads==reads && w.module.calls.size()==calls);
 std::cout<<"PASS "<<which<<"\n";
}
'''
    return source


class IndicatorStaleIrq(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse((ROOT/'test/test_indicator_radio_hal.py').read_text())
        stubs={n.targets[0].id:ast.literal_eval(n.value) for n in ast.walk(tree)
               if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)
               and n.targets[0].id in ('arduino','buffered','gfx')}
        cls.temp=tempfile.TemporaryDirectory(prefix='indicator-stale-irq-')
        cls.addClassCleanup(cls.temp.cleanup)
        folder=Path(cls.temp.name);(folder/'helpers/radiolib').mkdir(parents=True)
        arduino=stubs['arduino'].replace('constexpr uint32_t RADIOLIB_NC = 0xffffffff;','')
        (folder/'Arduino.h').write_text(arduino)
        (folder/'SPI.h').write_text('#pragma once\nstruct SPIClass {};\n')
        (folder/'LovyanGFX.hpp').write_text(stubs['gfx'])
        (folder/'helpers/radiolib/ESP32BufferedRadioHal.h').write_text(stubs['buffered'])
        (folder/'fixture.cpp').write_text(fixture());cls.binary=folder/'fixture'
        made=subprocess.run([os.environ.get('CXX','g++'),'-std=c++11','-O2','-Wall','-Wextra','-Werror',
            '-fsanitize=address,undefined','-fno-omit-frame-pointer','-no-pie','-DSENSECAP_INDICATOR_LORA','-DTEST_FS=0',
            '-I',str(folder),'-I',str(ROOT/'test/fixtures/rxps_pulse_integration'),'-I',str(ROOT/'src'),
            str(folder/'fixture.cpp'),'-o',str(cls.binary)],capture_output=True,text=True)
        if made.returncode:raise AssertionError(made.stderr)

    def check(self,name):
        run=subprocess.run([str(self.binary),name],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(run.stdout.strip(),'PASS '+name)

    def test_checked_zero_irq_recovers_only_real_original_wake_bounds(self):
        for name in ('normal','min','max','short','long','deadline'):
            with self.subTest(name=name):self.check(name)
    def test_irq_sources_and_read_failures_preserve_fifo_and_handoff(self):
        for name in ('rx_done','crc','header','timeout','unknown','read_error','standby_error'):
            with self.subTest(name=name):self.check(name)
    def test_unconfirmed_wake_does_not_hide_a_real_rtc_restore_error(self):
        self.check('short_rtc_error')
    def test_counter_and_microsecond_wrap_are_preserved(self):
        for name in ('abort_wrap','clock_wrap','read_generation_wrap'):
            with self.subTest(name=name):self.check(name)
    def test_stale_context_cancel_and_high_physical_dio_never_get_credit(self):
        for name in ('generation','physical_generation','dual_generation','preamble','tuple','cancel',
                     'pre_cancel','early','already_cycle','post_dio','stale_post'):
            with self.subTest(name=name):self.check(name)
    def test_slow_original_observation_and_stale_post_irq_cache_keep_old_handoff(self):
        for name in ('slow_observation','stale_post_time'):
            with self.subTest(name=name):self.check(name)

    def test_new_hooks_and_replay_compile_out_of_other_radio_families(self):
        files=('src/helpers/radiolib/RadioLibWrappers.h', 'src/helpers/radiolib/RadioLibWrappers.cpp',
               'src/helpers/radiolib/SX126xPulseWrapper.h', 'src/helpers/radiolib/RxpsPulse.h')
        for name in files:
            lines=(ROOT/name).read_text().splitlines(True)
            blocks=[];index=0
            while index<len(lines):
                if lines[index].startswith('#if') and 'SENSECAP_INDICATOR_LORA' in lines[index]:
                    start=index;depth=1;index+=1
                    while depth:
                        if lines[index].startswith('#if'):depth+=1
                        if lines[index].startswith('#endif'):depth-=1
                        index+=1
                    blocks.append(''.join(lines[start:index]))
                else:index+=1
            self.assertTrue(blocks,name)
            made=subprocess.run([os.environ.get('CXX','g++'),'-E','-P','-x','c++',
                '-DMESH_WARM_RX_REFRESH=1','-'],input='\n'.join(blocks),capture_output=True,text=True)
            self.assertEqual(made.returncode,0,made.stderr)
            for token in ('StaleAGCIrq','agcInputReadGeneration','claimAGCSoftwareInterrupt','replayStaleIrqWake'):
                self.assertNotIn(token,made.stdout,name)


if __name__=='__main__':unittest.main()
