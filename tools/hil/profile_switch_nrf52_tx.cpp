// Application-only, RAM-only USB reference transmitter for the XIAO nRF52840
// plus Wio SX1262. No Mesh identity/settings, BLE, autonomous TX or flash writes.
#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <SPI.h>
#include <helpers/radiolib/CustomSX1262.h>
#include "ProfileSwitchUsb.h"
#include "ProfileMixedChannels.h"

CustomSX1262 chip = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY,
                              SPI, SPISettings(8000000, MSBFIRST, SPI_MODE0));
bool ready = false;
unsigned channelStepKhz=250;

void referenceTransmit(unsigned channel, unsigned seq, unsigned len,
                       unsigned preamble, unsigned sf, float bw) {
  if (seq == lastTxResult.sequence) { emitBenchResult(lastTxResult); return; }
  const uint32_t requested = micros();
  int rc = chip.std_init(&SPI) ? RADIOLIB_ERR_NONE : RADIOLIB_ERR_CHIP_NOT_FOUND;
  const unsigned frequencyKhz=909500+channelStepKhz*channel;
  if (!rc) rc = chip.setFrequency(float(frequencyKhz)/1000);
  if (!rc) rc = chip.setLoRaModulationParams(bw, sf, 5);
  if (!rc) rc = chip.setPreambleLength(preamble);
  if (!rc) rc = chip.setOutputPower(-9);
  uint8_t bytes[255];
  memcpy(bytes, "CHS1", 4); memcpy(bytes+4, &seq, 4); bytes[8] = channel;
  for (unsigned i=9; i<len; ++i) bytes[i] = uint8_t(i ^ seq);
  const uint32_t txStart = micros();
  if (!rc) rc = chip.transmit(bytes, len);
  const uint32_t txEnd = micros();
  recordBenchResult(lastTxResult, seq,
    "{\"sent\":%u,\"channel\":%u,\"len\":%u,\"preamble\":%u,\"rc\":%d,\"power_dbm\":-9,\"sf\":%u,\"bw_khz\":%.3f,\"freq_khz\":%u,\"channel_step_khz\":%u,\"setup_us\":%u,\"transmit_call_us\":%u}\n",
    seq, channel, len, preamble, rc, sf, double(bw), frequencyKhz,channelStepKhz,unsigned(txStart-requested), unsigned(txEnd-txStart));
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  ready = chip.std_init(&SPI);
}

void loop() {
  static char line[80];
  static unsigned used=0;
  static bool overflow=false;
  while (Serial.available()) {
    const char c=Serial.read();
    if (c=='\r' || c=='\n') {
      if (overflow) { overflow=false; used=0; Serial.println("{\"error\":\"command too long\"}"); continue; }
      if (!used) continue;
      line[used]=0; used=0;
      unsigned channel,seq,len,preamble,sf,bw;
      char kind[12],trailing;
      if (!strcmp(line,"mixinfo")) {
        Serial.println(mixedChannelInfo);
      } else if (!strcmp(line,"info")) {
        Serial.printf("{\"ready\":%s,\"bench\":\"production-profile-switch-v8\",\"board\":\"XIAO nRF52840 Wio SX1262 TX\",\"channel_tx\":1,\"channel_sweep\":0,\"channel_sf_select\":1,\"channel_bw_select\":1,\"channel_step_select\":1,\"autonomous_tx\":false}\n",ready?"true":"false");
      } else if (sscanf(line,"scanstep %u %c",&channel,&trailing)==1 && (channel==250 || channel==1000)) {
        channelStepKhz=channel;Serial.printf("{\"channel_step_khz\":%u}\n",channelStepKhz);
      } else if (!strcmp(line,"reboot")) {
        Serial.flush(); delay(20); NVIC_SystemReset();
      } else if (sscanf(line,"result %11s %u %c",kind,&seq,&trailing)==2) {
        replayBenchResult(kind,seq);
      } else if (sscanf(line,"mixtx %u %u %c",&channel,&seq,&trailing)==2 && ready && channel<4 && seq) {
        const auto& p=mixedChannelProfiles[channel];
        referenceTransmit(channel,seq,16,32,p.sf,p.bwKhz);
      } else if (sscanf(line,"scantx %u %u %u %u %u %u %c",&channel,&seq,&len,&preamble,&sf,&bw,&trailing)==6
                 && ready && channel<4 && seq && len==16 && preamble==32
                 && sf>=5 && sf<=10 && (bw==125 || bw==250)) {
        referenceTransmit(channel,seq,len,preamble,sf,bw);
      } else {
        Serial.println("{\"error\":\"unsupported reference TX command\"}");
      }
    } else if (!overflow) {
      if (used<sizeof(line)-1) line[used++]=c;
      else overflow=true;
    }
  }
  delay(1);
}
