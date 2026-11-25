"""Compile build-local fixes for the framework's nRF52 USB power startup.

Never modify PlatformIO's shared framework package. Fail closed if a framework
update changes the code this narrow backport expects. The same handler policy
is carried by OTAFIX's mikecarper/tinyusb fork.
"""

from pathlib import Path


OLD_READY = """    case USB_EVT_READY:
      // Skip if pull-up is enabled and HCLK is already running.
      // Application probably call this more than necessary.
      if (NRF_USBD->USBPULLUP && hfclk_running()) break;

      // Waiting for USBD peripheral enabled
      while (!(USBD_EVENTCAUSE_READY_Msk & NRF_USBD->EVENTCAUSE)) {}

"""
READY = """    case USB_EVT_READY:
    {
      // READY is a consumed event, not a persistent status bit. In particular,
      // a post-SoftDevice callback may find USB already attached but HFCLK no
      // longer requested by this context. Restore the clock before testing
      // completion; do not wait for a second peripheral READY event.
      uint32_t remaining = 100000;
      if ( !NRF_USBD->ENABLE ) break;
      hfclk_enable();
      while ( !hfclk_running() )
      {
        if ( !NRF_USBD->ENABLE || !--remaining ) return;
      }

      // Recheck attachment inside the wait: another READY handler can preempt
      // this one and consume EVENTCAUSE before it attaches. Both waits share
      // a finite poll budget, independent of ticks/interrupts being available.
      while ( !(USBD_EVENTCAUSE_READY_Msk & NRF_USBD->EVENTCAUSE) )
      {
        if ( NRF_USBD->USBPULLUP || !NRF_USBD->ENABLE || !--remaining ) return;
      }
      if ( NRF_USBD->USBPULLUP || !NRF_USBD->ENABLE ) break;

"""
OLD_ATTACH = """      // Wait for HFCLK
      while (!hfclk_running()) {}

      // Enable pull up
      NRF_USBD->USBPULLUP = 1;
      __ISB();
      __DSB(); // for sync
      break;

    case USB_EVT_REMOVED:"""
ATTACH = """      // Enable pull up
      if ( NRF_USBD->ENABLE ) NRF_USBD->USBPULLUP = 1;
      __ISB();
      __DSB(); // for sync
    }
      break;

    case USB_EVT_REMOVED:"""

OLD_PORT_INIT = """  if (usb_reg & POWER_USBREGSTATUS_VBUSDETECT_Msk) {
    tusb_hal_nrf_power_event(NRFX_POWER_USB_EVT_DETECTED);
  }
}
"""
PORT_INIT = """  if (usb_reg & POWER_USBREGSTATUS_VBUSDETECT_Msk) {
    tusb_hal_nrf_power_event(NRFX_POWER_USB_EVT_DETECTED);
  }
  // On an application handoff, VBUS and the regulator may already be ready.
  // The power driver only reports future edges, so replay both initial states.
  if (usb_reg & POWER_USBREGSTATUS_OUTPUTRDY_Msk) {
    tusb_hal_nrf_power_event(NRFX_POWER_USB_EVT_READY);
  }
}
"""

# A FIFO clear does not cancel the packet already submitted to the controller.
# Expose only a read-only owner-state query; firmware uses the supported USB
# detach/re-enumeration path when a session boundary finds an armed IN packet.
# Do not invent an endpoint abort that would desynchronize bulk data toggles.
CDC_WRITE_CLEAR = """bool tud_cdc_n_write_clear(uint8_t itf) {
  return tu_fifo_clear(&_cdcd_itf[itf].tx_ff);
}
"""
CDC_SESSION_QUERY = """
bool mesh_tud_cdc_n_tx_pending(uint8_t itf) {
  if (itf >= CFG_TUD_CDC) return false;
  uint8_t const ep_in = _cdcd_itf[itf].ep_in;
  return ep_in != 0 && usbd_edpt_busy(0, ep_in);
}
"""
CDC_LOCAL_INCLUDE = '#include "cdc_device.h"'
CDC_ROOT_INCLUDE = '#include "class/cdc/cdc_device.h"'
NRF_DISCONNECT = """void dcd_disconnect(uint8_t rhport) {
  (void) rhport;
  NRF_USBD->USBPULLUP = 0;

  // Disable Pull-up does not trigger Power USB Removed, in fact it have no
  // impact on the USB Power status at all -> need to submit unplugged event to the stack.
  dcd_event_bus_signal(0, DCD_EVENT_UNPLUGGED, false);
}
"""


def patched_source(source):
    source = source.replace("\r\n", "\n")
    # Session recovery waits for TinyUSB's owner to consume this explicit
    # local unplug event. A physical VBUS edge is neither required nor assumed.
    if source.count(NRF_DISCONNECT) != 1:
        raise RuntimeError(
            "nRF52 USB session fix: changed disconnect/unmount behavior; "
            "review the framework update before building"
        )
    if READY in source and ATTACH in source and OLD_READY not in source and OLD_ATTACH not in source:
        return source
    if source.count(OLD_READY) != 1 or source.count(OLD_ATTACH) != 1:
        raise RuntimeError(
            "nRF52 USB power fix: unrecognized TinyUSB driver; review the "
            "framework update before building (shared SDK was not modified)"
        )
    return source.replace(OLD_READY, READY).replace(OLD_ATTACH, ATTACH)


def patched_port_source(source):
    source = source.replace("\r\n", "\n")
    if PORT_INIT in source and OLD_PORT_INIT not in source:
        return source
    if source.count(OLD_PORT_INIT) != 1:
        raise RuntimeError(
            "nRF52 USB power fix: unrecognized USB startup; review the "
            "framework update before building (shared SDK was not modified)"
        )
    return source.replace(OLD_PORT_INIT, PORT_INIT)


def patched_cdc_source(source):
    source = source.replace("\r\n", "\n")
    if (source.count(CDC_SESSION_QUERY) == 1
            and source.count(CDC_ROOT_INCLUDE) == 1
            and CDC_LOCAL_INCLUDE not in source):
        return source
    if (source.count(CDC_WRITE_CLEAR) != 1 or source.count(CDC_LOCAL_INCLUDE) != 1
            or CDC_SESSION_QUERY in source or CDC_ROOT_INCLUDE in source):
        raise RuntimeError(
            "nRF52 USB session fix: unrecognized TinyUSB CDC driver; review "
            "the framework update before building (shared SDK was not modified)"
        )
    # Middleware relocates this source into BUILD_DIR. Its original sibling
    # include no longer resolves there; the TinyUSB root already on CPPPATH
    # provides a portable, SDK-local path (including cdc_device.h's siblings).
    return source.replace(CDC_LOCAL_INCLUDE, CDC_ROOT_INCLUDE).replace(
        CDC_WRITE_CLEAR, CDC_WRITE_CLEAR + CDC_SESSION_QUERY)


def replace_driver(build_env, node):
    # SCons passes a not-yet-created VariantDir node, not the SDK source path.
    source = Path(node.srcnode().get_abspath())
    patcher = {"Adafruit_TinyUSB_nrf.cpp": patched_port_source,
               "cdc_device.c": patched_cdc_source}.get(source.name, patched_source)
    patched = patcher(source.read_text(encoding="utf-8"))
    if source.name == "dcd_nrf5x.c":
        entry = "void tusb_hal_nrf_power_event(uint32_t event) {"
        if patched.count(entry) != 1:
            raise RuntimeError("Unrecognized Nordic USB power event handler")
        patched = patched.replace(entry,
            "extern bool meshNrfUsbPowerLocked(void) __attribute__((weak));\n" + entry +
            "\n  if (meshNrfUsbPowerLocked && meshNrfUsbPowerLocked()) return;")

    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-usb" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print("nRF52 USB: build-local bounded startup/session fix enabled")
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(
        replace_driver,
        "*Adafruit_TinyUSB_Arduino*src*portable*nordic*nrf5x*dcd_nrf5x.c",
    )
    build_env.AddBuildMiddleware(
        replace_driver,
        "*Adafruit_TinyUSB_Arduino*src*arduino*ports*nrf*Adafruit_TinyUSB_nrf.cpp",
    )
    build_env.AddBuildMiddleware(
        replace_driver,
        "*Adafruit_TinyUSB_Arduino*src*class*cdc*cdc_device.c",
    )


if "Import" in globals():
    Import("env")
    install(env)
