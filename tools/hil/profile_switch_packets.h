#pragma once
// Explicit host-requested packet probes only. No autonomous advertisements/TX.
bool packetListening = false;
uint32_t packetSequence = 0, packetDeadline = 0;
uint32_t packetErrorsAtStart = 0;
unsigned packetTarget = 0, packetSf = 0;

bool packetHop(uint8_t target) {
  const uint32_t start = millis();
  while (uint32_t(millis() - start) < 2000) {
    drain();
    if (!waitBusy()) return false;
    const auto rc = driver.hop(target);
    if (rc == mesh::RadioParamApplyResult::APPLIED) return waitBusy();
    if (rc == mesh::RadioParamApplyResult::FAILED) return false;
    delay(1);
  }
  return false;
}

void listenPacket(unsigned sf, unsigned target, unsigned seq, unsigned mask, unsigned spiMHz) {
  packetListening = false;
  if (!ready || !waitBusy()) { Serial.println("{\"error\":\"receiver not ready\"}"); return; }
  drain();
  radioHal.spiSettings = SPISettings(spiMHz * 1000000, MSBFIRST, SPI_MODE0);
  driver.batched = true;
  chip.experiment = mask & 255;
  chip.productionPath = chip.experiment == 0;
  chip.setProfileSwitchOptimization((mask & 512) != 0);
  const uint32_t fastStart=chip.getOptimizedProfileSwitches();
  radioHal.bulkTransfer = (mask & 256) != 0;
  mesh::RadioProfileParams primary;
  primary.freq=909.5f;primary.bw=62.5f;primary.sf=7;primary.cr=5;
  if (driver.trySetPrimaryParams(primary,true) != mesh::RadioParamApplyResult::APPLIED) {
    Serial.println("{\"error\":\"receiver primary rejected\"}"); return;
  }
  mesh::RadioProfileConfig secondary;
  secondary.params.freq=910.5f;secondary.params.bw=500;secondary.params.sf=sf;secondary.params.cr=5;
  secondary.mode=mesh::RadioProfileMode::Rx;
  driver.profiles()->setSecondary(secondary,true);
  if (!waitBusy()) { Serial.println("{\"error\":\"receiver busy\"}"); return; }
  driver.loop();
  if (!waitBusy() || !chip.standbyXOSC || !packetHop(target ^ 1) || !packetHop(target)
      || sx126xReceiveMode(&chip) != 1) {
    Serial.println("{\"error\":\"receiver hop failed\"}"); return;
  }
  packetSequence=seq;packetTarget=target;packetSf=sf;packetListening=true;
  packetErrorsAtStart=driver.getPacketsRecvErrors();
  packetDeadline=millis()+5000;
  recordBenchResult(lastListenResult,seq,"{\"listening\":%u,\"profile\":%u,\"sf500\":%u,\"preamble\":%u,\"optimized_rx_resumes\":%u}\n",
                seq,target,sf,driver.profilePreamble(target),unsigned(chip.getOptimizedProfileSwitches()-fastStart));
}

void sendPacket(unsigned sf, unsigned target, unsigned seq, unsigned len, unsigned preamble, int power, unsigned spiMHz, bool bulk) {
  packetListening=false;
  chip.endHop();chip.experiment=0;chip.rxPrimed=false;
  chip.productionPath=true;chip.setProfileSwitchOptimization(false);
  radioHal.bulkTransfer=bulk;
  radioHal.spiSettings=SPISettings(spiMHz*1000000,MSBFIRST,SPI_MODE0);
  // A fresh normal chip setup avoids depending on the receiver-only wrapper's
  // state after a direct TX. Reset/init is outside the receiver's timed hop.
  int16_t rc=chip.std_init(&radioSpi) ? RADIOLIB_ERR_NONE : RADIOLIB_ERR_CHIP_NOT_FOUND;
  if (rc==0) rc=chip.setFrequency(target ? 910.5f : 909.5f);
  if (rc==0) rc=chip.setLoRaModulationParams(target ? 500.0f : 62.5f,target ? sf : 7,5);
  if (rc==0) rc=chip.setPreambleLength(preamble);
  if (rc==0) rc=chip.setOutputPower(power);
  uint8_t data[255];
  memcpy(data,"PSW3",4);memcpy(data+4,&seq,4);data[8]=target;data[9]=sf;
  for(unsigned i=10;i<len;++i) data[i]=uint8_t(i ^ seq);
  if (rc==0) rc=benchTransmit(data,len);
  recordBenchResult(lastTxResult,seq,"{\"sent\":%u,\"len\":%u,\"rc\":%d,\"power_dbm\":%d,\"spi_mhz\":%u,\"bulk\":%s}\n",
                    seq,len,rc,power,spiMHz,bulk ? "true":"false");
}

void servicePacketProbe() {
  if (!packetListening) return;
#ifdef HIL_INDICATOR
  radioHal.serviceInterrupt();
#endif
  uint8_t data[256];
  const int len=driver.recvRaw(data,sizeof(data));
  driver.onReceiveProcessed();
  if (len>=12 && !memcmp(data,"PSW3",4)) {
    uint32_t seq;memcpy(&seq,data+4,4);
    if (seq==packetSequence) {
      bool valid=data[8]==packetTarget && data[9]==packetSf
          && driver.receiveProfile()==packetTarget;
      for(int i=10;i<len;++i) if(data[i]!=uint8_t(i ^ seq)) valid=false;
      recordBenchResult(lastRxResult,seq,"{\"received\":%u,\"len\":%d,\"valid\":%s,\"profile\":%u,\"rssi\":%.1f,\"snr\":%.1f,\"rx_errors\":%u}\n",
                    seq,len,valid ? "true":"false",driver.receiveProfile(),driver.getLastRSSI(),driver.getLastSNR(),
                    driver.getPacketsRecvErrors()-packetErrorsAtStart);
      packetListening=false;
    }
  }
  if (packetListening && int32_t(millis()-packetDeadline)>=0) {
    recordBenchResult(lastRxResult,packetSequence,"{\"received\":%u,\"timeout\":true,\"valid\":false,\"rx_errors\":%u,\"irq\":%u,\"rx_mode\":%d,\"device_errors\":%u}\n",
                  packetSequence,driver.getPacketsRecvErrors()-packetErrorsAtStart,
                  unsigned(chip.getIrqFlags()),sx126xReceiveMode(&chip),unsigned(chip.getDeviceErrors()));
    packetListening=false;
  }
}
