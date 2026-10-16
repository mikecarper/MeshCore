"""Host-only Ethernet power-off ownership, shared registry and legacy lifecycle."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_rak_companion_ethernet_worker import build_worker

ROOT = Path(__file__).resolve().parents[1]
ARDUINO = """#pragma once
#include <cstddef>
#include <cstdint>
#define INPUT 0
#define OUTPUT 1
#define HIGH 1
void pinMode(int,int);
"""
FLAGS = ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
         "-fsanitize=address,undefined", "-fno-pie", "-no-pie"]


def run_sources(test, sources, cases=("0",), negative=False, extra_files=None):
    with tempfile.TemporaryDirectory(prefix="ethernet-terminal-") as folder:
        work = Path(folder)
        (work / "Arduino.h").write_text(ARDUINO)
        for name, content in (extra_files or {}).items():
            (work / name).write_text(content)
        paths = []
        for index, source in enumerate(sources):
            path = work / f"part{index}.cpp"
            path.write_text(source)
            paths.append(str(path))
        binary = work / "case"
        built = subprocess.run([os.environ.get("CXX", "g++"), *FLAGS,
                                "-I"+str(work), "-I"+str(ROOT/"src"),
                                *paths, "-o", str(binary)], text=True,
                               capture_output=True, timeout=60)
        test.assertEqual(built.returncode, 0, built.stderr)
        for case in cases:
            with test.subTest(case=case):
                ran = subprocess.run([str(binary), case], text=True, capture_output=True, timeout=10)
                if negative:
                    test.assertNotEqual(ran.returncode, 0)
                else:
                    test.assertEqual(ran.returncode, 0, ran.stdout+ran.stderr)


LEGACY = r'''
#include <Arduino.h>
#include <atomic>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <vector>
#include <helpers/nrf52/TerminalPeripheralShutdown.h>
static uint32_t now=0;
uint32_t millis(){return now;}
using TaskHandle_t=int*;
static int task=0,create_result=1,ends=0,pads=0,deletes=0,notifications=0,waits=0;
static bool spi=false,run_on_delay=false,stop_in_server=false;
static void(*scheduled)(void*)=nullptr;
struct Done{};
#define pdPASS 1
#define pdTRUE 1
#define taskENTER_CRITICAL() do {} while(0)
#define taskEXIT_CRITICAL() do {} while(0)
#define pdMS_TO_TICKS(ms) (ms)
#define ETHERNET_RETRY_INTERVAL_MS 30000
#define PIN_ETHERNET_RESET 21
#define PIN_ETHERNET_SS 26
#define ETHERNET_TCP_PORT 23
#define ETHERNET_CLI_LOG(...) do { if(false) std::printf(__VA_ARGS__); } while(0)
static std::atomic<bool> ethernet_running{false};
static bool ethernet_session_reset=false;
struct Spi { void begin(){assert(!spi);spi=true;} void end(){assert(spi);spi=false;++ends;} } ETHERNET_SPI_PORT;
struct EthernetClient {};
static EthernetClient ethernet_client;
struct IPAddress { unsigned operator[](unsigned) const{return 1;} };
enum {EthernetNoHardware=0,LinkOFF=0};
struct {
  int result=1,hardware=1;
  void init(Spi&,int){assert(spi);}
  int begin(uint8_t*,int,int){assert(spi);return result;}
  int hardwareStatus(){return hardware;}
  int linkStatus(){return LinkOFF;}
  IPAddress localIP(){return {};}
} Ethernet;
void serverBegin();
struct {void begin(){serverBegin();}} ethernet_server;
void generateEthernetMac(uint8_t* p){std::memset(p,0,6);}
void pinMode(int pin,int mode){
  if(mode==INPUT){assert(!spi);assert(pin==3||pin==29||pin==30||pin==26||pin==21);++pads;}
  else assert(pin==21&&mode==OUTPUT);
}
void digitalWrite(int pin,int value){assert(pin==21&&value==HIGH);}
int xTaskCreate(void(*fn)(void*),const char*,int,void*,int,TaskHandle_t* handle){
  if(create_result!=pdPASS)return create_result;
  scheduled=fn;*handle=&task;return pdPASS;
}
void xTaskNotifyGive(TaskHandle_t handle){assert(handle==&task);++notifications;}
void vTaskDelete(TaskHandle_t handle){assert(!handle);++deletes;throw Done{};}
void vTaskDelay(uint32_t ticks);
uint32_t ulTaskNotifyTake(int,uint32_t ticks);
@LEGACY@
void serverBegin(){assert(spi);if(stop_in_server)ethernet_legacy_stop.store(true);}
void runWorker(){assert(scheduled);try{scheduled(nullptr);}catch(const Done&){} scheduled=nullptr;}
void vTaskDelay(uint32_t ticks){now+=ticks;if(run_on_delay){run_on_delay=false;runWorker();}}
uint32_t ulTaskNotifyTake(int clear,uint32_t ticks){
  assert(clear==pdTRUE&&ticks==30000);++waits;
  // Model the stop notification arriving during a retry wait, not a 30s delay.
  ethernet_legacy_stop.store(true);return 1;
}
int main(int argc,char** argv){
  assert(argc==2);int which=std::atoi(argv[1]);
  ethernet_start_task();
  assert(ethernet_legacy_worker_active.load());
  if(which==0){ // Cancellation before the worker ever acquires hardware.
    run_on_delay=true;assert(mesh::nrf52::terminalEthernetShutdown(20));
    assert(!spi&&ends==0&&pads==0&&deletes==1);
  }else if(which==1){ // Active vendor call: bounded, no deletion/end/pad changes.
    spi=true;ethernet_legacy_spi_started=true;
    now=UINT32_MAX-9;assert(!mesh::nrf52::terminalEthernetShutdown(20));
    assert(now<100&&spi&&ends==0&&pads==0&&deletes==0);
    runWorker();assert(mesh::nrf52::terminalEthernetShutdown(20));
    assert(!spi&&ends==1&&pads==5&&deletes==1);
  }else if(which==2){ // Normal DHCP completion publishes no further hardware work.
    runWorker();assert(ethernet_running&&!ethernet_legacy_worker_active.load());
    assert(mesh::nrf52::terminalEthernetShutdown(0));
    assert(!spi&&ends==1&&pads==5&&deletes==1);
  }else if(which==3){ // Legacy retry uses an interruptible notification wait.
    Ethernet.result=0;runWorker();assert(waits==1&&now==0);
    assert(mesh::nrf52::terminalEthernetShutdown(0));
    assert(!spi&&ends==1&&pads==5&&deletes==1);
  }else if(which==4){ // Stop between DHCP completion and online publication.
    stop_in_server=true;runWorker();assert(!ethernet_running);
    assert(mesh::nrf52::terminalEthernetShutdown(0));
    assert(!ethernet_running&&!spi&&ends==1&&pads==5);
  }else assert(false);
  const int previous_ends=ends;
  assert(mesh::nrf52::terminalEthernetShutdown(0));assert(ends==previous_ends);
  assert(!ethernet_legacy_worker_active.load()&&ethernet_legacy_task_handle==nullptr);
  ethernet_start_task();assert(!ethernet_legacy_worker_active.load());
}
'''


SYNC = r'''
#include <Arduino.h>
#include <cassert>
#include <cstdlib>
#include <cstdint>
#include <helpers/nrf52/TerminalPeripheralShutdown.h>
static bool spi=false;
static int begins=0,ends=0,pads=0,cancels=0,closes=0,reads=0;
void pinMode(int,int mode){if(mode==INPUT){assert(!spi);++pads;}}
void digitalWrite(int,int){}
#define ETHERNET_TCP_PORT 5000
#define PIN_ETHERNET_RESET 21
#define PIN_ETHERNET_SS 26
#define ETHERNET_DEBUG_PRINTLN(...) do {} while(0)
#define ETHERNET_DEBUG_PRINT_IP(...) do {} while(0)
struct SPIClass {void begin(){assert(!spi);spi=true;++begins;} void end(){assert(spi);spi=false;++ends;}} ETHERNET_SPI_PORT;
struct IPAddress {uint8_t operator[](int) const{return 1;} operator uint32_t() const{return 1;}};
struct EthernetClient {
  explicit operator bool() const{return false;}
  bool connected() const{assert(spi);return false;}
  void stop(){assert(false&&"vendor socket stop must not block terminal shutdown");}
  int available(){assert(spi);++reads;return 0;}
  int read(){assert(spi);++reads;return -1;}
  size_t write(const uint8_t*,size_t){assert(spi);++reads;return 0;}
  IPAddress remoteIP(){return {};}
  uint16_t remotePort(){return 1;}
};
struct EthernetServer {explicit EthernetServer(int){} void begin(){} EthernetClient accept(){return {};}};
enum {EthernetNoHardware=0,LinkOFF=0};
struct {
  int result=1;
  void init(SPIClass&,int){assert(spi);}
  int begin(uint8_t*){return result;}
  int hardwareStatus(){return EthernetNoHardware;}
  int linkStatus(){return LinkOFF;}
  IPAddress localIP(){return {};}
  void maintain(){assert(spi);++reads;}
} Ethernet;
void generateEthernetMac(uint8_t*){}
namespace mesh {namespace nrf52 {struct EthernetCliTransmit {
  static bool close(uint8_t){assert(spi);++closes;return false;}
};}}
struct SerialEthernetInterface {
  virtual ~SerialEthernetInterface()=default;
  virtual void disconnectClient()=0;
  void disable(){disconnectClient();++cancels;}
  void onClientDisconnected(){}
  void onClientConnected(uint32_t){}
  bool isEnabled() const{return true;}
  virtual void loop()=0;
  virtual bool isConnected() const=0;
  virtual int available()=0;
  virtual int read()=0;
  virtual size_t write(const uint8_t*,size_t)=0;
};
@CLASS@
@METHODS@
bool otherOwner(void*,uint32_t){return false;}
int main(int argc,char** argv){
  assert(argc==2);int which=std::atoi(argv[1]);RAK13800EthernetInterface owner;
  if(which==0){assert(mesh::nrf52::terminalEthernetShutdown(0));assert(begins==0&&ends==0&&pads==0);}
  else if(which==3){
    assert(mesh::nrf52::registerTerminalEthernetShutdown(otherOwner,nullptr));
    assert(!owner.begin());assert(begins==0&&pads==0);
  }else{
    if(which==2)Ethernet.result=0;
    assert(owner.begin()==(which==1));
    assert(mesh::nrf52::terminalEthernetShutdown(0));
    assert(begins==1&&ends==1&&!spi&&pads==5&&closes==4&&cancels==1);
    assert(owner.available()==0&&owner.read()==-1&&owner.write(nullptr,0)==0);
    owner.loop();assert(reads==0);
    assert(mesh::nrf52::terminalEthernetShutdown(0));assert(ends==1&&pads==5);
    assert(!owner.begin());
  }
}
'''


class EthernetTerminalShutdownTests(unittest.TestCase):
    def test_registry_is_shared_across_translation_units_and_rejects_conflicts(self):
        app = r'''
#include <Arduino.h>
#include <cassert>
#include <helpers/nrf52/TerminalPeripheralShutdown.h>
static int calls=0;
bool stopped(void* context,uint32_t budget){assert(context==&calls&&budget==17);++calls;return false;}
bool other(void*,uint32_t){return true;}
void registerApp(){
  assert(!mesh::nrf52::registerTerminalEthernetShutdown(nullptr,nullptr));
  assert(mesh::nrf52::registerTerminalEthernetShutdown(stopped,&calls));
  assert(mesh::nrf52::registerTerminalEthernetShutdown(stopped,&calls));
  assert(!mesh::nrf52::registerTerminalEthernetShutdown(other,&calls));
  assert(!mesh::nrf52::registerTerminalEthernetShutdown(stopped,nullptr));
}
void* appOwner(){return &mesh::nrf52::terminalEthernetOwner();}
int callCount(){return calls;}
'''
        board = r'''
#include <Arduino.h>
#include <cassert>
#include <helpers/nrf52/TerminalPeripheralShutdown.h>
void pinMode(int,int){}
void registerApp();void* appOwner();int callCount();
int main(){
  assert(mesh::nrf52::terminalEthernetShutdown());registerApp();
  assert(appOwner()==&mesh::nrf52::terminalEthernetOwner());
  assert(!mesh::nrf52::terminalEthernetShutdown(17)&&callCount()==1);
}
'''
        run_sources(self, (app, board))
        # Prove this specifically detects a mistakenly TU-local registry.
        header = (ROOT/"src/helpers/nrf52/TerminalPeripheralShutdown.h").read_text()
        broken = header.replace("inline TerminalEthernetOwner& terminalEthernetOwner()",
                                "static TerminalEthernetOwner& terminalEthernetOwner()")
        self.assertNotEqual(header,broken)
        local = tuple(text.replace("<helpers/nrf52/TerminalPeripheralShutdown.h>", '"Registry.h"')
                      for text in (app,board))
        run_sources(self,local,negative=True,extra_files={"Registry.h":broken})

    def test_actual_legacy_worker_cancellation_timeout_and_retry_wait(self):
        source = (ROOT/"src/helpers/nrf52/EthernetCLI.h").read_text()
        start = source.index("static std::atomic<bool> ethernet_legacy_stop")
        end = source.index("\n#endif", source.index("static void ethernet_start_task()", start))
        run_sources(self, (LEGACY.replace("@LEGACY@", source[start:end]),), ("0","1","2","3","4"))

    def test_rak3401_rejects_electrically_conflicting_ethernet_before_hardware(self):
        with tempfile.TemporaryDirectory(prefix="rak3401-ethernet-terminal-") as folder:
            binary = build_worker(self, folder, adaptive=False, rak3401=True)
            result = subprocess.run([str(binary), "22"], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_async_companion_shutdown_is_bounded_and_initialized_only(self):
        with tempfile.TemporaryDirectory(prefix="rak-companion-ethernet-terminal-") as folder:
            binary = build_worker(self, folder)
            for scenario in (14, 23, 24, 25, 26, 27):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(binary), str(scenario)], text=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
