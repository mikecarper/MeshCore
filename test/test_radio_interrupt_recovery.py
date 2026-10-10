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
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
#define RADIOLIB_ERR_NONE 0
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
static uint32_t now_ms=100;
uint32_t millis(){return now_ms;}
struct LR2021 {
 bool stopped=false, inject_read_irq=false;
 int rx_status=1, tx_status=1, fifo_status=0, clear_status=0, base_status=0, standby_status=0;
 uint32_t irq=0;
 uint16_t fifo=4;
 int packet_length=4;
 unsigned reads=0, clears=0, standbys=0, arms=0, irq_clears=0, tx_starts=0;
 void (*callback)()=nullptr;
 void clearPacketReceivedAction(){callback=nullptr;}
 void setPacketReceivedAction(void (*fn)()){callback=fn;}
 int checkIrq(int type){return type==RADIOLIB_IRQ_RX_DONE?rx_status:tx_status;}
 int standby(){++standbys;if(standby_status)return standby_status;stopped=true;return 0;}
 int clearIrqFlags(uint32_t){assert(stopped);++irq_clears;rx_status=0;return 0;}
 int getPacketLength(){return packet_length;}
 float getSNR(){return 7;}
 float getRSSI(){return -90;}
 int getRxFifoLevel(uint16_t* output){*output=fifo;return fifo_status;}
 int clearRxFifo(){++clears;if(clear_status)return clear_status;fifo=0;return 0;}
 virtual int16_t readData(uint8_t* output,size_t len){
   ++reads;
#if defined(USE_LR2021)
   // Real RadioLib clears FIFO and all IRQs. A concurrent packet must not be
   // able to modify the FIFO while those destructive operations run.
   assert(stopped);
#endif
   memset(output,0xa5,len);fifo=0;
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
 bool _loop_event_pending=false, _cw_active=false;
 bool _rx_ps_armed=false, _rx_hold_continuous=false, _rx_ps_enabled=false;
 bool _rx_ps_continuous_fallback=false, _nf_calib_active=false;
 float _last_snr=0, _last_rssi=0;
 unsigned n_recv=0,n_recv_errors=0,n_sent=0;
 uint32_t last_recv_millis=0,last_radio_interrupt_millis=0,_nf_sample_from=0;
 Board board;Board* _board=&board;
 Chip chip;Chip* _radio=&chip;
protected:
 ~RadioLibWrapper()=default;
public:
 bool registerInterruptAction();void unregisterInterruptAction();
 bool hasPendingRadioInterrupt()const;bool claimRadioInterrupt();
 void requestRestartRecv();bool isPacketReady();
 bool startSendRaw(const uint8_t*,int);bool isSendComplete();
 int recvRaw(uint8_t*,int);
 void onSendFinished();
 void idle(){state=STATE_IDLE;}
 bool recoverRadio(bool){return !hasPendingRadioInterrupt();}
 void stopReceiveDutyCycle(){_rx_ps_armed=false;chip.standby();}
 void startRecv(){if(chip.startReceive()==0)state=STATE_RX;}
};
@CALLBACKS@
@METHODS@
@CONCRETE_CLASSES@
'''


class RadioInterruptRecoveryTests(unittest.TestCase):
    def test_actual_wrapper_header_uses_actual_radio_base(self):
        # Keep Mesh/Dispatcher and the wrapper header real: the common test
        # Mesh.h gives Radio a virtual destructor that production does not.
        with tempfile.TemporaryDirectory(prefix="meshcore-radio-header-") as tmp:
            folder = Path(tmp)
            (folder / "RadioLib.h").write_text(r'''
#pragma once
#include <cstdint>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_NC 0xffffffff
class PhysicalLayer {
public:
 int setOutputPower(int8_t){return 0;}
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

    def source(self):
        wrapper = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        custom = (ROOT / "src/helpers/radiolib/CustomLR2021.h").read_text()
        callbacks = wrapper[wrapper.index("#define RADIO_INTERRUPT_INTERNAL_"):
                            wrapper.index("// The Adafruit nRF52")]
        callbacks += "\n" + wrapper[wrapper.index("static\n#if defined(ESP8266)"):
                                      wrapper.index("bool RadioLibWrapper::registerInterruptAction()")]
        names = [("bool", "registerInterruptAction"), ("void", "unregisterInterruptAction"),
                 ("bool", "hasPendingRadioInterrupt"), ("bool", "claimRadioInterrupt"),
                 ("void", "requestRestartRecv"), ("bool", "isPacketReady"),
                 ("bool", "startSendRaw"), ("bool", "isSendComplete"), ("int", "recvRaw"),
                 ("void", "onSendFinished")]
        methods = "\n".join(method(wrapper, f"{kind} RadioLibWrapper::{name}(")
                            for kind, name in names)
        lr_methods = "\n".join(method(custom, signature) for signature in
                               ("int16_t readData(", "bool isReceiving()", "int16_t startReceive()"))
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
        return (PRELUDE.replace("@LR_METHODS@", lr_methods).replace("@CALLBACKS@", callbacks)
                .replace("@METHODS@", methods).replace("@CONCRETE_CLASSES@", "\n".join(concrete)))

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


if __name__ == "__main__":
    unittest.main()
