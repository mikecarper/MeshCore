"""Fault-inject real bridge sources: pool pressure, TX backoff and lost callbacks."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

MOCKS = r'''
#include <vector>
#include <algorithm>
#include <array>
#define MAX_TRANS_UNIT 255
using portMUX_TYPE=int;
#define portMUX_INITIALIZER_UNLOCKED 0
struct esp_now_recv_info_t {const uint8_t* src_addr;};
struct esp_now_send_info_t {const uint8_t* des_addr;};
constexpr int ESP_ERR_ESPNOW_NO_MEM=-77;
uint32_t now_ms=100;
uint32_t millis(){return now_ms;}
int send_result=0,send_calls=0;
std::vector<std::vector<uint8_t>> sent;
int esp_now_send(const uint8_t*,const uint8_t* bytes,size_t len){
 assert(espnow_active);++send_calls;
 if(send_result!=ESP_OK)return send_result;
 sent.emplace_back(bytes,bytes+len);return ESP_OK;
}
namespace mesh {
struct RTCClock {};
struct Packet {
 uint8_t data[MAX_TRANS_UNIT]={};uint16_t length=4;
 bool readFrom(const uint8_t* bytes,size_t len){
  if(len==0||len>sizeof(data)||bytes[0]==0xee)return false;
  memcpy(data,bytes,len);length=len;return true;
 }
 int getRawLength()const{return length;}
 uint16_t writeTo(uint8_t* out)const{memcpy(out,data,length);return length;}
 bool violatesRoutePolicy()const{return false;}
};
struct PacketManager {
 std::array<Packet,8> pool;std::array<bool,8> used{};
 bool exhausted=false;unsigned frees=0,allocs=0;
 std::vector<uint8_t> received;
 Packet* allocNew(){
  if(exhausted)return nullptr;
  for(size_t i=0;i<used.size();++i)if(!used[i]){used[i]=true;++allocs;return &pool[i];}
  return nullptr;
 }
 void free(Packet* p){size_t i=p-pool.data();assert(i<used.size()&&used[i]);used[i]=false;++frees;}
 void queueInbound(Packet* p,uint32_t){received.push_back(p->data[0]);free(p);}
};
}
struct NodePrefs {
 int bridge_format=mesh::bridge::ESPNOW_FORMAT_RAW;
 uint8_t bridge_channel=6;uint32_t bridge_baud=115200,bridge_delay=1;
 char bridge_secret[32]="fleet-test";
};
struct Seen {
 std::vector<uint8_t> ids;
 bool wasSeen(const mesh::Packet* p){return std::find(ids.begin(),ids.end(),p->data[0])!=ids.end();}
 void markSeen(const mesh::Packet* p){if(!wasSeen(p))ids.push_back(p->data[0]);}
 void clear(const mesh::Packet* p){ids.erase(std::remove(ids.begin(),ids.end(),p->data[0]),ids.end());}
};
class BridgeBase {
public:
 static constexpr uint16_t BRIDGE_PACKET_MAGIC=0xc03e;
 static constexpr uint16_t BRIDGE_MAGIC_SIZE=2,BRIDGE_LENGTH_SIZE=2,BRIDGE_CHECKSUM_SIZE=2;
 NodePrefs* _prefs;mesh::PacketManager* _mgr;mesh::RTCClock* _rtc;
 bool _initialized=false,allowed=true;Seen _seen_packets;
 BridgeBase(NodePrefs* prefs,mesh::PacketManager* mgr,mesh::RTCClock* rtc):_prefs(prefs),_mgr(mgr),_rtc(rtc){}
 virtual ~BridgeBase()=default;
 virtual void begin()=0;virtual void end()=0;virtual void loop()=0;
 virtual void sendPacket(mesh::Packet*)=0;virtual void onPacketReceived(mesh::Packet*)=0;
 bool allowsPacket(const mesh::Packet*)const{return allowed;}
 static uint16_t fletcher16(const uint8_t*,size_t);
 bool validateChecksum(const uint8_t*,size_t,uint16_t);
 void handleReceivedPacket(mesh::Packet*);
};
class Stream {
public:
 std::vector<uint8_t> input,output;size_t position=0,write_limit=1000;unsigned reads=0;
 int available(){return input.size()-position;}
 int read(){++reads;return position<input.size()?input[position++]:-1;}
 size_t write(const uint8_t* bytes,size_t length){
  length=std::min(length,write_limit);output.insert(output.end(),bytes,bytes+length);return length;
 }
};
class HardwareSerial:public Stream {
public:
 bool setPins(int16_t,int16_t){return true;}
 void begin(uint32_t){}void end(){}explicit operator bool()const{return true;}
};
'''


def code():
    # Reuse the existing SDK/facade model, with its separate caches and shared
    # infrastructure owner. Only hardware APIs and Packet allocation are mocked.
    sdk = (ROOT / "test/fixtures/espnow_wifi_handoff.cpp").read_text().split("class Bridge {")[0]
    sdk = sdk.replace("int esp_now_deinit() { assert(espnow_active); espnow_active=false; return ESP_OK; }",
                      "int esp_now_deinit() { assert(espnow_active); if(failure==18)return -1; espnow_active=false; return ESP_OK; }")
    base = (ROOT / "src/helpers/bridges/BridgeBase.cpp").read_text()
    base_methods = "\n".join(method(base, signature) for signature in (
        "uint16_t BridgeBase::fletcher16(", "bool BridgeBase::validateChecksum(",
        "void BridgeBase::handleReceivedPacket("))
    headers, sources = [], []
    for name in ("RS232Bridge", "ESPNowBridge"):
        header = (ROOT / f"src/helpers/bridges/{name}.h").read_text()
        source = (ROOT / f"src/helpers/bridges/{name}.cpp").read_text()
        headers.append(re.sub(r"^\s*#(?:include|pragma).*?$", "", header, flags=re.MULTILINE).replace("private:", "public:"))
        sources.append(re.sub(r"^\s*#include.*?$", "", source, flags=re.MULTILINE))
    return sdk + MOCKS + "\n".join(headers) + base_methods + "\n".join(sources)


COMMON = r'''
static std::vector<uint8_t> uart_frame(uint8_t id){
 uint8_t payload[4]={id,1,2,3};uint16_t sum=BridgeBase::fletcher16(payload,4);
 return {0xc0,0x3e,0,4,id,1,2,3,uint8_t(sum>>8),uint8_t(sum)};
}
static void append(HardwareSerial& serial,const std::vector<uint8_t>& frame){
 serial.input.insert(serial.input.end(),frame.begin(),frame.end());
}
static mesh::Packet packet(uint8_t id,size_t length=4){
 mesh::Packet p;p.length=length;memset(p.data,id,sizeof(p.data));return p;
}
static void complete(ESPNowBridge& bridge,int status=ESP_NOW_SEND_SUCCESS){
 const uint8_t mac[6]={255,255,255,255,255,255};
 bridge.onDataSent(mac,static_cast<esp_now_send_status_t>(status));bridge.loop();
}
'''


class BridgeRecoveryTests(unittest.TestCase):
    def run_fixture(self, checks, versions=(50200,)):
        with tempfile.TemporaryDirectory(prefix="meshcore-bridge-recovery-") as tmp:
            cpp, exe = Path(tmp) / "test.cpp", Path(tmp) / "test"
            cpp.write_text(code() + COMMON + checks)
            for version in versions:
                with self.subTest(idf=version):
                    result = subprocess.run([
                        os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra",
                        "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                        "-DESP32=1", "-DWITH_ESPNOW_BRIDGE=1", "-DWITH_RS232_BRIDGE=1",
                        f"-DESP_IDF_VERSION={version}", f"-DESP_IDF_VERSION_MAJOR={version//10000}",
                        "-I", str(ROOT / "src"), str(cpp), "-o", str(exe),
                    ], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run([str(exe)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_uart_pool_pressure_order_parser_bounds_and_restart(self):
        self.run_fixture(r'''
int main(){
 NodePrefs prefs;mesh::PacketManager mgr;mesh::RTCClock rtc;HardwareSerial serial;
 RS232Bridge bridge(&prefs,serial,1,2,&mgr,&rtc);bridge.begin();
 append(serial,uart_frame(1));append(serial,uart_frame(2));mgr.exhausted=true;
 bridge.loop();assert(bridge._rx_packet_ready&&serial.available()==10&&mgr.received.empty());
 const auto read=serial.reads;bridge.loop();assert(serial.reads==read&&mgr.received.empty());
 mgr.exhausted=false;bridge.loop();assert((mgr.received==std::vector<uint8_t>{1,2}));
 append(serial,{0xc0,0x3e,0,0}); // empty length must resynchronize
 append(serial,{0xc0,0x3e,1,1}); // oversized length must resynchronize
 auto corrupt=uart_frame(3);corrupt.back()^=1;append(serial,corrupt);
 auto invalid=uart_frame(0xee);append(serial,invalid);append(serial,uart_frame(4));
 for(unsigned i=0;i<4;++i)bridge.loop();
 assert((mgr.received==std::vector<uint8_t>{1,2,4}));
 serial.input.insert(serial.input.end(),2000,0x00);const auto before=serial.reads;
 bridge.loop();assert(serial.reads-before==512); // noisy UART has bounded loop work
 serial.position=serial.input.size();append(serial,uart_frame(5));mgr.exhausted=true;
 bridge.loop();assert(bridge._rx_packet_ready);bridge.end();mgr.exhausted=false;bridge.begin();
 bridge.loop();assert((mgr.received==std::vector<uint8_t>{1,2,4})); // held frame cannot cross restart
 auto outgoing=packet(6);serial.write_limit=2;bridge.sendPacket(&outgoing);
 assert(!bridge._seen_packets.wasSeen(&outgoing)); // failed admission can retry
 serial.write_limit=1000;bridge.sendPacket(&outgoing);assert(bridge._seen_packets.wasSeen(&outgoing));
 bridge.allowed=false;auto blocked=packet(7);auto total=serial.output.size();
 bridge.sendPacket(&blocked);assert(serial.output.size()==total);
 bridge.allowed=true;serial.input.clear();serial.position=0;
 for(uint8_t id=10;id<40;++id)append(serial,uart_frame(id));
 bridge.loop();assert(serial.available()==0&&mgr.received.size()==33); // one bounded batch preserves throughput
}
''')

    def test_espnow_rx_pool_pressure_reassembly_and_filter_ownership(self):
        self.run_fixture(r'''
int main(){
 Backend backend;backend.wifi=true;mesh::wireless::control().begin(backend);
 sdk_initialized=sdk_started=true;sdk_mode=WIFI_STA;
 NodePrefs prefs;mesh::PacketManager mgr;mesh::RTCClock rtc;ESPNowBridge bridge(&prefs,&mgr,&rtc);bridge.begin();
 const uint8_t mac[6]={1,2,3,4,5,6};auto p=packet(1);
 bridge.queueReceivedFrame(mac,p.data,p.length);mgr.exhausted=true;bridge.loop();
 assert(bridge._rx_count==1&&mgr.received.empty());
 mgr.exhausted=false;bridge.loop();assert((mgr.received==std::vector<uint8_t>{1}));
 mesh::espnow::ESPNowRawFrames fragments;auto full=packet(2,255);
 assert(mesh::espnow::encodeEspNowRawFrames(full.data,full.length,fragments));
 bridge.queueReceivedFrame(mac,fragments.data[0],fragments.lengths[0]);bridge.loop();
 bridge.queueReceivedFrame(mac,fragments.data[1],fragments.lengths[1]);mgr.exhausted=true;
 bridge.loop();assert(bridge._rx_count==1&&mgr.received.size()==1);
 mgr.exhausted=false;bridge.loop();assert((mgr.received==std::vector<uint8_t>{1,2}));
 auto blocked=packet(3);bridge.allowed=false;bridge.queueReceivedFrame(mac,blocked.data,blocked.length);
 bridge.loop();assert(mgr.received.size()==2);bridge.allowed=true;
 auto bad=packet(0xee);bridge.queueReceivedFrame(mac,bad.data,bad.length);bridge.loop();
 assert(mgr.received.size()==2&&mgr.allocs==mgr.frees);
 bridge.end();prefs.bridge_format=mesh::bridge::ESPNOW_FORMAT_WRAPPED;bridge.begin();
 auto wrapped=packet(4);uint8_t bytes[8]={0xc0,0x3e};uint16_t sum=BridgeBase::fletcher16(wrapped.data,4);
 bytes[2]=sum>>8;bytes[3]=sum;memcpy(bytes+4,wrapped.data,4);assert(bridge.xorCrypt(bytes+2,6));
 bridge.queueReceivedFrame(mac,bytes,sizeof(bytes));mgr.exhausted=true;bridge.loop();
 assert(bridge._rx_count==1);mgr.exhausted=false;bridge.loop();
 assert((mgr.received==std::vector<uint8_t>{1,2,4}));
 bridge.end();assert(mgr.allocs==mgr.frees&&sdk_started); // shared WiFi survives
}
''')

    def test_espnow_start_retry_budget_wrap_and_fragment_order(self):
        self.run_fixture(r'''
int main(){
 Backend backend;backend.wifi=true;mesh::wireless::control().begin(backend);
 sdk_initialized=sdk_started=true;sdk_mode=WIFI_STA;
 NodePrefs prefs;mesh::PacketManager mgr;mesh::RTCClock rtc;ESPNowBridge bridge(&prefs,&mgr,&rtc);bridge.begin();
 auto p=packet(1,255);bridge.sendPacket(&p);send_result=ESP_ERR_ESPNOW_NO_MEM;
 now_ms=UINT32_MAX-19;bridge.loop();assert(send_calls==1&&bridge._tx_count==1);
 for(unsigned i=0;i<3;++i){now_ms+=19;bridge.loop();assert(send_calls==int(i+1));++now_ms;bridge.loop();}
 assert(send_calls==4&&bridge._tx_count==0&&!bridge._seen_packets.wasSeen(&p));
 send_result=ESP_OK;bridge.sendPacket(&p);bridge.loop();assert(sent.size()==1);
 complete(bridge);assert(sent.size()==2&&bridge._tx_count==1);complete(bridge);assert(bridge._tx_count==0);
 mesh::espnow::ESPNowRawReassembler assembler;uint8_t out[255],mac[6]={};size_t size=0;
 assembler.acceptFrame(mac,sent[0].data(),sent[0].size(),now_ms,out,sizeof(out),size);
 assert(assembler.acceptFrame(mac,sent[1].data(),sent[1].size(),now_ms,out,sizeof(out),size)==mesh::espnow::ESPNowRawReassemblyResult::PACKET_COMPLETE);
 assert(size==255&&!memcmp(out,p.data,size));
 auto fail=packet(2,255);bridge.sendPacket(&fail);bridge.loop();const size_t prior=sent.size();
 for(unsigned i=0;i<3;++i){
   complete(bridge,ESP_NOW_SEND_FAIL);assert(sent.size()==prior+i&&bridge._tx_count==1);
   now_ms+=19;bridge.loop();assert(sent.size()==prior+i);
   ++now_ms;bridge.loop();assert(sent.size()==prior+i+1);
 }
 complete(bridge,ESP_NOW_SEND_FAIL);assert(bridge._tx_count==0); // bounded budget, no orphan sibling
 for(size_t i=prior-1;i<sent.size();++i)assert(sent[i]==sent[prior-1]); // retries are part0 only
 auto two=packet(3,255);bridge.sendPacket(&two);bridge.loop();send_result=ESP_ERR_ESPNOW_NO_MEM;
 complete(bridge);assert(bridge._tx_count==1&&bridge._tx_queue[bridge._tx_tail].next_frame==1);
 send_result=ESP_OK;now_ms+=20;bridge.loop();complete(bridge);assert(bridge._tx_count==0);
 bridge.end();
}
''', (40400, 50200, 50500))

    def test_missing_callback_restarts_session_and_failed_teardown_blocks_admission(self):
        self.run_fixture(r'''
int main(){
 Backend backend;backend.wifi=true;mesh::wireless::control().begin(backend);
 sdk_initialized=sdk_started=true;sdk_mode=WIFI_STA;
 NodePrefs prefs;mesh::PacketManager mgr;mesh::RTCClock rtc;ESPNowBridge bridge(&prefs,&mgr,&rtc);bridge.begin();
 auto p=packet(1,255),waiting=packet(2);bridge.sendPacket(&p);bridge.sendPacket(&waiting);bridge.loop();
 now_ms+=999;bridge.loop();assert(send_calls==1&&bridge._tx_count==2);
 ++now_ms;bridge.loop();assert(bridge._initialized&&bridge._tx_count==0&&send_calls==1);
 assert(!bridge._seen_packets.wasSeen(&waiting)&&sdk_started); // untouched WiFi, unsent can retry
 complete(bridge);assert(send_calls==1); // callback arriving before a new send cannot fake progress
 bridge.sendPacket(&waiting);bridge.loop();complete(bridge);assert(bridge._tx_count==0);
 auto bad=packet(3,255);bridge.sendPacket(&bad);bridge.loop();failure=18;now_ms+=1000;bridge.loop();
 assert(!bridge._initialized&&bridge._sdk_teardown_failed&&espnow_active);
 const int started=send_calls;bridge.sendPacket(&p);bridge.loop();complete(bridge);
 bridge.begin();assert(!bridge._initialized&&send_calls==started); // old driver still owns accepted TX
 failure=0;bridge.begin();assert(bridge._initialized&&!bridge._sdk_teardown_failed);
 auto good=packet(4);bridge.sendPacket(&good);bridge.loop();complete(bridge);
 assert(bridge._tx_count==0&&send_calls==started+1);bridge.end();
}
''')


if __name__ == "__main__":
    unittest.main()
