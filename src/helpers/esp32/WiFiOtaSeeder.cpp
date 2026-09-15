#include "WiFiOtaSeeder.h"

#if defined(ESP32_PLATFORM) && defined(ENABLE_OTA) && \
    (defined(WIFI_OTA_SEEDER) || defined(WIFI_SSID))

#include <Arduino.h>
#include <WiFi.h>

#include <helpers/WiFiOtaSeederPolicy.h>
#include <helpers/WiFiOtaSeederStatus.h>
#include <helpers/UsbLogging.h>
#include <helpers/ota/FolderMotaStore.h>
#include <helpers/ota/MotaSourceSerial.h>
#include <helpers/ota/OtaContext.h>

namespace mesh {
namespace ota {

namespace {

WiFiServer seeder_server(OTA_SEEDER_TCP_PORT);
WiFiClient seeder_client;
// WiFiClient::flush() clears received bytes rather than flushing TX. Disabling
// the serial-only flush prevents a fast host reply from being discarded and
// turning every block read into a three-second timeout/retry.
SerialMotaSource seeder_source(seeder_client,
                               MotaStreamWritePolicy::NoFlush, 3000);
FolderMotaStore folder_store(seeder_client,
                             MotaStreamWritePolicy::NoFlush, 3000);
bool listener_active = false;
bool tcp_folder_attached = false;

bool networkReady() {
  if (WiFi.status() == WL_CONNECTED) return true;
  const wifi_mode_t mode = WiFi.getMode();
  if (mode != WIFI_AP && mode != WIFI_AP_STA) return false;
  return static_cast<uint32_t>(WiFi.softAPIP()) != 0;
}

void detachTcpFolder(bool disconnected) {
  if (!tcp_folder_attached) return;
  OtaContext* context = ota_context_if_active();
  if (context && context->folderLink() == OtaContext::FOLDER_LINK_TCP) {
    if (disconnected) context->disconnect_folder();
    else {
      context->detach_folder();
      context->clear_folder_dest();
    }
    context->manager.announce();
  }
  tcp_folder_attached = false;
}

void stopClient(bool disconnected = false) {
  detachTcpFolder(disconnected);
  if (seeder_client) seeder_client.stop();
}

}  // namespace

void WiFiOtaSeeder::loop() {
  using WiFiOtaSeederPolicy::ListenerAction;
  switch (WiFiOtaSeederPolicy::listenerAction(networkReady(), listener_active)) {
    case ListenerAction::Start:
      seeder_server.begin();
      listener_active = true;
      mesh::usbLoggingPort().printf(
          "OTA seeder listening on :%u (motatool serve --tcp)\n",
          static_cast<unsigned>(OTA_SEEDER_TCP_PORT));
      break;
    case ListenerAction::Stop:
      stopClient(true);
      seeder_server.end();
      listener_active = false;
      return;
    case ListenerAction::Keep:
      break;
  }

  if (!listener_active) return;

  // An idle listener must leave all 256 queue slots available. A CLI detach
  // can also return the shared context before the next WiFi poll.
  OtaContext* active = ota_context_if_active();
  if (WiFiOtaSeederPolicy::tcpFolderWasDetached(tcp_folder_attached,
          active && active->folder_active &&
          active->folderLink() == OtaContext::FOLDER_LINK_TCP)) {
    if (seeder_client) seeder_client.stop();
    // A different host transport may already own the replacement context.
    if (active && !active->folder_active) active->clear_folder_dest();
    tcp_folder_attached = false;
  }
  if (seeder_client && seeder_client.connected()) return;

  stopClient(true);
  WiFiClient incoming = seeder_server.available();
  if (!incoming) return;

  incoming.setNoDelay(true);                       // tiny framed requests should leave immediately

  char attach_reply[120];
  if (!ota_acquire_context(attach_reply, sizeof(attach_reply))) {
    incoming.stop();
    mesh::usbLoggingPort().printf(
        "OTA seeder rejected TCP client: %s\n", attach_reply);
    return;
  }
  OtaContext& context = ota_ctx();
  if (!WiFiOtaSeederPolicy::canAttachTcpFolder(context.folder_active,
                                                tcp_folder_attached)) {
    incoming.stop();
    mesh::usbLoggingPort().println(
        "OTA seeder rejected TCP client: another folder link is active");
    return;
  }

  seeder_client = incoming;
  if (!context.attach_folder_source(&seeder_source, OtaContext::FOLDER_LINK_TCP,
                                    "tcp", attach_reply, sizeof(attach_reply))) {
    seeder_client.stop();
    mesh::usbLoggingPort().printf(
        "OTA seeder rejected TCP client: %s\n", attach_reply);
    return;
  }

  tcp_folder_attached = true;
  char link_info[24];
  snprintf(link_info, sizeof(link_info), "tcp %s",
           seeder_client.remoteIP().toString().c_str());
  context.set_folder_dest(&folder_store, link_info);
  context.manager.announce();
  mesh::usbLoggingPort().printf(
      "OTA seeder client connected (%s)\n", link_info);
}

void WiFiOtaSeeder::stop() {
  stopClient();
  if (listener_active) seeder_server.end();
  listener_active = false;
}

bool WiFiOtaSeeder::isListening() {
  return listener_active;
}

bool WiFiOtaSeeder::isAttached() {
  return tcp_folder_attached;
}

bool WiFiOtaSeeder::appendStatus(char* reply, size_t capacity) {
  return WiFiOtaSeederStatus::append(
      reply, capacity, listener_active, tcp_folder_attached,
      OTA_SEEDER_TCP_PORT);
}

}  // namespace ota
}  // namespace mesh

#endif
