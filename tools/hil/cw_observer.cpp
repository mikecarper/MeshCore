// Independent RF witness: instantaneous RSSI detects an unmodulated carrier;
// explicit packet probes verify LoRa recovery. Settings live only in RAM.
#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <SPI.h>
#include <RadioLib.h>

SX1262 chip = new Module(42, 47, 38, 46);
volatile bool received = false;
bool ready = false;
uint32_t packets = 0, sampleUntil = 0, sampleAt = 0;
uint32_t frequency = 0;
void irq() { received = true; }

void command(const char* line) {
  unsigned khz, bw, sf, cr, preamble, duration, sequence;
  char extra;
  int rc = 0;
  if (!strcmp(line, "info")) {
    Serial.printf("{\"observer\":\"cw-v1\",\"ready\":%s,\"freq_khz\":%u,\"packets\":%u}\n",
        ready ? "true" : "false", unsigned(frequency), unsigned(packets));
  } else if (sscanf(line, "listen %u %u %u %u %u %c", &khz, &bw, &sf, &cr, &preamble, &extra)==5
      && khz>=902000 && khz<=928000 && bw>=62 && bw<=500 && sf>=5 && sf<=12
      && cr>=5 && cr<=8 && preamble>=8 && preamble<=256) {
    sampleUntil=0;
    chip.standby();
    rc=chip.begin(khz/1000.0f, bw==62 ? 62.5f : float(bw), sf, cr,
                  RADIOLIB_SX126X_SYNC_WORD_PRIVATE, -9, preamble, 1.8);
    if (!rc) rc=chip.setDio2AsRfSwitch(true);
    if (!rc) rc=chip.setCRC(true);
    if (!rc) rc=chip.explicitHeader();
    chip.setPacketReceivedAction(irq); received=false;
    if (!rc) rc=chip.startReceive();
    ready=rc==0; frequency=khz;
    Serial.printf("{\"listen_rc\":%d,\"freq_khz\":%u}\n",rc,khz);
  } else if (ready && sscanf(line,"sample %u %c",&duration,&extra)==1 && duration && duration<=15000) {
    sampleUntil=millis()+duration; sampleAt=millis();
    Serial.printf("{\"sampling_ms\":%u,\"at_ms\":%u}\n",duration,unsigned(millis()));
  } else if (ready && sscanf(line,"packet %u %c",&sequence,&extra)==1) {
    sampleUntil=0;
    uint8_t bytes[16]={'M','C','W','1'};
    memcpy(bytes+4,&sequence,sizeof(sequence));
    chip.clearPacketReceivedAction(); received=false;
    rc=chip.transmit(bytes,sizeof(bytes));
    chip.setPacketReceivedAction(irq);
    const int rx=chip.startReceive();
    Serial.printf("{\"packet\":%u,\"tx_rc\":%d,\"rx_rc\":%d}\n",sequence,rc,rx);
  } else if (!strcmp(line,"stop")) {
    sampleUntil=0; Serial.println("{\"stopped\":true}");
  } else Serial.println("{\"error\":\"use info, listen kHz BW SF CR preamble, sample ms, packet sequence, stop\"}");
}

void setup() {
  Serial.begin(115200);
  SPI.setPins(45,43,44); SPI.begin();
}

void loop() {
  if (NRF_WDT->RUNSTATUS) for(unsigned i=0;i<8;++i) NRF_WDT->RR[i]=0x6E524635;
  static char line[96]; static unsigned used=0; static bool overflow=false;
  while (Serial.available()) {
    const char c=Serial.read();
    if (c=='\r' || c=='\n') {
      if (overflow) { overflow=false;used=0;continue; }
      if (used) { line[used]=0;used=0;command(line); }
    } else if (used<sizeof(line)-1) line[used++]=c;
    else overflow=true;
  }
  if (ready && received) {
    received=false;
    uint8_t bytes[255]; const size_t len=chip.getPacketLength();
    const int rc=chip.readData(bytes, len<=sizeof(bytes) ? len : sizeof(bytes));
    if (!rc) ++packets;
    const int rx=chip.startReceive();
    Serial.printf("{\"received\":%u,\"len\":%u,\"rc\":%d,\"rx_rc\":%d}\n",unsigned(packets),unsigned(len),rc,rx);
  }
  if (sampleUntil && int32_t(millis()-sampleUntil)>=0) {
    sampleUntil=0; Serial.println("{\"samples_done\":true}");
  }
  if (sampleUntil && int32_t(millis()-sampleAt)>=0) {
    sampleAt=millis()+10;
    Serial.printf("{\"ms\":%u,\"rssi_x10\":%d}\n",unsigned(millis()),int(chip.getRSSI(false)*10));
  }
  delay(1);
}
