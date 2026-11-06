#include <cstdint>
#include <cstddef>
#include <cassert>
#include <vector>
#include <string>
#include <type_traits>
#include <cmath>
#include <limits>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNSUPPORTED -2
#define RADIOLIB_ERR_UNKNOWN -3
#define RADIOLIB_ERR_PACKET_TOO_LONG -4
#define RADIOLIB_ERR_PACKET_TOO_SHORT -30
#define RADIOLIB_ERR_TX_TIMEOUT -5
#define RADIOLIB_ERR_WRONG_MODEM -20
#define RADIOLIB_SX126X_LORA_HEADER_IMPLICIT 1
#define RADIOLIB_SX126X_RX_TIMEOUT_INF 0xFFFFFFUL
#define RADIOLIB_SX126X_PACKET_TYPE_LORA 1
#define RADIOLIB_SX126X_MAX_PACKET_LENGTH 255
#define RADIOLIB_SX126X_IRQ_ALL 0x43FF
#define RADIOLIB_SX126X_IRQ_TX_DONE 1
#define RADIOLIB_SX126X_IRQ_TIMEOUT 0x200
#define RADIOLIB_IRQ_TIMEOUT 9
#define RADIOLIB_SX126X_CMD_SET_TX_CONTINUOUS_WAVE 0xD1
#define RADIOLIB_SX126X_LORA_CR_4_8 4
#define RADIOLIB_SX126X_LORA_CRC_ON 1
#define RADIOLIB_ASSERT(rc) do { if(rc) return rc; } while(false)
using RadioLibTime_t=uint32_t;
enum ModemType_t {RADIOLIB_MODEM_FSK,RADIOLIB_MODEM_LORA,RADIOLIB_MODEM_LRFHSS,RADIOLIB_MODEM_NONE};
struct DataRate_t {struct {uint8_t spreadingFactor=9;float bandwidth=125;uint8_t codingRate=5;} lora;};
struct PacketConfig_t {struct {uint16_t preambleLength=32;bool implicitHeader=false,crcEnabled=true,ldrOptimize=false;} lora;};
struct ConfigFSK_t {};struct ConfigBPSK_t {};struct ConfigLRFHSS_t {};
enum RadioModeType_t {RADIOLIB_RADIO_MODE_NONE,RADIOLIB_RADIO_MODE_RX,RADIOLIB_RADIO_MODE_TX};
struct RadioModeConfig_t {
 struct {size_t len=0;uint32_t timeout=RADIOLIB_SX126X_RX_TIMEOUT_INF,irqFlags=0x123,irqMask=2;} receive;
 struct {const uint8_t* data=nullptr;size_t len=0;} transmit;
};
struct Driver {
 std::vector<std::string> calls;
 int fail_at=-1;int16_t failure=-777;uint8_t modem=1,headerType=0,implicitLen=255,crcTypeLoRa=1,invertIQEnabled=0;
 uint8_t spreadingFactor=9,codingRate=1,ldrOptimize=0;float bandwidthKhz=125;
 uint16_t preambleLengthLoRa=32;uint32_t rxTimeout=0;
 int16_t start_rc=0,finish_rc=0;size_t sent_len=0,toa_len=0;const uint8_t* sent_data=nullptr;
 uint8_t sent_addr=0;bool fixed_airtime=false;RadioLibTime_t airtime_us=1000;
 RadioModeType_t stagedMode=RADIOLIB_RADIO_MODE_NONE;
 uint16_t flags=0,mask=0;std::vector<unsigned> packet;
 struct Mod {
   Driver* owner;unsigned rf=0,opcode=0;
   struct Hal {
     RadioLibTime_t now=0,tick=1;unsigned yields=0,polls=0;int irq_after_polls=0;
     RadioLibTime_t millis() const {return now;}
     void yield() {now+=tick;++yields;}
     bool digitalRead(unsigned pin) {
       assert(pin==17);
       const unsigned current=polls++;
       return irq_after_polls>=0 && current>=unsigned(irq_after_polls);
     }
   } clock{};
   Hal* hal=&clock;
   unsigned getIrq() const {return 17;}
   void setRfSwitchState(unsigned mode) {rf=mode;owner->calls.push_back("rf");}
   int16_t SPIwriteStream(unsigned op,const void*,size_t) {opcode=op;return owner->step("cw");}
 } module{this};Mod* mod=&module;unsigned txMode=7;
 int16_t step(const char* name) {calls.emplace_back(name);return int(calls.size())-1==fail_at?failure:0;}
 uint16_t getIrqMapped(uint32_t v) {return v^0x40;}
 int16_t standby() {return step("standby");}
 int16_t setDioIrqParams(uint16_t f,uint16_t m) {flags=f;mask=m;return step("dio");}
 int16_t setBufferBaseAddress() {return step("bases");}
 int16_t clearIrqStatus(uint16_t flags) {assert(flags==RADIOLIB_SX126X_IRQ_ALL);return step("clear");}
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
 virtual RadioLibTime_t calculateTimeOnAir(ModemType_t,DataRate_t,PacketConfig_t,size_t) {
   calls.emplace_back("fullairtime");return 4242;
 }
 // The specialization deliberately inherits this vendor tuple packing. Keep
 // its CR normalization in the adapter so tests exercise virtual dispatch.
 RadioLibTime_t getTimeOnAir(size_t len) {
   toa_len=len;
   if(getPacketType()!=RADIOLIB_SX126X_PACKET_TYPE_LORA) return RadioLibTime_t(RADIOLIB_ERR_WRONG_MODEM);
   if(fixed_airtime) return airtime_us;
   uint8_t cr=codingRate;
   if(cr<5) cr+=4;else if(cr==7) ++cr;
   DataRate_t dr;dr.lora={spreadingFactor,bandwidthKhz,cr};
   PacketConfig_t pc;pc.lora={preambleLengthLoRa,headerType==RADIOLIB_SX126X_LORA_HEADER_IMPLICIT,bool(crcTypeLoRa),bool(ldrOptimize)};
   return calculateTimeOnAir(RADIOLIB_MODEM_LORA,dr,pc,len);
 }
 int16_t startTransmit(const uint8_t* data,size_t len,uint8_t addr) {
   sent_data=data;sent_len=len;sent_addr=addr;const int16_t rc=step("starttx");return rc?rc:start_rc;
 }
 int16_t finishTransmit() {const int16_t rc=step("finish");return rc?rc:finish_rc;}
 virtual int16_t transmit(const uint8_t*,size_t,uint8_t=0) {return step("fulltransmit");}
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
static_assert(std::is_same<decltype(&Concrete::getTimeOnAir),decltype(&Driver::getTimeOnAir)>::value,
              "The vendor airtime tuple and CR normalization remain inherited");

// Independent symbol-count oracle. The explicit modulo reproduces the pinned
// driver's 32-bit multiplication even on hosts with 64-bit unsigned long.
RadioLibTime_t airtimeOracle(const DataRate_t& dr,const PacketConfig_t& pc,size_t len) {
 const int sf=dr.lora.spreadingFactor;
 const int payload_bits=8*int(len)+16*pc.lora.crcEnabled-4*sf+(sf<=6?0:8)+(pc.lora.implicitHeader?0:20);
 const unsigned groups=unsigned(std::ceil(double(payload_bits>0?payload_bits:0)/(4*(sf-(pc.lora.ldrOptimize?2:0)))));
 const uint64_t quarters=uint64_t(pc.lora.preambleLength+8)*4+(sf<=6?25:17)+uint64_t(groups)*dr.lora.codingRate*4;
 const uint32_t symbol_us=uint32_t(float(uint32_t(10000)<<sf)/(dr.lora.bandwidth*10));
 return uint32_t(uint64_t(symbol_us)*quarters)/4;
}

void checkAirtime(bool trimmed) {
 Concrete d;Driver* api=&d;DataRate_t dr;PacketConfig_t pc;
 if(!trimmed) {
   assert(api->calculateTimeOnAir(RADIOLIB_MODEM_LORA,dr,pc,12)==4242);
   assert(d.calls==std::vector<std::string>{"fullairtime"});return;
 }
 for(uint8_t sf=5;sf<=12;++sf) for(float bw : {7.8f,10.4f,15.6f,20.8f,31.25f,41.7f,62.5f,125.0f,250.0f,500.0f})
  for(uint8_t cr=4;cr<=8;++cr) for(uint16_t preamble : {8,16,32,512,65528})
   for(size_t len : {0U,1U,7U,8U,12U,253U,255U}) for(unsigned bits=0;bits<8;++bits) {
    dr.lora={sf,bw,cr};pc.lora={preamble,bool(bits&1),bool(bits&2),bool(bits&4)};
    assert(api->calculateTimeOnAir(RADIOLIB_MODEM_LORA,dr,pc,len)==airtimeOracle(dr,pc,len));
   }
 for(auto modem : {RADIOLIB_MODEM_FSK,RADIOLIB_MODEM_LRFHSS,RADIOLIB_MODEM_NONE})
   assert(api->calculateTimeOnAir(modem,dr,pc,12)==RadioLibTime_t(RADIOLIB_ERR_WRONG_MODEM));
 for(uint8_t raw_cr=0;raw_cr<=7;++raw_cr) {
   d.codingRate=raw_cr;d.spreadingFactor=7;d.bandwidthKhz=125;d.preambleLengthLoRa=16;
   dr.lora={7,125,uint8_t(raw_cr<5?raw_cr+4:raw_cr==7?8:raw_cr)};
   pc.lora={16,false,true,false};d.calls.clear();
   assert(d.getTimeOnAir(12)==airtimeOracle(dr,pc,12));
   assert(d.calls==std::vector<std::string>{"packettype"});
 }
}

void checkBlocking(bool trimmed,bool faults) {
 const uint8_t bytes[]={11,12};
 if(!trimmed) {
   Concrete d;Driver* api=&d;assert(api->transmit(bytes,2,7)==0);
   assert(d.calls==std::vector<std::string>{"fulltransmit"});return;
 }
 if(!faults) {
   for(RadioLibTime_t start : {0U,std::numeric_limits<uint32_t>::max()-4}) {
     Concrete d;d.fixed_airtime=true;d.module.clock.now=start;d.module.clock.irq_after_polls=9;
     Driver* api=&d;assert(api->transmit(bytes,2,7)==0);
     assert(d.calls==std::vector<std::string>({"standby","packettype","starttx","packettype","finish"}));
     assert(d.toa_len==2&&d.sent_len==2&&d.sent_data==bytes&&d.sent_addr==7);
     assert(d.module.clock.yields==10&&d.module.clock.polls==10);
   }
   for(uint8_t cr : {0,4,5,7}) for(uint8_t crc : {0,1}) for(size_t len : {0U,7U,8U,253U,254U,255U,256U}) {
     Concrete d;d.codingRate=cr;d.crcTypeLoRa=crc;d.fixed_airtime=true;
     const int16_t expected=cr>4&&len<8?RADIOLIB_ERR_PACKET_TOO_SHORT:
       (len>255||(cr>4&&crc==1&&len>253)?RADIOLIB_ERR_PACKET_TOO_LONG:0);
     assert(d.transmit(bytes,len,0)==expected);
     if(expected) assert(d.calls==std::vector<std::string>{"standby"});
     else assert(d.sent_len==len&&d.module.clock.yields==1);
   }
   return;
 }
 Concrete standby;standby.fail_at=0;
 assert(standby.transmit(bytes,2)==standby.failure&&standby.calls==std::vector<std::string>{"standby"});
 Concrete start;start.fixed_airtime=true;start.start_rc=-710;
 assert(start.transmit(bytes,2)==-710);
 assert(start.calls==std::vector<std::string>({"standby","packettype","starttx"})&&start.module.clock.yields==0);
 Concrete finish;finish.fixed_airtime=true;finish.finish_rc=-711;
 assert(finish.transmit(bytes,2)==-711&&finish.calls.back()=="finish");
 for(RadioLibTime_t begin : {0U,std::numeric_limits<uint32_t>::max()-4}) for(int16_t finish_rc : {0,-712}) {
   Concrete timeout;timeout.fixed_airtime=true;timeout.finish_rc=finish_rc;
   timeout.module.clock.now=begin;timeout.module.clock.irq_after_polls=10;
   assert(timeout.transmit(bytes,2)==RADIOLIB_ERR_TX_TIMEOUT);
   assert(timeout.calls==std::vector<std::string>({"standby","packettype","starttx","packettype","finish"}));
   // Equality with the 10ms deadline still polls IRQ; the 11th yield times
   // out before polling and retires TX even if finishTransmit itself fails.
   assert(timeout.module.clock.yields==11&&timeout.module.clock.polls==10);
 }
}

int main(int argc,char**argv) {
 assert(argc==2);std::string name=argv[1];
 constexpr bool trimmed=MESH_COMPACT_STM32_RADIO&&!MESH_ENABLE_MORSE_RX;
 if(name=="controls") {
  Concrete d;ConfigFSK_t f;ConfigBPSK_t b;ConfigLRFHSS_t l;
  for(int16_t rc : {d.beginFSK(f),d.beginFSK(1,2,3,4,5,6,7,false),d.beginBPSK(b),d.beginBPSK(1,2,3,4,false),
      d.beginLRFHSS(l),d.beginLRFHSS(1,2,3,false,4,5,false),d.setBitRate(1),d.setFrequencyDeviation(1),d.setDataShaping(1),d.setEncoding(1)})
    assert(rc==(trimmed?RADIOLIB_ERR_UNSUPPORTED:0));
  assert(d.calls.size()==(trimmed?0U:10U));
 } else if(name=="airtime") {
  checkAirtime(trimmed);
 } else if(name=="blocking" || name=="blocking_faults") {
  checkBlocking(trimmed,name=="blocking_faults");
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
