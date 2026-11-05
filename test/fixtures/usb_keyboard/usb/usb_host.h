#pragma once
#include <stddef.h>
#include <stdint.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NOT_FINISHED 1
#define USB_HOST_LIB_EVENT_FLAGS_ALL_FREE 2
typedef void* usb_host_client_handle_t;
typedef void* usb_device_handle_t;
enum usb_host_client_event_t { USB_HOST_CLIENT_EVENT_NEW_DEV, USB_HOST_CLIENT_EVENT_DEV_GONE };
struct usb_host_client_event_msg_t {
  usb_host_client_event_t event;
  struct { uint8_t address; } new_dev;
  struct { usb_device_handle_t dev_hdl; } dev_gone;
};
struct usb_host_config_t { bool skip_phy_setup; int intr_flags; };
struct usb_host_client_config_t {
  bool is_synchronous;
  int max_num_event_msg;
  struct { void (*client_event_callback)(const usb_host_client_event_msg_t*, void*); void* callback_arg; } async;
};
struct __attribute__((packed)) usb_config_desc_t { uint8_t length, type; uint16_t wTotalLength; };
struct __attribute__((packed)) usb_setup_packet_t { uint8_t bmRequestType, bRequest; uint16_t wValue, wIndex, wLength; };
enum usb_transfer_status_t { USB_TRANSFER_STATUS_COMPLETED, USB_TRANSFER_STATUS_CANCELED };
struct usb_transfer_t {
  uint8_t* data_buffer;
  int num_bytes, actual_num_bytes;
  usb_device_handle_t device_handle;
  uint8_t bEndpointAddress;
  usb_transfer_status_t status;
  void (*callback)(usb_transfer_t*);
  void* context;
};
esp_err_t usb_host_install(const usb_host_config_t*);
esp_err_t usb_host_uninstall();
esp_err_t usb_host_client_register(const usb_host_client_config_t*, usb_host_client_handle_t*);
esp_err_t usb_host_client_deregister(usb_host_client_handle_t);
esp_err_t usb_host_lib_handle_events(int, uint32_t*);
esp_err_t usb_host_client_handle_events(usb_host_client_handle_t, int);
esp_err_t usb_host_device_open(usb_host_client_handle_t, uint8_t, usb_device_handle_t*);
esp_err_t usb_host_device_close(usb_host_client_handle_t, usb_device_handle_t);
esp_err_t usb_host_device_free_all();
esp_err_t usb_host_get_active_config_descriptor(usb_device_handle_t, const usb_config_desc_t**);
esp_err_t usb_host_interface_claim(usb_host_client_handle_t, usb_device_handle_t, uint8_t, uint8_t);
esp_err_t usb_host_interface_release(usb_host_client_handle_t, usb_device_handle_t, uint8_t);
esp_err_t usb_host_endpoint_halt(usb_device_handle_t, uint8_t);
esp_err_t usb_host_endpoint_flush(usb_device_handle_t, uint8_t);
esp_err_t usb_host_transfer_alloc(size_t, int, usb_transfer_t**);
esp_err_t usb_host_transfer_free(usb_transfer_t*);
esp_err_t usb_host_transfer_submit_control(usb_host_client_handle_t, usb_transfer_t*);
esp_err_t usb_host_transfer_submit(usb_transfer_t*);
