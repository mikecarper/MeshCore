#define MESH_USB_KEYBOARD_HOST 1
#include "../src/helpers/esp32/UsbKeyboardHost.cpp"
#include <assert.h>
#include <stdlib.h>
#include <vector>

using namespace mesh::usbkeyboard;
static std::vector<Event> queued;
static int claims = 0, releases = 0, closes = 0, halts = 0, flushes = 0;
static int deregisters = 0, frees = 0, uninstalls = 0, blocking_waits = 0;
static int pm_created = 0, pm_acquired = 0, pm_released = 0, pm_deleted = 0;
static uint32_t fake_now = 0;
static bool simulate_keyboard = false, announced = false, removed = false, cancel_input = false;
static bool deferred_free = false;
static usb_host_client_config_t saved_client{};
static usb_transfer_t* pending_control = nullptr;
static usb_transfer_t* pending_input = nullptr;
struct StopTest {};
static uint8_t config[] = {
  9, 2, 25, 0, 1, 1, 0, 0x80, 50,
  9, 4, 2, 0, 1, 3, 1, 1, 0,
  7, 5, 0x82, 3, 8, 0, 10,
};

QueueHandle_t xQueueCreate(unsigned, size_t) { return reinterpret_cast<void*>(1); }
int xQueueSend(QueueHandle_t, const void* e, int) { queued.push_back(*static_cast<const Event*>(e)); return pdTRUE; }
int xQueueReceive(QueueHandle_t, void*, int) { return 0; }
void xQueueReset(QueueHandle_t) { queued.clear(); }
void vQueueDelete(QueueHandle_t) {}
int xTaskCreatePinnedToCore(void (*)(void*), const char*, int, void*, int, void*, int) { return pdPASS; }
unsigned ulTaskNotifyTake(int, unsigned wait) {
  if (wait == portMAX_DELAY) {
    if (blocking_waits++ == 0) return 1;
    throw StopTest{};  // Exit the idle worker after one scan for this test.
  }
  return 0;
}
void xTaskNotifyGive(TaskHandle_t) {}
void vTaskDelete(void*) {}
void vTaskDelay(int) {}
uint32_t millis() { return fake_now; }
int esp_pm_lock_create(int, int, const char*, esp_pm_lock_handle_t* h) { ++pm_created; *h = reinterpret_cast<void*>(3); return ESP_OK; }
int esp_pm_lock_acquire(esp_pm_lock_handle_t) { ++pm_acquired; return ESP_OK; }
int esp_pm_lock_release(esp_pm_lock_handle_t) { ++pm_released; return ESP_OK; }
int esp_pm_lock_delete(esp_pm_lock_handle_t) { ++pm_deleted; return ESP_OK; }
esp_err_t usb_host_install(const usb_host_config_t*) { return ESP_OK; }
esp_err_t usb_host_uninstall() { ++uninstalls; return ESP_OK; }
esp_err_t usb_host_client_register(const usb_host_client_config_t* c, usb_host_client_handle_t* h) { saved_client = *c; *h = reinterpret_cast<void*>(1); return ESP_OK; }
esp_err_t usb_host_client_deregister(usb_host_client_handle_t) { ++deregisters; return ESP_OK; }
esp_err_t usb_host_lib_handle_events(int, uint32_t* flags) {
  fake_now += 1000;
  if (deferred_free && frees) *flags = USB_HOST_LIB_EVENT_FLAGS_ALL_FREE;
  return ESP_OK;
}
esp_err_t usb_host_client_handle_events(usb_host_client_handle_t, int) {
  if (!simulate_keyboard) return ESP_OK;
  if (!announced) {
    usb_host_client_event_msg_t e{};
    e.event = USB_HOST_CLIENT_EVENT_NEW_DEV; e.new_dev.address = 7;
    saved_client.async.client_event_callback(&e, saved_client.async.callback_arg);
    announced = true;
  } else if (pending_control) {
    auto* transfer = pending_control; pending_control = nullptr;
    transfer->status = USB_TRANSFER_STATUS_COMPLETED;
    transfer->callback(transfer);
  } else if (fake_now >= 70000 && !removed) {
    usb_host_client_event_msg_t e{};
    e.event = USB_HOST_CLIENT_EVENT_DEV_GONE; e.dev_gone.dev_hdl = reinterpret_cast<void*>(2);
    saved_client.async.client_event_callback(&e, saved_client.async.callback_arg);
    removed = true;
  } else if (cancel_input && pending_input) {
    auto* transfer = pending_input; pending_input = nullptr; cancel_input = false;
    transfer->status = USB_TRANSFER_STATUS_CANCELED;
    transfer->callback(transfer);
  }
  return ESP_OK;
}
esp_err_t usb_host_device_open(usb_host_client_handle_t, uint8_t, usb_device_handle_t* h) { *h = reinterpret_cast<void*>(2); return ESP_OK; }
esp_err_t usb_host_device_close(usb_host_client_handle_t, usb_device_handle_t) { ++closes; return ESP_OK; }
esp_err_t usb_host_device_free_all() { ++frees; return deferred_free ? ESP_ERR_NOT_FINISHED : ESP_OK; }
esp_err_t usb_host_get_active_config_descriptor(usb_device_handle_t, const usb_config_desc_t** d) { *d = reinterpret_cast<usb_config_desc_t*>(config); return ESP_OK; }
esp_err_t usb_host_interface_claim(usb_host_client_handle_t, usb_device_handle_t, uint8_t, uint8_t) { ++claims; return ESP_OK; }
esp_err_t usb_host_interface_release(usb_host_client_handle_t, usb_device_handle_t, uint8_t) { ++releases; return ESP_OK; }
esp_err_t usb_host_endpoint_halt(usb_device_handle_t, uint8_t) { ++halts; return ESP_OK; }
esp_err_t usb_host_endpoint_flush(usb_device_handle_t, uint8_t) { ++flushes; if (simulate_keyboard) cancel_input = true; return ESP_OK; }
esp_err_t usb_host_transfer_alloc(size_t size, int, usb_transfer_t** t) { *t = new usb_transfer_t{}; (*t)->data_buffer = new uint8_t[size]{}; return ESP_OK; }
esp_err_t usb_host_transfer_free(usb_transfer_t* t) { delete[] t->data_buffer; delete t; return ESP_OK; }
esp_err_t usb_host_transfer_submit_control(usb_host_client_handle_t, usb_transfer_t* t) { if (simulate_keyboard) pending_control = t; return ESP_OK; }
esp_err_t usb_host_transfer_submit(usb_transfer_t* t) { if (simulate_keyboard) pending_input = t; return ESP_OK; }

int main() {
  ProbeWindow window;
  window.extend(0xfffffff0U);
  assert(!window.expired(0xfffffff1U, false));
  assert(!window.expired(15, false));  // millis() rollover
  assert(window.expired(30000, false));
  assert(!window.expired(60000, true));
  assert(!window.expired(89999, false));
  assert(window.expired(90000, false));
  events = reinterpret_cast<void*>(1);
  Driver driver;
  driver.client = reinterpret_cast<void*>(1);
  usb_host_client_event_msg_t e{};
  e.event = USB_HOST_CLIENT_EVENT_NEW_DEV;
  e.new_dev.address = 7;
  Driver::clientEvent(&e, &driver);
  driver.service();
  assert(claims == 1 && driver.control_pending && !driver.ready);
  const auto* setup = reinterpret_cast<usb_setup_packet_t*>(driver.control->data_buffer);
  assert(setup->bmRequestType == 0x21 && setup->bRequest == 0x0b && setup->wIndex == 2);
  driver.control->status = USB_TRANSFER_STATUS_COMPLETED;
  Driver::controlDone(driver.control);
  driver.service();
  assert(driver.ready && driver.input_pending && queued.back().type == EventType::Connected);
  driver.input->actual_num_bytes = 8;
  driver.input->status = USB_TRANSFER_STATUS_COMPLETED;
  driver.input->data_buffer[2] = 4;
  Driver::inputDone(driver.input);
  assert(queued.back().type == EventType::Report && queued.back().report[2] == 4);
  const size_t count = queued.size();
  driver.service();
  Driver::inputDone(driver.input);  // Repeated identical reports are suppressed.
  assert(queued.size() == count);
  driver.service();
  e.event = USB_HOST_CLIENT_EVENT_DEV_GONE;
  e.dev_gone.dev_hdl = driver.device;
  Driver::clientEvent(&e, &driver);
  assert(queued.back().type == EventType::Disconnected);
  driver.service();
  assert(halts == 1 && flushes == 1 && driver.input_pending);
  driver.input->status = USB_TRANSFER_STATUS_CANCELED;
  Driver::inputDone(driver.input);
  driver.service();
  assert(!driver.device && releases == 1 && closes == 1);
  e.event = USB_HOST_CLIENT_EVENT_NEW_DEV;
  e.new_dev.address = 8;
  Driver::clientEvent(&e, &driver);
  driver.service();
  assert(driver.control_pending && claims == 2);  // Reattach after cleanup.
  driver.control->status = USB_TRANSFER_STATUS_CANCELED;
  Driver::controlDone(driver.control);
  driver.service();
  assert(queued.back().type == EventType::Unsupported && closes == 2);

  queued.clear();
  fake_now = 0;
  probe_generation.store(1);
  try { run(nullptr); } catch (const StopTest&) {}
  assert(fake_now >= 30000 && fake_now <= 32000);
  assert(deregisters == 1 && frees == 1 && uninstalls == 1);
  assert(pm_created == 1 && pm_acquired == 1 && pm_released == 1 && pm_deleted == 1);
  assert(!active() && queued.back().type == EventType::Stopped);

  queued.clear(); fake_now = 0; blocking_waits = 0;
  deregisters = frees = uninstalls = 0;
  simulate_keyboard = true; deferred_free = true;
  try { run(nullptr); } catch (const StopTest&) {}
  assert(announced && removed);
  assert(fake_now >= 99000 && fake_now <= 102000);  // Remains awake until 30 s after unplugging.
  assert(deregisters == 1 && frees == 1 && uninstalls == 1 && !active());
}
