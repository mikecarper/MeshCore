// Included inside a CustomLR11xx/CustomLR2021 public section. The family
// selects its own opcodes/IRQ layout; no SX126x register commands are reused.
#if MESH_WARM_RX_REFRESH
    uint32_t agcPulseTransitionUs() const {
#if MC_LR_AGC_2021 && MESH_LR2021_RXPS_AVAILABLE
      return MESH_LR2021_RXPS_TRANSITION_US;
#else
      return tcxoDelay + 1000UL;
#endif
    }
    uint32_t agcPulsePacketBudgetUs() const { return _maxPayloadMillis * 1000UL; }
    static bool agcPulsePacketDone(uint32_t irq) {
#if MC_LR_AGC_2021
      return irq & RADIOLIB_LR2021_IRQ_RX_DONE;
#else
      return irq & RADIOLIB_LR11X0_IRQ_RX_DONE;
#endif
    }
    static bool agcPulseActivity(uint32_t irq) {
#if MC_LR_AGC_2021
      return irq & (RADIOLIB_LR2021_IRQ_RX_DONE | RADIOLIB_LR2021_IRQ_PREAMBLE_DETECTED
          | RADIOLIB_LR2021_IRQ_LORA_HEADER_VALID);
#else
      return irq & (RADIOLIB_LR11X0_IRQ_RX_DONE | RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED
          | RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID);
#endif
    }
    int16_t agcPulseStatus(uint32_t& irq,uint8_t& mode) {
      uint8_t stat1=0,stat2=0;
      // getStatus clocks six raw bytes, including stat1/stat2. The normal
      // read prefix would otherwise discard the leading status bytes.
      auto& statusWidth=mod->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS];
      const auto savedWidth=statusWidth;
      statusWidth=Module::BITS_0;
      const int16_t rc=getStatus(&stat1,&stat2,&irq);
      statusWidth=savedWidth;
#if MC_LR_AGC_2021
      // LR2021 uses Stat2 bits2:0; LR11xx uses bits3:1.
      mode=stat2&7;
#else
      mode=(stat2>>1)&7;
#endif
      if(rc) return rc;
      const uint8_t commandStatus=stat1&0x0e;
      return commandStatus==0x04 || commandStatus==0x06 ? RADIOLIB_ERR_NONE : RADIOLIB_ERR_SPI_CMD_FAILED;
    }
    int16_t agcPulseStandby() {
#if MC_LR_AGC_2021
      return standby(RADIOLIB_LR2021_STANDBY_RC);
#else
      return standby(RADIOLIB_LR11X0_STANDBY_RC);
#endif
    }
    int16_t agcPulseStage() {
      // BUSY must stay asserted while asleep: releasing it hides the natural
      // sleep/wake witness and would permit another SPI owner to wake the chip.
#if MC_LR_AGC_2021
      int16_t rc=configLfClock(RADIOLIB_LR2021_LF_CLOCK_INTERNAL_RC);
#else
      _lfClockRestoreNeeded=true;
      int16_t rc=configLfClock(RADIOLIB_LR11X0_LF_CLK_RC | RADIOLIB_LR11X0_LF_BUSY_RELEASE_DISABLED);
#endif
      if(rc) return rc;
      RadioModeConfig_t cfg={};
      cfg.receive.timeout=0xFFFFFFUL;
      cfg.receive.irqFlags=RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL<<RADIOLIB_IRQ_PREAMBLE_DETECTED);
      cfg.receive.irqMask=RADIOLIB_IRQ_RX_DEFAULT_MASK;
      return stageMode(RADIOLIB_RADIO_MODE_RX,&cfg);
    }
    int16_t agcPulseArm(uint32_t rx_ticks,uint32_t sleep_ticks) {
#if MC_LR_AGC_2021
      const uint32_t period=rx_ticks+sleep_ticks;
      if(period>0xFFFFFFUL) return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;
      const uint16_t command=RADIOLIB_LR2021_CMD_SET_RX_DUTY_CYCLE;
#else
      const uint32_t period=sleep_ticks;
      const uint16_t command=RADIOLIB_LR11X0_CMD_SET_RX_DUTY_CYCLE;
#endif
      uint8_t bytes[]={uint8_t(rx_ticks>>16),uint8_t(rx_ticks>>8),uint8_t(rx_ticks),
          uint8_t(period>>16),uint8_t(period>>8),uint8_t(period),0};
      mod->setRfSwitchState(Module::MODE_RX);
      // Deliberately omit RadioLib's second NSS/status transaction after arm.
      // Completion is instead checked after a natural RTC/BUSY sleep cycle.
      const int16_t rc=mod->SPIwriteStream(command,bytes,sizeof(bytes),true,false);
      if(!rc) { _activityAt=0;_headerSeen=false; }
      return rc;
    }
    int16_t agcPulseStopClock() {
#if MC_LR_AGC_2021
      return configLfClock(RADIOLIB_LR2021_LF_CLOCK_INTERNAL_RC);
#else
      return restoreLfClock();
#endif
    }
#endif
