#pragma once
// Lab-only N-channel scheduler. The two production profile slots are staging
// slots, not a claim that the public two-profile protocol supports N channels.
// Retunes still use production tuneProfile, ownership guards and fast RX.
struct ChannelSweep {
  bool active=false, expecting=false, heldVisit=false;
  unsigned channels=0, current=0, expectedChannel=0, preamble=32, sf=6;
  uint32_t sequence=0, deadline=0, received=0, missed=0, deferred=0;
  uint32_t failures=0, modeErrors=0, cacheErrors=0, fastStart=0, errorsStart=0;
  uint32_t dwellUs=2458, visitStartedUs=0;
  Timing switches, idleDwell, heldDwell;
} channelSweep;

float sweepFrequency(unsigned channel) { return 909.5f + 0.25f*channel; }
mesh::RadioProfileParams sweepParams(unsigned channel,unsigned preamble,unsigned sf=6) {
  mesh::RadioProfileParams p;
  p.freq=sweepFrequency(channel);p.bw=125;p.sf=sf;p.cr=5;p.preamble=preamble;
  return p;
}

void startChannelSweep(unsigned channels,unsigned preamble,unsigned seq,unsigned dwellUs=2458,bool trace=false,unsigned sf=6) {
  channelSweep.active=false;packetListening=false;
  channelTrace.active=false;channelTrace.enabled=trace;
  if (!ready || !waitBusy()) { Serial.println("{\"error\":\"receiver not ready\"}");return; }
  drain();
  chip.productionPath=true;chip.experiment=0;chip.setProfileSwitchOptimization(true);
  radioHal.bulkTransfer=true;radioHal.spiSettings=SPISettings(8000000,MSBFIRST,SPI_MODE0);
  driver.batched=true;
  // Validate/apply the fixed preamble in single-profile mode. The ordinary
  // CLI's two-channel automatic-preamble budget does not model this explicit
  // N-channel experiment. No packet/standby/BUSY ownership guard is removed.
  driver.profiles()->setSecondary({},true);
  if (driver.trySetPrimaryParams(sweepParams(0,preamble,sf),true)!=mesh::RadioParamApplyResult::APPLIED) {
    Serial.println("{\"error\":\"receiver primary rejected\"}");return;
  }
  mesh::RadioProfileConfig second;
  second.params=sweepParams(1,preamble,sf);second.mode=mesh::RadioProfileMode::Rx;
  driver.profiles()->setSecondary(second,true);
  driver.loop(); // safe oscillator/RXPS entry, then this scheduler owns visits
  if (!chip.standbyXOSC || !packetHop(0) || sx126xReceiveMode(&chip)!=1) {
    Serial.println("{\"error\":\"scanner setup failed\"}");return;
  }
  channelSweep={};channelSweep.active=true;channelSweep.channels=channels;
  channelSweep.preamble=preamble;
  channelSweep.sf=sf;
  channelSweep.dwellUs=dwellUs;
  channelSweep.visitStartedUs=driver.receiveStartedUs();
  channelSweep.fastStart=chip.getOptimizedProfileSwitches();
  channelSweep.errorsStart=driver.getPacketsRecvErrors();
  channelSweep.heldVisit=true; // exclude setup/USB acknowledgement from idle dwell
  recordBenchResult(lastListenResult,seq,
      "{\"listening\":%u,\"channels\":%u,\"sf\":%u,\"bw_khz\":125,\"cr\":5,\"preamble\":%u,\"dwell_us\":%u,\"base_mhz\":909.5,\"step_mhz\":0.25,\"spi_mhz\":8,\"bulk\":true,\"trace\":%s,\"irq_poll_us\":%u}\n",
      seq,channels,sf,preamble,dwellUs,trace?"true":"false",trace?64:0);
}

void expectChannelPacket(unsigned channel,unsigned seq) {
  if (!channelSweep.active || channel>=channelSweep.channels || channelSweep.expecting) {
    Serial.println("{\"error\":\"invalid or overlapping scan expectation\"}");return;
  }
  // Arming an expectation must NOT retune or reset the visit clock. The
  // transmitter arrives independently of the receiver's round-robin phase.
  channelSweep.sequence=seq;channelSweep.expectedChannel=channel;
  channelSweep.deadline=millis()+10000;channelSweep.expecting=true;
  channelTrace.arm(seq,channelSweep.current,channel,micros()-channelSweep.visitStartedUs);
  recordBenchResult(lastListenResult,seq,"{\"listening\":%u,\"channel\":%u,\"scanning\":true}\n",seq,channel);
}

void channelSweepStatus(unsigned seq) {
  lastRunResult.sequence=seq;lastRunResult.text[0]=0;
  appendBenchResult(lastRunResult,
      "{\"result\":%u,\"channels\":%u,\"active\":%s,\"received\":%u,\"missed\":%u,\"deferred\":%u,\"failures\":%u,\"rx_mode_errors\":%u,\"cache_errors\":%u,\"rx_errors\":%u,\"optimized_rx_resumes\":%u,",
      seq,channelSweep.channels,channelSweep.active?"true":"false",channelSweep.received,
      channelSweep.missed,channelSweep.deferred,channelSweep.failures,channelSweep.modeErrors,
      channelSweep.cacheErrors,driver.getPacketsRecvErrors()-channelSweep.errorsStart,
      unsigned(chip.getOptimizedProfileSwitches()-channelSweep.fastStart));
  channelSweep.switches.print("switch");appendBenchResult(lastRunResult,",");
  channelSweep.idleDwell.print("idle_dwell");appendBenchResult(lastRunResult,",");
  channelSweep.heldDwell.print("held_dwell");appendBenchResult(lastRunResult,"}\n");
  if(lastRunResult.sequence) emitBenchResult(lastRunResult);
  else Serial.println("{\"error\":\"result overflow\"}");
}

void channelSweepTrace(unsigned seq,unsigned offset) {
  if(channelTrace.active || seq!=channelTrace.sequence || offset>channelTrace.size) {
    Serial.println("{\"error\":\"trace unavailable or still recording\"}");return;
  }
  const unsigned end=min(offset+24,channelTrace.size);
  lastRunResult.sequence=seq;lastRunResult.text[0]=0;
  appendBenchResult(lastRunResult,
      "{\"result\":%u,\"trace\":%u,\"trace_format\":2,\"offset\":%u,\"next\":%u,\"total\":%u,\"overflow\":%u,\"origin_us\":%u,\"poll_count\":%u,\"poll_total_us\":%u,\"poll_max_us\":%u,\"events\":[",
      seq,seq,offset,end,channelTrace.size,channelTrace.overflow,channelTrace.origin,
      channelTrace.pollCount,channelTrace.pollTotalUs,channelTrace.pollMaxUs);
  for(unsigned i=offset;i<end;++i) {
    const auto& e=channelTrace.events[i];
    appendBenchResult(lastRunResult,"%s[%u,%u,%u,%u,%u,%u]",i==offset?"":",",e.us,e.kind,e.channel,e.irq,e.a,e.b);
  }
  appendBenchResult(lastRunResult,"]}\n");
  if(lastRunResult.sequence) emitBenchResult(lastRunResult);
  else Serial.println("{\"error\":\"trace page overflow\"}");
}

void stopChannelSweep() {
  channelSweep.active=channelSweep.expecting=false;
  channelTrace.active=false;
  chip.standbyXOSC=false;chip.standby();ready=false;
  Serial.println("{\"stopped\":true,\"reboot_required\":true}");Serial.flush();
}

void sendChannelPacket(unsigned channel,unsigned seq,unsigned len,unsigned preamble,unsigned sf=6) {
  const uint32_t requested=micros();
  packetListening=false;
  if(channelSweep.active) { Serial.println("{\"error\":\"scanner cannot also be reference transmitter\"}");return; }
  chip.productionPath=true;chip.experiment=0;chip.setProfileSwitchOptimization(false);
  radioHal.bulkTransfer=true;radioHal.spiSettings=SPISettings(8000000,MSBFIRST,SPI_MODE0);
  int16_t rc=chip.std_init(&radioSpi)?0:RADIOLIB_ERR_CHIP_NOT_FOUND;
  if(!rc) rc=chip.setFrequency(sweepFrequency(channel));
  if(!rc) rc=chip.setLoRaModulationParams(125,sf,5);
  if(!rc) rc=chip.setPreambleLength(preamble);
  if(!rc) rc=chip.setOutputPower(-9);
  uint8_t bytes[255];memcpy(bytes,"CHS1",4);memcpy(bytes+4,&seq,4);bytes[8]=channel;
  for(unsigned i=9;i<len;++i) bytes[i]=uint8_t(i^seq);
  const uint32_t txStart=micros();
  if(!rc) rc=chip.transmit(bytes,len);
  const uint32_t txEnd=micros();
  recordBenchResult(lastTxResult,seq,
      "{\"sent\":%u,\"channel\":%u,\"len\":%u,\"preamble\":%u,\"rc\":%d,\"power_dbm\":-9,\"sf\":%u,\"bw_khz\":125,\"setup_us\":%u,\"transmit_call_us\":%u}\n",
      seq,channel,len,preamble,rc,sf,unsigned(txStart-requested),unsigned(txEnd-txStart));
}

void serviceChannelSweep() {
  if(!channelSweep.active) return;
#ifdef HIL_INDICATOR
  radioHal.serviceInterrupt();
#endif
  // Bounded diagnostic sampling; read-only, no IRQ clear and no serial output.
  // Existing production guard reads/clears are also observed by the HIL HAL.
  if(channelTrace.active && uint32_t(micros()-channelTrace.pollAt)>=64 && !chip.isChipBusy()) {
    channelTrace.pollAt=micros();
    chip.getIrqFlags();
    const uint32_t cost=micros()-channelTrace.pollAt;
    ++channelTrace.pollCount;channelTrace.pollTotalUs+=cost;
    channelTrace.pollMaxUs=max(channelTrace.pollMaxUs,cost);
  }
  uint8_t bytes[256];
  const int len=driver.recvRaw(bytes,sizeof(bytes));
  driver.onReceiveProcessed();
  if(len>0) channelSweep.heldVisit=true;
  if(channelSweep.expecting && len>=12 && !memcmp(bytes,"CHS1",4)) {
    uint32_t seq;memcpy(&seq,bytes+4,4);
    if(seq==channelSweep.sequence) {
      bool valid=bytes[8]==channelSweep.expectedChannel && bytes[8]==channelSweep.current;
      for(int i=9;i<len;++i) if(bytes[i]!=uint8_t(i^seq)) valid=false;
      channelSweep.expecting=false;
      if(valid) ++channelSweep.received;else ++channelSweep.missed;
      channelTrace.add(ChannelTrace::Packet,valid,len);channelTrace.active=false;
      recordBenchResult(lastRxResult,seq,
          "{\"received\":%u,\"channel\":%u,\"len\":%d,\"valid\":%s,\"rssi\":%.1f,\"snr\":%.1f}\n",
          seq,channelSweep.current,len,valid?"true":"false",driver.getLastRSSI(),driver.getLastSNR());
      if(!valid) channelSweep.active=false; // stop at the first failed payload
    }
  }
  if(channelSweep.expecting && int32_t(millis()-channelSweep.deadline)>=0) {
    channelSweep.expecting=false;channelSweep.active=false;++channelSweep.missed;
    channelTrace.add(ChannelTrace::Timeout,channelSweep.expectedChannel);channelTrace.active=false;
    recordBenchResult(lastRxResult,channelSweep.sequence,
        "{\"received\":%u,\"channel\":%u,\"valid\":false,\"timeout\":true,\"device_errors\":%u,\"rx_mode\":%d}\n",
        channelSweep.sequence,channelSweep.expectedChannel,unsigned(chip.getDeviceErrors()),sx126xReceiveMode(&chip));
  }
  if(!channelSweep.active) return;
  const uint32_t now=micros();
  const uint32_t dwell=now-driver.receiveStartedUs();
  // startRecv() resets the production timestamp after consuming a packet.
  // Track channel occupancy separately so held visits include reception.
  const uint32_t visitAge=now-channelSweep.visitStartedUs;
  if(dwell<channelSweep.dwellUs) return;
  const unsigned next=(channelSweep.current+1)%channelSweep.channels;
  const uint8_t slot=driver.receiveProfile()^1;
  if(slot) {
    auto second=driver.profiles()->secondary;second.params=sweepParams(next,channelSweep.preamble,channelSweep.sf);
    driver.profiles()->setSecondary(second,true);
  } else driver.profiles()->setPrimary(sweepParams(next,channelSweep.preamble,channelSweep.sf),true);
  const uint32_t started=micros();
  const auto rc=driver.hop(slot);
  if(rc==mesh::RadioParamApplyResult::BUSY) {
    if(!channelSweep.heldVisit) channelTrace.add(ChannelTrace::Hold,visitAge,next);
    ++channelSweep.deferred;channelSweep.heldVisit=true;return;
  }
  if(rc!=mesh::RadioParamApplyResult::APPLIED || !waitBusy()) {
    ++channelSweep.failures;channelSweep.active=false;
    Serial.println("{\"error\":\"channel retune failed\"}");return;
  }
  channelSweep.switches.add(micros()-started);
  if(!channelSweep.heldVisit) channelSweep.idleDwell.add(dwell);
  else channelSweep.heldDwell.add(visitAge);
  channelSweep.heldVisit=false;channelSweep.current=next;
  channelSweep.visitStartedUs=driver.receiveStartedUs();
  channelTrace.channel=next;
  channelTrace.add(ChannelTrace::Hop,visitAge,micros()-started);
  if(sx126xReceiveMode(&chip)!=1) ++channelSweep.modeErrors;
  if(chip.spreadingFactor!=channelSweep.sf || chip.bandwidthKhz!=125 || chip.codingRate!=1
      || chip.preambleLengthLoRa!=channelSweep.preamble) ++channelSweep.cacheErrors;
  if(channelSweep.modeErrors || channelSweep.cacheErrors) {
    channelSweep.active=false;Serial.println("{\"error\":\"channel RX/cache check failed\"}");
  }
}
