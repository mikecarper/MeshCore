#pragma once
// Fixed-channel positive control: full normal initialization once, then RX
// rearming only. No profile scheduler, retunes, cache shortcut, or autonomous TX.
struct StationaryBaseline {
  bool active=false, expecting=false;
  unsigned channel=0, sf=10, expectedChannel=0;
  uint16_t seenIrq=0;
  uint32_t sequence=0, deadline=0, stopAt=0, rfStart=0;
  uint32_t received=0, missed=0, readErrors=0, foreign=0, failures=0;
} stationaryBaseline;

bool stationaryPayloadMatches(const uint8_t* bytes,unsigned len,uint32_t seq,unsigned channel) {
  if(len!=16 || memcmp(bytes,"CHS1",4) || bytes[8]!=channel) return false;
  uint32_t actual;memcpy(&actual,bytes+4,4);
  if(actual!=seq) return false;
  for(unsigned i=9;i<len;++i) if(bytes[i]!=uint8_t(i^seq)) return false;
  return true;
}

void startStationaryBaseline(unsigned channel,unsigned sf,unsigned seq,float bwKhz=125) {
  if(!ready || stationaryBaseline.active || channelSweep.active || preambleDiagnostic.active) {
    Serial.println("{\"error\":\"reboot before stationary baseline\"}");return;
  }
  packetListening=false;channelTrace.active=channelTrace.enabled=false;
  const unsigned khz=909500+channelStepKhz*channel;
  int rc=prepareDiagnosticRadio(sf,khz,bwKhz);
  if(!rc) rc=chip.startReceive();
  if(rc || !waitBusy() || sx126xReceiveMode(&chip)!=1) {
    Serial.println("{\"error\":\"stationary baseline setup failed\"}");return;
  }
  stationaryBaseline={};stationaryBaseline.active=true;
  stationaryBaseline.channel=channel;stationaryBaseline.sf=sf;
  stationaryBaseline.rfStart=channelTrace.rfWrites;
  stationaryBaseline.stopAt=millis()+600000; // bounded even if host disappears
  recordBenchResult(lastListenResult,seq,
    "{\"listening\":%u,\"stationary\":true,\"full_init\":true,\"channel\":%u,\"sf\":%u,\"bw_khz\":%.3f,\"freq_khz\":%u,\"rf_word\":%u,\"preamble\":32,\"rx_gain_reg\":%d}\n",
    seq,channel,sf,double(bwKhz),khz,channelTrace.rfWord,readBenchRxGain());
}

void expectStationaryPacket(unsigned seq,int txChannel=-1) {
  if(!stationaryBaseline.active || stationaryBaseline.expecting || !seq) {
    Serial.println("{\"error\":\"invalid stationary expectation\"}");return;
  }
  stationaryBaseline.sequence=seq;stationaryBaseline.expecting=true;
  stationaryBaseline.expectedChannel=txChannel<0 ? stationaryBaseline.channel : unsigned(txChannel);
  stationaryBaseline.seenIrq=0;
  stationaryBaseline.deadline=millis()+3000;
  recordBenchResult(lastListenResult,seq,"{\"listening\":%u,\"stationary\":true,\"channel\":%u,\"expected_channel\":%u}\n",
                    seq,stationaryBaseline.channel,stationaryBaseline.expectedChannel);
}

void stationaryBaselineStatus(unsigned seq) {
  recordBenchResult(lastRunResult,seq,
    "{\"result\":%u,\"stationary\":true,\"active\":%s,\"received\":%u,\"missed\":%u,\"read_errors\":%u,\"foreign\":%u,\"failures\":%u,\"rf_commands\":%u,\"rf_word\":%u,\"rx_gain_reg\":%d,\"rx_mode\":%d,\"device_errors\":%u}\n",
    seq,stationaryBaseline.active?"true":"false",stationaryBaseline.received,stationaryBaseline.missed,
    stationaryBaseline.readErrors,stationaryBaseline.foreign,stationaryBaseline.failures,
    unsigned(channelTrace.rfWrites-stationaryBaseline.rfStart),channelTrace.rfWord,
    readBenchRxGain(),sx126xReceiveMode(&chip),unsigned(chip.getDeviceErrors()));
}

void stopStationaryBaseline() {
  stationaryBaseline.active=stationaryBaseline.expecting=false;
  chip.standbyXOSC=false;chip.standby();ready=false;
  Serial.println("{\"stopped\":true,\"reboot_required\":true}");Serial.flush();
}

void serviceStationaryBaseline() {
  if(!stationaryBaseline.active) return;
#ifdef HIL_INDICATOR
  radioHal.serviceInterrupt();
#endif
  if(int32_t(millis()-stationaryBaseline.stopAt)>=0) { stopStationaryBaseline();return; }
  if(chip.isChipBusy()) return;
  const uint16_t irq=chip.getIrqFlags();
  if(stationaryBaseline.expecting) stationaryBaseline.seenIrq|=irq;
  if(irq & RADIOLIB_SX126X_IRQ_RX_DONE) {
    uint8_t bytes[255];const unsigned len=min(unsigned(chip.getPacketLength()),unsigned(sizeof(bytes)));
    const float rssi=chip.getRSSI(),snr=chip.getSNR();
    const int rc=chip.readData(bytes,len);
    const bool valid=!rc && stationaryBaseline.expecting
      && stationaryPayloadMatches(bytes,len,stationaryBaseline.sequence,stationaryBaseline.expectedChannel);
    if(rc) ++stationaryBaseline.readErrors;
    else if(!valid) ++stationaryBaseline.foreign; // never expose unrelated payloads
    if(chip.startReceive()!=0) {
      ++stationaryBaseline.failures;stationaryBaseline.active=false;
      Serial.println("{\"error\":\"stationary RX rearm failed\"}");return;
    }
    if(valid) {
      stationaryBaseline.expecting=false;++stationaryBaseline.received;
      recordBenchResult(lastRxResult,stationaryBaseline.sequence,
        "{\"received\":%u,\"stationary\":true,\"channel\":%u,\"payload_channel\":%u,\"len\":%u,\"valid\":true,\"seen_irq\":%u,\"rf_word_at_read\":%u,\"rssi\":%.1f,\"snr\":%.1f}\n",
        stationaryBaseline.sequence,stationaryBaseline.channel,stationaryBaseline.expectedChannel,len,
        unsigned(stationaryBaseline.seenIrq),channelTrace.rfWord,rssi,snr);
    }
  }
  if(stationaryBaseline.expecting && int32_t(millis()-stationaryBaseline.deadline)>=0) {
    stationaryBaseline.expecting=false;++stationaryBaseline.missed;
    recordBenchResult(lastRxResult,stationaryBaseline.sequence,
      "{\"received\":%u,\"stationary\":true,\"channel\":%u,\"valid\":false,\"timeout\":true,\"seen_irq\":%u,\"rf_word_at_read\":%u,\"rx_mode\":%d,\"device_errors\":%u}\n",
      stationaryBaseline.sequence,stationaryBaseline.channel,unsigned(stationaryBaseline.seenIrq),channelTrace.rfWord,
      sx126xReceiveMode(&chip),unsigned(chip.getDeviceErrors()));
  }
}
