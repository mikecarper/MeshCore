#pragma once
#include "ProfileChannelVisitClock.h"
#include "ProfileMixedChannels.h"
#include "ProfilePairPlan.h"
bool channelPair=false;
unsigned pairLoopUs=0;
unsigned pairSlowExtraUs=0;
bool channelMixed=false; // explicit HIL experiment, never a production default
unsigned channelSettleUs=0; // explicit HIL policy; never a production default
// Bit 0: full RX restart; bit 1: individual SF/BW/CR setters;
// bit 2: byte-wise SPI; bit 3: ordinary RC standby (also disables fast RX).
unsigned channelRollback=0;
unsigned channelTcxoUs=MC_TCXO_DELAY_US;
unsigned channelStepKhz=250; // explicit lab spacing; wider mode is bounded to four channels
bool channelAcceptOffChannel=false; // separate payload-delivery diagnostic, not a strict capacity pass
// Lab-only N-channel scheduler. The two production profile slots are staging
// slots, not a claim that the public two-profile protocol supports N channels.
// Retunes still use production tuneProfile, ownership guards and fast RX.
struct ChannelSweep {
  bool active=false, expecting=false, heldVisit=false;
  unsigned channels=0, current=0, expectedChannel=0, preamble=32, sf=6, bwKhz=125;
  uint32_t sequence=0, deadline=0, received=0, missed=0, deferred=0;
  uint32_t strictReceived=0, offChannelReceived=0;
  uint32_t failures=0, modeErrors=0, cacheErrors=0, fastStart=0, errorsStart=0;
  uint32_t dwellUs=2458, visitStartedUs=0;
  uint32_t rfWritesStart=0, modulationWritesStart=0;
  uint32_t detourChecksStart=0;
  Timing switches, idleDwell, heldDwell;
  Timing pairIdleDwell[2],pairSwitch[2];
  Timing firstPass,secondPass;
  Timing withModulation, withoutModulation;
  Timing settling, switchAndSettle, idleCycle;
  ChannelVisitClock visitClock;
  ChannelCycleClock cycleClock;
} channelSweep;

bool channelContinueOnMiss=false; // HIL fixed-sample PER only; stop-first default
void completeChannelExpectation(bool accepted) {
  channelSweep.expecting=false;
  if(accepted) ++channelSweep.received;
  else {
    ++channelSweep.missed;
    if(!channelContinueOnMiss) channelSweep.active=false;
  }
}

float sweepFrequency(unsigned channel) { return float(909500+channelStepKhz*channel)/1000; }
mesh::RadioProfileParams sweepParams(unsigned channel,unsigned preamble,unsigned sf=6,unsigned bwKhz=125) {
  mesh::RadioProfileParams p;
  p.freq=sweepFrequency(channel);p.bw=bwKhz;p.sf=sf;p.cr=5;p.preamble=preamble;
  if(channelMixed && channel<4) { p.sf=mixedChannelProfiles[channel].sf;p.bw=mixedChannelProfiles[channel].bwKhz; }
  if(channelPair && channel<2) { p.sf=pairProfiles[channel].sf;p.bw=pairProfiles[channel].bw; }
  return p;
}

void startChannelSweep(unsigned channels,unsigned preamble,unsigned seq,unsigned dwellUs=2458,bool trace=false,unsigned sf=6,unsigned bwKhz=125) {
  if(channelPair && (channels!=2 || preamble!=32 || channelMixed || channelStepKhz!=1000
      || channelSettleUs || channelRollback || driver.hopPasses!=1 || driver.firstPassDetour || chip.repeatFrequency)) {
    Serial.println("{\"error\":\"pair mode requires two single-pass fast profiles and no added settling\"}");return;
  }
  if(driver.firstPassDetour && driver.hopPasses!=2) {
    Serial.println("{\"error\":\"first-pass detour requires two complete passes\"}");return;
  }
  if(driver.hopPasses==2 && (channelMixed || channelRollback || channels!=4 || sf!=10 || bwKhz!=125 || preamble!=32)) {
    Serial.println("{\"error\":\"full repeat requires uniform SF10/125 four-channel fast path\"}");return;
  }
  if(channelMixed && (channels!=4 || sf!=10 || bwKhz!=125 || preamble!=32)) {
    Serial.println("{\"error\":\"mixed scan needs four channels and SF10/125 timing anchor, preamble 32\"}");return;
  }
  if(channelStepKhz==1000 && channels>4) { Serial.println("{\"error\":\"wide spacing supports only four lab channels\"}");return; }
  channelSweep.active=false;packetListening=false;
  channelTrace.active=false;channelTrace.enabled=trace;
  if (!ready || !waitBusy()) { Serial.println("{\"error\":\"receiver not ready\"}");return; }
  if(channelTcxoUs!=chip.tcxoDelay) {
    // Only lengthen/restore the existing supply timing; voltage never changes.
    // Reject existing errors rather than letting setTCXO clear their evidence.
    if(chip.tcxoVoltage<=0 || chip.getDeviceErrors()!=0
        || chip.setTCXO(chip.tcxoVoltage,channelTcxoUs)!=RADIOLIB_ERR_NONE
        || chip.calibrate(RADIOLIB_SX126X_CALIBRATE_ALL)!=RADIOLIB_ERR_NONE) {
      Serial.println("{\"error\":\"TCXO rollback setup failed\"}");return;
    }
    delay(50);
  }
  drain();
  chip.productionPath=true;chip.experiment=0;
  chip.setProfileSwitchOptimization((channelRollback & 9)==0);
  radioHal.bulkTransfer=(channelRollback & 4)==0;
  radioHal.spiSettings=SPISettings(8000000,MSBFIRST,SPI_MODE0);
  driver.batched=(channelRollback & 2)==0;
  driver.warmStandby=(channelRollback & 8)==0;
  // Validate/apply the fixed preamble in single-profile mode. The ordinary
  // CLI's two-channel automatic-preamble budget does not model this explicit
  // N-channel experiment. No packet/standby/BUSY ownership guard is removed.
  driver.profiles()->setSecondary({},true);
  if (driver.trySetPrimaryParams(sweepParams(0,preamble,sf,bwKhz),true)!=mesh::RadioParamApplyResult::APPLIED) {
    Serial.println("{\"error\":\"receiver primary rejected\"}");return;
  }
  mesh::RadioProfileConfig second;
  second.params=sweepParams(1,preamble,sf,bwKhz);second.mode=mesh::RadioProfileMode::Rx;
  driver.profiles()->setSecondary(second,true);
  driver.loop(); // safe oscillator/RXPS entry, then this scheduler owns visits
  if (chip.standbyXOSC!=driver.warmStandby || !packetHop(0) || sx126xReceiveMode(&chip)!=1) {
    Serial.println("{\"error\":\"scanner setup failed\"}");return;
  }
  channelSweep={};channelSweep.active=true;channelSweep.channels=channels;
  channelSweep.preamble=preamble;
  channelSweep.sf=sf;
  channelSweep.bwKhz=bwKhz;
  channelSweep.dwellUs=dwellUs;
  channelSweep.visitStartedUs=driver.receiveStartedUs();
  const uint32_t initialBusyLow=micros();
  while(uint32_t(micros()-initialBusyLow)<channelSettleUs) {}
  channelSweep.visitClock.begin(driver.receiveStartedUs(),micros(),channelSettleUs!=0);
  channelSweep.fastStart=chip.getOptimizedProfileSwitches();
  channelSweep.errorsStart=driver.getPacketsRecvErrors();
  channelSweep.rfWritesStart=channelTrace.rfWrites;
  channelSweep.modulationWritesStart=channelTrace.modulationWrites;
  channelSweep.detourChecksStart=driver.detourChecks;
  channelSweep.heldVisit=true; // exclude setup/USB acknowledgement from idle dwell
  recordBenchResult(lastListenResult,seq,
      "{\"listening\":%u,\"channels\":%u,\"sf\":%u,\"bw_khz\":%u,\"cr\":5,\"preamble\":%u,\"dwell_us\":%u,\"settle_us\":%u,\"base_mhz\":909.5,\"step_mhz\":%.3f,\"spi_mhz\":8,\"bulk\":%s,\"rollback\":%u,\"trace\":%s,\"irq_poll_us\":%u}\n",
      seq,channels,sf,bwKhz,preamble,dwellUs,channelSettleUs,double(channelStepKhz)/1000,radioHal.bulkTransfer?"true":"false",channelRollback,trace?"true":"false",trace?64:0);
}

void expectChannelPacket(unsigned channel,unsigned seq) {
  if (!channelSweep.active || channel>=channelSweep.channels || channelSweep.expecting) {
    Serial.println("{\"error\":\"invalid or overlapping scan expectation\"}");return;
  }
  // Arming an expectation must NOT retune or reset the visit clock. The
  // transmitter arrives independently of the receiver's round-robin phase.
  channelSweep.sequence=seq;channelSweep.expectedChannel=channel;
  channelSweep.deadline=millis()+(channelPair?1000:10000);channelSweep.expecting=true;
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
  appendBenchResult(lastRunResult,"\"modulation_cache\":%s,\"rf_commands\":%u,\"modulation_commands\":%u,\"rx_gain_reg\":%d,",
      driver.forceModulationWrite?"false":"true",unsigned(channelTrace.rfWrites-channelSweep.rfWritesStart),
      unsigned(channelTrace.modulationWrites-channelSweep.modulationWritesStart),readBenchRxGain());
  channelSweep.switches.print("switch");appendBenchResult(lastRunResult,",");
  channelSweep.withModulation.print("with_modulation");appendBenchResult(lastRunResult,",");
  channelSweep.withoutModulation.print("without_modulation");appendBenchResult(lastRunResult,",");
  appendBenchResult(lastRunResult,"\"settle_us\":%u,",channelSettleUs);
  appendBenchResult(lastRunResult,"\"continue_on_miss\":%s,",channelContinueOnMiss?"true":"false");
  appendBenchResult(lastRunResult,"\"frequency_repeat\":%s,",chip.repeatFrequency?"true":"false");
  appendBenchResult(lastRunResult,"\"tcxo_us\":%u,",unsigned(chip.tcxoDelay));
  appendBenchResult(lastRunResult,"\"channel_step_khz\":%u,",channelStepKhz);
  appendBenchResult(lastRunResult,"\"mixed_profiles\":%s,",channelMixed?"true":"false");
  appendBenchResult(lastRunResult,"\"pair_profiles\":%s,\"pair_loop_us\":%u,\"pair_slow_extra_us\":%u,\"pair_dwell_us\":[%u,%u],",
      channelPair?"true":"false",pairLoopUs,pairSlowExtraUs,
      pairListenUs(0,pairLoopUs,pairSlowExtraUs),pairListenUs(1,pairLoopUs,pairSlowExtraUs));
  channelSweep.pairIdleDwell[0].print("pair_idle_0");appendBenchResult(lastRunResult,",");
  channelSweep.pairIdleDwell[1].print("pair_idle_1");appendBenchResult(lastRunResult,",");
  channelSweep.pairSwitch[0].print("pair_switch_to_0");appendBenchResult(lastRunResult,",");
  channelSweep.pairSwitch[1].print("pair_switch_to_1");appendBenchResult(lastRunResult,",");
  appendBenchResult(lastRunResult,"\"retune_passes\":%u,\"second_pass_blocked\":%u,",driver.hopPasses,driver.secondPassBlocked);
  appendBenchResult(lastRunResult,"\"first_pass_detour\":%s,\"detour\":{\"n\":%u,\"bad\":%u,\"first_rf\":%u,\"final_rf\":%u,\"first_mod\":%u,\"final_mod\":%u},",
      driver.firstPassDetour?"true":"false",unsigned(driver.detourChecks-channelSweep.detourChecksStart),driver.detourErrors,
      driver.detourFirstRf,driver.detourFinalRf,driver.detourFirstMod,driver.detourFinalMod);
  channelSweep.firstPass.print("first_pass");appendBenchResult(lastRunResult,",");
  channelSweep.secondPass.print("second_pass");appendBenchResult(lastRunResult,",");
  appendBenchResult(lastRunResult,"\"accept_offchannel\":%s,\"strict_received\":%u,\"offchannel_received\":%u,",
                    channelAcceptOffChannel?"true":"false",channelSweep.strictReceived,channelSweep.offChannelReceived);
  appendBenchResult(lastRunResult,"\"rollback\":%u,\"warm_standby\":%s,\"batched_modulation\":%s,\"bulk_spi\":%s,",
                    channelRollback,chip.standbyXOSC?"true":"false",driver.batched?"true":"false",radioHal.bulkTransfer?"true":"false");
  channelSweep.settling.print("settling");appendBenchResult(lastRunResult,",");
  channelSweep.switchAndSettle.print("switch_and_settle");appendBenchResult(lastRunResult,",");
  channelSweep.idleCycle.print("idle_cycle");appendBenchResult(lastRunResult,",");
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

void sendChannelPacket(unsigned channel,unsigned seq,unsigned len,unsigned preamble,unsigned sf=6,unsigned bwKhz=125) {
  if(channelStepKhz==1000 && channel>=4) { Serial.println("{\"error\":\"wide TX channel out of lab range\"}");return; }
  const uint32_t requested=micros();
  packetListening=false;
  if(channelSweep.active) { Serial.println("{\"error\":\"scanner cannot also be reference transmitter\"}");return; }
  chip.productionPath=true;chip.experiment=0;chip.setProfileSwitchOptimization(false);
  radioHal.bulkTransfer=true;radioHal.spiSettings=SPISettings(8000000,MSBFIRST,SPI_MODE0);
  int16_t rc=chip.std_init(&radioSpi)?0:RADIOLIB_ERR_CHIP_NOT_FOUND;
  if(!rc) rc=chip.setFrequency(sweepFrequency(channel));
  if(!rc) rc=chip.setLoRaModulationParams(bwKhz,sf,5);
  if(!rc) rc=chip.setPreambleLength(preamble);
  if(!rc) rc=chip.setOutputPower(-9);
  uint8_t bytes[255];memcpy(bytes,"CHS1",4);memcpy(bytes+4,&seq,4);bytes[8]=channel;
  for(unsigned i=9;i<len;++i) bytes[i]=uint8_t(i^seq);
  const uint32_t txStart=micros();
  if(!rc) rc=benchTransmit(bytes,len);
  const uint32_t txEnd=micros();
  recordBenchResult(lastTxResult,seq,
      "{\"sent\":%u,\"channel\":%u,\"len\":%u,\"preamble\":%u,\"rc\":%d,\"power_dbm\":-9,\"sf\":%u,\"bw_khz\":%u,\"freq_khz\":%u,\"channel_step_khz\":%u,\"setup_us\":%u,\"transmit_call_us\":%u}\n",
      seq,channel,len,preamble,rc,sf,bwKhz,909500+channelStepKhz*channel,channelStepKhz,unsigned(txStart-requested),unsigned(txEnd-txStart));
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
  const uint32_t rfWordAtRead=channelTrace.rfWord;
  const unsigned sfAtRead=chip.spreadingFactor;
  const float bwAtRead=chip.bandwidthKhz;
  const unsigned profileAtRead=driver.receiveProfile();
  const int len=driver.recvRaw(bytes,sizeof(bytes));
  driver.onReceiveProcessed();
  if(len>0) channelSweep.heldVisit=true;
  if(channelSweep.expecting && len>=12 && !memcmp(bytes,"CHS1",4)) {
    uint32_t seq;memcpy(&seq,bytes+4,4);
    if(seq==channelSweep.sequence) {
      bool payloadValid=len==16 && bytes[8]==channelSweep.expectedChannel;
      for(int i=9;i<len;++i) if(bytes[i]!=uint8_t(i^seq)) payloadValid=false;
      const bool valid=payloadValid && bytes[8]==channelSweep.current;
      const bool accepted=valid || (channelAcceptOffChannel && payloadValid);
      if(valid) ++channelSweep.strictReceived;
      else if(payloadValid) ++channelSweep.offChannelReceived;
      completeChannelExpectation(accepted);
      channelTrace.add(ChannelTrace::Packet,valid,len);channelTrace.active=false;
      recordBenchResult(lastRxResult,seq,
          "{\"received\":%u,\"channel\":%u,\"len\":%d,\"valid\":%s,\"accepted\":%s,\"payload_valid\":%s,\"payload_channel\":%u,\"expected_channel\":%u,\"rf_word_at_read\":%u,\"profile_at_read\":%u,\"sf_at_read\":%u,\"bw_khz_at_read\":%.3f,\"rssi\":%.1f,\"snr\":%.1f}\n",
          seq,channelSweep.current,len,valid?"true":"false",accepted?"true":"false",payloadValid?"true":"false",unsigned(bytes[8]),channelSweep.expectedChannel,
          rfWordAtRead,profileAtRead,sfAtRead,double(bwAtRead),driver.getLastRSSI(),driver.getLastSNR());
    }
  }
  if(channelSweep.expecting && int32_t(millis()-channelSweep.deadline)>=0) {
    completeChannelExpectation(false);
    channelTrace.add(ChannelTrace::Timeout,channelSweep.expectedChannel);channelTrace.active=false;
    recordBenchResult(lastRxResult,channelSweep.sequence,
        "{\"received\":%u,\"channel\":%u,\"valid\":false,\"timeout\":true,\"device_errors\":%u,\"rx_mode\":%d}\n",
        channelSweep.sequence,channelSweep.expectedChannel,unsigned(chip.getDeviceErrors()),sx126xReceiveMode(&chip));
  }
  if(!channelSweep.active) return;
  const uint32_t now=micros();
  const uint32_t dwell=channelSweep.visitClock.dwell(now,driver.receiveStartedUs());
  // startRecv() resets the production timestamp after consuming a packet.
  // Track channel occupancy separately so held visits include reception.
  const uint32_t visitAge=now-channelSweep.visitStartedUs;
  if(dwell<(channelPair ? pairListenUs(channelSweep.current,pairLoopUs,pairSlowExtraUs) : channelSweep.dwellUs)) return;
  const unsigned next=(channelSweep.current+1)%channelSweep.channels;
  const uint8_t slot=driver.receiveProfile()^1;
  if(slot) {
    auto second=driver.profiles()->secondary;second.params=sweepParams(next,channelSweep.preamble,channelSweep.sf,channelSweep.bwKhz);
    driver.profiles()->setSecondary(second,true);
  } else driver.profiles()->setPrimary(sweepParams(next,channelSweep.preamble,channelSweep.sf,channelSweep.bwKhz),true);
  const uint32_t modBefore=channelTrace.modulationWrites;
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
  const uint32_t busyLowAt=micros();
  const uint32_t elapsed=busyLowAt-started;
  // No SPI/IRQ work here. Preserve the full requested visit after this delay;
  // the SX1262 remains in RX and can acquire a packet while the CPU waits.
  while(uint32_t(micros()-busyLowAt)<channelSettleUs) {}
  const uint32_t settledAt=micros();
  channelSweep.visitClock.begin(driver.receiveStartedUs(),settledAt,channelSettleUs!=0);
  channelSweep.settling.add(settledAt-busyLowAt);
  channelSweep.switchAndSettle.add(settledAt-started);
  uint32_t cycleUs=0;
  if(channelSweep.cycleClock.hop(next,settledAt,channelSweep.heldVisit,cycleUs))
    channelSweep.idleCycle.add(cycleUs);
  channelSweep.switches.add(elapsed);
  if(channelPair) {
    channelSweep.pairSwitch[next].add(elapsed);
    if(!channelSweep.heldVisit) channelSweep.pairIdleDwell[channelSweep.current].add(dwell);
  }
  channelSweep.firstPass.add(driver.lastFirstPassUs);
  if(driver.hopPasses==2) channelSweep.secondPass.add(driver.lastSecondPassUs);
  if(channelTrace.modulationWrites!=modBefore) channelSweep.withModulation.add(elapsed);
  else channelSweep.withoutModulation.add(elapsed);
  if(!channelSweep.heldVisit) channelSweep.idleDwell.add(dwell);
  else channelSweep.heldDwell.add(visitAge);
  channelSweep.heldVisit=false;channelSweep.current=next;
  channelSweep.visitStartedUs=driver.receiveStartedUs();
  channelTrace.channel=next;
  channelTrace.add(ChannelTrace::Hop,visitAge,micros()-started);
  if(sx126xReceiveMode(&chip)!=1) ++channelSweep.modeErrors;
  const auto expectedParams=sweepParams(next,channelSweep.preamble,channelSweep.sf,channelSweep.bwKhz);
  if(chip.spreadingFactor!=expectedParams.sf || chip.bandwidthKhz!=expectedParams.bw || chip.codingRate!=1
      || chip.preambleLengthLoRa!=channelSweep.preamble) ++channelSweep.cacheErrors;
  if(channelSweep.modeErrors || channelSweep.cacheErrors) {
    channelSweep.active=false;Serial.println("{\"error\":\"channel RX/cache check failed\"}");
  }
}
