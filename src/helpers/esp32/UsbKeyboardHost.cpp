#include "UsbKeyboardHost.h"

#if defined(MESH_USB_KEYBOARD_HOST) && MESH_USB_KEYBOARD_HOST
#include <Arduino.h>
#include "UsbKeyboardDescriptor.h"
#include "UsbKeyboardProbeWindow.h"
#if !defined(CONFIG_IDF_TARGET_ESP32S3) || (defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT)
#error "USB keyboard host requires ESP32-S3 with native USB CDC disabled"
#endif

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <esp_pm.h>
#include <usb/usb_host.h>
#include <atomic>

namespace mesh { namespace usbkeyboard {
namespace {

QueueHandle_t events = nullptr;
TaskHandle_t host_task = nullptr;
std::atomic<bool> host_active(false);
std::atomic<uint32_t> probe_generation(0);

void post(EventType type, const uint8_t* report = nullptr) {
  Event event = {};
  event.type = type;
  if (report) memcpy(event.report, report, sizeof(event.report));
  if (xQueueSend(events, &event, 0) == pdTRUE) return;
  // Do not execute a partially delivered line after queue overflow. Resetting
  // the session also clears previous HID state and any in-progress command.
  xQueueReset(events);
  Event reset = {};
  reset.type = EventType::Disconnected;
  xQueueSend(events, &reset, 0);
  if (type == EventType::Connected || type == EventType::Report) {
    reset.type = EventType::Connected;
    xQueueSend(events, &reset, 0);
  } else if (type != EventType::Disconnected) {
    xQueueSend(events, &event, 0);
  }
}

struct Driver {
  usb_host_client_handle_t client = nullptr;
  usb_device_handle_t device = nullptr;
  usb_transfer_t* control = nullptr;
  usb_transfer_t* input = nullptr;
  uint8_t address = 0, interface_number = 0, endpoint = 0;
  uint16_t packet_size = 0;
  bool claimed = false, closing = false, halted = false;
  bool control_pending = false, input_pending = false, ready = false;
  bool protocol_ok = false;
  bool have_report = false;
  uint8_t last_report[8] = {};

  static void clientEvent(const usb_host_client_event_msg_t* event, void* arg) {
    Driver& self = *static_cast<Driver*>(arg);
    if (event->event == USB_HOST_CLIENT_EVENT_NEW_DEV) {
      if (!self.device && !self.address) self.address = event->new_dev.address;
    } else if (event->event == USB_HOST_CLIENT_EVENT_DEV_GONE
               && self.device == event->dev_gone.dev_hdl) {
      self.close();
    }
  }

  static void controlDone(usb_transfer_t* transfer) {
    Driver& self = *static_cast<Driver*>(transfer->context);
    self.control_pending = false;
    if (self.closing) return;
    if (transfer->status != USB_TRANSFER_STATUS_COMPLETED) {
      post(EventType::Unsupported); self.close(); return;
    }
    self.protocol_ok = true;
  }

  static void inputDone(usb_transfer_t* transfer) {
    Driver& self = *static_cast<Driver*>(transfer->context);
    self.input_pending = false;
    if (self.closing) return;
    if (transfer->status != USB_TRANSFER_STATUS_COMPLETED) { self.close(); return; }
    if (transfer->actual_num_bytes >= 8
        && (!self.have_report || memcmp(self.last_report, transfer->data_buffer, 8) != 0)) {
      memcpy(self.last_report, transfer->data_buffer, 8);
      self.have_report = true;
      post(EventType::Report, transfer->data_buffer);
    }
  }

  void close() {
    if (ready) post(EventType::Disconnected);
    ready = false;
    closing = true;
  }

  bool findKeyboard() {
    const usb_config_desc_t* config = nullptr;
    if (usb_host_get_active_config_descriptor(device, &config) != ESP_OK || !config) return false;
    const uint8_t* bytes = reinterpret_cast<const uint8_t*>(config);
    const size_t total = config->wTotalLength;
    BootEndpoint found;
    if (!findBootKeyboard(bytes, total, found)) return false;
    interface_number = found.interface_number;
    endpoint = found.endpoint;
    packet_size = found.packet_size;
    return true;
  }

  void open() {
    const uint8_t new_address = address;
    address = 0;
    if (usb_host_device_open(client, new_address, &device) != ESP_OK) { device = nullptr; return; }
    if (!findKeyboard()) { post(EventType::Unsupported); close(); return; }
    if (usb_host_interface_claim(client, device, interface_number, 0) != ESP_OK) {
      post(EventType::Error); close(); return;
    }
    claimed = true;
    if (usb_host_transfer_alloc(8, 0, &control) != ESP_OK
        || usb_host_transfer_alloc(packet_size, 0, &input) != ESP_OK) {
      post(EventType::Error); close(); return;
    }
    // SET_PROTOCOL(boot) ensures an eight-byte report even for keyboards that
    // default to the more complicated report protocol.
    usb_setup_packet_t* setup = reinterpret_cast<usb_setup_packet_t*>(control->data_buffer);
    setup->bmRequestType = 0x21;
    setup->bRequest = 0x0b;
    setup->wValue = 0;
    setup->wIndex = interface_number;
    setup->wLength = 0;
    control->device_handle = device;
    control->bEndpointAddress = 0;
    control->callback = controlDone;
    control->context = this;
    control->num_bytes = 8;
    if (usb_host_transfer_submit_control(client, control) != ESP_OK) {
      post(EventType::Error); close(); return;
    }
    control_pending = true;
  }

  void service() {
    if (closing) {
      if (input_pending && !halted) {
        usb_host_endpoint_halt(device, endpoint);
        usb_host_endpoint_flush(device, endpoint);
        halted = true;
      }
      // Cancellation completions are delivered through client_handle_events.
      if (control_pending || input_pending) return;
      if (claimed) usb_host_interface_release(client, device, interface_number);
      if (control) usb_host_transfer_free(control);
      if (input) usb_host_transfer_free(input);
      if (device) usb_host_device_close(client, device);
      device = nullptr; control = input = nullptr;
      address = interface_number = endpoint = 0; packet_size = 0;
      claimed = closing = halted = ready = protocol_ok = false;
      have_report = false;
      return;
    }
    if (!device && address) open();
    if (!device || !protocol_ok || input_pending) return;
    input->device_handle = device;
    input->bEndpointAddress = endpoint;
    input->callback = inputDone;
    input->context = this;
    input->num_bytes = packet_size;
    if (usb_host_transfer_submit(input) != ESP_OK) { post(EventType::Error); close(); return; }
    input_pending = true;
    if (!ready) { ready = true; post(EventType::Connected); }
  }
};

void run(void*) {
  for (;;) {
    // The task and queue remain allocated but consume no CPU while idle.
    // A notification is retained even if the previous scan is shutting down.
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    host_active.store(true);
    uint32_t requested = probe_generation.load();
#if CONFIG_PM_ENABLE
  // USB host cannot wake the S3 on attachment or keystrokes in light sleep.
  esp_pm_lock_handle_t usb_lock = nullptr;
  if (esp_pm_lock_create(ESP_PM_NO_LIGHT_SLEEP, 0, "usb-keyboard", &usb_lock) != ESP_OK
      || esp_pm_lock_acquire(usb_lock) != ESP_OK) {
    if (usb_lock) esp_pm_lock_delete(usb_lock);
    post(EventType::Error);
    host_active.store(false);
    if (probe_generation.load() != requested) host_active.store(true);
    post(EventType::Stopped);
    continue;
  }
#endif
  usb_host_config_t host_config = {};
  host_config.intr_flags = ESP_INTR_FLAG_LEVEL1;
  if (usb_host_install(&host_config) != ESP_OK) {
#if CONFIG_PM_ENABLE
    esp_pm_lock_release(usb_lock); esp_pm_lock_delete(usb_lock);
#endif
    post(EventType::Error);
    host_active.store(false);
    if (probe_generation.load() != requested) host_active.store(true);
    post(EventType::Stopped);
    continue;
  }
  Driver driver;
  usb_host_client_config_t client_config = {};
  client_config.max_num_event_msg = 8;
  client_config.async.client_event_callback = Driver::clientEvent;
  client_config.async.callback_arg = &driver;
  if (usb_host_client_register(&client_config, &driver.client) != ESP_OK) {
    usb_host_uninstall();
#if CONFIG_PM_ENABLE
    esp_pm_lock_release(usb_lock); esp_pm_lock_delete(usb_lock);
#endif
    post(EventType::Error);
    host_active.store(false);
    if (probe_generation.load() != requested) host_active.store(true);
    post(EventType::Stopped);
    continue;
  }
  ProbeWindow window;
  window.extend(millis());
  bool cleanup_error_reported = false;
  for (;;) {
    uint32_t flags = 0;
    usb_host_lib_handle_events(pdMS_TO_TICKS(5), &flags);
    usb_host_client_handle_events(driver.client, pdMS_TO_TICKS(5));
    if (ulTaskNotifyTake(pdTRUE, 0)) {
      requested = probe_generation.load();
      window.extend(millis());
    }
    driver.service();
    if (window.expired(millis(), driver.ready)) {
      if (driver.device || driver.closing) {
        if (!driver.closing) driver.close();
        driver.service();
      } else if (usb_host_client_deregister(driver.client) == ESP_OK) {
        break;
      } else {
        if (!cleanup_error_reported) post(EventType::Error);
        cleanup_error_reported = true;
        window.extend(millis());
      }
    }
    vTaskDelay(1);
  }
  // Follow the IDF shutdown order: deregister the client, free all devices,
  // wait for the daemon's all-free event if needed, then release the PHY.
  esp_err_t freed = usb_host_device_free_all();
  while (freed != ESP_OK && freed != ESP_ERR_NOT_FINISHED) {
    if (!cleanup_error_reported) post(EventType::Error);
    cleanup_error_reported = true;
    vTaskDelay(pdMS_TO_TICKS(100));
    freed = usb_host_device_free_all();
  }
  if (freed == ESP_ERR_NOT_FINISHED) {
    uint32_t flags = 0;
    while (!(flags & USB_HOST_LIB_EVENT_FLAGS_ALL_FREE))
      usb_host_lib_handle_events(pdMS_TO_TICKS(100), &flags);
  }
  while (usb_host_uninstall() != ESP_OK) {
    if (!cleanup_error_reported) post(EventType::Error);
    cleanup_error_reported = true;
    vTaskDelay(pdMS_TO_TICKS(100));
  }
#if CONFIG_PM_ENABLE
  esp_pm_lock_release(usb_lock);
  esp_pm_lock_delete(usb_lock);
#endif
  host_active.store(false);
  if (probe_generation.load() != requested) host_active.store(true);
  post(EventType::Stopped);
  }
}
}  // namespace

bool probe() {
  if (!host_task) {
    events = xQueueCreate(32, sizeof(Event));
    if (!events) return false;
    if (xTaskCreatePinnedToCore(run, "usb-keyboard", 4096, nullptr, 2, &host_task, 0) != pdPASS) {
      vQueueDelete(events); events = nullptr; host_task = nullptr; return false;
    }
  }
  probe_generation.fetch_add(1);
  host_active.store(true);
  xTaskNotifyGive(host_task);
  return true;
}

bool active() { return host_active.load(); }
bool next(Event& event) { return events && xQueueReceive(events, &event, 0) == pdTRUE; }

} }
#endif
