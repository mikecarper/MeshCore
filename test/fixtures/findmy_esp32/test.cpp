
#include "helpers/findmy/FindMyEsp32.h"
#include <cassert>
int main() {
  mesh::findmy::Esp32Beacon b;
  uint8_t addr[6]={0xc1,2,3,4,5,6}, payload[29]={0x4c,0};
  controller=ESP_BT_CONTROLLER_STATUS_ENABLED;
  assert(!b.start(addr,payload));assert(controller==ESP_BT_CONTROLLER_STATUS_ENABLED && init_count==0);
  assert(b.stop());assert(controller==ESP_BT_CONTROLLER_STATUS_ENABLED);
  controller=0;assert(b.start(addr,payload));assert(b.isActive());
  assert(!memcmp(addr,captured_addr,6));assert(captured_raw[0]==30&&captured_raw[1]==0xff);
  assert(!memcmp(payload,captured_raw+2,29));assert(b.stop());
  assert(!b.isActive()&&host==0&&controller==0&&stop_count==1);
  fail_address=1;assert(!b.start(addr,payload));assert(host==0&&controller==0);fail_address=0;
  fail_data=1;assert(!b.start(addr,payload));assert(host==0&&controller==0);fail_data=0;
  fail_start=1;assert(!b.start(addr,payload));assert(host==0&&controller==0&&!b.isActive());fail_start=0;
  assert(b.start(addr,payload));assert(b.stop());assert(host==0&&controller==0);

  // Failed stop and host disable leave the advertising state and ownership
  // intact. Another owner cannot use the controller until a retry succeeds.
  mesh::findmy::Esp32Beacon other;
  assert(b.start(addr,payload));fail_adv_stop=1;fail_host_disable=1;
  assert(!b.stop());assert(b.isActive());assert(!other.start(addr,payload));
  fail_adv_stop=0;fail_host_disable=0;assert(b.stop());assert(!b.isActive());

  // Once the host is disabled, later teardown failures still retain ownership
  // but the beacon is no longer advertising.
  assert(b.start(addr,payload));fail_host_deinit=1;
  assert(!b.stop());assert(!b.isActive());assert(!other.start(addr,payload));
  fail_host_deinit=0;assert(b.stop());
  assert(b.start(addr,payload));fail_controller_disable=1;
  assert(!b.stop());assert(!b.isActive());assert(!other.start(addr,payload));
  fail_controller_disable=0;assert(b.stop());
  assert(b.start(addr,payload));fail_controller_deinit=1;
  assert(!b.stop());assert(!b.isActive());assert(!other.start(addr,payload));
  fail_controller_deinit=0;assert(b.stop());
  assert(other.start(addr,payload));assert(other.stop());
}
