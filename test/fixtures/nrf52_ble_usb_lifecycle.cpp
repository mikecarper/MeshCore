
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <helpers/BleTxStallWatchdog.h>
#include <helpers/nrf52/SecuritySessionTimer.h>
#include <helpers/nrf52/BleRxFrameQueue.h>
#define BLE_CONN_HANDLE_INVALID 0xffff
#define BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION 0x13
#define BLE_HEALTH_CHECK_INTERVAL 10000
#define BLE_COMPANION_START_TIMEOUT_MS 15000
#define BLE_MIN_CONN_INTERVAL 12
#define BLE_MAX_CONN_INTERVAL 24
#define BLE_SLAVE_LATENCY 4
#define BLE_CONN_SUP_TIMEOUT 200
#define NRF_SUCCESS 0
#define NRF_ERROR_INVALID_STATE 8
#define COMPANION_FEATURE_BLE_MOTA_SOURCE 0
#define BLE_DEBUG_PRINTLN(...) do {} while(0)
#define meshBleTrace(...) do {} while(0)
static uint32_t clock_ms=100;
static bool physical_link=false;
static unsigned disconnect_requests=0;
static uint16_t last_disconnect_handle=0xffff;
uint32_t millis(){return clock_ms;}
struct ble_gap_addr_t {};
struct ble_gap_conn_params_t {uint16_t min_conn_interval,max_conn_interval,slave_latency,conn_sup_timeout;};
struct BLEConnection {
 bool connected()const{return physical_link;}
 bool secured()const{return true;}
 bool bonded()const{return true;}
 ble_gap_addr_t getPeerAddr()const{return {};}
};
struct {
 BLEConnection connection;
 struct {
  bool running=false,auto_restart=false;
  void restartOnDisconnect(bool enabled){auto_restart=enabled;}
  bool stop(){running=false;return true;}
  bool start(unsigned){running=!physical_link;return running;}
  bool isRunning()const{return running;}
 } Advertising;
 BLEConnection* Connection(uint16_t handle){return physical_link&&handle==7?&connection:nullptr;}
 unsigned connected()const{return physical_link?1:0;}
} Bluefruit;
uint32_t sd_ble_gap_disconnect(uint16_t handle,uint8_t){++disconnect_requests;last_disconnect_handle=handle;return physical_link?NRF_SUCCESS:NRF_ERROR_INVALID_STATE;}
uint32_t sd_ble_gap_conn_param_update(uint16_t,const ble_gap_conn_params_t*){return NRF_SUCCESS;}
struct SerialBLEInterface {
 struct Guard {};
 struct Frame {uint8_t len=0;uint8_t buf[176]={};};
 using RxQueueGuard=Guard;
 mesh_nrf52::BleRxFrameQueue<Frame,12,Guard> recv_queue;
 struct {void flush(){}} bleuart;
 bool _isEnabled=false,_isDeviceConnected=false,_bonded_only=false,_peer_address_valid=false;
 uint16_t _conn_handle=BLE_CONN_HANDLE_INVALID;
 uint32_t _last_health_check=0,_last_retry_attempt=0;
 std::atomic<bool> _tx_reset_pending{false},_pairingRequestPending{false},_companionDataSeen{false};
 std::atomic<bool> _advertisingSuppressed{false},_bondedOnlyRecoveryPending{false};
 uint8_t send_queue_len=0;
 uint16_t _tx_recovery_handle=BLE_CONN_HANDLE_INVALID;
 uint32_t _tx_recovery_generation=0;
 mesh::BleTxStallWatchdog _tx_stall_watchdog;
 mesh::BleDisconnectRecovery _tx_disconnect_recovery;
 SecuritySessionTimer _security_timer,_companion_start_timer;
 unsigned recovery_services=0;
 void clearBuffers();void resetTxState();bool servicePendingTxReset();
 void enable();void disable();void disconnect();void loop();
 bool advertisingAllowed()const;bool isAdvertising()const;
 bool startAdvertising(const char*);bool isConnected()const;
 bool isValidConnection(uint16_t,bool waiting=false)const;
 void recoverStalledTx(const char*,uint16_t handle=BLE_CONN_HANDLE_INVALID,uint32_t generation=0);
 void serviceTxRecovery(uint32_t);
 void serviceBondedOnlyTransition(){}
 bool removeStoredBondForPeer(const char*){return false;}
 void requestBondedOnlyRecovery(const char*){}
 void noteSuccessfulConnection(const ble_gap_addr_t&){}
 static void onConnect(uint16_t);static void onSecured(uint16_t);static void onDisconnect(uint16_t,uint8_t);
};
static SerialBLEInterface* instance=nullptr;
@METHODS@


static void normal_session() {
 physical_link=false;disconnect_requests=0;clock_ms=100;
 SerialBLEInterface link;instance=&link;link.enable();
 physical_link=true;Bluefruit.Advertising.running=false;link.recv_queue.connect(7);
 SerialBLEInterface::onConnect(7);SerialBLEInterface::onSecured(7);
 assert(link.isConnected());
 clock_ms=15100;link.loop();
 assert(link._tx_disconnect_recovery.pending()&&disconnect_requests==1);
}
static void finish_disconnect(SerialBLEInterface& link) {
 physical_link=false;link.recv_queue.disconnect(7);
 SerialBLEInterface::onDisconnect(7,BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
 clock_ms+=11000;link.loop();
 assert(link._conn_handle==BLE_CONN_HANDLE_INVALID&&!link.isConnected());
 assert(Bluefruit.Advertising.running);
}
static void callback_crosses_disable_enable(bool delayed_security_only) {
 physical_link=false;disconnect_requests=0;clock_ms=100;
 SerialBLEInterface link;instance=&link;link.enable();
 physical_link=true;Bluefruit.Advertising.running=false;link.recv_queue.connect(7);
 if(delayed_security_only)SerialBLEInterface::onConnect(7);
 link.disable();clock_ms+=501;link.enable();
 if(!delayed_security_only)SerialBLEInterface::onConnect(7);
 SerialBLEInterface::onSecured(7);
 uint32_t generation=0;
 assert(link._isEnabled&&!link._isDeviceConnected&&physical_link);
 assert(!link.recv_queue.snapshot(7,generation)&&!link.isConnected());
 assert(disconnect_requests>=1&&last_disconnect_handle==7);
 link.loop();assert(link._tx_disconnect_recovery.pending());
 unsigned before=disconnect_requests;clock_ms+=999;link.loop();assert(disconnect_requests==before);
 clock_ms+=1;link.loop();assert(disconnect_requests==before+1);
 finish_disconnect(link);
}
static void reused_handle_and_late_disconnect() {
 physical_link=false;disconnect_requests=0;clock_ms=100;
 SerialBLEInterface link;instance=&link;link.enable();
 physical_link=true;link.recv_queue.connect(7);SerialBLEInterface::onConnect(7);SerialBLEInterface::onSecured(7);
 link.recv_queue.disconnect(7); // Raw disconnect invalidates the old epoch.
 link.recv_queue.connect(7); // A replacement physical connection reuses7.
 SerialBLEInterface::onConnect(7);SerialBLEInterface::onSecured(7);
 uint32_t generation;assert(link.recv_queue.snapshot(7,generation));
 SerialBLEInterface::Frame frame;frame.len=1;frame.buf[0]=0x42;
 assert(link.recv_queue.push(7,generation,frame)==decltype(link.recv_queue)::PushResult::Accepted);
 SerialBLEInterface::onDisconnect(7,BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
 assert(link._conn_handle==7&&link.isConnected()&&link.recv_queue.size()==1);
 SerialBLEInterface::onConnect(8); // A stale different handle cannot replace7.
 assert(last_disconnect_handle==8&&link._conn_handle==7&&link.isConnected());
}
static void closed_physical_link_cannot_block_advertising() {
 physical_link=false;disconnect_requests=0;clock_ms=100;
 SerialBLEInterface link;instance=&link;link.enable();link._conn_handle=7;
 Bluefruit.Advertising.running=false;link.loop();
 assert(link._conn_handle==BLE_CONN_HANDLE_INVALID&&Bluefruit.Advertising.running);
}
static void repeated_idle_usb_cycles_restore_advertising() {
 physical_link=false;clock_ms=100;SerialBLEInterface link;instance=&link;
 for(unsigned n=0;n<5;++n){link.enable();assert(Bluefruit.Advertising.running);link.disable();assert(!Bluefruit.Advertising.running);clock_ms+=501;}
 link.enable();assert(Bluefruit.Advertising.running);
}
int main(){normal_session();callback_crosses_disable_enable(false);callback_crosses_disable_enable(true);
 reused_handle_and_late_disconnect();closed_physical_link_cannot_block_advertising();repeated_idle_usb_cycles_restore_advertising();}
