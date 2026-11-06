// Included inside CustomSTM32WLx's public section. Existing compact firmware
// exposes LoRa packet RX/TX and CW/Morse TX. FSK capture is independently opted
// in with MESH_ENABLE_MORSE_RX; that opt-in keeps the complete RadioLib driver.
#if MESH_COMPACT_STM32_RADIO && !MESH_ENABLE_MORSE_RX
    int16_t beginFSK(const ConfigFSK_t&) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t beginFSK(float,float,float,float,int8_t,uint16_t,float,bool) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t beginBPSK(const ConfigBPSK_t&) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t beginBPSK(float,float,int8_t,float,bool) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t beginLRFHSS(const ConfigLRFHSS_t&) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t beginLRFHSS(float,uint8_t,uint8_t,bool,int8_t,float,bool) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t setBitRate(float) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t setFrequencyDeviation(float) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t setDataShaping(uint8_t) override { return RADIOLIB_ERR_UNSUPPORTED; }
    int16_t setEncoding(uint8_t) override { return RADIOLIB_ERR_UNSUPPORTED; }

    // Keep the pinned RadioLib LoRa branches and the real launchMode/STM32 RF
    // switch. Only the unexposed GFSK/BPSK/LR-FHSS staging paths are omitted.
    int16_t stageMode(RadioModeType_t mode,RadioModeConfig_t* cfg) override {
      int16_t rc;
      if(mode==RADIOLIB_RADIO_MODE_RX) {
        if(headerType==RADIOLIB_SX126X_LORA_HEADER_IMPLICIT && cfg->receive.len)
          implicitLen=cfg->receive.len;
        rc=standby();if(rc) return rc;
        if(cfg->receive.timeout!=RADIOLIB_SX126X_RX_TIMEOUT_INF)
          cfg->receive.irqMask|=(1UL<<RADIOLIB_IRQ_TIMEOUT);
        rc=setDioIrqParams(getIrqMapped(cfg->receive.irqFlags),getIrqMapped(cfg->receive.irqMask));
        if(rc) return rc;
        rc=setBufferBaseAddress();if(rc) return rc;
        rc=clearIrqStatus(RADIOLIB_SX126X_IRQ_ALL);if(rc) return rc;
        if(getPacketType()!=RADIOLIB_SX126X_PACKET_TYPE_LORA) return RADIOLIB_ERR_UNKNOWN;
        rc=setPacketParams(preambleLengthLoRa,crcTypeLoRa,implicitLen,headerType,invertIQEnabled);
        if(cfg->receive.timeout==0xFFFFFFFFUL) cfg->receive.timeout=0xFFFFFFUL;
        rxTimeout=cfg->receive.timeout;
      } else if(mode==RADIOLIB_RADIO_MODE_TX) {
        if(cfg->transmit.len>RADIOLIB_SX126X_MAX_PACKET_LENGTH) return RADIOLIB_ERR_PACKET_TOO_LONG;
        if(getPacketType()!=RADIOLIB_SX126X_PACKET_TYPE_LORA) return RADIOLIB_ERR_UNKNOWN;
        rc=setPacketParams(preambleLengthLoRa,crcTypeLoRa,cfg->transmit.len,headerType,invertIQEnabled);
        if(rc) return rc;
        rc=setDioIrqParams(RADIOLIB_SX126X_IRQ_TX_DONE|RADIOLIB_SX126X_IRQ_TIMEOUT,RADIOLIB_SX126X_IRQ_TX_DONE);
        if(rc) return rc;
        rc=setBufferBaseAddress();if(rc) return rc;
        rc=writeBuffer(cfg->transmit.data,cfg->transmit.len);if(rc) return rc;
        rc=clearIrqStatus(RADIOLIB_SX126X_IRQ_ALL);if(rc) return rc;
        rc=fixSensitivity();if(rc) return rc;
      } else return RADIOLIB_ERR_UNSUPPORTED;
      stagedMode=mode;
      return rc;
    }
#endif
