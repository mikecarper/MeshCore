#!/usr/bin/env python3
import importlib.util
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT=Path(__file__).resolve().parents[1]
CORE=ROOT/'test/fixtures/hwcdc_tx_backport'
spec=importlib.util.spec_from_file_location('usb_fix',ROOT/'scripts/esp32_usb_session_fix.py')
module=importlib.util.module_from_spec(spec)
# Exercise the current transform after rapid edits without timestamp-based
# bytecode caches retaining an earlier same-size fingerprint literal.
exec(compile(Path(spec.origin).read_text(),spec.origin,'exec'),module.__dict__)
RAW=(CORE/'HWCDC.cpp').read_text()
PATCHED=module.patched_hwcdc_source(RAW)

HARNESS=r'''
#include <algorithm>
#include <atomic>
#include <thread>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <mutex>
#include <vector>
using UBaseType_t=unsigned;using portBASE_TYPE=int;
using portMUX_TYPE=std::recursive_mutex;
#define portMUX_INITIALIZER_UNLOCKED {}
static thread_local unsigned critical_depth;
static thread_local bool producer_thread=false;
static std::atomic<bool> producer_attempted{false};
static void enter_mux(portMUX_TYPE* mux) {
 if(producer_thread)producer_attempted.store(true,std::memory_order_release);
 mux->lock();++critical_depth;
}
#define portENTER_CRITICAL_SAFE(m) enter_mux(m)
#define portEXIT_CRITICAL_SAFE(m) do{--critical_depth;(m)->unlock();}while(0)
#define portENTER_CRITICAL_ISR(m) portENTER_CRITICAL_SAFE(m)
#define portEXIT_CRITICAL_ISR(m) portEXIT_CRITICAL_SAFE(m)
#define portYIELD_FROM_ISR() ((void)0)
#define pdTRUE 1
#define USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY 1
#define USB_SERIAL_JTAG_INTR_SERIAL_OUT_RECV_PKT 2
#define USB_SERIAL_JTAG_INTR_BUS_RESET 4
#define ARDUINO_HW_CDC_EVENTS 5
#define ARDUINO_HW_CDC_TX_EVENT 6
#define ARDUINO_HW_CDC_RX_EVENT 7
#define ARDUINO_HW_CDC_BUS_RESET_EVENT 8
struct Ring {std::deque<std::vector<uint8_t>> data;std::vector<uint8_t> active;unsigned returns=0;} ring;
static Ring* tx_ring_buf=&ring;
static void* rx_queue=nullptr;
static uint8_t rx_data_buf[64]{};
static volatile bool connected=false;
static bool plugged=false,irq_enabled=true;
static uint32_t intr_status=0;
static unsigned fifo_limit=64,flushes=0;
static std::vector<uint8_t> staged;
static std::vector<unsigned> event_lengths;
static std::function<void()> before_empty_return;
static uint32_t usb_serial_jtag_ll_get_intsts_mask(){return intr_status;}
static void usb_serial_jtag_ll_clr_intsts_mask(uint32_t bits){intr_status&=~bits;}
static void usb_serial_jtag_ll_ena_intr_mask(uint32_t){assert(critical_depth);irq_enabled=true;}
static void usb_serial_jtag_ll_disable_intr_mask(uint32_t){assert(critical_depth);irq_enabled=false;}
static int usb_serial_jtag_ll_txfifo_writable(){return 1;}
static unsigned usb_serial_jtag_ll_write_txfifo(const uint8_t*data,unsigned len){
 unsigned count=std::min(len,fifo_limit);staged.insert(staged.end(),data,data+count);return count;
}
static void usb_serial_jtag_ll_txfifo_flush(){++flushes;}
static uint32_t usb_serial_jtag_ll_read_rxfifo(uint8_t*,unsigned){return 0;}
static bool xQueueSendFromISR(void*,uint8_t*,int*){return true;}
static uint8_t* xRingbufferReceiveUpToFromISR(Ring*r,size_t*size,unsigned limit){
 if(r->data.empty()){
   *size=0;if(before_empty_return){auto callback=before_empty_return;before_empty_return=nullptr;callback();}return nullptr;
 }
 r->active=r->data.front();r->data.pop_front();
 if(r->active.size()>limit){std::vector<uint8_t>tail(r->active.begin()+limit,r->active.end());r->data.push_front(tail);r->active.resize(limit);}
 *size=r->active.size();return r->active.data();
}
static void vRingbufferReturnItemFromISR(Ring*r,uint8_t*,int*){++r->returns;r->active.clear();}
static void vRingbufferGetInfo(Ring*r,void*,void*,void*,void*,unsigned*waiting){
 *waiting=0;for(const auto& item:r->data)*waiting+=item.size();
}
union arduino_hw_cdc_event_data_t{struct{unsigned len;}tx,rx;};
static void arduino_hw_cdc_event_post(int,int event,arduino_hw_cdc_event_data_t*data,size_t,int*){
 if(event==ARDUINO_HW_CDC_TX_EVENT)event_lengths.push_back(data->tx.len);
}
struct HWCDC {static bool isPlugged(){return plugged;}static bool isCDC_Connected();};
@SUPPORT@
@CONNECTED@
@ISR@
static void pulse(unsigned limit=64){fifo_limit=limit;intr_status=1;hw_cdc_isr_handler(nullptr);}
static void reset(){
 ring=Ring{};staged.clear();event_lengths.clear();mesh_hwcdc_tx_stash_len=0;
 mesh_hwcdc_fifo_pending=false;mesh_hwcdc_active_writers=0;mesh_hwcdc_tx_allowed=true;
 irq_enabled=true;plugged=false;connected=false;before_empty_return=nullptr;flushes=0;
}
int main(){
 // Exact suffix ownership across zero, short and full FIFO admissions, two
 // ring items and a 64-byte boundary. Later data must never precede a suffix.
 reset();std::vector<uint8_t> expected(78);for(unsigned n=0;n<expected.size();n++)expected[n]=n;
 ring.data.push_back(std::vector<uint8_t>(expected.begin(),expected.begin()+64));
 ring.data.push_back(std::vector<uint8_t>(expected.begin()+64,expected.end()));
 pulse(0);assert(ring.returns==1&&staged.empty()&&mesh_hwcdc_tx_stash_len==64&&irq_enabled);
 assert(event_lengths.back()==0&&meshEsp32HwcdcTxPending());
 pulse(1);assert(staged.size()==1&&mesh_hwcdc_tx_stash_len==63);
 pulse(17);pulse(17);pulse(29);assert(staged.size()==64&&ring.returns==1);
 assert(mesh_hwcdc_tx_stash_len==0&&meshEsp32HwcdcTxPending()); // later ring item still queued
 pulse(64);assert(ring.returns==2&&staged==expected);
 assert(mesh_hwcdc_tx_stash_len==0&&ring.data.empty()&&meshEsp32HwcdcTxPending()); // FIFO payload unacknowledged
 pulse();assert(!meshEsp32HwcdcTxPending()&&!irq_enabled); // actual host pickup empties transport
 assert(connected&&!plugged); // real IN_EMPTY outranks false SOF for progress

 // Synthetic same-thread ISR-reentrant enqueue/rearm witness. This tests
 // the locked recheck specifically; it is NOT the ordinary SMP task race.
 reset();before_empty_return=[] {ring.data.push_back({9,8,7});mesh_hwcdc_enable_tx_intr();};
 pulse();assert(irq_enabled&&meshEsp32HwcdcTxPending());pulse();pulse();
 assert((staged==std::vector<uint8_t>{9,8,7})&&!meshEsp32HwcdcTxPending());

 // Real cross-thread task producer: ring enqueue completes while the ISR
 // owns the driver mux, then protected rearm blocks until ISR unlocks. The
 // post-unlock rearm repairs progress even without the empty-branch recheck.
 reset();std::atomic<bool> start{false},queued{false},rearmed{false};
 producer_attempted.store(false);
 std::thread producer([&]{
   producer_thread=true;
   while(!start.load(std::memory_order_acquire))std::this_thread::yield();
   ring.data.push_back({6,5,4});queued.store(true,std::memory_order_release);
   mesh_hwcdc_enable_tx_intr();rearmed.store(true,std::memory_order_release);
 });
 before_empty_return=[&]{
   start.store(true,std::memory_order_release);
   while(!queued.load(std::memory_order_acquire))std::this_thread::yield();
   while(!producer_attempted.load(std::memory_order_acquire)
         && !rearmed.load(std::memory_order_acquire))std::this_thread::yield();
   assert(producer_attempted.load()&&!rearmed.load());
 };
 pulse();producer.join();assert(rearmed.load()&&irq_enabled);
 pulse();pulse();assert((staged==std::vector<uint8_t>{6,5,4})&&!meshEsp32HwcdcTxPending());

 // SDK status must remain suffix-aware after ring item return, and ordinary
 // stash flush must not claim already staged FIFO DATA has reached the host.
 reset();ring.data.push_back({1,2,3,4});pulse(2);
 assert(ring.data.empty()&&mesh_hwcdc_tx_stash_len==2&&mesh_hwcdc_fifo_pending);
 assert(!meshEsp32HwcdcDiscardTxStash()); // cannot discard live-session suffix
 mesh_hwcdc_clear_tx_stash();assert(mesh_hwcdc_tx_stash_len==0&&meshEsp32HwcdcTxPending());
 pulse();assert(!meshEsp32HwcdcTxPending());

 // Quarantine inhibits all kicks/ISR drains. Explicit detached discard waits
 // until even previously entered raw writers have stopped, then clears the
 // old epoch's stash and FIFO tracking. Resume never resurrects old bytes.
 reset();ring.data.push_back({1,2,3,4});pulse(1);unsigned old_size=staged.size();
 {mesh_hwcdc_writer_scope writer;assert(writer);
  meshEsp32HwcdcSetTxAllowed(false);assert(!irq_enabled);
  assert(!meshEsp32HwcdcDiscardTxStash());assert(meshEsp32HwcdcTxPending());
  meshEsp32HwcdcKickTx();assert(!irq_enabled);pulse();assert(staged.size()==old_size&&!irq_enabled);
 }
 assert(meshEsp32HwcdcDiscardTxStash()&&!meshEsp32HwcdcTxPending());
 meshEsp32HwcdcSetTxAllowed(true);ring.data.push_back({5,6});meshEsp32HwcdcKickTx();pulse();pulse();
 assert((staged==std::vector<uint8_t>{1,5,6}));

 // BUS_RESET itself drops the previous FIFO suffix/in-flight tracking; a SOF
 // false status poll does not. Existing async callback policy stays separate.
 reset();ring.data.push_back({1,2,3,4});pulse(1);
 assert(!HWCDC::isCDC_Connected()&&mesh_hwcdc_tx_stash_len==3&&mesh_hwcdc_fifo_pending);
 intr_status=USB_SERIAL_JTAG_INTR_BUS_RESET;hw_cdc_isr_handler(nullptr);
 assert(!connected&&mesh_hwcdc_tx_stash_len==0&&!mesh_hwcdc_fifo_pending&&!meshEsp32HwcdcTxPending());
 // Every reconnect poll repairs the wakeup; no old one-shot running flag.
 plugged=true;irq_enabled=false;assert(!HWCDC::isCDC_Connected()&&irq_enabled);
 irq_enabled=false;assert(!HWCDC::isCDC_Connected()&&irq_enabled);
}
'''

class BackportTest(unittest.TestCase):
    def compile_run(self,harness, *, expect_success=True):
        with tempfile.TemporaryDirectory(prefix='hwcdc-native-',dir=ROOT) as directory:
            tmp=Path(directory);(tmp/'test.cpp').write_text(harness)
            command=['g++','-std=c++11','-Os','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-sign-compare',
                '-pthread','-I'+str(ROOT/'src'),'-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
                str(tmp/'test.cpp'),'-o',str(tmp/'test')]
            build=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stdout+build.stderr)
            run=subprocess.run([str(tmp/'test')],capture_output=True,text=True)
            if expect_success:self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            else:self.assertNotEqual(run.returncode,0,'negative control did not fail')
    def harness(self,source=PATCHED, *, support=None, skip_reentrant=False):
        harness=HARNESS
        if skip_reentrant:
            start=harness.index(' // Synthetic same-thread ISR-reentrant')
            end=harness.index(' // Real cross-thread task producer:',start)
            harness=harness[:start]+harness[end:]
        return harness.replace('@SUPPORT@',module.HWCDC_TX_SUPPORT if support is None else support).replace('@CONNECTED@',
            body(source,'bool HWCDC::isCDC_Connected()')).replace('@ISR@',body(source,'static void hw_cdc_isr_handler('))
    def test_actual_isr_suffix_wakeup_pending_quarantine_and_reset(self):
        self.compile_run(self.harness())
    def phy_harness(self, source=PATCHED, *, s3=1, mode=1, cdc=1):
        hal=(CORE/'usb_phy_ll.h').read_text()
        self.assertEqual(hashlib.sha256(hal.encode()).hexdigest(),
                         '07f869755299e96fc73fb913bb0b9fed2ade580f12a22c058bbb07247f5c959c')
        helper_names=[('mesh_hwcdc_int_jtag_enable','usb_phy_ll_int_jtag_enable'),
                      ('mesh_hwcdc_usb_wrap_pad_enable','usb_phy_ll_usb_wrap_pad_enable')]
        hal_functions=[]
        for local,official in helper_names:
            helper=body(source,'static inline void '+local+'(')
            self.assertEqual(helper.replace(local,official,1),
                             body(hal,'static inline void '+official+'('))
            hal_functions.append(helper)
        hal_functions='\n'.join(hal_functions)
        self.assertNotIn('#include "hal/usb_phy_ll.h"',source)
        harness=r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
@CONFIG@
#define USB_SERIAL_JTAG_LL_INTR_MASK 7
#define USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY 1
#define USB_SERIAL_JTAG_INTR_SERIAL_OUT_RECV_PKT 2
#define USB_SERIAL_JTAG_INTR_BUS_RESET 4
#define ETS_USB_SERIAL_JTAG_INTR_SOURCE 1
#define ESP_OK 0
#define log_e(...) ((void)0)
#define isr_log_e(...) ((void)0)
struct usb_serial_jtag_dev_t {
 struct {unsigned phy_sel=1,pad_pull_override=1,dp_pullup=0,usb_pad_enable=0;} conf0;
} USB_SERIAL_JTAG;
struct usb_wrap_dev_t {struct {unsigned pad_enable=1;} otg_conf;} USB_WRAP;
struct {struct {unsigned sw_hw_usb_phy_sel=1,sw_usb_phy_sel=1;} usb_conf;} RTCCNTL;
static unsigned delays=0,interrupt_mask=0,allocations=0;
static void delay(unsigned ms){
 // The old TinyUSB owner must be detached before any PHY ownership change.
 assert(ms==20&&!USB_WRAP.otg_conf.pad_enable);
 assert(RTCCNTL.usb_conf.sw_hw_usb_phy_sel==1&&RTCCNTL.usb_conf.sw_usb_phy_sel==1);
 ++delays;
}
#if CONFIG_IDF_TARGET_ESP32S3 && ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
@HAL@
#endif
static int handle_token;
static void* tx_lock=&handle_token;
static void* rx_queue=&handle_token;
static void* tx_ring_buf=&handle_token;
static void* intr_handle=nullptr;
static void* xSemaphoreCreateMutex(){return &handle_token;}
static void usb_serial_jtag_ll_disable_intr_mask(unsigned){interrupt_mask=0;}
static void usb_serial_jtag_ll_ena_intr_mask(unsigned mask){interrupt_mask=mask;}
static void hw_cdc_isr_handler(void*){}
static int esp_intr_alloc(int,int,void(*)(void*),void*,void** out){
 ++allocations;*out=&handle_token;return ESP_OK;
}
struct HWCDC {
 bool setRxBufferSize(size_t){return true;}
 bool setTxBufferSize(size_t){return true;}
 void end(){}
 void begin(unsigned long baud);
};
@BEGIN@
int main(){
 HWCDC serial;
 // Simulate RTC USB registers retained across esp_restart from TinyUSB.
 serial.begin(115200);
#if CONFIG_IDF_TARGET_ESP32S3 && ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
 assert(RTCCNTL.usb_conf.sw_hw_usb_phy_sel==1&&RTCCNTL.usb_conf.sw_usb_phy_sel==0);
 assert(delays==1&&!USB_WRAP.otg_conf.pad_enable);
#else
 // C3, UART-backed Serial and native TinyUSB do not change RTC ownership.
 assert(RTCCNTL.usb_conf.sw_hw_usb_phy_sel==1&&RTCCNTL.usb_conf.sw_usb_phy_sel==1);
 assert(delays==0&&USB_WRAP.otg_conf.pad_enable);
#endif
 assert(USB_SERIAL_JTAG.conf0.phy_sel==0&&!USB_SERIAL_JTAG.conf0.pad_pull_override);
 assert(USB_SERIAL_JTAG.conf0.dp_pullup==1&&USB_SERIAL_JTAG.conf0.usb_pad_enable==1);
 assert(interrupt_mask==7&&allocations==1);
 unsigned prior_delays=delays;
 serial.begin(115200);assert(delays==prior_delays&&allocations==1);
 // Normal cold/continued HWCDC startup needs no forced disconnect delay.
 RTCCNTL.usb_conf.sw_hw_usb_phy_sel=0;RTCCNTL.usb_conf.sw_usb_phy_sel=0;
 serial.begin(115200);assert(delays==prior_delays);
}
'''
        config='\n'.join('#define '+name+' '+str(value) for name,value in [
            ('CONFIG_IDF_TARGET_ESP32S3',s3),('ARDUINO_USB_MODE',mode),('ARDUINO_USB_CDC_ON_BOOT',cdc)])
        # Suppress unused functions only for branches whose complete HAL is
        # intentionally unavailable; the real source's begin() still executes.
        harness=harness.replace('static void delay(unsigned ms)',
                                'static void __attribute__((unused)) delay(unsigned ms)')
        return harness.replace('@CONFIG@',config).replace('@HAL@',hal_functions).replace(
            '@BEGIN@',body(source,'void HWCDC::begin(unsigned long baud)'))
    def test_s3_begin_reclaims_retained_tinyusb_phy_after_host_disconnect(self):
        self.compile_run(self.phy_harness())
    def test_s3_legacy_begin_retains_otg_ownership_negative_control(self):
        # Keep verified production helpers while executing only legacy begin.
        harness=self.phy_harness().replace(body(PATCHED,'void HWCDC::begin(unsigned long baud)'),
                                          body(RAW,'void HWCDC::begin(unsigned long baud)'))
        self.compile_run(harness,expect_success=False)
    def test_s3_handoff_requires_disconnecting_the_old_controller_negative_control(self):
        source=PATCHED.replace('        mesh_hwcdc_usb_wrap_pad_enable(&USB_WRAP, false);\n','',1)
        self.assertNotEqual(source,PATCHED)
        self.compile_run(self.phy_harness(source),expect_success=False)
    def test_phy_handoff_is_excluded_from_c3_uart_and_tinyusb(self):
        for s3,mode,cdc in [(0,1,1),(1,1,0),(1,0,1)]:
            with self.subTest(s3=s3,mode=mode,cdc=cdc):
                self.compile_run(self.phy_harness(s3=s3,mode=mode,cdc=cdc))
    def test_ignored_fifo_count_negative_control(self):
        source=PATCHED.replace('sent_size = usb_serial_jtag_ll_write_txfifo(queued_buff, queued_size);',
            'usb_serial_jtag_ll_write_txfifo(queued_buff, queued_size);sent_size = queued_size;',1)
        self.compile_run(self.harness(source),expect_success=False)
    def test_missing_empty_recheck_isr_reentrant_negative_control(self):
        source=PATCHED.replace('if (waiting || mesh_hwcdc_tx_stash_len) {','if (false) {',1)
        self.compile_run(self.harness(source),expect_success=False)
    def test_cross_thread_protected_rearm_remains_live_without_recheck(self):
        source=PATCHED.replace('if (waiting || mesh_hwcdc_tx_stash_len) {','if (false) {',1)
        self.compile_run(self.harness(source,skip_reentrant=True))
    def test_legacy_unprotected_rearm_and_missing_recheck_negative_control(self):
        source=PATCHED.replace('if (waiting || mesh_hwcdc_tx_stash_len) {','if (false) {',1)
        support=module.HWCDC_TX_SUPPORT
        helper=body(support,'static inline void mesh_hwcdc_enable_tx_intr()')
        broken=helper.replace('    portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);\n','').replace(
            '    portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);\n','')
        # Leave LL's context assertion satisfied for this explicitly unsafe
        # simulated legacy producer. The SMP blocking invariant must still fail.
        broken=broken.replace('    if (mesh_hwcdc_tx_allowed) {',
            '    ++critical_depth;\n    if (mesh_hwcdc_tx_allowed) {').replace('\n}', '\n    --critical_depth;\n}')
        support=support.replace(helper,broken)
        self.compile_run(self.harness(source,support=support,skip_reentrant=True),expect_success=False)
    def test_strict_source_hash_idempotence_and_build_local_modes(self):
        self.assertEqual(module.patched_hwcdc_source(PATCHED),PATCHED)
        self.assertEqual(module.patched_hwcdc_source(RAW.replace('\n','\r\n')),PATCHED)
        for source in [RAW+'\n',PATCHED+'\n',RAW.replace('queued_size);','queued_size-1);',1)]:
            with self.assertRaises(RuntimeError):module.patched_hwcdc_source(source)
        class Node:
            def __init__(self,path):self.path=path
            def srcnode(self):return self
            def get_abspath(self):return str(self.path)
        class Env(dict):
            def __init__(self,defines,build):super().__init__(CPPDEFINES=defines);self.build=build;self.paths=[]
            def subst(self,value):assert value=='$BUILD_DIR';return str(self.build)
            def File(self,path):return path
            def AppendUnique(self,**kw):self.paths+=kw['CPPPATH']
        with tempfile.TemporaryDirectory(prefix='hwcdc-source-',dir=ROOT) as directory:
            tmp=Path(directory);src=tmp/'sdk';src.mkdir();path=src/'HWCDC.cpp';path.write_text(RAW)
            version=src/'esp_arduino_version.h';version.write_text((CORE/'esp_arduino_version.h').read_text());node=Node(path)
            for defines in [[],[('ARDUINO_USB_CDC_ON_BOOT',0),('ARDUINO_USB_MODE',1)],
                    [('ARDUINO_USB_CDC_ON_BOOT',1),('ARDUINO_USB_MODE',0)]]:
                env=Env(defines,tmp/'build');self.assertIs(module.replace_hwcdc_source(env,node),node)
                self.assertFalse((tmp/'build').exists())
            env=Env([('ARDUINO_USB_CDC_ON_BOOT',1),('ARDUINO_USB_MODE',1)],tmp/'build')
            result=module.replace_hwcdc_source(env,node)
            self.assertEqual(Path(result).read_text(),PATCHED);self.assertEqual(path.read_text(),RAW)
            self.assertEqual(env.paths,[str(src)])
            before=Path(result).stat().st_mtime_ns;module.replace_hwcdc_source(env,node)
            self.assertEqual(Path(result).stat().st_mtime_ns,before)
            version.write_text('#define ESP_ARDUINO_VERSION_MAJOR 3\n#define ESP_ARDUINO_VERSION_MINOR 3\n#define ESP_ARDUINO_VERSION_PATCH 12\n')
            self.assertIs(module.replace_hwcdc_source(env,node),node)
            version.write_text('#define ESP_ARDUINO_VERSION_MAJOR 2\n')
            with self.assertRaises(RuntimeError):module.replace_hwcdc_source(env,node)
    def test_executed_logging_kick_purge_observation_and_new_reset_epoch(self):
        source=(ROOT/'src/helpers/UsbLogging.cpp').read_text()
        functions='\n'.join(body(source,s) for s in [
            'static void handleEsp32HwcdcEvent(',
            'static void serviceEsp32HwcdcTxKickExclusive(',
            'static void purgeEsp32HwcdcQueues(',
            'bool resetUsbCompanionTransport(',
            'UsbLoggingObservation observeUsbLoggingTransport('])
        prefix=self.harness()[:self.harness().index('int main(){')]
        extra=r'''
#include <helpers/UsbAsciiBinarySwitch.h>
#define MESH_ESP32_HWCDC_SESSION_GUARD 1
#define MESH_HWCDC_PINNED_TX_BACKPORT 1
#define portENTER_CRITICAL(m) portENTER_CRITICAL_SAFE(m)
#define portEXIT_CRITICAL(m) portEXIT_CRITICAL_SAFE(m)
using esp_event_base_t=int;
static bool xPortInIsrContext(){return false;}
static uint32_t clock_ms;
static uint32_t millis(){return clock_ms;}
static void delay(uint32_t ms){clock_ms+=ms;}
static bool pads_enabled=true;
static std::function<void()> at_restore;
static bool detachEsp32HwcdcPads(){bool prior=pads_enabled;pads_enabled=false;plugged=false;return prior;}
static void restoreEsp32HwcdcPads(bool enabled){pads_enabled=enabled;plugged=enabled;if(at_restore){auto f=at_restore;at_restore=nullptr;f();}}
static void setPlatformDebugOutputEnabled(bool){}
static bool isUsbDebugLoggingEnabled(){return false;}
static void clearUsbLoggingClientActivity(){}
static void noteUsbLoggingTxComplete(){}
static std::atomic<uint32_t> esp32_hwcdc_access_generation{0},esp32_hwcdc_allowed_generation{0};
static std::atomic<uint32_t> esp32_hwcdc_bus_reset_generation{0};
static mesh::UsbSelfResetBurstGuard esp32_hwcdc_self_reset_guard;
static std::atomic<bool> esp32_hwcdc_startup_pending{false}; // post-setup runtime fixture
static std::atomic<bool> esp32_hwcdc_tx_kick_pending{false},esp32_hwcdc_tx_primed{false};
static portMUX_TYPE esp32_hwcdc_session_mux;
static std::atomic<size_t> esp32_hwcdc_tx_buffer_capacity{4096};
static bool esp32_hwcdc_cleanup_pending=false,esp32_hwcdc_restore_pad_enabled=false;
static uint32_t esp32_hwcdc_cleanup_generation=0;
static std::atomic<uint32_t> usb_logging_tx_progress{0};
static std::atomic<bool> usb_logging_tx_waiting{false};
static bool canAccessEsp32Hwcdc(void*){return esp32_hwcdc_access_generation.load()==esp32_hwcdc_allowed_generation.load();}
struct SerialMock {
 unsigned flush_calls=0;
 int availableForWrite(){unsigned waiting=0;vRingbufferGetInfo(&ring,nullptr,nullptr,nullptr,nullptr,&waiting);return 4096-waiting;}
 void flush(){++flush_calls;if(!ring.data.empty())ring.data.pop_front();}
 int read(){return -1;}
 bool isPlugged(){return plugged;}
 operator bool(){return connected&&plugged;}
 void setDebugOutput(bool){}
} Serial;
struct Exclusive {bool busy=false;bool tryRunExclusive(void(*f)(void*),void*opaque){if(busy)return false;f(opaque);return true;}} guarded_esp32_hwcdc_port;
struct Esp32HwcdcPurgeResult{bool tx_empty=false;};
struct UsbLoggingObservation{uint32_t tx_progress=0;bool supported=false,host_connected=false,reader_connected=false,pending=false;};
@FUNCTIONS@
int main(){
 // Ring capacity alone cannot stop a kick or mark a pending suffix/FIFO empty.
 reset();plugged=true;ring.data.push_back({1,2,3,4});pulse(2);
 assert(Serial.availableForWrite()==4096 && mesh_hwcdc_tx_stash_len==2);
 esp32_hwcdc_tx_kick_pending.store(true);irq_enabled=false;
 serviceEsp32HwcdcTxKickExclusive(nullptr);
 assert(esp32_hwcdc_tx_kick_pending.load()&&irq_enabled);
 assert(observeUsbLoggingTransport().pending);
 pulse();assert(mesh_hwcdc_tx_stash_len==0&&mesh_hwcdc_fifo_pending);
 assert(observeUsbLoggingTransport().pending);
 serviceEsp32HwcdcTxKickExclusive(nullptr);assert(esp32_hwcdc_tx_kick_pending.load());
 pulse();assert(!observeUsbLoggingTransport().pending);
 serviceEsp32HwcdcTxKickExclusive(nullptr);assert(!esp32_hwcdc_tx_kick_pending.load());

 // No live-session discard, and no explicit purge while an entered writer remains.
 reset();ring.data.push_back({1,2,3});pulse(1);
 Esp32HwcdcPurgeResult result;purgeEsp32HwcdcQueues(&result);assert(!result.tx_empty&&Serial.flush_calls==0);
 {mesh_hwcdc_writer_scope writer;assert(writer);meshEsp32HwcdcSetTxAllowed(false);
  purgeEsp32HwcdcQueues(&result);assert(!result.tx_empty&&Serial.flush_calls==0);}
 // Real reset closes generation+driver gates, detaches, discards old stash/FIFO
 // tracking, purges both contiguous ring pieces, and only then reopens.
 ring.data.push_back({7,8});ring.data.push_back({9});
 assert(resetUsbCompanionTransport());assert(!meshEsp32HwcdcTxPending());
 assert(mesh_hwcdc_tx_allowed&&canAccessEsp32Hwcdc(nullptr)&&pads_enabled);
 assert(Serial.flush_calls>=2&&ring.data.empty());

 // An actual newer BUS_RESET during old cleanup must not reopen the TX gate.
 // Fresh RX proves enumeration ended before the later reset callback arrives.
 reset();esp32_hwcdc_access_generation.store(0);esp32_hwcdc_allowed_generation.store(0);
 esp32_hwcdc_cleanup_pending=false;
 at_restore=[] {
  handleEsp32HwcdcEvent(nullptr,0,ARDUINO_HW_CDC_RX_EVENT,nullptr);
  handleEsp32HwcdcEvent(nullptr,0,ARDUINO_HW_CDC_BUS_RESET_EVENT,nullptr);
 };
 assert(resetUsbCompanionTransport());
 assert(!mesh_hwcdc_tx_allowed&&!canAccessEsp32Hwcdc(nullptr));
 assert(esp32_hwcdc_allowed_generation.load()<esp32_hwcdc_access_generation.load());
 assert(resetUsbCompanionTransport());assert(mesh_hwcdc_tx_allowed&&canAccessEsp32Hwcdc(nullptr));
}
'''.replace('@FUNCTIONS@',functions)
        self.compile_run(prefix+extra)
        # Negative control: a stale cleanup's unconditional resume violates the
        # same-epoch assertion despite all software queues being emptied.
        bad=functions.replace('if (esp32_hwcdc_cleanup_generation\n      == esp32_hwcdc_access_generation.load(std::memory_order_acquire)) {',
                              'if (true) {',1)
        self.assertNotEqual(bad,functions)
        self.compile_run(prefix+extra.replace(functions,bad),expect_success=False)

    def test_integration_all_three_empty_proofs_and_common_gate(self):
        source=(ROOT/'src/helpers/UsbLogging.cpp').read_text()
        self.assertEqual(source.count('meshEsp32HwcdcTxPending()'),4) # declaration plus three proofs
        self.assertEqual(source.count('meshEsp32HwcdcSetTxAllowed(false);'),2)
        self.assertEqual(source.count('meshEsp32HwcdcSetTxAllowed(true);'),1)
        self.assertIn('if (!meshEsp32HwcdcDiscardTxStash()) return;',source)
        for signature in ['static void serviceEsp32HwcdcTxKickExclusive(',
                          'static void purgeEsp32HwcdcQueues(', 'UsbLoggingObservation observeUsbLoggingTransport(']:
            self.assertIn('meshEsp32HwcdcTxPending()',body(source,signature))
        # Explicit new-hook branch and existing fallback keep the old API/layout
        # outside pinned2.0.17 C3/S3 mode1. Link hooks are strong, never silent weak stubs.
        self.assertIn('ESP_ARDUINO_VERSION_PATCH == 17',source)
        self.assertIn('CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32S3',source)
        self.assertNotIn('__attribute__((weak))',source[:source.index('namespace mesh {')])
        self.assertIn('Serial.setTxTimeoutMs(5);',source)
        self.assertIn('tx_timeout_ms / portTICK_PERIOD_MS',PATCHED)
        self.assertNotIn('max_consec_timeouts',PATCHED)

if __name__=='__main__':unittest.main(verbosity=2)
