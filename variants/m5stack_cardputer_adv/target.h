#pragma once

#define RADIOLIB_STATIC_ONLY 1
#include <RadioLib.h>
#include <SPI.h>
#include <helpers/radiolib/RadioLibWrappers.h>
#include <helpers/ESP32Board.h>
#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include <helpers/AutoDiscoverRTCClock.h>
#include "CardputerAdvBoard.h"
#include "CardputerSensorManager.h"

#if CARDPUTER_ADV_UI
  #include <SD.h>
  #include "hw/CardputerDisplay.h"
#endif

extern CardputerAdvBoard board;
extern WRAPPER_CLASS radio_driver;
extern AutoDiscoverRTCClock rtc_clock;
// The microSD slot shares SCK/MISO/MOSI with the Cap LoRa-1262.
extern SPIClass lora_spi;

extern CardputerSensorManager sensors;
#if CARDPUTER_ADV_UI
extern CardputerDisplay display;
#endif

bool radio_init();
mesh::LocalIdentity radio_new_identity();
