#include <helpers/UsbLogging.h>
#include <helpers/UsbMode.h>
#include <helpers/UsbLoggingStatus.h>
#include <cassert>
#include <cstring>
#include <cstdio>
#include <functional>
MockUart Serial1;
UnusedNativeUsb Serial;
void yield() {}
static constexpr uint32_t USB_CLIENT_IDLE_TIMEOUT=10*60*1000UL;
struct FrameLease {
  bool received=false;
  uint32_t at=0;
  std::function<bool()> connected;
  bool hasReceivedFrame() const { return received; }
  uint32_t getLastFrameMillis() const { return at; }
  void setConnectedCheck(std::function<bool()> callback) { connected=callback; }
} usb_serial_interface;

int main() {
  static_assert(MESH_PRIMARY_SERIAL_IS_UART, "explicit UART required");
  static_assert(!MESH_NRF52_USB_CONSOLE_COOPERATIVE, "not a native USB backend");
  assert(!strcmp(mesh::usbModeName<decltype(Serial1)>(), "uart"));
  assert(!strcmp(mesh::usbModeName<decltype(Serial)>(), "unknown"));
  assert(!mesh::isUsbLoggingEnabled());
  mesh::beginUsbLoggingPort();mesh::serviceUsbLoggingPort();
  assert(!mesh::hasDedicatedUsbLoggingPort());
  assert(!mesh::isUsbCompanionClientConnected());
  @BINARY_CONNECTION@
  assert(!usb_serial_interface.connected());
  assert(!mesh::takeUsbTerminalSessionReset());
  assert(mesh::tryCompleteUsbTerminalSessionReset());
  assert(!mesh::isUsbLoggingTransportRecoveryPending());
  assert(mesh::recoverUsbLoggingTransport(1)==mesh::UsbLoggingRecoveryResult::Unsupported);

  auto& binary=mesh::usbCompanionPort();
  Serial1.input={0x12,0x34};
  assert(mesh::isUsbCompanionClientConnected());
  assert(!usb_serial_interface.connected());
  usb_serial_interface.received=true;
  assert(usb_serial_interface.connected());
  assert(binary.peek()==0x12);assert(binary.available()==2);
  assert(binary.read()==0x12);assert(binary.read()==0x34);
  g_mock_millis=600001;assert(!mesh::isUsbCompanionClientConnected());
  assert(!usb_serial_interface.connected());
  g_mock_millis=0;assert(!mesh::isUsbCompanionClientConnected());
  g_mock_millis=UINT32_MAX-5;Serial1.input={0x56};
  assert(binary.read()==0x56);g_mock_millis=10;
  assert(mesh::isUsbCompanionClientConnected());
  g_mock_millis=600010;assert(!mesh::isUsbCompanionClientConnected());

  const uint8_t frame[]={1,2,3,4};Serial1.capacity=2;
  assert(binary.write(frame,sizeof(frame))==2);
  assert(binary.write(frame,sizeof(frame))==0);binary.flush();
  auto& terminal=mesh::usbTerminalPort();
  assert(terminal.write(frame,sizeof(frame))==sizeof(frame));
  assert(mesh::hasPendingUsbTerminalOutput());
  Serial1.capacity=2;mesh::serviceUsbTerminalPort();
  assert(mesh::hasPendingUsbTerminalOutput());
  Serial1.capacity=2;mesh::serviceUsbTerminalPort();
  assert(!mesh::hasPendingUsbTerminalOutput());

  assert(terminal.write(frame,sizeof(frame))==sizeof(frame));
  mesh::setUsbBinaryStreamOwned(true);
  assert(!mesh::hasPendingUsbTerminalOutput());
  const auto count=Serial1.output.size();
  assert(terminal.write(frame,sizeof(frame))==sizeof(frame));
  Serial1.capacity=64;mesh::serviceUsbTerminalPort();
  assert(Serial1.output.size()==count);
  mesh::setUsbLoggingEnabled(true);mesh::setUsbDebugEnabled(true);
  assert(mesh::usbLoggingPort().write(frame,sizeof(frame))==sizeof(frame));
  assert(Serial1.output.size()==count);
  mesh::setUsbBinaryStreamOwned(false);
  assert(mesh::usbDebugPort().write(frame,sizeof(frame))==sizeof(frame));
  assert(Serial1.output.size()==count+sizeof(frame));
  assert(mesh::isUsbLoggingEnabled()&&mesh::isUsbDebugEnabled());

  auto& mota=mesh::usbMotaPort();Serial1.capacity=3;
  assert(mota.write(frame,sizeof(frame))==0);
  Serial1.capacity=4;assert(mota.write(frame,sizeof(frame))==sizeof(frame));
  auto& logger=mesh::usbLoggingPort();
  logger.flush(); // Must never forward HardwareSerial::flush's wire wait.
  const auto before_reentrant=Serial1.output.size();
  Serial1.capacity=4;
  Serial1.duringWrite=[&]() { assert(logger.write(frame,sizeof(frame))==0); };
  assert(binary.write(frame,sizeof(frame))==sizeof(frame));
  assert(Serial1.output.size()==before_reentrant+sizeof(frame));
  Serial1.capacity=4;
  Serial1.duringWrite=[&]() { assert(binary.write(frame,sizeof(frame))==0); };
  assert(logger.write(frame,sizeof(frame))==sizeof(frame));
  assert(Serial1.output.size()==before_reentrant+2*sizeof(frame));
  assert(!mesh::observeUsbLoggingTransport().supported);
  assert(strstr(mesh::usbLoggingPortDescription(), "UART"));
  puts("nRF52 UART bridge console transport passed");
}
