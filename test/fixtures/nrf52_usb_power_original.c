/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2019 Ha Thach (tinyusb.org)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 *
 * This file is part of the TinyUSB stack.
 */

// Pinned framework d541301: real power handler before the build-local fix.
void tusb_hal_nrf_power_event(uint32_t event);
void tusb_hal_nrf_power_event(uint32_t event) {
  // Value is chosen to be as same as NRFX_POWER_USB_EVT_* in nrfx_power.h
  enum {
    USB_EVT_DETECTED = 0,
    USB_EVT_REMOVED = 1,
    USB_EVT_READY = 2
  };

#if CFG_TUSB_DEBUG >= 3
  const char* const power_evt_str[] = {"Detected", "Removed", "Ready"};
  TU_LOG(3, "Power USB event: %s\r\n", power_evt_str[event]);
#endif

  switch (event) {
    case USB_EVT_DETECTED:
      if (!NRF_USBD->ENABLE) {
        // Prepare for receiving READY event: disable interrupt since we will blocking wait
        NRF_USBD->INTENCLR = USBD_INTEN_USBEVENT_Msk;
        NRF_USBD->EVENTCAUSE = USBD_EVENTCAUSE_READY_Msk;
        __ISB();
        __DSB(); // for sync

#ifdef NRF52_SERIES // NRF53 does not need this errata
        // ERRATA 171, 187, 166
        if (nrf52_errata_187()) {
          // CRITICAL_REGION_ENTER();
          if (*((volatile uint32_t*) (0x4006EC00)) == 0x00000000) {
            *((volatile uint32_t*) (0x4006EC00)) = 0x00009375;
            *((volatile uint32_t*) (0x4006ED14)) = 0x00000003;
            *((volatile uint32_t*) (0x4006EC00)) = 0x00009375;
          } else {
            *((volatile uint32_t*) (0x4006ED14)) = 0x00000003;
          }
          // CRITICAL_REGION_EXIT();
        }

        if (nrf52_errata_171()) {
          // CRITICAL_REGION_ENTER();
          if (*((volatile uint32_t*) (0x4006EC00)) == 0x00000000) {
            *((volatile uint32_t*) (0x4006EC00)) = 0x00009375;
            *((volatile uint32_t*) (0x4006EC14)) = 0x000000C0;
            *((volatile uint32_t*) (0x4006EC00)) = 0x00009375;
          } else {
            *((volatile uint32_t*) (0x4006EC14)) = 0x000000C0;
          }
          // CRITICAL_REGION_EXIT();
        }
#endif

        // Enable the peripheral (will cause Ready event)
        NRF_USBD->ENABLE = 1;
        __ISB();
        __DSB(); // for sync

        // Enable HFCLK
        hfclk_enable();
      }
      break;

    case USB_EVT_READY:
      // Skip if pull-up is enabled and HCLK is already running.
      // Application probably call this more than necessary.
      if (NRF_USBD->USBPULLUP && hfclk_running()) break;

      // Waiting for USBD peripheral enabled
      while (!(USBD_EVENTCAUSE_READY_Msk & NRF_USBD->EVENTCAUSE)) {}

      NRF_USBD->EVENTCAUSE = USBD_EVENTCAUSE_READY_Msk;
      __ISB();
      __DSB(); // for sync

#ifdef NRF52_SERIES
      if (nrf52_errata_171()) {
        // CRITICAL_REGION_ENTER();
        if (*((volatile uint32_t*) (0x4006EC00)) == 0x00000000) {
          *((volatile uint32_t*) (0x4006EC00)) = 0x00009375;
          *((volatile uint32_t*) (0x4006EC14)) = 0x00000000;
          *((volatile uint32_t*) (0x4006EC00)) = 0x00009375;
        } else {
          *((volatile uint32_t*) (0x4006EC14)) = 0x00000000;
        }

        // CRITICAL_REGION_EXIT();
      }

      if (nrf52_errata_187()) {
        // CRITICAL_REGION_ENTER();
        if (*((volatile uint32_t*) (0x4006EC00)) == 0x00000000) {
          *((volatile uint32_t*) (0x4006EC00)) = 0x00009375;
          *((volatile uint32_t*) (0x4006ED14)) = 0x00000000;
          *((volatile uint32_t*) (0x4006EC00)) = 0x00009375;
        } else {
          *((volatile uint32_t*) (0x4006ED14)) = 0x00000000;
        }
        // CRITICAL_REGION_EXIT();
      }

      if (nrf52_errata_166()) {
        *((volatile uint32_t*) (NRF_USBD_BASE + 0x800)) = 0x7E3;
        *((volatile uint32_t*) (NRF_USBD_BASE + 0x804)) = 0x40;

        __ISB();
        __DSB();
      }
#endif

      // ISO buffer Lower half for IN, upper half for OUT
      NRF_USBD->ISOSPLIT = USBD_ISOSPLIT_SPLIT_HalfIN;

      // Enable bus-reset interrupt
      NRF_USBD->INTENSET = USBD_INTEN_USBRESET_Msk;

      // Enable interrupt, priorities should be set by application
      NVIC_ClearPendingIRQ(USBD_IRQn);

      // Don't enable USBD interrupt yet, if dcd_init() did not finish yet
      // Interrupt will be enabled by tud_init(), when USB stack is ready
      // to handle interrupts.
      if (tud_inited()) {
        NVIC_EnableIRQ(USBD_IRQn);
      }

      // Wait for HFCLK
      while (!hfclk_running()) {}

      // Enable pull up
      NRF_USBD->USBPULLUP = 1;
      __ISB();
      __DSB(); // for sync
      break;

    case USB_EVT_REMOVED:
      if (NRF_USBD->ENABLE) {
        // Abort all transfers

        // Disable pull up
        NRF_USBD->USBPULLUP = 0;
        __ISB();
        __DSB(); // for sync

        // Disable Interrupt
        NVIC_DisableIRQ(USBD_IRQn);

        // disable all interrupt
        NRF_USBD->INTENCLR = NRF_USBD->INTEN;

        NRF_USBD->ENABLE = 0;
        __ISB();
        __DSB(); // for sync

        hfclk_disable();

        dcd_event_bus_signal(0, DCD_EVENT_UNPLUGGED, is_in_isr());
      }
      break;

    default:
      break;
  }
}
