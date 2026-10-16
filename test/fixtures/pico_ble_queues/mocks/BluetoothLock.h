#pragma once
#include <btstack.h>
struct BluetoothLock {
  BluetoothLock() { ++fake_pico::lock_depth; }
  ~BluetoothLock() { --fake_pico::lock_depth; }
};
