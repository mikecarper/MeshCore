// Included inside the public section of uncached SX126x-family drivers.
// The packet fields below are the driver's real acknowledged RadioLib tuple.
// Unlike CustomSX1262 these drivers have no fast-hop cache to revoke.
#if MESH_WARM_RX_REFRESH
    uint32_t getTcxoDelay() const { return tcxoDelay; }
    bool isChipBusy() {
      const uint32_t busy=getMod()->getGpio();
      return busy!=RADIOLIB_NC && getMod()->hal->digitalRead(busy);
    }
    void invalidatePulseReceiveState() { _activityAt=0;_headerSeen=false; }
    uint64_t pulsePacketBudgetUs() const { return (uint64_t(_preambleMillis)+_maxPayloadMillis)*1000ULL; }
    int16_t stagePulseReceive() {
      Module* mod=getMod();
      invalidatePulseReceiveState();
      const uint16_t flags=getIrqMapped(RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL<<RADIOLIB_IRQ_PREAMBLE_DETECTED));
      const uint16_t mask=getIrqMapped((1UL<<RADIOLIB_IRQ_RX_DONE) | (1UL<<RADIOLIB_IRQ_TIMEOUT)
          | (1UL<<RADIOLIB_IRQ_CRC_ERR) | (1UL<<RADIOLIB_IRQ_HEADER_ERR));
      const uint8_t dio[]={uint8_t(flags>>8),uint8_t(flags),uint8_t(mask>>8),uint8_t(mask),0,0,0,0};
      int16_t rc=mod->SPIwriteStream(RADIOLIB_SX126X_CMD_SET_DIO_IRQ_PARAMS,dio,sizeof(dio),true,false);
      if(rc) return rc;
      const uint8_t bases[]={0,0};
      rc=mod->SPIwriteStream(RADIOLIB_SX126X_CMD_SET_BUFFER_BASE_ADDRESS,bases,sizeof(bases),true,false);
      if(rc) return rc;
      // Caller has stopped RX and preserved any completion before clearing.
      const uint8_t clear[]={0xFF,0xFF};
      rc=mod->SPIwriteStream(RADIOLIB_SX126X_CMD_CLEAR_IRQ_STATUS,clear,sizeof(clear),true,false);
      if(rc) return rc;
      uint8_t modem=0;
      rc=mod->SPIreadStream(RADIOLIB_SX126X_CMD_GET_PACKET_TYPE,&modem,1,true,false);
      if(rc) return rc;
      if(modem!=RADIOLIB_SX126X_PACKET_TYPE_LORA) return RADIOLIB_ERR_WRONG_MODEM;
      const uint8_t read_iq[]={RADIOLIB_SX126X_CMD_READ_REGISTER,0x07,0x36};
      uint8_t iq=0;
      rc=mod->SPIreadStream(read_iq,sizeof(read_iq),&iq,1,true,false);
      if(rc) return rc;
      if(invertIQEnabled==RADIOLIB_SX126X_LORA_IQ_INVERTED) iq&=0xFB;else iq|=0x04;
      const uint8_t write_iq[]={RADIOLIB_SX126X_CMD_WRITE_REGISTER,0x07,0x36};
      rc=mod->SPIwriteStream(write_iq,sizeof(write_iq),&iq,1,true,false);
      if(rc) return rc;
      const uint8_t packet[]={uint8_t(preambleLengthLoRa>>8),uint8_t(preambleLengthLoRa),headerType,implicitLen,crcTypeLoRa,invertIQEnabled};
      rc=mod->SPIwriteStream(RADIOLIB_SX126X_CMD_SET_PACKET_PARAMS,packet,sizeof(packet),true,false);
      if(!rc) rxTimeout=RADIOLIB_SX126X_RX_TIMEOUT_INF;
      return rc;
    }
    int16_t verifyPulseReceive() {
      Module* mod=getMod();
      if(isChipBusy()) return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
      uint8_t status=0;
      const int16_t rc=sx126xReadStatus(mod,status);
      if(rc) return rc;
      return status && status!=0xFF && ((status>>4)&7)==5 ? RADIOLIB_ERR_NONE : -11023;
    }
    int16_t enterPulseContinuous() {
      // Called only from owned awake RC standby after checked RTC cleanup.
      Module* mod=getMod();
      const int16_t rc=stagePulseReceive();
      if(rc) return rc;
      mod->setRfSwitchState(Module::MODE_RX);
      const uint8_t continuous[]={0xFF,0xFF,0xFF};
      const int16_t result=mod->SPIwriteStream(RADIOLIB_SX126X_CMD_SET_RX,continuous,sizeof(continuous),true,false);
      if(!result) { _headerSeen=false;_activityAt=0; }
      return result;
    }
#endif
