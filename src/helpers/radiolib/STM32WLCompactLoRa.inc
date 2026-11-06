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

    // RadioLib's inherited getTimeOnAir still packs and normalizes the cached
    // LoRa tuple. Keep the pinned integer calculation, including its uint32_t
    // product and rounding, without retaining the unexposed modem formulas.
    RadioLibTime_t calculateTimeOnAir(ModemType_t modem,DataRate_t dr,PacketConfig_t pc,size_t len) override {
      if(modem!=RADIOLIB_MODEM_LORA) return RADIOLIB_ERR_WRONG_MODEM;
      uint32_t symbolLength_us=((uint32_t)(1000*10)<<dr.lora.spreadingFactor)/(dr.lora.bandwidth*10);
      uint8_t sfCoeff1_x4=17,sfCoeff2=8;
      if(dr.lora.spreadingFactor==5 || dr.lora.spreadingFactor==6) {
        sfCoeff1_x4=25;sfCoeff2=0;
      }
      uint8_t sfDivisor=4*dr.lora.spreadingFactor;
      if(pc.lora.ldrOptimize) sfDivisor=4*(dr.lora.spreadingFactor-2);
      const int8_t bitsPerCrc=16;
      const int8_t N_symbol_header=pc.lora.implicitHeader?0:20;
      int16_t bitCount=(int16_t)8*len+pc.lora.crcEnabled*bitsPerCrc-4*dr.lora.spreadingFactor+sfCoeff2+N_symbol_header;
      if(bitCount<0) bitCount=0;
      uint16_t nPreCodedSymbols=(bitCount+(sfDivisor-1))/sfDivisor;
      uint32_t nSymbol_x4=(pc.lora.preambleLength+8)*4+sfCoeff1_x4+nPreCodedSymbols*dr.lora.codingRate*4;
      return (symbolLength_us*nSymbol_x4)/4;
    }

    // Preserve the pinned blocking LoRa TX behavior. Packet TX normally uses
    // startTransmit directly; this virtual entry must not pin LR-FHSS hopping.
    int16_t transmit(const uint8_t* data,size_t len,uint8_t addr=0) override {
      int16_t rc=standby();if(rc) return rc;
      if(codingRate>RADIOLIB_SX126X_LORA_CR_4_8) {
        if(len<8) return RADIOLIB_ERR_PACKET_TOO_SHORT;
        if(crcTypeLoRa==RADIOLIB_SX126X_LORA_CRC_ON && len>RADIOLIB_SX126X_MAX_PACKET_LENGTH-2)
          return RADIOLIB_ERR_PACKET_TOO_LONG;
      }
      if(len>RADIOLIB_SX126X_MAX_PACKET_LENGTH) return RADIOLIB_ERR_PACKET_TOO_LONG;
      const RadioLibTime_t timeout=5+(getTimeOnAir(len)*5)/1000;
      rc=startTransmit(data,len,addr);if(rc) return rc;
      // Retain the normal LoRa transaction sequence, even though the modem
      // value no longer selects an unexposed frequency-hopping branch.
      (void)getPacketType();
      const RadioLibTime_t start=mod->hal->millis();
      while(true) {
        mod->hal->yield();
        if(mod->hal->millis()-start>timeout) {
          finishTransmit();return RADIOLIB_ERR_TX_TIMEOUT;
        }
        if(mod->hal->digitalRead(mod->getIrq())) break;
      }
      return finishTransmit();
    }

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
