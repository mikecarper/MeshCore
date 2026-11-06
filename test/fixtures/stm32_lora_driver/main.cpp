#include <cstdint>
#include <cstddef>
#include <cassert>
#include <vector>
#include <string>
#include <type_traits>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNSUPPORTED -2
#define RADIOLIB_ERR_UNKNOWN -3
#define RADIOLIB_ERR_PACKET_TOO_LONG -4
#define RADIOLIB_SX126X_LORA_HEADER_IMPLICIT 1
#define RADIOLIB_SX126X_RX_TIMEOUT_INF 0xFFFFFFUL
#define RADIOLIB_SX126X_PACKET_TYPE_LORA 1
#define RADIOLIB_SX126X_MAX_PACKET_LENGTH 255
#define RADIOLIB_SX126X_IRQ_TX_DONE 1
#define RADIOLIB_SX126X_IRQ_TIMEOUT 0x200
#define RADIOLIB_IRQ_TIMEOUT 9
#define RADIOLIB_SX126X_CMD_SET_TX_CONTINUOUS_WAVE 0xD1
#define RADIOLIB_ASSERT(rc) do { if(rc) return rc; } while(false)
struct ConfigFSK_t {};struct ConfigBPSK_t {};struct ConfigLRFHSS_t {};
enum RadioModeType_t {RADIOLIB_RADIO_MODE_NONE,RADIOLIB_RADIO_MODE_RX,RADIOLIB_RADIO_MODE_TX};
struct RadioModeConfig_t {
 struct {size_t len=0;uint32_t timeout=RADIOLIB_SX126X_RX_TIMEOUT_INF,irqFlags=0x123,irqMask=2;} receive;
 struct {const uint8_t* data=nullptr;size_t len=0;} transmit;
};
struct Driver {
 std::vector<std::string> calls;
 int fail_at=-1;int16_t failure=-777;uint8_t modem=1,headerType=0,implicitLen=255,crcTypeLoRa=1,invertIQEnabled=0;
 uint16_t preambleLengthLoRa=32;uint32_t rxTimeout=0;
 RadioModeType_t stagedMode=RADIOLIB_RADIO_MODE_NONE;
 uint16_t flags=0,mask=0;std::vector<unsigned> packet;
 struct Mod {
   Driver* owner;unsigned rf=0,opcode=0;
   void setRfSwitchState(unsigned mode) {rf=mode;owner->calls.push_back("rf");}
   int16_t SPIwriteStream(unsigned op,const void*,size_t) {opcode=op;return owner->step("cw");}
 } module{this};Mod* mod=&module;unsigned txMode=7;
 int16_t step(const char* name) {calls.emplace_back(name);return int(calls.size())-1==fail_at?failure:0;}
 uint16_t getIrqMapped(uint32_t v) {return v^0x40;}
 int16_t standby() {return step("standby");}
 int16_t setDioIrqParams(uint16_t f,uint16_t m) {flags=f;mask=m;return step("dio");}
 int16_t setBufferBaseAddress() {return step("bases");}
 int16_t clearIrqStatus() {return step("clear");}
 uint8_t getPacketType() {calls.emplace_back("packettype");return modem;}
 int16_t setPacketParams(uint16_t p,uint8_t crc,uint8_t len,uint8_t h,uint8_t iq) {
   packet={p,crc,len,h,iq};return step("packet");
 }
 int16_t writeBuffer(const uint8_t* data,size_t len) {assert(len==2&&data[0]==11&&data[1]==12);return step("fifo");}
 int16_t fixSensitivity() {return step("sensitivity");}
 int16_t setRfFrequency(uint32_t) {return step("freq");}
 virtual int16_t beginFSK(const ConfigFSK_t&) {return step("fsk");}
 virtual int16_t beginFSK(float,float,float,float,int8_t,uint16_t,float,bool) {return step("fsk");}
 virtual int16_t beginBPSK(const ConfigBPSK_t&) {return step("bpsk");}
 virtual int16_t beginBPSK(float,float,int8_t,float,bool) {return step("bpsk");}
 virtual int16_t beginLRFHSS(const ConfigLRFHSS_t&) {return step("lrfhss");}
 virtual int16_t beginLRFHSS(float,uint8_t,uint8_t,bool,int8_t,float,bool) {return step("lrfhss");}
 virtual int16_t setBitRate(float) {return step("bitrate");}
 virtual int16_t setFrequencyDeviation(float) {return step("deviation");}
 virtual int16_t setDataShaping(uint8_t) {return step("shaping");}
 virtual int16_t setEncoding(uint8_t) {return step("encoding");}
 virtual int16_t stageMode(RadioModeType_t,RadioModeConfig_t*) {return step("fullstage");}
 // Exact pinned RadioLib transmitDirect body: continuous wave never selects
 // another packet modem. The concrete firmware inherits it unchanged.
 int16_t transmitDirect(uint32_t frf) {
   this->mod->setRfSwitchState(this->txMode);
   int16_t state=RADIOLIB_ERR_NONE;
   if(frf!=0) state=setRfFrequency(frf);
   RADIOLIB_ASSERT(state);
   return this->mod->SPIwriteStream(RADIOLIB_SX126X_CMD_SET_TX_CONTINUOUS_WAVE,NULL,0);
 }
};
struct Concrete : Driver {
#include <helpers/radiolib/STM32WLCompactLoRa.inc>
};
static_assert(std::is_same<decltype(&Concrete::transmitDirect),decltype(&Driver::transmitDirect)>::value,
              "CW must remain the inherited pinned driver method");
int main(int argc,char**argv) {
 assert(argc==2);std::string name=argv[1];
 constexpr bool trimmed=MESH_COMPACT_STM32_RADIO&&!MESH_ENABLE_MORSE_RX;
 if(name=="controls") {
  Concrete d;ConfigFSK_t f;ConfigBPSK_t b;ConfigLRFHSS_t l;
  for(int16_t rc : {d.beginFSK(f),d.beginFSK(1,2,3,4,5,6,7,false),d.beginBPSK(b),d.beginBPSK(1,2,3,4,false),
      d.beginLRFHSS(l),d.beginLRFHSS(1,2,3,false,4,5,false),d.setBitRate(1),d.setFrequencyDeviation(1),d.setDataShaping(1),d.setEncoding(1)})
    assert(rc==(trimmed?RADIOLIB_ERR_UNSUPPORTED:0));
  assert(d.calls.size()==(trimmed?0U:10U));
 } else if(name=="cw") {
  for(unsigned frf : {0U,123U}) {
   Concrete d;assert(d.transmitDirect(frf)==0);
   assert(d.module.rf==d.txMode&&d.module.opcode==0xD1);
   assert(d.calls==(frf?std::vector<std::string>({"rf","freq","cw"}):std::vector<std::string>({"rf","cw"})));
  }
  Concrete failed;failed.fail_at=1;assert(failed.transmitDirect(123)==failed.failure&&failed.module.opcode==0);
  Concrete cw_failed;cw_failed.fail_at=1;assert(cw_failed.transmitDirect(0)==cw_failed.failure&&cw_failed.module.opcode==0xD1);
 } else if(!trimmed) {
  Concrete d;RadioModeConfig_t cfg;assert(d.stageMode(RADIOLIB_RADIO_MODE_RX,&cfg)==0&&d.calls==std::vector<std::string>{"fullstage"});
 } else if(name=="rx") {
  for(uint32_t timeout : {0xFFFFFFUL,0xFFFFFFFFUL,17UL}) for(unsigned implicit : {0U,1U}) {
   Concrete d;RadioModeConfig_t cfg;cfg.receive.len=9;cfg.receive.timeout=timeout;d.headerType=implicit;
   assert(d.stageMode(RADIOLIB_RADIO_MODE_RX,&cfg)==0);
   assert(d.calls==std::vector<std::string>({"standby","dio","bases","clear","packettype","packet"}));
   assert(d.flags==(0x123^0x40)&&d.mask==uint16_t((2|(timeout==0xFFFFFF?0:0x200))^0x40));
   assert(d.packet==std::vector<unsigned>({32,1,implicit?9U:255U,implicit,0}));
   assert(d.rxTimeout==(timeout==0xFFFFFFFF?0xFFFFFF:timeout)&&cfg.receive.timeout==d.rxTimeout);
   assert(d.stagedMode==RADIOLIB_RADIO_MODE_RX);
  }
 } else if(name=="tx") {
  Concrete d;RadioModeConfig_t cfg;const uint8_t bytes[]={11,12};cfg.transmit.data=bytes;cfg.transmit.len=2;
  assert(d.stageMode(RADIOLIB_RADIO_MODE_TX,&cfg)==0);
  assert(d.calls==std::vector<std::string>({"packettype","packet","dio","bases","fifo","clear","sensitivity"}));
  assert(d.packet==std::vector<unsigned>({32,1,2,0,0})&&d.flags==0x201&&d.mask==1&&d.stagedMode==RADIOLIB_RADIO_MODE_TX);
 } else if(name=="faults") {
  for(bool tx : {false,true}) for(int point=0;point<(tx?7:6);++point) {
   if(point==(tx?0:4)) continue; // packetType has a byte-valued API, tested below.
   Concrete d;RadioModeConfig_t cfg;const uint8_t bytes[]={11,12};cfg.transmit.data=bytes;cfg.transmit.len=2;d.fail_at=point;
   assert(d.stageMode(tx?RADIOLIB_RADIO_MODE_TX:RADIOLIB_RADIO_MODE_RX,&cfg)==d.failure);
   assert(d.calls.size()==unsigned(point+1));
   assert(d.stagedMode==(!tx&&point==5?RADIOLIB_RADIO_MODE_RX:RADIOLIB_RADIO_MODE_NONE));
  }
  for(bool tx : {false,true}) for(uint8_t modem : {0,2,255}) {
   Concrete d;RadioModeConfig_t cfg;d.modem=modem;
   assert(d.stageMode(tx?RADIOLIB_RADIO_MODE_TX:RADIOLIB_RADIO_MODE_RX,&cfg)==RADIOLIB_ERR_UNKNOWN);
   assert(d.stagedMode==RADIOLIB_RADIO_MODE_NONE&&d.packet.empty());
  }
  Concrete longpacket;RadioModeConfig_t cfg;cfg.transmit.len=256;
  assert(longpacket.stageMode(RADIOLIB_RADIO_MODE_TX,&cfg)==RADIOLIB_ERR_PACKET_TOO_LONG&&longpacket.calls.empty());
  assert(longpacket.stageMode(RADIOLIB_RADIO_MODE_NONE,&cfg)==RADIOLIB_ERR_UNSUPPORTED&&longpacket.calls.empty());
 } else assert(false);
}
