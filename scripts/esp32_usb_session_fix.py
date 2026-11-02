"""Build-local owner-task hooks for Arduino-ESP32 native CDC session cleanup.

Do not patch the shared SDK. Fail closed when the native-CDC implementation
changes: the endpoint query must match the descriptor actually compiled.
"""
from pathlib import Path


GUARD = "#if ARDUINO_USB_CDC_ON_BOOT && !ARDUINO_USB_MODE\n"
CDC_INCLUDE = '#include "esp32-hal-tinyusb.h"\n'
CDC_HOOKS = GUARD + '''#include "device/usbd_pvt.h"
extern "C" void meshEsp32TinyUsbCdcLineState(bool dtr) __attribute__((weak));
extern "C" bool meshEsp32TinyUsbAcceptRx() __attribute__((weak));
extern "C" void meshEsp32TinyUsbTxComplete() __attribute__((weak));
extern "C" bool meshEsp32TinyUsbTxPending() {
    return usbd_edpt_busy(0, 0x84);
}
#endif
'''
LINE_STATE = "void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts)\n{\n"
LINE_HOOK = GUARD + '''    // Capture the close synchronously, before another host can enqueue RX.
    if (itf == 0 && meshEsp32TinyUsbCdcLineState) {
        meshEsp32TinyUsbCdcLineState(dtr);
    }
#endif
'''
RX = "void USBCDC::_onRX(){\n"
RX_HOOK = GUARD + '''    if (itf == 0 && meshEsp32TinyUsbAcceptRx && !meshEsp32TinyUsbAcceptRx()) {
        tud_cdc_n_read_flush(itf);
        return;
    }
#endif
'''
TX_COMPLETE = "void tud_cdc_tx_complete_cb(uint8_t itf){\n"
TX_HOOK = GUARD + '''    if (itf == 0 && meshEsp32TinyUsbTxComplete) {
        meshEsp32TinyUsbTxComplete();
    }
#endif
'''
USB_INCLUDE = '#include "esp32-hal-tinyusb.h"\n'
USB_HOOKS = GUARD + '''extern "C" void meshEsp32TinyUsbDeviceSessionBoundary(bool mounted) __attribute__((weak));
#endif
'''
MOUNT = "void tud_mount_cb(void){\n"
UNMOUNT = "void tud_umount_cb(void){\n"


def patched_cdc_source(source):
    source = source.replace("\r\n", "\n")
    descriptor = "TUD_CDC_DESCRIPTOR(*itf, str_index, 0x85, 64, 0x03, 0x84, 64)"
    if (source.count(CDC_HOOKS) == 1 and source.count(LINE_HOOK) == 1
            and source.count(RX_HOOK) == 1 and source.count(TX_HOOK) == 1
            and source.count(descriptor) == 1):
        return source
    if (source.count(CDC_INCLUDE) != 1 or source.count(LINE_STATE) != 1
            or source.count(RX) != 1 or source.count(TX_COMPLETE) != 1
            or source.count(descriptor) != 1
            or "meshEsp32TinyUsb" in source):
        raise RuntimeError("ESP32 USB session fix: changed native CDC/endpoint layout; review SDK update")
    return (source.replace(CDC_INCLUDE, CDC_INCLUDE + CDC_HOOKS)
            .replace(LINE_STATE, LINE_STATE + LINE_HOOK)
            .replace(RX, RX + RX_HOOK)
            .replace(TX_COMPLETE, TX_COMPLETE + TX_HOOK))


def patched_usb_source(source):
    source = source.replace("\r\n", "\n")
    mount_hook = GUARD + "    if (meshEsp32TinyUsbDeviceSessionBoundary) meshEsp32TinyUsbDeviceSessionBoundary(true);\n#endif\n"
    unmount_hook = GUARD + "    if (meshEsp32TinyUsbDeviceSessionBoundary) meshEsp32TinyUsbDeviceSessionBoundary(false);\n#endif\n"
    if (source.count(USB_HOOKS) == 1 and source.count(mount_hook) == 1
            and source.count(unmount_hook) == 1):
        return source
    if (source.count(USB_INCLUDE) != 1 or source.count(MOUNT) != 1
            or source.count(UNMOUNT) != 1 or "meshEsp32TinyUsb" in source):
        raise RuntimeError("ESP32 USB session fix: changed USB lifecycle callbacks; review SDK update")
    return (source.replace(USB_INCLUDE, USB_INCLUDE + USB_HOOKS)
            .replace(MOUNT, MOUNT + mount_hook)
            .replace(UNMOUNT, UNMOUNT + unmount_hook))


def native_primary_enabled(build_env):
    defines = {}
    for definition in build_env.get("CPPDEFINES", []):
        if isinstance(definition, (tuple, list)):
            defines[definition[0]] = str(definition[1])
        else:
            defines[str(definition)] = "1"
    return (defines.get("ARDUINO_USB_CDC_ON_BOOT") == "1"
            and defines.get("ARDUINO_USB_MODE") == "0")


def replace_source(build_env, node):
    if not native_primary_enabled(build_env):
        return node
    source = Path(node.srcnode().get_abspath())
    patcher = patched_cdc_source if source.name == "USBCDC.cpp" else patched_usb_source
    patched = patcher(source.read_text(encoding="utf-8"))
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-esp32-usb" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    return build_env.File(str(destination))



# HWCDC TX/PHY fixes are limited to the installed Arduino2.0.17 C3/S3 driver.
# The separately pinned 3.x transforms below only classify startup resets.
# Other framework versions retain their own driver unchanged.
PINNED_HWCDC_SHA256 = "d0a8ca606c2729c8522a041113285dbf27033c22a5a6af8649a7305ffe84c449"
PATCHED_HWCDC_SHA256 = "0969a94ae32edcfeeedb3daab19e5697e6c7647dc31578cdb0c3a0cb29e7c6d6"
HWCDC_S3_PHY_GUARD = "#if CONFIG_IDF_TARGET_ESP32S3 && ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT\n"
HWCDC_S3_PHY_HELPERS = HWCDC_S3_PHY_GUARD + r'''#include "soc/usb_wrap_struct.h"
#include "soc/rtc_cntl_struct.h"

// Exact ESP-IDF 4.4.7 usb_phy_ll.h bodies, renamed locally. The complete HAL
// header has unrelated volatile struct copies that cannot compile as C++.
static inline void mesh_hwcdc_int_jtag_enable(usb_serial_jtag_dev_t *hw)
{
    // USB_Serial_JTAG use internal PHY
    hw->conf0.phy_sel = 0;
    // Disable software control USB D+ D- pullup pulldown (Device FS: dp_pullup = 1)
    hw->conf0.pad_pull_override = 0;
    // Enable USB D+ pullup
    hw->conf0.dp_pullup = 1;
    // Enable USB pad function
    hw->conf0.usb_pad_enable = 1;
    // phy_sel is controlled by the following register value
    RTCCNTL.usb_conf.sw_hw_usb_phy_sel = 1;
    // phy_sel=sw_usb_phy_sel=0, USB_Serial_JTAG is connected with internal PHY
    RTCCNTL.usb_conf.sw_usb_phy_sel = 0;
}

static inline void mesh_hwcdc_usb_wrap_pad_enable(usb_wrap_dev_t *hw, bool pad_en)
{
    hw->otg_conf.pad_enable = pad_en;
}
#endif
'''
HWCDC_S3_PHY_CONFIG = r"""    // TinyUSB's RTC PHY selection survives a software restart (IDF #9826).
    // An OTA upgrade must release its old pullup before claiming the shared
    // PHY, so the host discards the previous USB device's descriptors.
    if (RTCCNTL.usb_conf.sw_hw_usb_phy_sel && RTCCNTL.usb_conf.sw_usb_phy_sel) {
        mesh_hwcdc_usb_wrap_pad_enable(&USB_WRAP, false);
        delay(20);
    }
    mesh_hwcdc_int_jtag_enable(&USB_SERIAL_JTAG);
"""
HWCDC_TX_SUPPORT = r"""
// MeshCore pinned HWCDC TX suffix/interrupt backport (upstream #12606).
extern "C" bool meshEsp32HwcdcShouldReportBusReset() __attribute__((weak));
static uint8_t mesh_hwcdc_tx_stash[64] = {0};
static size_t mesh_hwcdc_tx_stash_len = 0;
static bool mesh_hwcdc_tx_allowed = true;
static bool mesh_hwcdc_fifo_pending = false;
static uint32_t mesh_hwcdc_active_writers = 0;
static portMUX_TYPE mesh_hwcdc_tx_mux = portMUX_INITIALIZER_UNLOCKED;

static inline void mesh_hwcdc_enable_tx_intr() {
    portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    if (mesh_hwcdc_tx_allowed) {
        usb_serial_jtag_ll_ena_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
    }
    portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
}

extern "C" void meshEsp32HwcdcSetTxAllowed(bool allowed) {
    portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    mesh_hwcdc_tx_allowed = allowed;
    if (!allowed) usb_serial_jtag_ll_disable_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
    portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
}

extern "C" void meshEsp32HwcdcKickTx() {
    portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    if (mesh_hwcdc_tx_allowed) {
        usb_serial_jtag_ll_txfifo_flush();
        usb_serial_jtag_ll_ena_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
    }
    portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
}

extern "C" bool meshEsp32HwcdcTxPending() {
    portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    UBaseType_t waiting = 0;
    if (tx_ring_buf) vRingbufferGetInfo(tx_ring_buf, NULL, NULL, NULL, NULL, &waiting);
    const bool pending = mesh_hwcdc_tx_stash_len != 0 || waiting != 0
        || mesh_hwcdc_fifo_pending || mesh_hwcdc_active_writers != 0;
    portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    return pending;
}

static inline void mesh_hwcdc_clear_tx_stash() {
    portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    mesh_hwcdc_tx_stash_len = 0;
    portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
}

extern "C" bool meshEsp32HwcdcDiscardTxStash() {
    // Caller has detached pads and stopped producers. Do not mistake ordinary
    // flush/ring-empty for completion of a previously staged FIFO payload.
    portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    const bool permitted = !mesh_hwcdc_tx_allowed && mesh_hwcdc_active_writers == 0;
    if (permitted) {
        mesh_hwcdc_tx_stash_len = 0;
        mesh_hwcdc_fifo_pending = false;
    }
    portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    return permitted;
}

class mesh_hwcdc_writer_scope {
    bool _entered;
public:
    mesh_hwcdc_writer_scope() {
        portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
        _entered = mesh_hwcdc_tx_allowed;
        if (_entered) ++mesh_hwcdc_active_writers;
        portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    }
    ~mesh_hwcdc_writer_scope() {
        if (!_entered) return;
        portENTER_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
        --mesh_hwcdc_active_writers;
        portEXIT_CRITICAL_SAFE(&mesh_hwcdc_tx_mux);
    }
    operator bool() const { return _entered; }
    mesh_hwcdc_writer_scope(const mesh_hwcdc_writer_scope&) = delete;
    mesh_hwcdc_writer_scope& operator=(const mesh_hwcdc_writer_scope&) = delete;
};
"""
HWCDC_IN_EMPTY = r"""    if (usbjtag_intr_status & USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY) {
        // A real host pickup is stronger proof than the SOF tick heuristic.
        connected = true;
        usb_serial_jtag_ll_clr_intsts_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
        size_t sent_size = 0;
        bool staged = false;
        portENTER_CRITICAL_ISR(&mesh_hwcdc_tx_mux);
        mesh_hwcdc_fifo_pending = false; // this IN_EMPTY acknowledges the previous payload
        if (!mesh_hwcdc_tx_allowed) {
            usb_serial_jtag_ll_disable_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
        } else if (tx_ring_buf != NULL && usb_serial_jtag_ll_txfifo_writable() == 1) {
            size_t queued_size = mesh_hwcdc_tx_stash_len;
            bool from_stash = queued_size != 0;
            uint8_t* queued_buff = from_stash ? mesh_hwcdc_tx_stash
                : (uint8_t*)xRingbufferReceiveUpToFromISR(tx_ring_buf, &queued_size, 64);
            if (queued_buff != NULL && queued_size != 0) {
                sent_size = usb_serial_jtag_ll_write_txfifo(queued_buff, queued_size);
                usb_serial_jtag_ll_txfifo_flush();
                staged = true;
                mesh_hwcdc_fifo_pending = sent_size != 0;
                mesh_hwcdc_tx_stash_len = queued_size - sent_size;
                if (mesh_hwcdc_tx_stash_len) {
                    memmove(mesh_hwcdc_tx_stash, queued_buff + sent_size, mesh_hwcdc_tx_stash_len);
                }
                if (!from_stash) vRingbufferReturnItemFromISR(tx_ring_buf, queued_buff, &xTaskWoken);
                usb_serial_jtag_ll_ena_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
            } else {
                // Preserve the full-packet terminating ZLP. Disable and inspect
                // again under the producer/ISR lock so a wakeup cannot be lost.
                usb_serial_jtag_ll_txfifo_flush();
                usb_serial_jtag_ll_disable_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
                UBaseType_t waiting = 0;
                vRingbufferGetInfo(tx_ring_buf, NULL, NULL, NULL, NULL, &waiting);
                if (waiting || mesh_hwcdc_tx_stash_len) {
                    usb_serial_jtag_ll_ena_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
                }
            }
        }
        portEXIT_CRITICAL_ISR(&mesh_hwcdc_tx_mux);
        if (staged) {
            event.tx.len = sent_size;
            arduino_hw_cdc_event_post(ARDUINO_HW_CDC_EVENTS, ARDUINO_HW_CDC_TX_EVENT,
                &event, sizeof(arduino_hw_cdc_event_data_t), &xTaskWoken);
        }
    }

"""
HWCDC_CONNECTED = r"""bool HWCDC::isCDC_Connected()
{
    if (!isPlugged()) {
        connected = false;
        return false;
    }
    if (connected) return true;
    // Re-arm on every attempt. Keep a suffix across transient SOF false.
    meshEsp32HwcdcKickTx();
    return false;
}

"""


def patched_hwcdc_source(source):
    import hashlib
    source = source.replace("\r\n", "\n")
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == PATCHED_HWCDC_SHA256:
        return source
    if digest != PINNED_HWCDC_SHA256:
        raise RuntimeError("ESP32 HWCDC TX fix: changed pinned2.0.17 source; review SDK update")
    # Perform exact-shape transforms only after the complete input hash passes.
    source = source.replace('#include "esp_freertos_hooks.h"\n',
                            '#include "esp_freertos_hooks.h"\n#include <string.h>\n', 1)
    source = source.replace('#include "hal/usb_serial_jtag_ll.h"\n',
                            '#include "hal/usb_serial_jtag_ll.h"\n' + HWCDC_S3_PHY_HELPERS, 1)
    begin = source.index('void HWCDC::begin(unsigned long baud)')
    phy_begin = source.index('    // Configure PHY\n', begin)
    phy_end = source.index('    usb_serial_jtag_ll_disable_intr_mask(USB_SERIAL_JTAG_LL_INTR_MASK);', phy_begin)
    source = (source[:phy_begin] + HWCDC_S3_PHY_GUARD + HWCDC_S3_PHY_CONFIG
              + '#else\n' + source[phy_begin:phy_end] + '#endif\n' + source[phy_end:])
    source = source.replace('usb_serial_jtag_ll_ena_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);',
                            'mesh_hwcdc_enable_tx_intr();')
    begin = source.index('    if (usbjtag_intr_status & USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY) {')
    end = source.index('    if (usbjtag_intr_status & USB_SERIAL_JTAG_INTR_SERIAL_OUT_RECV_PKT) {', begin)
    source = source[:begin] + HWCDC_IN_EMPTY + source[end:]
    begin = source.index('bool HWCDC::isCDC_Connected()')
    end = source.index('static void flushTXBuffer(', begin)
    source = source[:begin] + HWCDC_CONNECTED + source[end:]
    source = source.replace('static xSemaphoreHandle tx_lock = NULL;\n',
                            'static xSemaphoreHandle tx_lock = NULL;\n' + HWCDC_TX_SUPPORT, 1)
    source = source.replace('        connected = false;\n    }\n\n//    if (usbjtag_intr_status',
                            '        portENTER_CRITICAL_ISR(&mesh_hwcdc_tx_mux);\n'
                            '        mesh_hwcdc_tx_stash_len = 0;\n        mesh_hwcdc_fifo_pending = false;\n'
                            '        portEXIT_CRITICAL_ISR(&mesh_hwcdc_tx_mux);\n        connected = false;\n    }\n\n//    if (usbjtag_intr_status', 1)
    source = source.replace(
        '        arduino_hw_cdc_event_post(ARDUINO_HW_CDC_EVENTS, ARDUINO_HW_CDC_BUS_RESET_EVENT, &event, sizeof(arduino_hw_cdc_event_data_t), &xTaskWoken);',
        '        // Classify at capture: setup-time enumeration must not become an\n'
        '        // active-session reset if the framework event task runs later.\n'
        '        if (!meshEsp32HwcdcShouldReportBusReset || meshEsp32HwcdcShouldReportBusReset()) {\n'
        '            arduino_hw_cdc_event_post(ARDUINO_HW_CDC_EVENTS, ARDUINO_HW_CDC_BUS_RESET_EVENT, &event, sizeof(arduino_hw_cdc_event_data_t), &xTaskWoken);\n'
        '        }', 1)
    source = source.replace('    if(buffer == NULL) {\n',
                            '    if(buffer == NULL) {\n        mesh_hwcdc_clear_tx_stash();\n', 1)
    source = source.replace('size_t HWCDC::setTxBufferSize(size_t tx_queue_len){\n',
                            'size_t HWCDC::setTxBufferSize(size_t tx_queue_len){\n    mesh_hwcdc_clear_tx_stash();\n', 1)
    source = source.replace('    if(tx_ring_buf == NULL) {\n        return;\n    }\n    if(!HWCDC::isConnected())',
                            '    if(tx_ring_buf == NULL) {\n        return;\n    }\n'
                            '    mesh_hwcdc_writer_scope writer;\n    if (!writer) return;\n    if(!HWCDC::isConnected())', 1)
    source = source.replace('size_t HWCDC::write(const uint8_t *buffer, size_t size)\n{\n',
                            'size_t HWCDC::write(const uint8_t *buffer, size_t size)\n{\n'
                            '    mesh_hwcdc_writer_scope writer;\n    if (!writer) return 0;\n', 1)
    source = source.replace('        flushTXBuffer((const uint8_t*)&c, 1);\n        return;\n',
                            '        flushTXBuffer((const uint8_t*)&c, 1);\n        meshEsp32HwcdcKickTx();\n        return;\n', 1)
    # The disconnected write path previously left new bytes parked without
    # an interrupt. Kick after its existing FIFO-policy admission.
    source = source.replace('        flushTXBuffer(buffer, size);\n    } else {\n',
                            '        flushTXBuffer(buffer, size);\n        meshEsp32HwcdcKickTx();\n    } else {\n', 1)
    return source


def hwcdc_primary_enabled(build_env):
    defines = {}
    for definition in build_env.get("CPPDEFINES", []):
        if isinstance(definition, (tuple, list)):
            defines[definition[0]] = str(definition[1])
        else:
            defines[str(definition)] = "1"
    return (defines.get("ARDUINO_USB_CDC_ON_BOOT") == "1"
            and defines.get("ARDUINO_USB_MODE") == "1")


# Keep each reviewed 3.x driver's own TX/FIFO/PHY implementation intact. Only
# classify the startup BUS_RESET event at capture, under an exact source pin.
PINNED_HWCDC_313_SHA256 = "a09e99f1a37931269625019f51f1541a1185cce83f686d7aca4204ecdfea91dd"
PATCHED_HWCDC_313_SHA256 = "a2efe89e363908baf955e60751985edf1411fa930f2971e2d98b69fda3a1d19c"
PINNED_HWCDC_3311_SHA256 = "c5ed5fdd05aa0df9b74d390812643599223f96b459256718d0ad328aeaba6a8a"
PATCHED_HWCDC_3311_SHA256 = "7b693bf5d72df97336360969ff429c67f3783d472123aeb79b3aabc8154b51ba"
HWCDC_STARTUP_PINS = {
    (3, 1, 3): (PINNED_HWCDC_313_SHA256, PATCHED_HWCDC_313_SHA256),
    (3, 3, 11): (PINNED_HWCDC_3311_SHA256, PATCHED_HWCDC_3311_SHA256),
    # The 3.3.12 HWCDC driver is byte-identical to the reviewed 3.3.11 driver.
    (3, 3, 12): (PINNED_HWCDC_3311_SHA256, PATCHED_HWCDC_3311_SHA256),
}
HWCDC_STARTUP_INCLUDE = '#include "hal/usb_serial_jtag_ll.h"\n'
HWCDC_STARTUP_HOOK = 'extern "C" bool meshEsp32HwcdcShouldReportBusReset() __attribute__((weak));\n'
HWCDC_RESET_POST = (
    '    arduino_hw_cdc_event_post(ARDUINO_HW_CDC_EVENTS, ARDUINO_HW_CDC_BUS_RESET_EVENT, '
    '&event, sizeof(arduino_hw_cdc_event_data_t), &xTaskWoken);')
HWCDC_RESET_CAPTURE = (
    '    // Snapshot startup before the framework event task can delay delivery.\n'
    '    if (!meshEsp32HwcdcShouldReportBusReset || meshEsp32HwcdcShouldReportBusReset()) {\n'
    + '  ' + HWCDC_RESET_POST + '\n'
    '    }')


def patched_hwcdc_startup_source(source, *, version=(3, 3, 11)):
    import hashlib
    original_digest, patched_digest = HWCDC_STARTUP_PINS[version]
    source = source.replace("\r\n", "\n")
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == patched_digest:
        return source
    if digest != original_digest:
        label = ".".join(str(part) for part in version)
        raise RuntimeError("ESP32 HWCDC startup fix: changed pinned" + label + " source; review SDK update")
    return source.replace(HWCDC_STARTUP_INCLUDE,
                          HWCDC_STARTUP_INCLUDE + HWCDC_STARTUP_HOOK, 1).replace(
                              HWCDC_RESET_POST, HWCDC_RESET_CAPTURE, 1)


def hwcdc_framework_version(source):
    import re
    version = source.parent / "esp_arduino_version.h"
    values = dict(re.findall(r"^#define ESP_ARDUINO_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)\s*$",
                             version.read_text(encoding="utf-8"), re.MULTILINE))
    if set(values) != {"MAJOR", "MINOR", "PATCH"}:
        raise RuntimeError("ESP32 HWCDC TX fix: malformed framework version header")
    return tuple(int(values[key]) for key in ("MAJOR", "MINOR", "PATCH"))


def pinned_hwcdc_framework(source):
    return hwcdc_framework_version(source) == (2, 0, 17)


def replace_hwcdc_source(build_env, node):
    if not hwcdc_primary_enabled(build_env):
        return node
    source = Path(node.srcnode().get_abspath())
    version = hwcdc_framework_version(source)
    if version == (2, 0, 17):
        patched = patched_hwcdc_source(source.read_text(encoding="utf-8"))
    elif version in HWCDC_STARTUP_PINS:
        patched = patched_hwcdc_startup_source(source.read_text(encoding="utf-8"), version=version)
    else:
        return node
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-esp32-usb" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    build_env.AppendUnique(CPPPATH=[str(source.parent)])
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(replace_source, "*cores*esp32*USBCDC.cpp")
    build_env.AddBuildMiddleware(replace_source, "*cores*esp32*USB.cpp")
    build_env.AddBuildMiddleware(replace_hwcdc_source, "*cores*esp32*HWCDC.cpp")


if "Import" in globals():
    Import("env")
    install(env)
