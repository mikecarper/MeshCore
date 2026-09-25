#pragma once

#include <WiFi.h>
#include <helpers/esp32/WiFiOtaSeeder.h>
#include <helpers/ota/FolderMotaStore.h>
#include <helpers/ota/MotaSeederProto.h>
#include <helpers/ota/OtaByteIO.h>

// Simulate only the TCP endpoint/host. The listener, stream framing, context,
// queue, mOTA manager, source reader and complete transferred bytes are real.
static std::shared_ptr<MockTcpState> connect_host() {
  auto state = std::make_shared<MockTcpState>();
  state->respond = [](const uint8_t* request, size_t length) {
    assert(length >= 4 && request[0] == 'M' && request[1] == 'S');
    uint8_t checksum = 0;
    for (size_t i = 2; i < length; ++i) checksum ^= request[i];
    assert(checksum == 0);
    std::vector<uint8_t> payload;
    const uint8_t op = request[2];
    if (op == MS_OP_COUNT) {
      payload.push_back(1);
    } else if (op == MS_OP_DESCRIBE) {
      assert(request[3] == 0);
      MotaDesc desc;
      Folder folder;
      assert(folder.describe(0, desc));
      payload.resize(MOTA_DESC_WIRE);
      memcpy(payload.data(), desc.mid, 4);
      wr_u32le(payload.data() + 4, desc.target_id);
      wr_u32le(payload.data() + 8, desc.fw_version);
      payload[12] = desc.codec_id;
      payload[13] = desc.flags;
      wr_u32le(payload.data() + 14, desc.total_size);
      wr_u32le(payload.data() + 18, desc.leaves_off);
      wr_u32le(payload.data() + 22, desc.block_count);
      wr_u32le(payload.data() + 26, desc.payload_off);
      wr_u32le(payload.data() + 30, desc.payload_size);
      payload[34] = desc.block_size_log2;
    } else {
      assert(op == MS_OP_READ && length == 11 && request[3] == 0);
      uint32_t offset = rd_u32le(request + 4);
      uint16_t count = request[8] | (uint16_t(request[9]) << 8);
      assert(uint64_t(offset) + count <= MOTA_VEC_LEN);
      payload.assign(MOTA_VEC + offset, MOTA_VEC + offset + count);
    }
    std::vector<uint8_t> reply = {'m', 's', op, MS_STATUS_OK};
    reply.insert(reply.end(), payload.begin(), payload.end());
    checksum = 0;
    for (uint8_t byte : reply) checksum ^= byte;
    reply.push_back(checksum);
    return reply;
  };
  WiFiServer::pending.emplace_back(state);
  return state;
}

template<class CheckMessages>
static void test_wifi_shared_queue(Queue& q, CheckMessages check_messages) {
  WiFiOtaSeeder::stop(); // stopping before startup never allocates
  WiFiOtaSeeder::loop();
  assert(!WiFiOtaSeeder::isListening() && !ota_context_if_active());
  WiFi.connected = true;
  for (int i = 0; i < 100; ++i) WiFiOtaSeeder::loop();
  assert(WiFiOtaSeeder::isListening() && !ota_context_if_active());
  assert(q.buffer.capacity() == 256);

  q.count = 129;
  Frame& extra = q.buffer.at((q.head + 128) % 256);
  extra.len = 176;
  memset(extra.buf, 129, sizeof extra.buf);
  auto refused = connect_host();
  WiFiOtaSeeder::loop();
  assert(!refused->connected && refused->requests == 0);
  assert(!ota_context_if_active() && q.buffer.capacity() == 256);
  assert(mesh::usbLoggingPort().output.find("sync unread messages") != std::string::npos);
  check_messages();
  --q.count;

  for (int cycle = 0; cycle < 12; ++cycle) {
    WiFi.connected = true;
    auto host = connect_host();
    WiFiOtaSeeder::loop();
    assert(host->connected && host->no_delay && WiFiOtaSeeder::isAttached());
    assert(q.buffer.capacity() == 128 && ota_ctx().folder_dest);
    assert(ota_ctx().folderLink() == OtaContext::FOLDER_LINK_TCP);
    assert(ota_ctx().manager.servedCount() == 1);
    transfer(ota_ctx(), &q);
    assert(host->requests > 2 && host->flushes == 0);
    ota_release_context_if_idle(false);
    assert(ota_context_if_active()); // a connected host owns the loan
    check_messages();
    switch (cycle % 4) {
      case 0: // TCP disconnect while listener remains available
        host->connected = false;
        WiFiOtaSeeder::loop();
        break;
      case 1: // WiFi loss
        WiFi.connected = false;
        WiFiOtaSeeder::loop();
        assert(!WiFiOtaSeeder::isListening());
        break;
      case 2: // CLI off can return the context before the listener sees it
        ota_ctx().detach_folder();
        ota_ctx().clear_folder_dest();
        ota_release_context_if_idle(true);
        assert(!ota_context_if_active());
        WiFiOtaSeeder::loop();
        break;
      case 3: WiFiOtaSeeder::stop(); break;
    }
    ota_release_context_if_idle(true);
    assert(!host->connected && !WiFiOtaSeeder::isAttached());
    assert(!ota_context_if_active() && q.buffer.capacity() == 256);
    check_messages();
    packets.clear();
  }

  // An incoming TCP client and listener stop must not detach a USB source.
  char reply[160];
  assert(ota_acquire_context(reply, sizeof reply));
  Folder usb;
  assert(ota_ctx().attach_folder_source(&usb, OtaContext::FOLDER_LINK_SERIAL,
                                        "USB", reply, sizeof reply));
  WiFi.connected = true;
  auto conflict = connect_host();
  WiFiOtaSeeder::loop();
  assert(!conflict->connected && !WiFiOtaSeeder::isAttached());
  WiFiOtaSeeder::stop();
  ota_release_context_if_idle(false);
  assert(ota_ctx().folderLink() == OtaContext::FOLDER_LINK_SERIAL);
  ota_ctx().detach_folder();
  ota_release_context_if_idle(true);
  assert(!ota_context_if_active() && q.buffer.capacity() == 256);

  // TCP's stale attached flag must not clear a new USB capture destination.
  auto old_host = connect_host();
  WiFiOtaSeeder::loop();
  assert(WiFiOtaSeeder::isAttached());
  ota_ctx().detach_folder();
  ota_ctx().clear_folder_dest();
  ota_release_context_if_idle(true);
  assert(ota_acquire_context(reply, sizeof reply));
  assert(ota_ctx().attach_folder_source(&usb, OtaContext::FOLDER_LINK_SERIAL,
                                        "USB", reply, sizeof reply));
  WiFiClient usb_stream;
  FolderMotaStore usb_dest(usb_stream, MotaStreamWritePolicy::NoFlush);
  ota_ctx().set_folder_dest(&usb_dest, "USB");
  WiFiOtaSeeder::loop();
  assert(!old_host->connected && !WiFiOtaSeeder::isAttached());
  assert(ota_ctx().folder_dest == &usb_dest);
  assert(ota_ctx().folderLink() == OtaContext::FOLDER_LINK_SERIAL);
  ota_ctx().detach_folder();
  ota_ctx().clear_folder_dest();
  ota_release_context_if_idle(true);
  WiFiOtaSeeder::stop();
  assert(!ota_context_if_active() && q.buffer.capacity() == 256);
  check_messages();
  packets.clear();
}
