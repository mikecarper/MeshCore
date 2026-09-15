#pragma once
// Stationary, full-initialization RX control. No profile hops, cache shortcuts,
// guard changes, or autonomous TX. All payload output is synthetic-test counts.
struct PreambleDiagnostic {
  bool active=false, txReady=false;
  unsigned sf=8, windowMs=450, frequencyKhz=910000, preambles=0, headers=0;
  unsigned packets=0, valid=0, foreign=0, readErrors=0;
  uint32_t sequence=0, deadline=0, txFrequencyKhz=0, rxWord=0, firstPreambleUs=0;
  uint16_t previousIrq=0, seenIrq=0;
  float preambleRssi=0;
} preambleDiagnostic;

int prepareDiagnosticRadio(unsigned sf,unsigned frequencyKhz,float bwKhz=125) {
  chip.productionPath=true;chip.experiment=0;chip.setProfileSwitchOptimization(false);
  radioHal.bulkTransfer=true;radioHal.spiSettings=SPISettings(8000000,MSBFIRST,SPI_MODE0);
  chip.standbyXOSC=false;
  int rc=chip.std_init(&radioSpi)?0:RADIOLIB_ERR_CHIP_NOT_FOUND;
  if(!rc) rc=chip.setFrequency(float(frequencyKhz)/1000);
  if(!rc) rc=chip.setLoRaModulationParams(bwKhz,sf,5);
  if(!rc) rc=chip.setPreambleLength(32);
  return rc;
}

void diagnosticTxSetup(unsigned sf,unsigned frequencyKhz,unsigned seq) {
  if(channelSweep.active || preambleDiagnostic.active) {
    Serial.println("{\"error\":\"stop receiver before TX setup\"}");return;
  }
  packetListening=false;preambleDiagnostic.txReady=false;channelTrace.active=false;
  int rc=prepareDiagnosticRadio(sf,frequencyKhz);
  if(!rc) rc=chip.setOutputPower(-9);
  preambleDiagnostic.txReady=rc==0;preambleDiagnostic.txFrequencyKhz=frequencyKhz;
  preambleDiagnostic.sf=sf;
  recordBenchResult(lastListenResult,seq,
      "{\"listening\":%u,\"tx_ready\":%s,\"rc\":%d,\"sf\":%u,\"freq_khz\":%u,\"rf_word\":%u,\"preamble\":32,\"power_dbm\":-9}\n",
      seq,preambleDiagnostic.txReady?"true":"false",rc,sf,frequencyKhz,channelTrace.rfWord);
}

void diagnosticTransmit(unsigned seq) {
  if(!preambleDiagnostic.txReady || preambleDiagnostic.active || channelSweep.active) {
    Serial.println("{\"error\":\"diagnostic TX not prepared\"}");return;
  }
  uint8_t bytes[16];memcpy(bytes,"DGP1",4);memcpy(bytes+4,&seq,4);
  for(unsigned i=8;i<sizeof(bytes);++i) bytes[i]=uint8_t(i^seq);
  channelTrace.txCommandUs=0;
  const uint32_t started=micros();
  const int rc=benchTransmit(bytes,sizeof(bytes));
  const uint32_t ended=micros();
  preambleDiagnostic.txReady=false; // a command is one-shot, even if reply is lost
  recordBenchResult(lastTxResult,seq,
      "{\"sent\":%u,\"rc\":%d,\"len\":16,\"sf\":%u,\"freq_khz\":%u,\"rf_word\":%u,\"power_dbm\":-9,\"preamble\":32,\"set_tx_seen\":%s,\"pre_set_tx_us\":%u,\"post_set_tx_us\":%u}\n",
      seq,rc,preambleDiagnostic.sf,preambleDiagnostic.txFrequencyKhz,channelTrace.txWord,
      channelTrace.txCommandUs?"true":"false",unsigned(channelTrace.txCommandUs-started),unsigned(ended-channelTrace.txCommandUs));
}

void diagnosticListen(unsigned sf,unsigned frequencyKhz,unsigned seq,unsigned windowMs) {
  if(channelSweep.active || preambleDiagnostic.active) {
    Serial.println("{\"error\":\"stop current receiver before diagnostic\"}");return;
  }
  packetListening=false;channelTrace.active=false;
  preambleDiagnostic={};preambleDiagnostic.sf=sf;preambleDiagnostic.sequence=seq;
  preambleDiagnostic.frequencyKhz=frequencyKhz;preambleDiagnostic.windowMs=windowMs;
  int rc=prepareDiagnosticRadio(sf,frequencyKhz);
  if(!rc) rc=chip.startReceive();
  if(rc || !waitBusy() || sx126xReceiveMode(&chip)!=1) {
    Serial.println("{\"error\":\"stationary receiver setup failed\"}");return;
  }
  preambleDiagnostic.rxWord=channelTrace.rfWord;
  channelTrace.enabled=true;channelTrace.arm(seq,0,0,0);
  preambleDiagnostic.deadline=millis()+windowMs;preambleDiagnostic.active=true;
  recordBenchResult(lastListenResult,seq,
      "{\"listening\":%u,\"stationary\":true,\"full_init\":true,\"sf\":%u,\"freq_khz\":%u,\"rf_word\":%u,\"window_ms\":%u,\"preamble\":32,\"irq_poll_us\":64,\"rx_gain_reg\":%d}\n",
      seq,sf,frequencyKhz,preambleDiagnostic.rxWord,windowMs,readBenchRxGain());
}

void stopPreambleDiagnostic() {
  preambleDiagnostic.active=preambleDiagnostic.txReady=false;channelTrace.active=false;
  chip.standbyXOSC=false;chip.standby();ready=false;
  Serial.println("{\"stopped\":true,\"reboot_required\":true}");Serial.flush();
}

void servicePreambleDiagnostic() {
  if(!preambleDiagnostic.active) return;
#ifdef HIL_INDICATOR
  radioHal.serviceInterrupt();
#endif
  if(int32_t(millis()-preambleDiagnostic.deadline)>=0) {
    channelTrace.active=false;preambleDiagnostic.active=false;
    const int mode=sx126xReceiveMode(&chip);
    const unsigned errors=chip.getDeviceErrors();
    chip.standby();ready=false;
    recordBenchResult(lastRxResult,preambleDiagnostic.sequence,
        "{\"received\":%u,\"window_complete\":true,\"sf\":%u,\"freq_khz\":%u,\"rf_word\":%u,\"seen_irq\":%u,\"preambles\":%u,\"headers\":%u,\"packets\":%u,\"valid\":%u,\"foreign\":%u,\"read_errors\":%u,\"first_preamble_us\":%u,\"preamble_rssi\":%.1f,\"rx_mode\":%d,\"device_errors\":%u}\n",
        preambleDiagnostic.sequence,preambleDiagnostic.sf,preambleDiagnostic.frequencyKhz,preambleDiagnostic.rxWord,
        preambleDiagnostic.seenIrq,preambleDiagnostic.preambles,preambleDiagnostic.headers,preambleDiagnostic.packets,
        preambleDiagnostic.valid,preambleDiagnostic.foreign,preambleDiagnostic.readErrors,
        preambleDiagnostic.firstPreambleUs,preambleDiagnostic.preambleRssi,mode,errors);
    return;
  }
  if(uint32_t(micros()-channelTrace.pollAt)<64 || chip.isChipBusy()) return;
  channelTrace.pollAt=micros();
  const uint16_t irq=chip.getIrqFlags();
  const uint32_t cost=micros()-channelTrace.pollAt;
  ++channelTrace.pollCount;channelTrace.pollTotalUs+=cost;channelTrace.pollMaxUs=max(channelTrace.pollMaxUs,cost);
  const uint16_t newly=irq & ~preambleDiagnostic.previousIrq;
  preambleDiagnostic.previousIrq=irq;preambleDiagnostic.seenIrq|=irq;
  if(newly & RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED) {
    ++preambleDiagnostic.preambles;
    if(!preambleDiagnostic.firstPreambleUs) {
      preambleDiagnostic.firstPreambleUs=micros()-channelTrace.origin;
      preambleDiagnostic.preambleRssi=chip.getRSSI(false);
    }
  }
  if(newly & RADIOLIB_SX126X_IRQ_HEADER_VALID) ++preambleDiagnostic.headers;
  if(irq & RADIOLIB_SX126X_IRQ_RX_DONE) {
    uint8_t bytes[255];const unsigned len=min(unsigned(chip.getPacketLength()),unsigned(sizeof(bytes)));
    const int rc=chip.readData(bytes,len);++preambleDiagnostic.packets;
    bool valid=rc==0 && len==16 && !memcmp(bytes,"DGP1",4);
    uint32_t seq=0;if(valid) memcpy(&seq,bytes+4,4);
    valid=valid && seq==preambleDiagnostic.sequence;
    for(unsigned i=8;valid && i<len;++i) if(bytes[i]!=uint8_t(i^seq)) valid=false;
    if(rc) ++preambleDiagnostic.readErrors;
    else if(valid) ++preambleDiagnostic.valid;
    else ++preambleDiagnostic.foreign; // never return unknown payload contents
    channelTrace.add(ChannelTrace::Packet,valid,len);
    if(chip.startReceive()!=0) { Serial.println("{\"error\":\"diagnostic RX rearm failed\"}");preambleDiagnostic.active=false; }
  }
}
