#include "CadRxPowerSaving.h"

#if MESH_RAK3401_CAD_RXPS
#include <Arduino.h>
#include <nrf_gpio.h>
#include "variant.h"
#include <RadioLib.h>

#if !defined(NRF52840_XXAA) || SPI_32MHZ_INTERFACE || MESH_HIL_SPI_STARTUP_DIAG \
    || MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD || defined(OTA_QSPI_RAK3401_RADIO_BUS_HANDOFF)
#error "CAD RXPS requires exclusive access to the RAK3401 SPIM2 radio bus and RTC2"
#endif

namespace rak3401::cadRxps {
namespace {

constexpr unsigned Cycles = 256;
// Leave room for the software deadline to retire a failed hardware counter.
constexpr unsigned SpareCycles = 8;
constexpr unsigned FrameSize = 3;
constexpr unsigned FirstChannel = 7;  // Application PPI channels on S140
constexpr unsigned ChannelCount = 7;
constexpr unsigned Group = 3;
constexpr uint32_t Channels = ((1u << ChannelCount) - 1) << FirstChannel;
constexpr uint32_t Starts = 0x0fu << FirstChannel;
constexpr uint32_t RtcEvents = 0x0fu << RTC_EVTEN_COMPARE0_Pos;
constexpr uint32_t PeriodTicks = 1638;  // 49.99 ms at 32.768 kHz
constexpr uint32_t CadTicks = 82;       // 2.50 ms for warm wake after asserting NSS
constexpr uint32_t SleepSelectTicks = 450;
constexpr uint32_t SleepTicks = 453;    // 11.32 ms after SetCad, with room for RX handoff

NRF_SPIM_Type* const Spi = NRF_SPIM2;
NRF_RTC_Type* const Clock = NRF_RTC2;
NRF_TIMER_Type* const Counter = NRF_TIMER3;
uint8_t frames[Cycles + SpareCycles][2][FrameSize];
int cs_channel = -1;
uint32_t started_ms = 0;

constexpr uint32_t eventConfig(uint32_t pin) {
  return (pin << GPIOTE_CONFIG_PSEL_Pos)
      | (GPIOTE_CONFIG_MODE_Event << GPIOTE_CONFIG_MODE_Pos)
      | (GPIOTE_CONFIG_POLARITY_LoToHi << GPIOTE_CONFIG_POLARITY_Pos);
}

void map(unsigned offset, volatile uint32_t& event, volatile uint32_t& task,
         volatile uint32_t* fork = nullptr) {
  const unsigned channel = FirstChannel + offset;
  NRF_PPI->CH[channel].EEP = reinterpret_cast<uint32_t>(&event);
  NRF_PPI->CH[channel].TEP = reinterpret_cast<uint32_t>(&task);
  NRF_PPI->FORK[channel].TEP = reinterpret_cast<uint32_t>(fork);
}

}  // namespace

bool ownsBus() {
  return cs_channel >= 0;
}

bool exhausted() {
  return ownsBus() && Counter->EVENTS_COMPARE[0] != 0;
}

bool overdue() {
  return ownsBus() && uint32_t(millis() - started_ms) >= Cycles * 50 + 250;
}

bool inScanWindow() {
  return ownsBus() && !exhausted() && Clock->COUNTER < SleepTicks + 8;
}

bool interruptAsserted() {
  return nrf_gpio_pin_read(g_ADigitalPinMap[P_LORA_DIO_1]);
}

bool start() {
  if (ownsBus() || interruptAsserted() || Spi->ENABLE || Clock->INTENSET || Clock->EVTEN
      || NRF_PPI->CHG[Group] || (NRF_PPI->CHEN & Channels)) return false;
  for (unsigned channel = FirstChannel; channel < FirstChannel + ChannelCount; ++channel) {
    if (NRF_PPI->CH[channel].EEP || NRF_PPI->CH[channel].TEP || NRF_PPI->FORK[channel].TEP) {
      return false;
    }
  }

  const uint32_t cs_pin = g_ADigitalPinMap[P_LORA_NSS];
  const uint32_t dio_config = eventConfig(g_ADigitalPinMap[P_LORA_DIO_1]);
  int dio_channel = -1;
  int free_channel = -1;
  for (unsigned i = 0; i < sizeof(NRF_GPIOTE->CONFIG) / sizeof(uint32_t); ++i) {
    const uint32_t config = NRF_GPIOTE->CONFIG[i];
    if (config == dio_config && (NRF_GPIOTE->INTENSET & (1u << i))) dio_channel = i;
    if (config == 0) free_channel = i;
  }
  if (dio_channel < 0 || free_channel < 0) return false;
  cs_channel = free_channel;
  auto& select = NRF_GPIOTE->TASKS_CLR[cs_channel];
  auto& deselect = NRF_GPIOTE->TASKS_SET[cs_channel];
  NRF_GPIOTE->CONFIG[cs_channel] = (cs_pin << GPIOTE_CONFIG_PSEL_Pos)
      | (GPIOTE_CONFIG_MODE_Task << GPIOTE_CONFIG_MODE_Pos)
      | (GPIOTE_CONFIG_OUTINIT_High << GPIOTE_CONFIG_OUTINIT_Pos);

  // SPIM array lists use a single frame width. The SX1262 ignores the trailing
  // zeroes in these two commands, verified on the RAK13302.
  for (auto& pair : frames) {
    pair[0][0] = RADIOLIB_SX126X_CMD_SET_CAD;
    pair[1][0] = RADIOLIB_SX126X_CMD_SET_SLEEP;
    pair[1][1] = RADIOLIB_SX126X_SLEEP_START_WARM;
  }
  Spi->CONFIG = 0;
  // The direct CAD test passed at 1 MHz and failed at 8 MHz on this
  // RAK13302. Keep the slower clock for autonomous SPI commands.
  Spi->FREQUENCY = SPIM_FREQUENCY_FREQUENCY_M1;
  Spi->TXD.PTR = reinterpret_cast<uint32_t>(frames);
  Spi->TXD.MAXCNT = FrameSize;
  Spi->TXD.LIST = SPIM_TXD_LIST_LIST_ArrayList;
  Spi->RXD.MAXCNT = 0;
  Spi->EVENTS_END = 0;
  Spi->ENABLE = SPIM_ENABLE_ENABLE_Enabled;

  Counter->TASKS_STOP = 1;
  Counter->MODE = TIMER_MODE_MODE_LowPowerCounter;
  Counter->BITMODE = TIMER_BITMODE_BITMODE_16Bit;
  Counter->TASKS_CLEAR = 1;
  Counter->CC[0] = Cycles * 2;
  Counter->EVENTS_COMPARE[0] = 0;
  Counter->TASKS_START = 1;

  Clock->TASKS_STOP = 1;
  Clock->TASKS_CLEAR = 1;
  Clock->PRESCALER = 0;
  Clock->CC[0] = PeriodTicks;
  Clock->CC[1] = CadTicks;
  Clock->CC[2] = SleepSelectTicks;
  Clock->CC[3] = SleepTicks;
  for (unsigned i = 0; i < 4; ++i) Clock->EVENTS_COMPARE[i] = 0;
  Clock->EVTENSET = RtcEvents;

  auto& cancel = NRF_PPI->TASKS_CHG[Group].DIS;
  map(0, Clock->EVENTS_COMPARE[0], Clock->TASKS_CLEAR, &select);
  map(1, Clock->EVENTS_COMPARE[1], Spi->TASKS_START, &Counter->TASKS_COUNT);
  map(2, Clock->EVENTS_COMPARE[2], select);
  map(3, Clock->EVENTS_COMPARE[3], Spi->TASKS_START, &Counter->TASKS_COUNT);
  map(4, Spi->EVENTS_END, deselect);
  map(5, NRF_GPIOTE->EVENTS_IN[dio_channel], cancel);
  // Stop before the next transfer even if the sketch task cannot run.
  map(6, Counter->EVENTS_COMPARE[0], cancel);
  NRF_PPI->CHG[Group] = Starts;
  NRF_PPI->CHENSET = Channels;
  if (interruptAsserted()) {
    stop();
    return false;
  }
  started_ms = millis();
  select = 1;
  Clock->TASKS_START = 1;
  return true;
}

void stop() {
  if (!ownsBus()) return;
  NRF_PPI->TASKS_CHG[Group].DIS = 1;
  Clock->TASKS_STOP = 1;
  Clock->EVTENCLR = RtcEvents;
  // Let a three-byte 1 MHz transfer already in flight finish before GPIO
  // and SPIM return to SPI1. When no DMA started, release NSS explicitly.
  delayMicroseconds(64);
  NRF_GPIOTE->TASKS_SET[cs_channel] = 1;
  NRF_PPI->CHENCLR = Channels;
  NRF_PPI->CHG[Group] = 0;
  for (unsigned i = 0; i < ChannelCount; ++i) {
    NRF_PPI->CH[FirstChannel + i].EEP = 0;
    NRF_PPI->CH[FirstChannel + i].TEP = 0;
    NRF_PPI->FORK[FirstChannel + i].TEP = 0;
  }
  Counter->TASKS_STOP = 1;
  Spi->ENABLE = SPIM_ENABLE_ENABLE_Disabled;
  Spi->TXD.LIST = SPIM_TXD_LIST_LIST_Disabled;
  nrf_gpio_pin_set(g_ADigitalPinMap[P_LORA_NSS]);
  NRF_GPIOTE->CONFIG[cs_channel] = 0;
  cs_channel = -1;
}

}  // namespace rak3401::cadRxps
#endif
