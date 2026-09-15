// Four-source bench: configure one channel once, then transmit only on request.
// RAM-only: no Mesh secrets, autonomous transmissions, SD card or flash writes.
#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <SPI.h>
#include <RadioLib.h>
#include "ProfileSwitchUsb.h"
#include "ProfilePairPlan.h"

class FixedTxHal : public ArduinoHal {
 public:
  using ArduinoHal::ArduinoHal;
  uint32_t rfWrites=0, modWrites=0;
  void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override {
#ifdef HIL_FIXED_LR1110
    if (len>=2 && out[0]==2 && out[1]==0x0B) ++rfWrites;
    if (len>=2 && out[0]==2 && out[1]==0x0F) ++modWrites;
#else
    if (len==5 && out[0]==0x86) ++rfWrites;
    if (len==5 && out[0]==0x8B) ++modWrites;
#endif
    ArduinoHal::spiTransfer(out,len,in);
  }
} radioHal(SPI, SPISettings(8000000, MSBFIRST, SPI_MODE0));
#ifdef HIL_FIXED_LR1110
LR1110 chip = new Module(&radioHal,P_LORA_NSS,P_LORA_DIO_1,P_LORA_RESET,P_LORA_BUSY);
static const uint32_t switchPins[Module::RFSWITCH_MAX_PINS]={
  RADIOLIB_LR11X0_DIO5,RADIOLIB_LR11X0_DIO6,RADIOLIB_LR11X0_DIO7,RADIOLIB_LR11X0_DIO8,RADIOLIB_NC};
static const Module::RfSwitchMode_t switchModes[]={
  {LR11x0::MODE_STBY,{LOW,LOW,LOW,LOW}},
  {LR11x0::MODE_RX,{HIGH,LOW,LOW,HIGH}},
  {LR11x0::MODE_TX,{HIGH,HIGH,LOW,HIGH}},
  {LR11x0::MODE_TX_HP,{LOW,HIGH,LOW,HIGH}},
  {LR11x0::MODE_TX_HF,{LOW,LOW,LOW,LOW}},
  {LR11x0::MODE_GNSS,{LOW,LOW,HIGH,LOW}},
  {LR11x0::MODE_WIFI,{LOW,LOW,LOW,LOW}}, END_OF_MODE_TABLE};
#else
SX1262 chip = new Module(&radioHal,P_LORA_NSS,P_LORA_DIO_1,P_LORA_RESET,P_LORA_BUSY);
#endif
bool prepared=false, failed=false;
unsigned fixedChannel=99, packetCount=0;
bool fixedPair=false;
unsigned fixedSf=10;
float fixedBw=125;
uint32_t lastSequence=0;

void prepare(unsigned channel,bool pair=false) {
  if (prepared || failed) { Serial.println("{\"error\":\"configuration locked until reboot\"}");return; }
  const float freq=(909500+1000*channel)/1000.0f;
  fixedPair=pair;
  fixedSf=pair ? pairProfiles[channel].sf : 10;
  fixedBw=pair ? pairProfiles[channel].bw : 125;
#ifdef HIL_FIXED_LR1110
  int rc=chip.begin(freq,fixedBw,fixedSf,5,RADIOLIB_LR11X0_LORA_SYNC_WORD_PRIVATE,-9,32,1.6);
  if (!rc) rc=chip.setCRC(2);
  if (!rc) rc=chip.explicitHeader();
  if (!rc) chip.setRfSwitchTable(switchPins,switchModes);
#else
  int rc=chip.begin(freq,fixedBw,fixedSf,5,RADIOLIB_SX126X_SYNC_WORD_PRIVATE,-9,32,1.8);
  if (!rc) rc=chip.setCRC(true);
  if (!rc) rc=chip.explicitHeader();
  if (!rc) rc=chip.setDio2AsRfSwitch(true);
  if (!rc) rc=chip.setCurrentLimit(140);
#endif
  prepared=rc==0;failed=rc!=0;fixedChannel=channel;
  Serial.printf("{\"prepared\":%s,\"channel\":%u,\"freq_khz\":%u,\"sf\":%u,\"bw_khz\":%.3f,\"cr\":5,\"preamble\":32,\"chip_power_dbm\":-9,\"rc\":%d,\"rf_writes\":%u,\"mod_writes\":%u}\n",
    prepared?"true":"false",channel,909500+1000*channel,fixedSf,double(fixedBw),rc,unsigned(radioHal.rfWrites),unsigned(radioHal.modWrites));
}

void transmit(unsigned channel, uint32_t seq) {
  if (!prepared || failed || channel!=fixedChannel) { Serial.println("{\"error\":\"fixed TX configuration mismatch\"}");return; }
  if (seq==lastTxResult.sequence) { emitBenchResult(lastTxResult);return; }
  if (seq<=lastSequence) { Serial.println("{\"error\":\"sequence already consumed\"}");return; }
  lastSequence=seq; // A failed call cannot be retried as another RF transmission.
  uint8_t bytes[16];memcpy(bytes,"CHS1",4);memcpy(bytes+4,&seq,4);bytes[8]=channel;
  for (unsigned i=9;i<sizeof(bytes);++i) bytes[i]=uint8_t(i^seq);
  const uint32_t rfBefore=radioHal.rfWrites,modBefore=radioHal.modWrites;
  const uint32_t started=micros();
  const int rc=chip.transmit(bytes,sizeof(bytes)); // No init, setFrequency or modulation setters here.
  const uint32_t elapsed=micros()-started;
  const unsigned rfDelta=radioHal.rfWrites-rfBefore,modDelta=radioHal.modWrites-modBefore;
  ++packetCount;
  if (rc || rfDelta || modDelta) failed=true;
  recordBenchResult(lastTxResult,seq,
    "{\"sent\":%u,\"channel\":%u,\"len\":16,\"preamble\":32,\"rc\":%d,\"power_dbm\":-9,\"sf\":%u,\"bw_khz\":%.3f,\"freq_khz\":%u,\"channel_step_khz\":1000,\"transmit_call_us\":%u,\"rf_commands\":%u,\"modulation_commands\":%u,\"packet_count\":%u,\"fixed_tx\":true,\"failed\":%s}\n",
    unsigned(seq),channel,rc,fixedSf,double(fixedBw),909500+1000*channel,unsigned(elapsed),rfDelta,modDelta,packetCount,failed?"true":"false");
}

void setup() {
  Serial.begin(115200);delay(1500);
  NRF_POWER->DCDCEN=1;
#if defined(HIL_FIXED_RAK)
  pinMode(37,OUTPUT);digitalWrite(37,HIGH);
#elif defined(HIL_FIXED_T096)
  // Production KCT8103L path, held in TX mode. -9 dBm is chip output,
  // not calibrated antenna-port power; the external PA remains in this path.
  pinMode(30,OUTPUT);digitalWrite(30,HIGH);
  pinMode(12,OUTPUT);digitalWrite(12,HIGH);
  pinMode(41,OUTPUT);digitalWrite(41,HIGH);
#elif defined(HIL_FIXED_TOWER)
  pinMode(LORA_KCT8103L_EN,OUTPUT);digitalWrite(LORA_KCT8103L_EN,HIGH);
  pinMode(LORA_KCT8103L_TX_RX,OUTPUT);digitalWrite(LORA_KCT8103L_TX_RX,HIGH);
  pinMode(EXTERNAL_WATCHDOG_DONE_PIN,OUTPUT);digitalWrite(EXTERNAL_WATCHDOG_DONE_PIN,LOW);
  pinMode(PIN_GPS_EN,OUTPUT);digitalWrite(PIN_GPS_EN,HIGH);
  pinMode(32,OUTPUT);digitalWrite(32,HIGH); // SD CS deasserted; no card access.
#endif
  delay(10);SPI.setPins(P_LORA_MISO,P_LORA_SCLK,P_LORA_MOSI);SPI.begin();
}

void loop() {
  // A watchdog started by the previous application survives a soft DFU reset.
  if (NRF_WDT->RUNSTATUS) for (unsigned i=0;i<8;++i) NRF_WDT->RR[i]=0x6E524635;
#ifdef HIL_FIXED_TOWER
  static uint32_t fed=0;
  if (millis()-fed>=10000) {
    digitalWrite(EXTERNAL_WATCHDOG_DONE_PIN,HIGH);delay(1);
    digitalWrite(EXTERNAL_WATCHDOG_DONE_PIN,LOW);fed=millis();
  }
#endif
  static char line[96];static unsigned used=0;static bool overflow=false;
  while (Serial.available()) {
    const char c=Serial.read();
    if (c=='\r' || c=='\n') {
      if (overflow) { overflow=false;used=0;Serial.println("{\"error\":\"command too long\"}");continue; }
      if (!used) continue;
      line[used]=0;used=0;
      unsigned ch,seq,len,pre,sf,bw;char tail,kind[12];
      if (!strcmp(line,"info")) {
        Serial.printf("{\"ready\":true,\"bench\":\"production-profile-switch-v8\",\"fixed_tx\":1,\"board\":\"%s\",\"prepared\":%s,\"failed\":%s,\"channel\":%u,\"packet_count\":%u,\"sd_fwid\":%u,\"app_base\":%u,\"autonomous_tx\":false}\n",
          HIL_FIXED_BOARD,prepared?"true":"false",failed?"true":"false",fixedChannel,packetCount,
          unsigned(*reinterpret_cast<volatile uint16_t*>(0x300c)),unsigned(*reinterpret_cast<volatile uint32_t*>(0x3008)));
      } else if (!strcmp(line,"pairinfo")) Serial.println(pairInfo);
      else if (sscanf(line,"pairprepare %u %c",&ch,&tail)==1 && ch<2) prepare(ch,true);
      else if (sscanf(line,"pairtx %u %u %c",&ch,&seq,&tail)==2 && ch<2 && seq && fixedPair) transmit(ch,seq);
      else if (sscanf(line,"txprepare %u %c",&ch,&tail)==1 && ch<4) prepare(ch);
      else if (sscanf(line,"scantx %u %u %u %u %u %u %c",&ch,&seq,&len,&pre,&sf,&bw,&tail)==6
                 && ch<4 && seq && len==16 && pre==32 && sf==10 && bw==125 && !fixedPair) transmit(ch,seq);
      else if (sscanf(line,"result %11s %u %c",kind,&seq,&tail)==2) replayBenchResult(kind,seq);
      else if (!strcmp(line,"reboot")) { Serial.flush();delay(20);NVIC_SystemReset(); }
      else Serial.println("{\"error\":\"unsupported fixed TX command\"}");
    } else if (!overflow) {
      if (used<sizeof(line)-1) line[used++]=c;else overflow=true;
    }
  }
  delay(1);
}
