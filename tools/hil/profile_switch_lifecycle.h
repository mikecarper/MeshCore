#pragma once
// Explicit host-requested, bounded lifecycle qualification. TX is one 16-byte
// -9 dBm probe per pass, never an autonomous transmission.
int16_t lifecycleHop() {
  chip.beginProfileSwitch(true);
  int16_t rc=chip.standby();
  if (!rc) rc=chip.setLoRaModulationParams(500,7,5);
  if (!rc) rc=chip.setPreambleLength(32);
  if (!rc) rc=chip.startReceive();
  chip.endProfileSwitch(rc==0);
  return rc;
}

void runLifecycle() {
  packetListening=false;
  chip.productionPath=true;chip.experiment=0;
  radioHal.bulkTransfer=true;
  radioHal.spiSettings=SPISettings(8000000,MSBFIRST,SPI_MODE0);
  Serial.print("{\"lifecycle\":true,\"spi_mhz\":8,\"bulk\":true,\"checks\":[");
  const char* names[]={"tx","cad","sleep","reset","sleep_rc","sleep_retcxo"};
  for (unsigned round=0;round<3;++round) for (unsigned operation=0;operation<6;++operation) {
    chip.setProfileSwitchOptimization(true);
    bool ok=chip.std_init(&radioSpi);
    chip.standbyXOSC=operation!=4;
    int16_t rc=ok ? chip.startReceive() : RADIOLIB_ERR_CHIP_NOT_FOUND;
    uint32_t before=chip.getOptimizedProfileSwitches();
    if (!rc) rc=lifecycleHop();
    const unsigned initial=chip.getOptimizedProfileSwitches()-before;
    if (!rc) {
      if (operation==0) {
        uint8_t data[16]={'P','S','W','L'};
        rc=chip.setOutputPower(-9);
        if (!rc) rc=chip.transmit(data,sizeof(data));
      } else if (operation==1) {
        rc=chip.scanChannel();
        if (rc==RADIOLIB_CHANNEL_FREE || rc==RADIOLIB_LORA_DETECTED) rc=0;
      } else if (operation==2 || operation>=4) {
        rc=chip.sleep(true);
        if (!rc && operation==5) rc=chip.setTCXO(chip.tcxoVoltage,chip.tcxoDelay);
      } else {
        rc=chip.reset();
        if (!rc && !chip.std_init(&radioSpi)) rc=RADIOLIB_ERR_CHIP_NOT_FOUND;
      }
    }
    before=chip.getOptimizedProfileSwitches();
    if (!rc) rc=lifecycleHop();  // must do full RX setup after invalidation
    const unsigned fallback=chip.getOptimizedProfileSwitches()-before;
    before=chip.getOptimizedProfileSwitches();
    if (!rc) rc=lifecycleHop();  // now a freshly primed RX can use fast resume
    const unsigned resumed=chip.getOptimizedProfileSwitches()-before;
    const int mode=rc ? -1 : sx126xReceiveMode(&chip);
    const unsigned errors=unsigned(chip.getDeviceErrors());
    if (round || operation) Serial.print(',');
    Serial.printf("{\"operation\":\"%s\",\"round\":%u,\"rc\":%d,\"initial_fast\":%u,\"fallback_fast\":%u,\"resumed_fast\":%u,\"rx_mode\":%d,\"device_errors\":%u}",
                  names[operation],round+1,rc,initial,fallback,resumed,mode,errors);
  }
  chip.standbyXOSC=false;
  chip.standby();
  ready=false; // direct chip tests deliberately bypass wrapper state: reboot
  Serial.println("],\"reboot_required\":true}");
  Serial.flush();
}
