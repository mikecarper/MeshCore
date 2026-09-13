// Standalone, RAM-only CAD experiment. No autonomous TX and no NVS writes.
#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include <driver/rtc_io.h>

#ifdef CAD_BOARD_XIAO
constexpr int PIN_CS=41, PIN_IRQ=39, PIN_RST=42, PIN_BUSY=40, PIN_SCK=7, PIN_MISO=8, PIN_MOSI=9;
#else
constexpr int PIN_CS=8, PIN_IRQ=14, PIN_RST=12, PIN_BUSY=13, PIN_SCK=9, PIN_MISO=11, PIN_MOSI=10;
#endif
Module module(PIN_CS, PIN_IRQ, PIN_RST, PIN_BUSY, SPI);
class TestRadio: public SX1262 {
public:
  bool warm=false;
  TestRadio():SX1262(&module) {}
  int16_t standby() override { return SX1262::standby(warm ? RADIOLIB_SX126X_STANDBY_XOSC : RADIOLIB_SX126X_STANDBY_RC); }
} radio;
constexpr float channels[]={909.5f,910.5f,911.5f,912.5f,913.5f,914.5f,915.5f,916.5f,917.5f,918.5f,919.5f,920.5f};
constexpr int MAX_CHANNELS=sizeof(channels)/sizeof(channels[0]);
int channelCount=4;
bool mixedProfiles=false;
int mixedSecondSf=9, mixedPreamble=32, appliedProfile=-1, rxSymbols=4;
int profileSf(int c){return mixedProfiles?(c==0?7:mixedSecondSf):5;}
float profileBw(int c){return mixedProfiles?(c==0?62.5f:500.0f):125.0f;}
uint32_t windowUs(int c){return uint32_t(rxSymbols*(1UL<<profileSf(c))*1000.0f/profileBw(c));}
uint32_t receiveLimitMs=250;
struct Timing {
  uint32_t n=0, lo=UINT32_MAX, hi=0; uint64_t sum=0;
  void add(uint32_t x){++n;sum+=x;lo=min(lo,x);hi=max(hi,x);}
  void print(const char* key){Serial.printf("\"%s\":{\"n\":%u,\"min_us\":%u,\"mean_us\":%.2f,\"max_us\":%u}",key,n,n?lo:0,n?double(sum)/n:0,hi);}
} tuneTime,cadTime,visitTime,sweepTime;
Timing channelVisit[MAX_CHANNELS];
uint32_t scans[MAX_CHANNELS]={}, hits[MAX_CHANNELS]={}, packets[MAX_CHANNELS]={}, errors=0, falseHits=0;
int mode=0, channel=0; // 0 idle, 1 CAD scan, 2 fixed RX, 3 RX-preamble scan, 4 minimal RX setup
bool automaticCad=false;
uint8_t cadCode=RADIOLIB_SX126X_CAD_ON_4_SYMB;
int cadSymbols=4;
uint32_t rxWindowUs=1024;
bool receiving=false; uint32_t receiveStart=0,sweepStart=0;
bool fem43=false;

void frontEnd(bool tx){
#ifdef CAD_BOARD_V4
  digitalWrite(2,HIGH);
  if(fem43) digitalWrite(5,tx?HIGH:LOW);
  else digitalWrite(46,LOW); // GC1109 bypass: keep test power low.
#endif
}
bool good(int16_t rc){if(rc==RADIOLIB_ERR_NONE)return true; ++errors;Serial.printf("{\"error\":%d}\n",rc);mode=0;return false;}
bool tune(int c){
  uint32_t t=micros();
  if(!good(radio.standby()))return false;
  if(!good(radio.setFrequency(channels[c],true)))return false;
  int profile=mixedProfiles?c:-1;
  if(mixedProfiles && appliedProfile!=profile){
    if(!good(radio.setSpreadingFactor(profileSf(c))))return false;
    if(!good(radio.setBandwidth(profileBw(c))))return false;
    if(!good(radio.setPreambleLength(c==0?32:mixedPreamble)))return false;
    appliedProfile=profile;
  }
  // Reserve enough time for a complete maximum-size packet on the active profile.
  receiveLimitMs=mixedProfiles?uint32_t(radio.getTimeOnAir(255)/1000)+100:250;
  tuneTime.add(micros()-t);return true;
}
void resetStats(){tuneTime={};cadTime={};visitTime={};sweepTime={};for(auto &v:channelVisit)v={};memset(scans,0,sizeof(scans));memset(hits,0,sizeof(hits));memset(packets,0,sizeof(packets));errors=falseHits=0;sweepStart=0;}
void stats(){
  Serial.printf("{\"stats\":true,\"warm\":%s,\"mode\":%d,\"automatic_cad\":%s,\"rx_window_us\":%u,\"channels\":%d,\"cad_symbols\":%d,",radio.warm?"true":"false",mode,automaticCad?"true":"false",rxWindowUs,channelCount,cadSymbols);
  tuneTime.print("tune");Serial.print(',');cadTime.print("cad");Serial.print(',');visitTime.print("visit");Serial.print(',');sweepTime.print("sweep");
  Serial.print(",\"scans\":[");for(int i=0;i<channelCount;i++){if(i)Serial.print(',');Serial.print(scans[i]);}
  Serial.print("],\"hits\":[");for(int i=0;i<channelCount;i++){if(i)Serial.print(',');Serial.print(hits[i]);}
  Serial.print("],\"packets\":[");for(int i=0;i<channelCount;i++){if(i)Serial.print(',');Serial.print(packets[i]);}
  Serial.printf("],\"mixed\":%s,\"second_sf\":%d,\"second_preamble\":%d,\"rx_symbols\":%d,\"profiles\":[",mixedProfiles?"true":"false",mixedSecondSf,mixedPreamble,rxSymbols);
  for(int i=0;i<channelCount;i++){if(i)Serial.print(',');Serial.printf("{\"sf\":%d,\"bw\":%.1f,\"window_us\":%u,\"visit_mean_us\":%.2f}",profileSf(i),profileBw(i),windowUs(i),channelVisit[i].n?double(channelVisit[i].sum)/channelVisit[i].n:0);}
  Serial.printf("],\"errors\":%u,\"false_hits\":%u}\n",errors,falseHits);
}
void beginRx(){frontEnd(false);if(good(radio.startReceive())){receiving=true;receiveStart=millis();}}
void rxLoop(){
  if(digitalRead(PIN_IRQ)){
    uint8_t data[255];size_t len=radio.getPacketLength();
    int16_t rc=radio.readData(data,min(len,sizeof(data)));
    bool valid=rc==0 && len>=12 && memcmp(data,"CAD4",4)==0 && data[8]==channel;
    if(valid){for(size_t i=9;i<len;i++)if(data[i]!=(uint8_t)(i^data[4]^channel))valid=false;}
    uint32_t seq=0;if(len>=8)memcpy(&seq,data+4,4);
    if(valid)++packets[channel];else ++errors;
    Serial.printf("{\"rx\":%u,\"ch\":%d,\"len\":%u,\"rc\":%d,\"valid\":%s,\"rssi\":%.1f,\"snr\":%.2f}\n",seq,channel,unsigned(len),rc,valid?"true":"false",radio.getRSSI(),radio.getSNR());
    radio.standby();receiving=false;sweepStart=0;
    if(mode==2)beginRx(); else channel=(channel+1)%channelCount;
  } else if(mode!=2 && millis()-receiveStart>receiveLimitMs){
    ++falseHits;radio.standby();receiving=false;channel=(channel+1)%channelCount;sweepStart=0;
  }
}
void command(char* s){
  int c=0,n=0,w=0;unsigned seq=0;
  if(!strcmp(s,"stats")){stats();return;}
  if(sscanf(s,"preamble500 %d",&n)==1 && n>=32 && n<=128 && (n%16==0 || n==40)){mode=0;receiving=false;radio.standby();mixedPreamble=n;appliedProfile=-1;resetStats();Serial.println("{\"ok\":\"preamble500\"}");return;}
  if(sscanf(s,"mixed %d",&n)==1 && n>=7 && n<=9){mode=0;receiving=false;radio.standby();mixedProfiles=true;mixedSecondSf=n;appliedProfile=-1;channelCount=2;channel=0;resetStats();Serial.println("{\"ok\":\"mixed\"}");return;}
  if(sscanf(s,"channels %d",&n)==1 && n>=1 && n<=MAX_CHANNELS){mode=0;receiving=false;radio.standby();channelCount=n;channel=0;resetStats();Serial.println("{\"ok\":\"channels\"}");return;}
  if(sscanf(s,"warm %d",&w)==1){mode=0;receiving=false;radio.warm=w;good(radio.standby());Serial.println("{\"ok\":\"warm\"}");return;}
  if(!strcmp(s,"idle")){mode=0;receiving=false;radio.standby();Serial.println("{\"ok\":\"idle\"}");return;}
  if(!strcmp(s,"scan")){radio.standby();receiving=false;resetStats();channel=0;mode=1;automaticCad=false;Serial.println("{\"ok\":\"scan\"}");return;}
  if(!strcmp(s,"cad-auto")){radio.startReceive();radio.standby();receiving=false;resetStats();channel=0;mode=1;automaticCad=true;Serial.println("{\"ok\":\"cad-auto\"}");return;}
  if(sscanf(s,"cad %d",&n)==1 && (n==1 || n==2 || n==4 || n==8 || n==16)){
    cadSymbols=n;cadCode=n==1?0:n==2?1:n==4?2:n==8?3:4;
    radio.startReceive();radio.standby();receiving=false;resetStats();channel=0;mode=1;automaticCad=true;Serial.println("{\"ok\":\"cad\"}");return;
  }
  if(sscanf(s,"rxscan %d",&n)==1 && n>=1 && n<=16){radio.standby();receiving=false;resetStats();channel=0;mode=3;rxSymbols=n;rxWindowUs=n*256;Serial.println("{\"ok\":\"rxscan\"}");return;}
  if(sscanf(s,"rxfast %d",&n)==1 && n>=1 && n<=16){radio.startReceive();radio.standby();receiving=false;resetStats();channel=0;mode=4;rxSymbols=n;rxWindowUs=n*256;Serial.println("{\"ok\":\"rxfast\"}");return;}
  if(sscanf(s,"fixed %d",&c)==1 && c>=0 && c<MAX_CHANNELS){mode=2;channel=c;receiving=false;if(tune(c))beginRx();Serial.println("{\"ok\":\"fixed\"}");return;}
  if(sscanf(s,"tx %d %u %d",&c,&seq,&n)==3 && c>=0 && c<MAX_CHANNELS && n>=12 && n<=255){
    mode=0;receiving=false;uint8_t data[255];memcpy(data,"CAD4",4);memcpy(data+4,&seq,4);data[8]=c;
    for(int i=9;i<n;i++)data[i]=(uint8_t)(i^data[4]^c);
    if(!tune(c))return;frontEnd(true);uint32_t t=micros();int16_t rc=radio.transmit(data,n);uint32_t dt=micros()-t;frontEnd(false);
    Serial.printf("{\"tx\":%u,\"ch\":%d,\"len\":%d,\"rc\":%d,\"elapsed_us\":%u,\"airtime_us\":%u}\n",seq,c,n,rc,dt,(unsigned)radio.getTimeOnAir(n));return;
  }
  Serial.println("{\"error\":\"unknown command\"}");
}
void setup(){
  Serial.begin(115200);delay(1500);
#ifdef CAD_BOARD_V4
  rtc_gpio_hold_dis(GPIO_NUM_7);pinMode(7,OUTPUT);digitalWrite(7,HIGH);
  rtc_gpio_hold_dis(GPIO_NUM_2);pinMode(2,INPUT);delay(10);fem43=digitalRead(2);pinMode(2,OUTPUT);digitalWrite(2,HIGH);
  pinMode(46,OUTPUT);digitalWrite(46,LOW);pinMode(5,OUTPUT);digitalWrite(5,LOW);
#endif
  SPI.begin(PIN_SCK,PIN_MISO,PIN_MOSI,PIN_CS);
  int16_t rc=radio.begin(channels[0],125.0,5,5,0x12,0,32,1.8,false);
#ifdef CAD_BOARD_XIAO
  radio.setRfSwitchPins(38,RADIOLIB_NC);
#endif
  if(!good(rc))return;
  good(radio.setDio2AsRfSwitch(true));good(radio.setCRC(true));good(radio.explicitHeader());good(radio.setRxBoostedGainMode(true));
  // Match the project's qualified TCXO delay; avoid RadioLib's default 5 ms.
  good(radio.setTCXO(1.8,1600));
  Serial.printf("{\"ready\":true,\"sf\":5,\"bw\":125,\"preamble\":32,\"cad_symbols\":4,\"det_peak\":18,\"det_min\":10,\"fem43\":%s}\n",fem43?"true":"false");
}
void loop(){
  static char line[96];static size_t used=0;
  while(Serial.available()){char c=Serial.read();if(c=='\n'||c=='\r'){if(used){line[used]=0;command(line);Serial.flush();used=0;}}else if(used<sizeof(line)-1)line[used++]=c;}
  if(!mode){delay(1);return;}
  if(receiving){rxLoop();return;}
  if(mode!=1 && mode!=3 && mode!=4)return;
  if(channel==0)sweepStart=micros();
  uint32_t start=micros();if(!tune(channel))return;frontEnd(false);
  if(mode==3 || mode==4){
    const uint32_t flags=RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL<<RADIOLIB_IRQ_PREAMBLE_DETECTED);
    uint32_t t=micros();
    if(mode==3){
      if(!good(radio.startReceive(RADIOLIB_SX126X_RX_TIMEOUT_INF,flags,RADIOLIB_IRQ_RX_DEFAULT_MASK | (1UL<<RADIOLIB_IRQ_PREAMBLE_DETECTED))))return;
    }else{
      module.setRfSwitchState(Module::MODE_RX);
      if(!good(radio.setDioIrqParams(0xFFFF,RADIOLIB_SX126X_IRQ_RX_DONE | RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED)))return;
      if(!good(radio.clearIrqStatus()))return;
      if(!good(radio.setRx(RADIOLIB_SX126X_RX_TIMEOUT_INF)))return;
    }
    uint32_t listen=micros();
    while(!digitalRead(PIN_IRQ) && micros()-listen<windowUs(channel)){}
    bool found=digitalRead(PIN_IRQ);
    cadTime.add(micros()-t);uint32_t visit=micros()-start;visitTime.add(visit);channelVisit[channel].add(visit);++scans[channel];
    if(channel==channelCount-1 && sweepStart)sweepTime.add(micros()-sweepStart);
    if(found){
      ++hits[channel];
      radio.setDioIrqParams(0xFFFF,RADIOLIB_SX126X_IRQ_RX_DONE);
      radio.clearIrqStatus(RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED);
      receiving=true;receiveStart=millis();
    }else channel=(channel+1)%channelCount;
    return;
  }
  ChannelScanConfig_t cfg={};cfg.cad.symNum=cadCode;cfg.cad.detPeak=18;cfg.cad.detMin=10;
  cfg.cad.exitMode=automaticCad?RADIOLIB_SX126X_CAD_GOTO_RX:RADIOLIB_SX126X_CAD_GOTO_STDBY;
  cfg.cad.timeout=250000;cfg.cad.irqFlags=RADIOLIB_IRQ_CAD_DEFAULT_FLAGS | RADIOLIB_IRQ_RX_DEFAULT_FLAGS;cfg.cad.irqMask=RADIOLIB_IRQ_CAD_DEFAULT_MASK;
  uint32_t t=micros();if(!good(radio.startChannelScan(cfg)))return;
  while(!digitalRead(PIN_IRQ) && micros()-t<30000){} // bounded polling; no USB logging in timed section
  if(!digitalRead(PIN_IRQ)){good(RADIOLIB_ERR_RX_TIMEOUT);return;}
  int16_t rc=radio.getChannelScanResult();cadTime.add(micros()-t);visitTime.add(micros()-start);++scans[channel];
  if(channel==channelCount-1 && sweepStart)sweepTime.add(micros()-sweepStart);
  if(rc==RADIOLIB_LORA_DETECTED){++hits[channel];
    if(automaticCad){radio.setDioIrqParams(0xFFFF,RADIOLIB_SX126X_IRQ_RX_DONE);radio.clearIrqStatus(RADIOLIB_SX126X_IRQ_CAD_DONE | RADIOLIB_SX126X_IRQ_CAD_DETECTED);receiving=true;receiveStart=millis();}
    else beginRx();
  }
  else if(rc==RADIOLIB_CHANNEL_FREE)channel=(channel+1)%channelCount;
  else good(rc);
}
