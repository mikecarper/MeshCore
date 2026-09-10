"""Compile a build-local fix for the framework's inherited nRF52 USB READY hang.

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


def patched_source(source):
    source = source.replace("\r\n", "\n")
    if READY in source and ATTACH in source and OLD_READY not in source and OLD_ATTACH not in source:
        return source
    if source.count(OLD_READY) != 1 or source.count(OLD_ATTACH) != 1:
        raise RuntimeError(
            "nRF52 USB power fix: unrecognized TinyUSB driver; review the "
            "framework update before building (shared SDK was not modified)"
        )
    return source.replace(OLD_READY, READY).replace(OLD_ATTACH, ATTACH)


def replace_driver(build_env, node):
    # SCons passes a not-yet-created VariantDir node, not the SDK source path.
    source = Path(node.srcnode().get_abspath())
    patched = patched_source(source.read_text(encoding="utf-8"))
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-usb" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print("nRF52 USB: build-local bounded READY/HFCLK fix enabled")
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(
        replace_driver,
        "*Adafruit_TinyUSB_Arduino*src*portable*nordic*nrf5x*dcd_nrf5x.c",
    )


if "Import" in globals():
    Import("env")
    install(env)
