"""Execute real ISR handoff, wrapper lifecycle and LR2021 receive failure paths."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


def compile_run(code, defines=()):
    with tempfile.TemporaryDirectory(prefix="meshcore-radio-irq-") as tmp:
        cpp, exe = Path(tmp) / "test.cpp", Path(tmp) / "test"
        cpp.write_text(code)
        command = [os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra",
                   "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                   "-pthread", "-I", str(ROOT / "src"), *defines, str(cpp), "-o", str(exe)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)


PRELUDE = r'''
#include <cassert>
#include <cstring>
#include <cstdint>
#include <initializer_list>
#include <helpers/radiolib/RadioInterruptEvents.h>
#include <helpers/radiolib/RadioProfileChip.h>
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_FREQUENCY -2
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_ERR_UNSUPPORTED -2
#define RADIOLIB_ERR_PACKET_TOO_SHORT -5
#define RADIOLIB_ERR_SPI_CMD_TIMEOUT -77
#define NF_CALIB_SETTLE_MS 7
#define RADIOLIB_LR2021_RX_TIMEOUT_INF UINT32_MAX
#define RADIOLIB_IRQ_RX_DEFAULT_FLAGS 0
#define RADIOLIB_IRQ_RX_DEFAULT_MASK 0
#define RADIOLIB_IRQ_PREAMBLE_DETECTED 5
#define RADIOLIB_IRQ_RX_DONE 1
#define RADIOLIB_IRQ_TX_DONE 2
#define RADIOLIB_LR2021_IRQ_RX_DONE 1
#define RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED 32
#define RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID 64
#define RADIOLIB_LR2021_IRQ_LORA_HDR_CRC_ERROR 512
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#if defined(ESP32_PLATFORM)
#define ICACHE_RAM_ATTR
#define RADIOLIB_NC UINT32_MAX
#define SOC_GPIO_PIN_COUNT 49
// Match the pinned S3 SDK: holes are invalid and the macro itself shifts.
#define GPIO_IS_VALID_GPIO(pin) (((UINT64_C(0x1fffffc3fffff) >> (pin)) & 1) != 0)
static bool gpio_levels[SOC_GPIO_PIN_COUNT]={};
static unsigned gpio_reads=0;
static void (*gpio_read_hook)()=nullptr;
struct RadioLibHal {
 const uint32_t GpioLevelHigh=1;
 uint32_t digitalRead(uint32_t pin) {
   assert(pin<SOC_GPIO_PIN_COUNT && GPIO_IS_VALID_GPIO(pin));
   ++gpio_reads;
   if(gpio_read_hook){auto hook=gpio_read_hook;gpio_read_hook=nullptr;hook();}
   return gpio_levels[pin];
 }
};
struct Module {
 RadioLibHal instance;RadioLibHal* hal=&instance;
 uint32_t irq=3;
 uint32_t getIrq()const{return irq;}
};
#endif
static uint32_t now_ms=100;
uint32_t millis(){return now_ms;}
struct LR2021 {
 bool stopped=false, inject_read_irq=false;
 int rx_status=1, tx_status=1, fifo_status=0, clear_status=0, base_status=0, standby_status=0;
 int irq_clear_status=0;
 uint32_t irq=0;
 uint16_t fifo=4;
 int packet_length=4;
 unsigned reads=0, clears=0, standbys=0, arms=0, irq_clears=0, tx_starts=0, irq_probes=0;
#if defined(ESP32_PLATFORM)
 Module module;Module* selected_module=&module;
 Module* getMod(){return selected_module;}
#endif
 void (*callback)()=nullptr;
 void clearPacketReceivedAction(){callback=nullptr;}
 void setPacketReceivedAction(void (*fn)()){callback=fn;}
 int checkIrq(int type){++irq_probes;return type==RADIOLIB_IRQ_RX_DONE?rx_status:tx_status;}
 int standby(){++standbys;if(standby_status)return standby_status;stopped=true;return 0;}
 int clearIrqFlags(uint32_t flags){
   if(flags&RADIOLIB_LR2021_IRQ_RX_DONE)assert(stopped);
   ++irq_clears;
   if(irq_clear_status)return irq_clear_status;
   irq &= ~flags;
   if(flags&RADIOLIB_LR2021_IRQ_RX_DONE)rx_status=0;
   return 0;
 }
 void assertStoppedSnapshot(){
#if defined(USE_LR2021)
   assert(stopped && "LR2021 RX must stop before metadata/FIFO snapshots");
#endif
 }
 int getPacketLength(){assertStoppedSnapshot();return packet_length;}
 float getSNR(){assertStoppedSnapshot();return 7;}
 float getRSSI(){assertStoppedSnapshot();return -90;}
 int getRxFifoLevel(uint16_t* output){assertStoppedSnapshot();*output=fifo;return fifo_status;}
 int clearRxFifo(){++clears;if(clear_status)return clear_status;fifo=0;return 0;}
 virtual int16_t readData(uint8_t* output,size_t len){
   ++reads;
#if defined(USE_LR2021)
   // Real RadioLib clears FIFO and all IRQs. A concurrent packet must not be
   // able to modify the FIFO while those destructive operations run.
   assert(stopped);
#endif
   memset(output,0xa5,len);fifo=0;
#if defined(ESP32_PLATFORM)
   // Actual FIFO service clears RX_DONE; the level then falls. A delayed
   // software callback after this point must not redeliver the old FIFO.
   rx_status=0;gpio_levels[module.irq]=false;
#endif
   if(inject_read_irq && !stopped && callback) callback();
   return base_status;
 }
 virtual int16_t startReceive(){++arms;stopped=false;return 0;}
 int16_t startReceive(uint32_t,uint32_t,uint32_t,int){assert(stopped);++arms;stopped=false;return 0;}
 int startTransmit(uint8_t*,int){++tx_starts;return 0;}
 int finishTransmit(){stopped=true;return 0;}
 uint32_t getIrqStatus(){return irq;}
};
struct Chip : LR2021 {
 uint32_t _activityAt=0, _preambleMillis=66, _maxPayloadMillis=3934;
 bool _headerSeen=false;
 float freqMHz=910.525f, _last_frequency_error_mhz=0;
 int32_t _last_frequency_error_hz=0;
 uint32_t _last_frequency_error_at=0;
 bool _last_frequency_error_valid=false;
 struct MeasurementHal {uint32_t millis(){return ::millis();}} measurement_hal;
 struct MeasurementModule {MeasurementHal* hal;} measurement_mod{&measurement_hal};
 MeasurementModule* mod=&measurement_mod;
 // The packet-status wire decoder has its own production-method tests. Keep
 // this fixture focused on preserving the IRQ/FIFO while publishing metadata.
 int16_t readPacketFrequencyErrorHz(int32_t& hz){hz=250;return 0;}
 bool isChipBusy(){return false;}
 @LR_METHODS@
};
struct Board {
 unsigned before=0,after=0;
 void (*before_hook)()=nullptr;
 void onBeforeTransmit(){++before;if(before_hook)before_hook();}
 void onAfterTransmit(){++after;}
};
struct RadioLibWrapper {
 uint8_t state=STATE_RX, _interrupt_slot=0xff, _irq_probe_failures=0;
 bool _loop_event_pending=false, _cw_active=false, _profile_repair_required=false, _profile_refresh_required=false;
 bool _rx_ps_armed=false, _rx_hold_continuous=false, _rx_ps_enabled=false;
 bool _rx_ps_continuous_fallback=false, _nf_calib_active=false;
 bool rxps_hardware=true;
 bool supportsRxPowerSaving()const{return rxps_hardware;}
 bool sx126x_late_irq_guard=true;
 bool preserveActiveReceiveOnLateIrq()const{return sx126x_late_irq_guard;}
 bool _primary_receive_preempt=false;
 uint32_t _rx_ps_eff_rx_us=0, _rx_ps_eff_sleep_us=0;
 float _last_snr=0, _last_rssi=0;
 unsigned n_recv=0,n_recv_errors=0,n_sent=0;
 uint32_t _rx_retry_at=0;
 uint16_t _rx_retry_delay_ms=0;
 bool _rx_retry_pending=false;
 uint32_t last_recv_millis=0,last_radio_interrupt_millis=0,_nf_sample_from=0;
 Board board;Board* _board=&board;
 Chip chip;Chip* _radio=&chip;
protected:
 ~RadioLibWrapper()=default;
public:
 bool registerInterruptAction();void unregisterInterruptAction();
 bool hasAssertedRadioInterrupt()const;bool hasPendingRadioInterrupt()const;bool claimRadioInterrupt();
 uint8_t beginReconfigure();
 void noteReconfigureAdmission(const char*,int16_t){}
 void requestRestartRecv();bool isPacketReady();
 bool startSendRaw(const uint8_t*,int);bool isSendComplete();
 int recvRaw(uint8_t*,int);
 void onSendFinished();
 void idle(){state=STATE_IDLE;}
 bool isChipBusy(){return false;}
 bool isReceivingPacket(){return false;}
 bool isPacketPendingOrReceiving(){return hasPendingRadioInterrupt();}
 bool recoverRadio(bool){return !hasPendingRadioInterrupt();}
 void stopReceiveDutyCycle(){_rx_ps_armed=false;chip.standby();}
 void startRecv(){if(chip.startReceive()==0)state=STATE_RX;}
};
@CALLBACKS@
@METHODS@
@CONCRETE_CLASSES@
'''


class RadioInterruptRecoveryTests(unittest.TestCase):
    def run_dispatcher_retry(self, soft_only=False, remove_guard=False):
        with tempfile.TemporaryDirectory(prefix="meshcore-radio-retry-") as tmp:
            dispatcher = ROOT / "src/Dispatcher.cpp"
            if remove_guard:
                original = dispatcher.read_text()
                guard = ("  if (outbound->isRouteFlood() && outbound->flood_retry_policy "
                         "== FLOOD_RETRY_POLICY_DENY) return false;\n")
                self.assertEqual(original.count(guard), 1)
                dispatcher = Path(tmp) / "Dispatcher.cpp"
                dispatcher.write_text(original.replace(guard, ""))
            exe = Path(tmp) / "retry"
            command = [os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                       "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src")]
            if soft_only:
                command.append("-DRADIO_LIVENESS_SOFT_ONLY")
            command += [str(dispatcher), str(ROOT / "src/Packet.cpp"),
                        str(ROOT / "src/helpers/StaticPoolPacketManager.cpp"),
                        str(ROOT / "test/fixtures/dispatcher/radio_retry_test.cpp"), "-o", str(exe)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            env = os.environ.copy()
            # Firmware-lifetime production pools do not have destructors.
            env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
            result = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
            if remove_guard:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("a denied flood must never be retransmitted", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_dispatcher_lost_tx_done_respects_flood_retry_policy(self):
        for soft_only in (False, True):
            with self.subTest(soft_only=soft_only):
                self.run_dispatcher_retry(soft_only=soft_only)

    def test_removed_flood_retry_guard_repeats_actual_timed_out_transmission(self):
        self.run_dispatcher_retry(remove_guard=True)

    def test_actual_wrapper_header_uses_actual_radio_base(self):
        # Keep Mesh/Dispatcher and the wrapper header real: the common test
        # Mesh.h gives Radio a virtual destructor that production does not.
        with tempfile.TemporaryDirectory(prefix="meshcore-radio-header-") as tmp:
            folder = Path(tmp)
            (folder / "RadioLib.h").write_text(r'''
#pragma once
#include <cstdint>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_FREQUENCY -2
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_ERR_UNSUPPORTED -2
#define RADIOLIB_NC 0xffffffff
using RadioLibIrqFlags_t = uint32_t;
class PhysicalLayer {
public:
 int setOutputPower(int8_t){return 0;}
 int setFrequency(float){return 0;}
 int standby(){return 0;}
 int startReceive(){return 0;}
 int transmitDirect(){return 0;}
 int setPreambleLength(uint16_t){return 0;}
 uint8_t randomByte(){return 0;}
};
''')
            (folder / "header.cpp").write_text(r'''
long random(long,long);
#include <helpers/radiolib/RadioLibWrappers.h>
#include <type_traits>
static_assert(!std::has_virtual_destructor<mesh::Radio>::value, "real Radio ABI");
static_assert(!std::has_virtual_destructor<RadioLibWrapper>::value, "concrete wrapper ownership ABI");
static_assert(!std::is_destructible<RadioLibWrapper>::value, "borrowed base cannot own destruction");
''')
            result = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-fsyntax-only",
                "-DLORA_SF=7", "-DLORA_BW=125", "-I", str(folder),
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                str(folder / "header.cpp"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_atomic_generation_latch_and_counter_wrap(self):
        compile_run(r'''
#include <helpers/radiolib/RadioInterruptEvents.h>
#include <atomic>
#include <cassert>
#include <cstring>
#include <thread>
int main(){
 RadioInterruptEvents a,b;
 assert(!a.claim());a.record();assert(a.pending()&&!b.pending());
 assert(a.claim()&&!a.pending());a.record();assert(a.claim());
 uint32_t wrapped[2]={UINT32_MAX,UINT32_MAX};
 static_assert(sizeof(a)==sizeof(wrapped),"bounded ISR storage");
 memcpy(&a,wrapped,sizeof(a));a.record();assert(a.pending()&&a.claim()&&!a.pending());
 std::atomic<bool> done{false};
 std::thread producer([&]{for(unsigned i=0;i<1000000;++i)a.record();done.store(true);});
 while(!done.load())a.claim();producer.join();a.claim();assert(!a.pending());
 b.record();assert(!a.pending()&&b.pending());
}
''')

    def source(self, sx126x=False):
        wrapper = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        custom = (ROOT / "src/helpers/radiolib/CustomLR2021.h").read_text()
        owners = wrapper.index("static RadioLibWrapper* radio_interrupt_owners")
        callbacks = wrapper[wrapper.index("#define RADIO_INTERRUPT_INTERNAL_"):
                            wrapper.index(";", owners) + 1]
        callbacks += "\n" + wrapper[wrapper.index("static\n#if defined(ESP8266)"):
                                      wrapper.index("bool RadioLibWrapper::registerInterruptAction()")]
        names = [("bool", "registerInterruptAction"), ("void", "unregisterInterruptAction"),
                 ("bool", "hasAssertedRadioInterrupt"),
                 ("bool", "hasPendingRadioInterrupt"), ("bool", "claimRadioInterrupt"),
                 ("uint8_t", "beginReconfigure"),
                 ("void", "requestRestartRecv"), ("bool", "isPacketReady"),
                 ("bool", "startSendRaw"), ("bool", "isSendComplete"), ("int", "recvRaw"),
                 ("void", "onSendFinished")]
        methods = "\n".join(method(wrapper, f"{kind} RadioLibWrapper::{name}(")
                            for kind, name in names)
        lr_methods = "\n".join(method(custom, signature) for signature in
                               ("int16_t readData(", "bool isReceiving()", "int16_t startReceive()"))
        if sx126x:
            # Keep the selected production activity timers and IRQ decisions.
            # Only SPI/FIFO transport and the destructive RX rearm are mocked.
            family = "CustomSX1262" if sx126x is True else sx126x
            sx = (ROOT / f"src/helpers/radiolib/{family}.h").read_text()
            lr_methods = r'''
 unsigned activity_probes=0;
 uint32_t getIrqFlags(){++activity_probes;return irq;}
 int clearIrqFlags(uint32_t flags){++irq_clears;irq &= ~flags;return 0;}
 ''' + method(sx, "bool isReceiving()")
        concrete = []
        for name in ("CustomLLCC68Wrapper", "CustomLR1110Wrapper", "CustomLR1121Wrapper",
                     "CustomLR2021Wrapper", "CustomSTM32WLxWrapper", "CustomSX1262Wrapper",
                     "CustomSX1268Wrapper", "CustomSX1276Wrapper"):
            header = (ROOT / f"src/helpers/radiolib/{name}.h").read_text()
            concrete.append(f"struct {name} : RadioLibWrapper {{" + method(header, f"~{name}()") + "};")
        # The two further-derived production families inherit concrete cleanup.
        for file in (ROOT / "src/helpers/radiolib/DacPaSX1276Wrapper.h",
                     ROOT / "variants/sensecap_indicator-espnow/IndicatorSX1262Wrapper.h"):
            import re
            declaration = re.search(r"class (\w+) : public (\w+)", file.read_text())
            concrete.append(f"struct {declaration[1]} : {declaration[2]} {{}};")
        result = (PRELUDE.replace("@LR_METHODS@", lr_methods).replace("@CALLBACKS@", callbacks)
                  .replace("@METHODS@", methods).replace("@CONCRETE_CLASSES@", "\n".join(concrete)))
        if sx126x:
            result = result.replace("#define NF_CALIB_SETTLE_MS 7", r'''
#define NF_CALIB_SETTLE_MS 7
#define RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED 4
#define RADIOLIB_SX126X_IRQ_HEADER_VALID 16
#define RADIOLIB_SX126X_IRQ_HEADER_ERR 32
#define RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID 8
''')
            result = result.replace("bool isReceivingPacket(){return false;}",
                                    "bool isReceivingPacket(){return chip.isReceiving();}")
            result = result.replace("bool isChipBusy(){return false;}\n bool isReceivingPacket()",
                                    "bool chip_busy=false;\n bool isChipBusy(){return chip_busy;}\n bool isReceivingPacket()")
            result = result.replace("++reads;\n#if defined(USE_LR2021)",
                                    "++reads;irq=0;\n#if defined(USE_LR2021)")
            result = result.replace(
                "virtual int16_t startReceive(){++arms;stopped=false;return 0;}",
                "virtual int16_t startReceive(){++arms;irq=0;rx_status=0;stopped=false;return 0;}")
        return result

    def test_mode_changes_lifecycle_late_callbacks_and_tx_status_retry(self):
        compile_run(self.source() + r'''
int main(){
 uint8_t bytes[16]={};
 auto* a=new CustomSX1262Wrapper;auto* b=new CustomSX1262Wrapper;
 assert(a->registerInterruptAction()&&b->registerInterruptAction());
 assert(a->chip.callback!=b->chip.callback);
 auto* c=new CustomSX1262Wrapper;assert(!c->registerInterruptAction());
 a->chip.callback();assert(a->hasPendingRadioInterrupt()&&!b->hasPendingRadioInterrupt());
 a->requestRestartRecv();assert(a->state==STATE_IDLE&&a->hasPendingRadioInterrupt());
 assert(!a->startSendRaw(bytes,4));assert(a->chip.tx_starts==0);
 a->_profile_repair_required=true;
 assert(a->recvRaw(bytes,sizeof(bytes))==0&&a->hasPendingRadioInterrupt()&&a->n_recv==0);
 a->_profile_repair_required=false;
 assert(a->recvRaw(bytes,sizeof(bytes))==4&&a->n_recv==1);
 b->chip.callback();assert(b->hasPendingRadioInterrupt()&&!a->hasPendingRadioInterrupt());
 assert(b->registerInterruptAction()&&!b->hasPendingRadioInterrupt());
 void(*late)()=a->chip.callback;delete a;late(); // static storage, no UAF
 assert(c->registerInterruptAction()&&!c->hasPendingRadioInterrupt());
 late();c->chip.rx_status=0;assert(c->recvRaw(bytes,sizeof(bytes))==0); // no ghost frame
 assert(c->recvRaw(bytes,sizeof(bytes))==0&&c->recvRaw(bytes,sizeof(bytes))==0);
 assert(!c->hasPendingRadioInterrupt());
 c->chip.rx_status=1;
 c->board.before_hook=c->chip.callback;
 assert(!c->startSendRaw(bytes,4)&&c->board.before==1&&c->board.after==1);
 assert(c->hasPendingRadioInterrupt()&&c->chip.tx_starts==0);
 c->claimRadioInterrupt();c->board.before_hook=nullptr;
 assert(c->startSendRaw(bytes,4)&&c->state==STATE_TX_WAIT);
 c->chip.callback();c->chip.tx_status=0;
 assert(!c->isSendComplete()&&c->hasPendingRadioInterrupt());
 c->chip.tx_status=-99;assert(!c->isSendComplete()&&c->hasPendingRadioInterrupt());
 c->chip.tx_status=1;assert(c->isSendComplete()&&c->n_sent==1);
 assert(!c->isSendComplete()&&c->n_sent==1);
 c->chip.tx_status=0;assert(c->startSendRaw(bytes,4));c->chip.callback();
 assert(!c->isSendComplete()&&c->hasPendingRadioInterrupt());
 c->onSendFinished();assert(!c->hasPendingRadioInterrupt()&&c->recoverRadio(true));
 // A receive status read temporarily returning zero retains the event.
 c->chip.callback();c->chip.rx_status=0;assert(c->recvRaw(bytes,sizeof(bytes))==0);
 c->chip.rx_status=1;c->chip.fifo=4;
 assert(c->recvRaw(bytes,sizeof(bytes))==4&&c->n_recv==1);
 delete b;delete c;
}
''')

    def test_all_concrete_families_release_slot_on_destruction(self):
        names = ("CustomLLCC68Wrapper", "CustomLR1110Wrapper", "CustomLR1121Wrapper",
                 "CustomLR2021Wrapper", "CustomSTM32WLxWrapper", "CustomSX1262Wrapper",
                 "CustomSX1268Wrapper", "CustomSX1276Wrapper", "DacPaSX1276Wrapper",
                 "IndicatorSX1262Wrapper")
        checks = ["int main(){"]
        for name in names:
            checks.append(f"""
+ {{
+   auto* original=new {name};assert(original->registerInterruptAction());
+   void(*late)()=original->chip.callback;delete original;
+   assert(!radio_interrupt_owners[0]&&!radio_interrupt_owners[1]);
+   late(); // no object access through an in-flight hardware callback
+   auto* replacement=new {name};assert(replacement->registerInterruptAction());
+   assert(!replacement->hasPendingRadioInterrupt());delete replacement;
+ }}
+""".replace("\n+", "\n"))
        checks.append("}")
        compile_run(self.source() + "\n".join(checks))

    def test_internal_stm32_slot_capacity_and_external_radio_capacity(self):
        checks = self.source() + r'''
int main(){
 CustomSTM32WLxWrapper first,second,third;
 assert(first.registerInterruptAction());
 second._interrupt_slot=0; // stale slot must never steal another owner
#if RADIO_INTERRUPT_SLOT_COUNT == 1
 static_assert(sizeof(radio_interrupt_owners)==sizeof(RadioLibWrapper*),"internal radio has one slot");
 assert(!second.registerInterruptAction()&&second._interrupt_slot==0xff);
 assert(!second.chip.callback&&radio_interrupt_owners[0]==&first);
#else
 static_assert(sizeof(radio_interrupt_owners)==2*sizeof(RadioLibWrapper*),"external radio retains two slots");
 assert(second.registerInterruptAction()&&second._interrupt_slot==1);
 assert(second.chip.callback!=first.chip.callback);
 assert(!third.registerInterruptAction()&&third._interrupt_slot==0xff);
 second.unregisterInterruptAction();
#endif
 first.chip.callback();assert(first.hasPendingRadioInterrupt());
 assert(first.registerInterruptAction()&&!first.hasPendingRadioInterrupt());
 void(*late)()=first.chip.callback;first.unregisterInterruptAction();late();
 assert(second.registerInterruptAction()&&!second.hasPendingRadioInterrupt());
 assert(!first.hasPendingRadioInterrupt());
 second.chip.callback();assert(second.hasPendingRadioInterrupt());
 second.unregisterInterruptAction();
 for(auto* owner:radio_interrupt_owners)assert(!owner);
}
'''
        for defines in ([], ["-DRADIO_CLASS=CustomSTM32WLx"],
                        ["-DSTM32_PLATFORM=1", "-DRADIO_CLASS=CustomSX1262"]):
            compile_run(checks, defines)

    def test_shared_phy_rejected_registration_preserves_owner_callback(self):
        checks = self.source() + r'''
int main(){
 auto* owner=new CustomSTM32WLxWrapper;
 auto* filler=new CustomSTM32WLxWrapper;
 assert(owner->registerInterruptAction());
#if RADIO_INTERRUPT_SLOT_COUNT > 1
 assert(filler->registerInterruptAction());
#endif
 auto* denied=new CustomSTM32WLxWrapper;
 denied->_radio=owner->_radio; // same integrated PhysicalLayer, distinct wrapper
 denied->_interrupt_slot=0; // stale slot is not ownership
 void(*active)()=owner->chip.callback;
 assert(!denied->registerInterruptAction()&&denied->_interrupt_slot==0xff);
 assert(owner->chip.callback==active&&radio_interrupt_owners[0]==owner);
 active();assert(owner->hasPendingRadioInterrupt());
 delete denied; // denied concrete destruction must not detach the owner's PHY
 assert(owner->chip.callback==active&&radio_interrupt_owners[0]==owner);
 assert(owner->claimRadioInterrupt());active();assert(owner->hasPendingRadioInterrupt());
 delete filler;delete owner;
 for(auto* slot_owner:radio_interrupt_owners)assert(!slot_owner);
}
'''
        for defines in ([], ["-DRADIO_CLASS=CustomSTM32WLx"],
                        ["-DSTM32_PLATFORM=1", "-DRADIO_CLASS=CustomSX1262"]):
            compile_run(checks, defines)

    def esp32_recovery_checks(self):
        return r'''
int main(){
 uint8_t bytes[16]={};
 auto* first=new CustomSX1262Wrapper;auto* second=new CustomSX1262Wrapper;
 first->chip.module.irq=3;second->chip.module.irq=4;
 assert(first->registerInterruptAction()&&second->registerInterruptAction());
 // A completed RF packet wakes the CPU, but no normal edge ISR was delivered.
 gpio_levels[3]=true;first->chip.rx_status=1;
 assert(!radio_interrupt_events[first->_interrupt_slot].pending());
 const unsigned spi_before=first->chip.irq_probes;
 assert(first->hasPendingRadioInterrupt()&&!second->hasPendingRadioInterrupt());
 assert(first->chip.irq_probes==spi_before); // level-only check, no SPI
 const unsigned arm_before=first->chip.arms,standby_before=first->chip.standbys;
 assert(first->beginReconfigure()==2); // no retune/rearm may discard its FIFO
 assert(first->chip.arms==arm_before&&first->chip.standbys==standby_before);
 assert(!first->startSendRaw(bytes,4)&&first->chip.tx_starts==0);
 assert(first->recvRaw(bytes,sizeof(bytes))==4&&first->n_recv==1&&first->chip.reads==1);
 assert(!gpio_levels[3]&&!first->hasPendingRadioInterrupt());
 assert(first->recvRaw(bytes,sizeof(bytes))==0&&first->chip.reads==1);

 // Physical and software ownership coalesce. Claim both instead of leaving
 // the software callback pending behind the physical level's short circuit.
 gpio_levels[3]=true;first->chip.rx_status=1;first->chip.fifo=4;first->chip.callback();
 assert(first->recvRaw(bytes,sizeof(bytes))==4&&first->n_recv==2&&first->chip.reads==2);
 assert(!radio_interrupt_events[first->_interrupt_slot].pending());
 assert(!first->hasPendingRadioInterrupt());

 // A delayed ISR after FIFO service has no RX_DONE. The unchanged status
 // probe prevents stale payloads despite its positive software notification.
 first->chip.callback();first->chip.rx_status=0;
 assert(first->recvRaw(bytes,sizeof(bytes))==0&&first->chip.reads==2);
 assert(first->recvRaw(bytes,sizeof(bytes))==0&&first->recvRaw(bytes,sizeof(bytes))==0);
 assert(first->n_recv==2&&!first->hasPendingRadioInterrupt());

 // Distinct module pins remain independent, including a rejected wrapper
 // with a stale slot number. No board-level shared pin is consulted.
 gpio_levels[4]=true;second->chip.rx_status=1;
 assert(!first->hasPendingRadioInterrupt()&&second->hasPendingRadioInterrupt());
 auto* denied=new CustomSX1262Wrapper;denied->_interrupt_slot=second->_interrupt_slot;
 denied->chip.module.irq=4;
 const unsigned reads_before=gpio_reads;
 assert(!denied->hasPendingRadioInterrupt()&&!denied->claimRadioInterrupt());
 assert(gpio_reads==reads_before);
 denied->_interrupt_slot=0xff;
 assert(!denied->hasPendingRadioInterrupt()&&!denied->claimRadioInterrupt());
 assert(gpio_reads==reads_before);
 delete denied;
 assert(second->recvRaw(bytes,sizeof(bytes))==4&&second->n_recv==1);

 // No GPIO access, or undefined SDK macro shift, for a missing module/HAL,
 // NC, pin beyond the S3 range, or its real GPIO22..25 holes.
 Chip* saved_chip=first->_radio;
 first->_radio=nullptr;
 unsigned checked_reads=gpio_reads;
 assert(!first->hasPendingRadioInterrupt()&&!first->claimRadioInterrupt());
 first->_radio=saved_chip;
 Module* saved=first->chip.selected_module;
 first->chip.selected_module=nullptr;
 assert(!first->hasPendingRadioInterrupt()&&!first->claimRadioInterrupt());
 first->chip.selected_module=saved;first->chip.module.hal=nullptr;
 assert(!first->hasPendingRadioInterrupt()&&!first->claimRadioInterrupt());
 first->chip.module.hal=&first->chip.module.instance;
 for(uint32_t invalid:{RADIOLIB_NC,uint32_t(49),uint32_t(64),UINT32_MAX-1,
                      uint32_t(22),uint32_t(23),uint32_t(24),uint32_t(25)}){
   first->chip.module.irq=invalid;
   assert(!first->hasPendingRadioInterrupt()&&!first->claimRadioInterrupt());
 }
 assert(gpio_reads==checked_reads);
 first->chip.module.irq=3;
 first->unregisterInterruptAction();gpio_levels[3]=true;
 assert(!first->hasPendingRadioInterrupt()&&!first->claimRadioInterrupt());
 assert(gpio_reads==checked_reads);
 gpio_levels[3]=false;delete first;delete second;
}
'''

    def test_esp32_asserted_module_dio_recovers_lost_wake_isr_once(self):
        compile_run(self.source()+self.esp32_recovery_checks(), ["-DESP32_PLATFORM=1"])

    def test_removed_physical_pending_or_claim_fallback_loses_real_receive(self):
        source=self.source()
        for name in ("hasPendingRadioInterrupt", "claimRadioInterrupt"):
            with self.subTest(removed=name):
                original=method(source, f"bool RadioLibWrapper::{name}(")
                mutant=original.replace(" || hasAssertedRadioInterrupt()", "")
                self.assertNotEqual(original,mutant)
                with self.assertRaises(AssertionError) as failure:
                    compile_run(source.replace(original,mutant)+self.esp32_recovery_checks(),
                                ["-DESP32_PLATFORM=1"])
                expected=("first->hasPendingRadioInterrupt()" if name=="hasPendingRadioInterrupt"
                          else "first->recvRaw(bytes,sizeof(bytes))==4")
                self.assertIn(expected,str(failure.exception))

    def test_non_esp32_retains_software_only_interrupt_contract(self):
        checks=self.source()+r'''
int main(){
 CustomSX1262Wrapper w;assert(w.registerInterruptAction());uint8_t bytes[16]={};
 assert(!w.hasAssertedRadioInterrupt()&&!w.hasPendingRadioInterrupt());
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.reads==0);
 w.chip.callback();assert(w.hasPendingRadioInterrupt());
 assert(w.recvRaw(bytes,sizeof(bytes))==4&&w.chip.reads==1&&!w.hasPendingRadioInterrupt());
 w.unregisterInterruptAction();
}
'''
        for defines in ([], ["-DNRF52_PLATFORM=1"], ["-DSTM32_PLATFORM=1"]):
            with self.subTest(defines=defines): compile_run(checks,defines)

    def sx126x_late_irq_checks(self):
        return r'''
static CustomSX1262Wrapper* staleEvent(){
 now_ms=100;uint8_t bytes[16]={};
 auto* w=new CustomSX1262Wrapper;assert(w->registerInterruptAction());
 w->chip.module.irq=3;w->chip.rx_status=1;w->chip.irq=1;gpio_levels[3]=true;
 // The edge ISR publishes after software claim, during observation of the
 // same completed packet's DIO. Actual claim/read/rearm leaves it pending.
 gpio_read_hook=w->chip.callback;
 assert(w->recvRaw(bytes,sizeof(bytes))==4);
 assert(w->n_recv==1&&w->chip.reads==1&&w->chip.arms==1);
 assert(w->hasPendingRadioInterrupt());
 assert(w->recvRaw(bytes,sizeof(bytes))==0&&w->_irq_probe_failures==1);
 assert(w->recvRaw(bytes,sizeof(bytes))==0&&w->_irq_probe_failures==2);
 return w;
}
int main(){
 uint8_t bytes[16]={};
 for(uint32_t activity:{uint32_t(4),uint32_t(16)}){
   auto* w=staleEvent();w->chip.irq=activity;
   // A new packet has entered preamble/header during the bounded stale-IRQ
   // probes. Rearming here does standby/clear IRQ/SetRx on the pinned SX1262.
   assert(w->isReceivingPacket());
   assert(w->recvRaw(bytes,sizeof(bytes))==0);
   assert(w->chip.arms==1&&"late IRQ must not rearm over a newer packet");
   assert(w->chip.irq==activity&&w->chip.reads==1&&w->state==STATE_RX);
   assert(w->_irq_probe_failures==0&&!w->hasPendingRadioInterrupt());
   // Its real later RX_DONE remains readable exactly once.
   w->chip.rx_status=1;w->chip.irq|=1;gpio_levels[3]=true;w->chip.callback();
   assert(w->recvRaw(bytes,sizeof(bytes))==4);
   assert(w->n_recv==2&&w->chip.reads==2&&w->chip.arms==2);
   assert(!w->hasPendingRadioInterrupt());
   assert(w->recvRaw(bytes,sizeof(bytes))==0&&w->chip.reads==2);
   delete w;
 }
 // Inactive and expired false activity still get the original recovery.
 for(uint32_t activity:{uint32_t(0),uint32_t(4),uint32_t(16)}){
   auto* w=staleEvent();w->chip.irq=activity;
   if(activity){
     assert(w->isReceivingPacket());
     now_ms+=activity==4?w->chip._preambleMillis+1:w->chip._maxPayloadMillis+1;
   }
   assert(w->recvRaw(bytes,sizeof(bytes))==0);
   assert(w->chip.arms==2&&w->chip.irq==0&&w->state==STATE_RX);
   assert(!w->hasPendingRadioInterrupt()&&w->n_recv==1);
   assert(w->recvRaw(bytes,sizeof(bytes))==0&&w->chip.arms==2);
   delete w;
 }
 // A current header error is failed activity, even when HEADER_VALID is
 // still latched. The activity probe clears it and recovery proceeds once.
 {
   auto* w=staleEvent();w->chip.irq=32|16|4;
   assert(w->recvRaw(bytes,sizeof(bytes))==0);
   assert(w->chip.arms==2&&w->chip.irq==0&&w->chip.irq_clears==1);
   assert(w->chip.reads==1&&w->n_recv==1&&!w->hasPendingRadioInterrupt());
   assert(w->recvRaw(bytes,sizeof(bytes))==0&&w->chip.arms==2);delete w;
 }
 // A genuine RX_DONE with empty metadata or FIFO read error still retires
 // its event. An activity bit must not turn it into an indefinite hold.
 for(bool empty:{false,true}){
   auto* w=staleEvent();w->chip.irq=16|1;w->chip.rx_status=1;
   if(empty)w->chip.packet_length=0;else w->chip.base_status=-88;
   assert(w->recvRaw(bytes,sizeof(bytes))==0);
   assert(w->chip.arms==2&&w->chip.irq==0&&w->n_recv==1);
   assert(w->n_recv_errors==(empty?0U:1U));
   assert(w->chip.activity_probes==0);
   delete w;
 }
 // Keep duty-cycle and idle/error-mode recovery outside this continuous-RX
 // workaround. BUSY also prevents a second activity-status SPI probe.
 for(unsigned bypass=0;bypass<3;++bypass){
   auto* w=staleEvent();w->chip.irq=16;
   if(bypass==0)w->_rx_ps_armed=true;
   if(bypass==1)w->state=STATE_IDLE;
   if(bypass==2)w->chip_busy=true;
   assert(w->recvRaw(bytes,sizeof(bytes))==0&&w->chip.arms==2);
   assert(w->chip.activity_probes==0);delete w;
 }
}
'''

    def test_sx126x_stale_irq_preserves_new_continuous_receive(self):
        for chip in ("CustomSX1262", "CustomSX1268", "CustomLLCC68"):
            with self.subTest(chip=chip):
                compile_run(self.source(sx126x=chip)+self.sx126x_late_irq_checks(),
                            ["-DESP32_PLATFORM=1", f"-DRADIO_CLASS={chip}"])

    def test_removed_sx126x_stale_irq_guard_aborts_new_receive(self):
        source = self.source(sx126x=True)
        start = source.index("    if (!packet_ready && state == STATE_RX && !_rx_ps_armed\n")
        end = source.index(" return 0;\n", start) + len(" return 0;\n")
        guard = source[start:end]
        with self.assertRaises(AssertionError) as failure:
            compile_run(source.replace(guard, "")+self.sx126x_late_irq_checks(),
                        ["-DESP32_PLATFORM=1", "-DRADIO_CLASS=CustomSX1262"])
        self.assertIn("late IRQ must not rearm over a newer packet", str(failure.exception))

    def test_universal_sx126x_retains_the_late_irq_guard(self):
        compile_run(self.source(sx126x=True)+self.sx126x_late_irq_checks(),
                    ["-DESP32_PLATFORM=1", "-DRADIO_CLASS=UniversalLoRaRadio",
                     "-DMESH_RADIO_AUTO_FAMILY=1"])

    def test_universal_sx1268_preserves_rx_without_claiming_rxps_support(self):
        checks = self.sx126x_late_irq_checks().replace(
            "auto* w=staleEvent();", "auto* w=staleEvent();w->rxps_hardware=false;")
        compile_run(self.source(sx126x="CustomSX1268") + checks,
                    ["-DESP32_PLATFORM=1", "-DRADIO_CLASS=UniversalLoRaRadio",
                     "-DMESH_RADIO_AUTO_FAMILY=1", "-DMESH_RADIO_AUTO_SX1268=1"])

    def universal_sx127x_late_irq_checks(self):
        checks = self.sx126x_late_irq_checks().split("int main(){", 1)[0]
        return checks + r'''
int main(){
 uint8_t bytes[16]={};
 auto* w=staleEvent();
 w->rxps_hardware=false; // The selected SX127x backend has no duty-cycle RX.
 w->sx126x_late_irq_guard=false;
 w->chip.irq=16;
 assert(w->recvRaw(bytes,sizeof(bytes))==0);
 assert(w->chip.arms==2&&"SX127x must retain its original IRQ rearm behavior");
 assert(w->chip.activity_probes==0&&w->chip.irq==0&&w->state==STATE_RX);
 delete w;
}
'''

    def test_universal_sx127x_does_not_use_the_sx126x_late_irq_guard(self):
        compile_run(self.source(sx126x=True)+self.universal_sx127x_late_irq_checks(),
                    ["-DESP32_PLATFORM=1", "-DRADIO_CLASS=UniversalLoRaRadio",
                     "-DMESH_RADIO_AUTO_FAMILY=1"])

    def test_removing_universal_runtime_family_gate_changes_sx127x_rearm(self):
        source = self.source(sx126x=True)
        self.assertEqual(source.count("        && preserveActiveReceiveOnLateIrq()\n"), 1)
        source = source.replace("        && preserveActiveReceiveOnLateIrq()\n", "")
        with self.assertRaises(AssertionError) as failure:
            compile_run(source+self.universal_sx127x_late_irq_checks(),
                        ["-DESP32_PLATFORM=1", "-DRADIO_CLASS=UniversalLoRaRadio",
                         "-DMESH_RADIO_AUTO_FAMILY=1"])
        self.assertIn("SX127x must retain its original IRQ rearm behavior", str(failure.exception))

    def test_stale_irq_guard_does_not_change_other_platforms_or_phys(self):
        checks = r'''
int main(){
 CustomSX1262Wrapper w;assert(w.registerInterruptAction());uint8_t bytes[16]={};
 w.chip.rx_status=0;w.chip.irq=16;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.arms==0);
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.arms==0);
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.arms==1);
 assert(w.chip.activity_probes==0);
}
'''
        for defines in ([], ["-DNRF52_PLATFORM=1"], ["-DSTM32_PLATFORM=1"],
                        ["-DESP32_PLATFORM=1", "-DRADIO_CLASS=CustomSX1276"],
                        ["-DESP32_PLATFORM=1", "-DRADIO_CLASS=CustomLR1110"]):
            with self.subTest(defines=defines):
                compile_run(self.source(sx126x=True)+checks,defines)

    def test_lr2021_fifo_guards_stopped_snapshot_cleanup_and_recovery(self):
        compile_run(self.source() + r'''
int main(){
 CustomLR2021Wrapper w;assert(w.registerInterruptAction());uint8_t bytes[16]={};
 for(int fifo:{0,3,5}){
   w.chip.rx_status=1;w.chip.fifo=fifo;w.chip.callback();
   const unsigned prior=w.chip.reads;
   assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.reads==prior);
   assert(w.chip.fifo==0&&!w.chip.stopped&&w.state==STATE_RX);
 }
 w.chip.rx_status=1;w.chip.fifo=4;w.chip.fifo_status=-99;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.irq_clears==4&&w.chip.fifo==0);
 w.chip.fifo_status=0;w.chip.rx_status=1;w.chip.fifo=4;
 w.chip.inject_read_irq=true;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==4&&w.n_recv==1&&!w.hasPendingRadioInterrupt());
 assert(w.chip.arms==5&&w.chip.irq_clears==5&&!w.chip.stopped);
 w.chip.rx_status=1;w.chip.fifo=4;w.chip.base_status=-88;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.n_recv_errors==5);
 w.chip.base_status=0;w.chip.rx_status=1;w.chip.fifo=4;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==4&&w.n_recv==2);
 // Zero-length metadata (including a silently failed status read) discards
 // its suspect FIFO before the next receive window.
 w.chip.packet_length=0;w.chip.fifo=8;w.chip.rx_status=1;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.fifo==0&&!w.chip.stopped);
 w.chip.packet_length=4;
 // A failed standby never clears FIFO/IRQ or rearms as though stopped.
 w.chip.standby_status=-55;w.chip.fifo=4;w.chip.rx_status=1;w.chip.callback();
 const unsigned armed=w.chip.arms,cleared=w.chip.irq_clears;
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.fifo==4);
 assert(w.chip.arms==armed&&w.chip.irq_clears==cleared);
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.arms==armed);
 w.chip.standby_status=0;w.chip.clear_status=-44;
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.arms==armed); // discard failure also blocks Set_RX
 w.chip.clear_status=0;w.recvRaw(bytes,sizeof(bytes));assert(w.chip.fifo==0&&w.chip.arms==armed+1);
 w.chip.rx_status=1;w.chip.fifo=4;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==4&&w.n_recv==3);
 // A stale header CRC flag must not hide a newly valid header/preamble.
 w.chip.irq=RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID|RADIOLIB_LR2021_IRQ_LORA_HDR_CRC_ERROR;
 assert(w.chip.isReceiving());
 w.chip.irq=RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED;
 assert(w.chip.isReceiving());
 w.chip.irq=0;assert(!w.chip.isReceiving());
}
''', ["-DUSE_LR2021=1"])

    def test_lr2021_upstream_3512_fifo_failures_never_copy_suspect_bytes(self):
        # #3512 at d208bfef94ab83280de30ba4a0675ff73b3ecef3 rejects FIFO
        # length mismatches. Keep the checked, stop-before-copy integration.
        compile_run(self.source() + r'''
int main(){
 Chip chip;chip.stopped=true;uint8_t bytes[8];memset(bytes,0x3c,sizeof(bytes));
 for(int fifo:{0,3,5}){
   chip.fifo=fifo;
   assert(chip.readData(bytes,4)==RADIOLIB_ERR_PACKET_TOO_SHORT);
   assert(chip.reads==0&&chip.fifo==0);
   for(uint8_t byte:bytes)assert(byte==0x3c);
 }
 chip.fifo=4;chip.fifo_status=-99;
 const unsigned clears=chip.clears;
 assert(chip.readData(bytes,4)==-99&&chip.clears==clears&&chip.reads==0);
 chip.fifo_status=0;chip.fifo=3;chip.clear_status=-44;
 assert(chip.readData(bytes,4)==-44&&chip.fifo==3&&chip.reads==0);
 for(uint8_t byte:bytes)assert(byte==0x3c);
 chip.clear_status=0;chip.fifo=4;
 // RadioLib permits len=0 to select the actual packet length.
 assert(chip.readData(bytes,0)==0&&chip.reads==1);
 chip.packet_length=0;chip.fifo=0;
 assert(chip.readData(bytes,0)==RADIOLIB_ERR_PACKET_TOO_SHORT&&chip.reads==1);
}
''', ["-DUSE_LR2021=1"])

    def test_lr2021_failed_rx_done_ack_blocks_rearm_until_recovery(self):
        compile_run(self.source() + r'''
int main(){
 CustomLR2021Wrapper w;assert(w.registerInterruptAction());uint8_t bytes[16]={};
 w.chip.irq_clear_status=-44;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==0);
 assert(w.chip.stopped&&w.state==STATE_IDLE&&w.chip.arms==0);
 assert(w.chip.irq_clears==1&&w.chip.rx_status==1&&w.n_recv_errors==1);
 w.chip.irq_clear_status=0;
 assert(w.recvRaw(bytes,sizeof(bytes))==0&&w.chip.arms==1&&w.state==STATE_RX);
 w.chip.fifo=4;w.chip.rx_status=1;w.chip.callback();
 assert(w.recvRaw(bytes,sizeof(bytes))==4&&w.chip.irq_clears==2);
 assert(!w.chip.stopped&&w.state==STATE_RX&&w.chip.arms==2);
}
''', ["-DUSE_LR2021=1"])

    def test_lr2021_upstream_3512_fresh_preamble_gets_a_new_activity_timeout(self):
        compile_run(self.source() + r'''
int main(){
 Chip chip;
 chip.irq=RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID|RADIOLIB_LR2021_IRQ_LORA_HDR_CRC_ERROR;
 assert(chip.isReceiving()&&chip._headerSeen);
 // A stale error flag and the preceding header timer must not cancel the
 // next frame's preamble after continuous RX cleared HEADER_VALID.
 now_ms+=chip._maxPayloadMillis;
 chip.irq=RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED|RADIOLIB_LR2021_IRQ_LORA_HDR_CRC_ERROR;
 assert(chip.isReceiving()&&!chip._headerSeen&&chip._activityAt==now_ms);
 assert(chip.irq_clears==0);
 now_ms+=chip._preambleMillis;
 assert(chip.isReceiving());
 ++now_ms;
 assert(!chip.isReceiving()&&chip._activityAt==0&&chip.irq_clears==1);
 assert(!(chip.irq&RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED));
}
''', ["-DUSE_LR2021=1"])


if __name__ == "__main__":
    unittest.main()
