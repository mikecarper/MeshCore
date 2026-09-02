// Include only TinyUSB's CDC protocol types here. cdc_device.h and the Arduino
// TinyUSB headers declare these callbacks weak, and that attribute would carry
// onto our definitions. This translation unit intentionally supplies the
// strong callbacks which override Adafruit's weak defaults for nRF52 USB
// Companion builds. CDC0 needs exact close events even on single-CDC targets;
// dual-CDC builds additionally forward CDC1 reconnect events.
#if defined(ARDUINO) && defined(NRF52_PLATFORM) \
    && defined(ENABLE_USB_INTERFACE)
#include <class/cdc/cdc.h>

extern "C" void meshTinyUsbCdcLineStateChanged(uint8_t instance, bool dtr,
                                                bool rts);
extern "C" void meshTinyUsbCdcLineCodingChanged(uint8_t instance);
extern "C" void meshTinyUsbDeviceSessionBoundary();
#if defined(MESH_DUAL_CDC_LOGGING)
extern "C" void meshTinyUsbStartOfFrame(uint32_t frame_count);
// cdc_device.h would attach TinyUSB's weak callback declarations to the strong
// overrides in this translation unit. Declare only the non-callback API used
// below, retaining the exact TinyUSB signature without importing that header.
extern "C" uint32_t tud_cdc_n_write_flush(uint8_t itf);

// The nRF core calls this weak hook from both the application task and the
// TinyUSB task. Never let those application-side yield()/delay() calls touch
// CDC1; its SOF bridge below is the sole endpoint owner. CDC0 retains the
// framework's ordinary opportunistic flush behavior.
extern "C" void TinyUSB_Device_FlushCDC(void) {
  (void)tud_cdc_n_write_flush(0);
}
#endif

// TinyUSB calls unmount for a hard unplug/configuration loss, but not for an
// ordinary bus-reset event. The subsequent mount is therefore also a session
// boundary; the forwarded handler is edge-sensitive so initial enumeration
// and an unmount/mount pair count at most once.
extern "C" void tud_mount_cb(void) {
  meshTinyUsbDeviceSessionBoundary();
}

extern "C" void tud_umount_cb(void) {
  meshTinyUsbDeviceSessionBoundary();
}

extern "C" void tud_cdc_line_state_cb(uint8_t instance, bool dtr, bool rts) {
  meshTinyUsbCdcLineStateChanged(instance, dtr, rts);
}

extern "C" void tud_cdc_line_coding_cb(uint8_t instance,
                                        cdc_line_coding_t const* line_coding) {
  (void)line_coding;
  meshTinyUsbCdcLineCodingChanged(instance);
}

#if defined(MESH_DUAL_CDC_LOGGING)
extern "C" void tud_sof_cb(uint32_t frame_count) {
  meshTinyUsbStartOfFrame(frame_count);
}
#endif
#endif
