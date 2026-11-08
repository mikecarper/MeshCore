"""Run the production terminal allocator/Stream against failing heap fixtures."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WebTerminalStreamTest(unittest.TestCase):
    def test_terminal_feature_compiles_with_wifi_without_usb(self):
        for defines, enabled in [
            (["ESP32_PLATFORM", 'WIFI_SSID="ssid"'], True),
            (["ESP32_PLATFORM", 'WIFI_SSID="ssid"', "WEBCONFIG_DISABLED"], False),
            (["NRF52_PLATFORM", "ENABLE_USB_INTERFACE"], True),
            (["ESP32_PLATFORM"], False),
        ]:
            with self.subTest(defines=defines):
                result = subprocess.run([
                    "c++", "-E", "-dM", "-x", "c++", "-", "-I" + str(ROOT / "src"),
                    "-include", str(ROOT / "examples/companion_radio/CompanionFeatures.h"),
                    *["-D" + value for value in defines],
                ], input="", text=True, capture_output=True, check=True)
                self.assertIn("#define COMPANION_FEATURE_TEXT_TERMINAL " + str(int(enabled)),
                              result.stdout)

    def test_growth_drain_failure_and_release(self):
        headers = {
            "Arduino.h": """#pragma once
#include <stdint.h>
#include <stddef.h>
class Print { public: virtual ~Print() {};
  virtual size_t write(uint8_t)=0;
  virtual size_t write(const uint8_t*,size_t)=0;
};
class Stream: public Print { public:
  virtual int available()=0; virtual int read()=0; virtual int peek()=0;
  virtual void flush()=0;
  virtual int availableForWrite(){return 0;}
};
""",
            "esp_heap_caps.h": """#pragma once
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_INTERNAL 2
#define MALLOC_CAP_8BIT 4
void* heap_caps_malloc(size_t,int);
size_t heap_caps_get_free_size(int);
""",
            "freertos/FreeRTOS.h": "#pragma once\n#define portMAX_DELAY 0\n",
            "freertos/semphr.h": """#pragma once
using SemaphoreHandle_t=void*;
inline void* xSemaphoreCreateMutex(){return reinterpret_cast<void*>(1);}
inline void xSemaphoreTake(void*,int){}
inline void xSemaphoreGive(void*){}
inline void vSemaphoreDelete(void*){}
""",
        }
        code = r"""
#include <cassert>
#include <cstdlib>
#include <map>
#include <string>
#include <esp_heap_caps.h>
bool psram = true;
size_t internal_free = 0;
std::map<void*,size_t> allocated;
void* heap_caps_malloc(size_t n,int caps) {
  if (caps & MALLOC_CAP_SPIRAM) { if (!psram) return nullptr; }
  else if(n>internal_free) return nullptr;
  void* p=std::malloc(n); if(p) allocated[p]=n; return p;
}
size_t heap_caps_get_free_size(int){return internal_free;}
void tracked_free(void* p){allocated.erase(p);std::free(p);}
#define free tracked_free
#include <helpers/esp32/WebTerminalStream.h>
#undef free
size_t bytes(){size_t n=0;for(auto& p:allocated)n+=p.second;return n;}
int main(){
  {
    WebTerminalStream terminal;
    assert(terminal.ready() && bytes()==mesh::kTerminalInitialOutputCapacity);
    std::string data(24000,'x');
    assert(terminal.write(reinterpret_cast<const uint8_t*>(data.data()),data.size())==data.size());
    assert(bytes()==32768);
    std::string result; uint64_t cursor=0; char chunk[1025]; bool lost;
    while(cursor<data.size()) {
      size_t n=terminal.readOutput(cursor,chunk,sizeof(chunk),lost);
      assert(n && !lost);result.append(chunk,n);
    }
    terminal.readOutput(cursor,chunk,sizeof(chunk),lost);
    assert(result==data && bytes()==mesh::kTerminalInitialOutputCapacity);
  }
  assert(allocated.empty());
  {
    WebTerminalStream t;
    assert(t.availableForWrite()==mesh::kTerminalInitialOutputCapacity);
    std::string data(mesh::kTerminalInitialOutputCapacity,'b');
    t.write(reinterpret_cast<const uint8_t*>(data.data()),data.size());
    assert(t.availableForWrite()==0);
    uint64_t cursor=0;char chunk[1025];bool lost=false;
    assert(t.readOutput(cursor,chunk,sizeof(chunk),lost)==1024 && !lost);
    assert(t.availableForWrite()==0); // A read response alone is not an ACK.
    assert(t.readOutput(cursor,chunk,sizeof(chunk),lost)==1024 && !lost);
    assert(t.availableForWrite()==1024); // The next cursor acknowledges it.
  }
  assert(allocated.empty());
  psram=false;internal_free=8192;
  {WebTerminalStream t;assert(!t.ready());assert(t.write('x')==0);}
  assert(allocated.empty());
  internal_free=40960;
  {
    WebTerminalStream t;assert(t.ready() && bytes()==mesh::kTerminalInitialOutputCapacity);
    std::string data(24000,'a');t.write(reinterpret_cast<const uint8_t*>(data.data()),data.size());
    assert(bytes()==mesh::kTerminalInitialOutputCapacity); // refused growth preserves the live buffer
    uint64_t cursor=0;char chunk[1025];bool lost=false;
    assert(t.readOutput(cursor,chunk,sizeof(chunk),lost)>0 && lost);
  }
  assert(allocated.empty());
}
"""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for name, text in headers.items():
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="ascii")
            binary = folder / "stream"
            for initial in (4096, 2048):
                with self.subTest(initial=initial):
                    subprocess.run([
                        "c++", "-std=c++17", "-x", "c++", "-", "-I" + str(folder),
                        "-I" + str(ROOT / "src"), "-fsanitize=address,undefined",
                        f"-DMESH_TERMINAL_INITIAL_OUTPUT_CAPACITY={initial}",
                        "-fno-pie", "-no-pie", "-o", str(binary),
                    ], input=code, text=True, check=True, capture_output=True)
                    subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
